"""AI Resume - Generate tailored resume for specific jobs."""

import os
from pathlib import Path

from rich.console import Console

from bosshunter.db import get_db

console = Console()

RESUME_TAILOR_PROMPT = """你是一位专业简历顾问。请根据以下岗位JD调整简历的侧重点。

规则：
1. 不要虚构任何信息，只能使用原简历中已有的内容
2. 只调整顺序、强调程度、措辞表达
3. 输出完整的Markdown格式简历
4. 针对岗位要求突出相关经验，弱化不相关部分
5. 保持简历整体结构完整

## 岗位信息
- 职位：{title}
- 公司：{company}
- 薪资：{salary}
- 核心要求：
{jd}

## 原始简历
{resume}

请直接输出调整后的Markdown简历：
"""


def _call_claude(prompt: str, config: dict) -> str | None:
    """Call Claude API and return response text."""
    try:
        import anthropic
    except ImportError:
        return None

    ai_cfg = config.get("ai", {})
    api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or ai_cfg.get("api_key")
    if not api_key:
        return None

    model = ai_cfg.get("model", "claude-sonnet-4-6")
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or ai_cfg.get("base_url")

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url

    client = anthropic.Anthropic(**kwargs)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        # Skip ThinkingBlock, find TextBlock
        for block in response.content:
            if hasattr(block, 'text'):
                return block.text.strip()
        return None
    except Exception as e:
        console.print(f"[red]API 调用失败: {e}[/red]")
        return None


def _render_pdf(markdown_text: str, output_path: Path) -> bool:
    """Render markdown to PDF via Chrome CDP (Page.printToPDF).

    Falls back to xhtml2pdf if CDP is unavailable.
    """
    import markdown2

    # Convert markdown to HTML
    html_body = markdown2.markdown(markdown_text, extras=["tables", "fenced-code-blocks"])

    # Wrap with CJK-friendly CSS
    full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{
        font-family: "Microsoft YaHei", "SimSun", "WenQuanYi Micro Hei", sans-serif;
        font-size: 11pt;
        line-height: 1.6;
        margin: 40px;
        color: #333;
    }}
    h1 {{ font-size: 18pt; color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 5px; }}
    h2 {{ font-size: 14pt; color: #2c3e50; margin-top: 20px; }}
    h3 {{ font-size: 12pt; color: #34495e; }}
    ul {{ padding-left: 20px; }}
    li {{ margin-bottom: 4px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
    th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
    th {{ background: #f5f5f5; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    # Strategy 1: Use Chrome CDP to print PDF (preferred, no extra deps)
    if _render_pdf_via_cdp(full_html, output_path):
        return True

    # Strategy 2: Fallback to xhtml2pdf (requires cairo on Windows)
    try:
        from xhtml2pdf import pisa
    except (ImportError, OSError):
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        status = pisa.CreatePDF(full_html, dest=f, encoding="utf-8")
    return not status.err


def _render_pdf_via_cdp(html_content: str, output_path: Path) -> bool:
    """Use Chrome CDP Page.printToPDF via proxy /pdf endpoint."""
    try:
        import httpx
    except ImportError:
        return False

    import tempfile
    import time

    CDP_PROXY_URL = "http://localhost:3456"

    try:
        # Check proxy is alive
        resp = httpx.get(f"{CDP_PROXY_URL}/health", timeout=3)
        if resp.status_code != 200:
            return False
    except (httpx.ConnectError, httpx.TimeoutException):
        return False

    # Write HTML to temp file
    temp_html = Path(tempfile.gettempdir()) / "bosshunter_resume.html"
    temp_html.write_text(html_content, encoding="utf-8")
    file_url = f"file:///{temp_html.as_posix()}"

    target_id = None
    try:
        # Open blank tab and navigate to HTML file
        resp = httpx.get(f"{CDP_PROXY_URL}/new?url={file_url}", timeout=15)
        if resp.status_code != 200:
            return False
        target_id = resp.json().get("targetId")
        if not target_id:
            return False

        time.sleep(2)

        # Export PDF via proxy /pdf endpoint
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_file = str(output_path.resolve()).replace("\\", "/")
        resp = httpx.get(
            f"{CDP_PROXY_URL}/pdf?target={target_id}&file={pdf_file}",
            timeout=30
        )

        if resp.status_code == 200:
            # Verify file was created
            if output_path.exists() and output_path.stat().st_size > 0:
                return True

        return False
    except Exception:
        return False
    finally:
        # Clean up
        if target_id:
            try:
                httpx.get(f"{CDP_PROXY_URL}/close?target={target_id}", timeout=5)
            except Exception:
                pass
        temp_html.unlink(missing_ok=True)


def generate_tailored_resume(job_id: str, config: dict) -> Path | None:
    """Generate a tailored resume for a specific job.

    Returns path to generated file, or None on failure.
    """
    db = get_db()

    # Get job info
    row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        console.print(f"[red]未找到岗位 ID: {job_id}[/red]")
        db.close()
        return None

    job = dict(row)

    # Load base resume
    resume_path = Path(config.get("profile", {}).get("resume_path", "./resume.md"))
    if not resume_path.exists():
        console.print("[red]简历文件不存在[/red]")
        db.close()
        return None

    resume_text = resume_path.read_text(encoding="utf-8")

    # Generate tailored resume via AI
    console.print(f"[bold]为 {job['company']} - {job['title']} 生成定制简历...[/bold]")

    prompt = RESUME_TAILOR_PROMPT.format(
        title=job["title"],
        company=job["company"],
        salary=job["salary"] or "面议",
        jd=job["jd"][:2000] if job["jd"] else "无详细描述",
        resume=resume_text,
    )

    tailored_md = _call_claude(prompt, config)
    if not tailored_md:
        console.print("[red]生成失败[/red]")
        db.close()
        return None

    # Determine output directory
    output_dir = Path(config.get("profile", {}).get("resume_output_dir", "./data/resumes"))
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build filename: company_title_jobid (sanitize for filesystem)
    safe_company = "".join(c for c in job["company"] if c not in r'\/:*?"<>|')[:20]
    safe_title = "".join(c for c in job["title"] if c not in r'\/:*?"<>|')[:20]
    base_name = f"{safe_company}_{safe_title}_{job_id}"

    # Save markdown version
    md_path = output_dir / f"{base_name}.md"
    md_path.write_text(tailored_md, encoding="utf-8")

    # Try PDF rendering
    pdf_path = output_dir / f"{base_name}.pdf"
    if _render_pdf(tailored_md, pdf_path):
        console.print(f"[green]✓ PDF 已生成: {pdf_path}[/green]")
        # Update DB
        db.execute(
            "UPDATE jobs SET resume_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(pdf_path), job_id)
        )
        db.commit()
        db.close()
        return pdf_path
    else:
        console.print(f"[yellow]PDF 渲染库未安装，已保存为 Markdown: {md_path}[/yellow]")
        console.print('[dim]  安装 PDF fallback 支持: pip install -e ".[pdf]"[/dim]')
        db.execute(
            "UPDATE jobs SET resume_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(md_path), job_id)
        )
        db.commit()
        db.close()
        return md_path


def generate_all_resumes(config: dict) -> int:
    """Generate tailored resumes for all scored jobs. Returns count generated."""
    db = get_db()
    threshold = config.get("scoring", {}).get("threshold", 60)

    # Get scored jobs without resume
    rows = db.execute(
        "SELECT id FROM jobs WHERE status IN ('scored', 'ready', 'approved') AND score >= ? AND resume_path IS NULL",
        (threshold,)
    ).fetchall()

    if not rows:
        console.print("[yellow]没有需要生成简历的岗位[/yellow]")
        db.close()
        return 0

    db.close()
    count = 0
    for row in rows:
        result = generate_tailored_resume(row["id"], config)
        if result:
            count += 1

    console.print(f"\n[green]✓ 共生成 {count} 份定制简历[/green]")
    return count
