"""AI Resume - Generate tailored resume for specific jobs."""

import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from rich.console import Console

from bosshunter.ai.credentials import call_anthropic_text
from bosshunter.browser import close_tab, new_tab, print_pdf, wait_for_load
from bosshunter.cancellation import OperationCancelled, run_cancellable, stop_requested
from bosshunter.db import get_db

console = Console()

RESUME_COMPLETION_MARKER = "<!-- BOSSHUNTER_RESUME_DONE -->"
DEFAULT_RESUME_MAX_PAGES = 3
DEFAULT_RESUME_CHARS_PER_PAGE = 1400

RESUME_TAILOR_PROMPT = """你是一位专业简历顾问。请输出一份正常投递用的 Markdown 简历。

规则：
1. 只输出简历正文，不输出任何前言、说明、备注、免责声明
2. 不要虚构任何信息，只能使用候选人简历中已有的内容
3. 只调整顺序、强调程度、措辞表达，保持简历整体结构完整
4. 相关优势只能自然融入个人优势、专业技能、工作经历、项目经历、个人总结等常规栏目
5. 不允许单独新增“岗位匹配亮点”“补充说明”等解释性栏目
6. 不允许把岗位要求里的职责写成候选人已经做过的经历
7. 输出中不得出现“以下内容基于”“基于原始简历”“原始简历事实”“未虚构”“针对该岗位”等过程性表达
8. 默认控制在 {resume_max_pages} 页以内，只保留与目标岗位相关的内容，不相关内容删除
9. 项目经历必须按岗位相关度排序，最相关项目放在最前；弱相关项目一句带过或删除
10. 先在内部拆解岗位JD，尽可能覆盖岗位JD中的职责和要求；无法用候选人真实经历支撑的要求不要硬编
11. 不要输出JD逐条对照、覆盖情况、匹配说明，只把真实可支撑的匹配点自然写入简历正文
12. 必须围绕岗位标题和核心要求重排内容，首屏突出最相关经历，不要几乎照搬原简历
13. 可以压缩或删除弱相关项目，但必须完整保留常规简历结构，尤其是基本信息、个人优势、工作经历、教育经历、相关技能
14. 如果岗位涉及媒体、PR、公关、传播、科技记者，请优先突出已有的新媒体内容、品牌传播、媒体资源、专家访谈、公众号/视频号、技术型业务表达经验
15. 如果岗位涉及小红书、抖音、短视频、内容运营、AIGC内容、热点资讯、平台增长，请优先保留候选人已有的平台案例和量化结果，包括阅读/观看、点赞收藏、粉丝增长、用户群运营等真实证据
16. 必须输出完整简历，不得半句结束；最后一行单独输出 {completion_marker}，系统保存前会自动移除该行

## 投递岗位
- 职位：{title}
- 公司：{company}
- 薪资：{salary}
- 学历要求：{education}
- 招聘类型：{recruitment_type}
- 核心要求：
{jd}

## 候选人简历
{resume}

请直接输出 Markdown 简历正文：
"""

RESUME_RETRY_PROMPT = """{base_prompt}

上一次生成结果质量检查未通过，原因如下：
{quality_issues}

请重新生成一版。要求：
1. 必须压缩到 {resume_max_pages} 页以内，正文非空白字符不超过 {resume_max_chars} 个
2. 优先删除弱相关、重复、解释性内容，保留最能支撑岗位JD的真实经历和量化结果
3. 不要新增任何候选人原简历中没有的事实
4. 仍然必须保留基本信息、个人优势、工作经历、教育经历、相关技能
5. 最后一行仍然单独输出 {completion_marker}

请直接输出压缩后的 Markdown 简历正文：
"""

RESUME_ARTIFACT_PHRASES = [
    "以下内容基于",
    "基于原始简历",
    "根据原始简历",
    "根据岗位JD",
    "岗位匹配亮点",
    "匹配该岗位",
    "结合岗位要求",
    "补充说明",
    "原始简历事实",
    "不虚构",
    "未虚构",
    "本次优化",
    "调整后的简历",
    "定制简历",
    "以下为优化后的",
    "针对该岗位",
    "针对本岗位",
    "岗位中的",
    "高度相关",
    "字节岗位",
    "可迁移到",
    "岗位要求",
    "高度匹配",
    "高度贴合",
    "高度适配",
    "JD逐条对照",
    "岗位JD覆盖",
    "逐条对照",
    "覆盖情况",
    "匹配说明",
    "无法覆盖",
]

REQUIRED_RESUME_SECTIONS = [
    "## 基本信息",
    "## 个人优势",
    "## 工作经历",
    "## 教育经历",
    "## 相关技能",
]

ROLE_KEYWORD_GROUPS = [
    {
        "name": "媒体/PR/传播",
        "triggers": ("PR", "媒体", "公关", "传播", "记者", "舆情"),
        "required": ("PR", "媒体", "公关", "传播", "新闻稿", "采访", "公众号", "视频号", "品牌"),
    },
    {
        "name": "AI/科技",
        "triggers": ("AI", "人工智能", "AIGC", "大模型", "智能", "科技"),
        "required": ("AI", "人工智能", "AIGC", "大模型", "智能", "技术", "科技"),
    },
]

PLACEHOLDER_PATTERNS = [
    re.compile(r"\{\{[^{}\n]{1,100}\}\}"),
    re.compile(r"\$\{[^{}\n]{1,100}\}"),
    re.compile(r"\[[^\]\n]{0,80}(?:待填写|待补充|请填写|占位符|placeholder|todo|tbd|xxx)[^\]\n]{0,80}\]", re.I),
    re.compile(r"<[^<>\n]{0,80}(?:待填写|待补充|请填写|占位符|placeholder|todo|tbd|xxx)[^<>\n]{0,80}>", re.I),
    re.compile(r"\b(?:TODO|TBD|XXX)\b", re.I),
    re.compile(r"(?:待填写|待补充|请填写|占位符)(?:[：:][^\s，。；;\n]{0,40})?"),
]

FACT_TOKEN_PATTERNS = [
    re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"),
    re.compile(r"https?://[^\s)>）】]+", re.I),
    re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)(?:19|20)\d{2}(?:[./年-](?:0?[1-9]|1[0-2]))?(?:[./月-](?:0?[1-9]|[12]\d|3[01]))?(?:日)?(?!\d)"),
    re.compile(
        r"(?<![\w.])\d+(?:\.\d+)?(?:\s*[-~至到]\s*\d+(?:\.\d+)?)?\s*"
        r"(?:%|％|年|个月|月|天|人|次|篇|万|亿|元|K|k|W|w|倍|\+)(?!\w)"
    ),
]

_resume_failure_reasons: dict[str, str] = {}
_last_resume_api_error = ""


def _find_resume_artifacts(markdown_text: str) -> list[str]:
    """Find process-disclosure phrases that should not appear in a resume."""
    return [phrase for phrase in RESUME_ARTIFACT_PHRASES if phrase in markdown_text]


def _normalize_validation_token(token: str) -> str:
    return re.sub(r"\s+", "", token).lower()


def _extract_validation_tokens(text: str, patterns: list[re.Pattern]) -> list[str]:
    tokens: list[str] = []
    for pattern in patterns:
        tokens.extend(match.group(0).strip() for match in pattern.finditer(text or ""))
    return tokens


def _extract_placeholder_tokens(text: str) -> list[str]:
    tokens = _extract_validation_tokens(text, PLACEHOLDER_PATTERNS)
    return [
        token
        for token in tokens
        if not any(token != other and token in other for other in tokens)
    ]


def _find_new_placeholders(markdown_text: str, base_resume: str) -> list[str]:
    """Return placeholders introduced or rewritten by the model.

    Placeholders already present verbatim in the source resume are an accepted
    baseline. Rewording one creates a new token and is therefore blocked.
    """
    base_counts = Counter(
        _normalize_validation_token(token)
        for token in _extract_placeholder_tokens(base_resume)
    )
    seen_counts: Counter[str] = Counter()
    introduced: list[str] = []
    for token in _extract_placeholder_tokens(markdown_text):
        normalized = _normalize_validation_token(token)
        seen_counts[normalized] += 1
        if seen_counts[normalized] > base_counts[normalized] and token not in introduced:
            introduced.append(token)
    return introduced


def _find_new_fact_tokens(markdown_text: str, base_resume: str) -> list[str]:
    """Return fact-sensitive values that do not exist in the source resume."""
    base_tokens = {
        _normalize_validation_token(token)
        for token in _extract_validation_tokens(base_resume, FACT_TOKEN_PATTERNS)
    }
    introduced: list[str] = []
    for token in _extract_validation_tokens(markdown_text, FACT_TOKEN_PATTERNS):
        normalized = _normalize_validation_token(token)
        if normalized not in base_tokens and token not in introduced:
            introduced.append(token)
    return introduced


def _markdown_section(markdown_text: str, heading: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(heading)}\s*$\n?(.*?)(?=^\s*##\s+|\Z)"
    )
    match = pattern.search(markdown_text or "")
    return match.group(1) if match else ""


def _find_missing_core_facts(markdown_text: str, base_resume: str) -> list[str]:
    """Keep fact-like contact/basic-info values from the source resume."""
    source_basic_info = _markdown_section(base_resume, "## 基本信息")
    if not source_basic_info:
        return []
    source_tokens = _extract_validation_tokens(source_basic_info, FACT_TOKEN_PATTERNS)
    generated_keys = {
        _normalize_validation_token(token)
        for token in _extract_validation_tokens(markdown_text, FACT_TOKEN_PATTERNS)
    }
    return [
        token
        for token in source_tokens
        if _normalize_validation_token(token) not in generated_keys
    ]


def _find_blocking_integrity_issues(
    markdown_text: str,
    base_resume: str,
) -> list[str]:
    """Return issues that must prevent a resume from being marked ready."""
    issues: list[str] = []

    missing_core_facts = _find_missing_core_facts(markdown_text, base_resume)
    if missing_core_facts:
        issues.append(
            "事实完整性校验失败：缺少基础简历中的关键信息："
            + ", ".join(missing_core_facts[:8])
        )

    new_facts = _find_new_fact_tokens(markdown_text, base_resume)
    if new_facts:
        issues.append(
            "事实完整性校验失败：模型新增了原始简历中不存在的数据："
            + ", ".join(new_facts[:8])
        )

    new_placeholders = _find_new_placeholders(markdown_text, base_resume)
    if new_placeholders:
        issues.append(
            "占位符校验失败：模型新增或改写了占位符："
            + ", ".join(new_placeholders[:8])
        )
    return issues


def _set_resume_failure_reason(job_id: str, reason: str) -> None:
    _resume_failure_reasons[str(job_id)] = str(reason).strip() or "未知原因"


def get_last_resume_failure_reason(job_id: str) -> str:
    """Return the latest in-process failure reason for monitor history."""
    return _resume_failure_reasons.get(str(job_id), "")


def _strip_completion_marker(markdown_text: str) -> tuple[str | None, str | None]:
    """Return any non-empty resume body, removing the optional completion marker."""
    marker_issue = None
    if RESUME_COMPLETION_MARKER not in markdown_text:
        marker_issue = "生成结果缺少完成标记，可能不完整"
    body = markdown_text.split(RESUME_COMPLETION_MARKER, 1)[0].strip()
    if not body:
        return None, "生成结果为空"
    return f"{body}\n", marker_issue


def _find_resume_quality_issues(
    markdown_text: str,
    base_resume: str,
    job: dict | None = None,
    max_chars: int | None = None,
    max_pages: int = DEFAULT_RESUME_MAX_PAGES,
) -> list[str]:
    """Find issues that make a generated resume unsafe to mark as ready."""
    issues: list[str] = []
    stripped = markdown_text.strip()

    if not stripped.startswith("#"):
        issues.append("生成结果不像 Markdown 简历正文")

    if max_chars and _resume_content_length(markdown_text) > max_chars:
        issues.append(f"简历内容过长，默认应控制在 {max_pages} 页以内")

    for section in _required_sections_from_base(base_resume):
        if section not in markdown_text:
            issues.append(f"缺少基础简历中的常规栏目：{section.replace('## ', '')}")

    last_line = _last_content_line(markdown_text)
    if _looks_abrupt(last_line):
        issues.append("简历末尾疑似半句截断")

    if _is_nearly_unchanged(markdown_text, base_resume):
        issues.append("生成结果与原始简历几乎一致，定制化不足")

    if job:
        job_text = " ".join(str(job.get(key) or "") for key in ("title", "company", "company_industry", "jd"))
        for group in ROLE_KEYWORD_GROUPS:
            if any(token in job_text for token in group["triggers"]):
                if not any(token in markdown_text for token in group["required"]):
                    issues.append(f"未体现岗位关键词方向：{group['name']}")

    return issues


def _required_sections_from_base(base_resume: str) -> list[str]:
    return [section for section in REQUIRED_RESUME_SECTIONS if section in base_resume]


def _last_content_line(markdown_text: str) -> str:
    for line in reversed(markdown_text.splitlines()):
        stripped = line.strip()
        if stripped and stripped != "---":
            return stripped
    return ""


def _looks_abrupt(line: str) -> bool:
    if not line:
        return True
    if line.startswith("#"):
        return True
    if len(line) < 12:
        return True
    if line[-1] in "。.!！?？；;)）]】》\"'”’":
        return False
    if re.search(r"(，|、|及|和|与|围绕|包括|病例|技术|项目)$", line):
        return True
    return False


def _is_nearly_unchanged(markdown_text: str, base_resume: str) -> bool:
    base = _normalize_resume_for_similarity(base_resume)
    tailored = _normalize_resume_for_similarity(markdown_text)
    if len(base) < 500 or len(tailored) < 500:
        return False
    return SequenceMatcher(None, base, tailored).ratio() >= 0.985


def _normalize_resume_for_similarity(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _resume_content_length(markdown_text: str) -> int:
    return len(re.sub(r"\s+", "", markdown_text))


def _resume_max_pages_from_config(config: dict) -> int:
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    return _positive_int(ai_cfg.get("resume_max_pages"), DEFAULT_RESUME_MAX_PAGES)


def _resume_max_chars_from_config(config: dict, max_pages: int) -> int:
    ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
    return _positive_int(ai_cfg.get("resume_max_chars"), max_pages * DEFAULT_RESUME_CHARS_PER_PAGE)


def _positive_int(value: object, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _pdf_page_count(pdf_path: Path) -> int | None:
    """Return PDF page count when it can be determined."""
    if not pdf_path.exists():
        return None

    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        pass

    try:
        data = pdf_path.read_bytes()
    except OSError:
        return None

    count = len(re.findall(rb"/Type\s*/Page\b", data))
    return count or None


def _call_claude(prompt: str, config: dict) -> str | None:
    """Call Claude API and return response text."""
    global _last_resume_api_error
    _last_resume_api_error = ""
    try:
        ai_cfg = config.get("ai", {}) if isinstance(config, dict) else {}
        max_tokens = int(ai_cfg.get("resume_max_tokens") or 8000)
        return run_cancellable(
            lambda: call_anthropic_text(prompt, config, max_tokens),
            config,
        )
    except OperationCancelled:
        raise
    except Exception as e:
        _last_resume_api_error = str(e)
        console.print(f"[red]API 调用失败: {e}[/red]")
        return None


def _render_pdf(markdown_text: str, output_path: Path) -> bool:
    """Render markdown to PDF via Chrome CDP (Page.printToPDF).

    If Chrome is unavailable, keep the UTF-8 Markdown instead of producing a
    PDF with missing CJK glyphs through a font-less fallback renderer.
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
    @page {{ size: A4; margin: 12mm; }}
    body {{
        font-family: "Microsoft YaHei", "SimSun", "WenQuanYi Micro Hei", sans-serif;
        font-size: 10.5pt;
        line-height: 1.48;
        margin: 0;
        color: #333;
    }}
    h1 {{ font-size: 18pt; color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 5px; }}
    h2 {{ font-size: 14pt; color: #2c3e50; margin-top: 14px; margin-bottom: 8px; }}
    h3 {{ font-size: 12pt; color: #34495e; }}
    ul {{ padding-left: 20px; }}
    li {{ margin-bottom: 2px; }}
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
    return False


def _render_pdf_via_cdp(html_content: str, output_path: Path) -> bool:
    """Use Browser Runtime Page.printToPDF via the Python browser facade."""
    import tempfile
    import time

    # The Browser Runtime is a separate process and may have a different
    # working directory. Always send it an absolute destination so a relative
    # resume_output_dir cannot create the PDF somewhere else.
    output_path = output_path.expanduser().resolve()
    temp_html = Path(tempfile.gettempdir()) / "bosshunter_resume.html"
    temp_html.write_text(html_content, encoding="utf-8")
    file_url = f"file:///{temp_html.as_posix()}"

    try:
        for attempt in range(2):
            target_id = None
            try:
                target_id = new_tab(file_url, background=True)
                if target_id and wait_for_load(target_id, timeout=10):
                    # Give Chrome a brief turn to resolve system fonts after
                    # the document load event before printing the page.
                    time.sleep(0.25)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.unlink(missing_ok=True)
                    if print_pdf(target_id, output_path):
                        if output_path.exists() and output_path.stat().st_size > 0:
                            return True
            except Exception:
                pass
            finally:
                if target_id:
                    close_tab(target_id)
            if attempt == 0:
                time.sleep(0.5)
        return False
    finally:
        temp_html.unlink(missing_ok=True)


def generate_tailored_resume(job_id: str, config: dict) -> Path | None:
    """Generate a tailored resume for a specific job.

    Returns path to generated file, or None on failure.
    """
    global _last_resume_api_error
    _resume_failure_reasons.pop(str(job_id), None)
    _last_resume_api_error = ""
    db = get_db()

    def fail(reason: str) -> None:
        _set_resume_failure_reason(job_id, reason)
        console.print(f"[red]定制简历生成失败：{reason}[/red]")
        db.close()
        return None

    # Get job info
    row = db.execute("SELECT * FROM jobs WHERE id = ? AND deleted_at IS NULL", (job_id,)).fetchone()
    if not row:
        return fail(f"未找到岗位 ID：{job_id}")

    job = dict(row)

    # Load base resume
    resume_path = Path(config.get("profile", {}).get("resume_path", "./resume.md"))
    if not resume_path.exists():
        return fail(f"基础简历文件不存在：{resume_path}")

    try:
        resume_text = resume_path.read_text(encoding="utf-8")
    except OSError as exc:
        return fail(f"无法读取基础简历：{exc}")
    resume_max_pages = _resume_max_pages_from_config(config)
    resume_max_chars = _resume_max_chars_from_config(config, resume_max_pages)

    # Generate tailored resume via AI
    console.print(f"[bold]为 {job['company']} - {job['title']} 生成定制简历...[/bold]")

    base_prompt = RESUME_TAILOR_PROMPT.format(
        title=job["title"],
        company=job["company"],
        salary=job["salary"] or "面议",
        education=job.get("education", "") or "未识别",
        recruitment_type={"campus": "校招", "experienced": "社招"}.get(
            job.get("recruitment_type", ""), "未识别"
        ),
        jd=job["jd"][:2000] if job["jd"] else "无详细描述",
        resume=resume_text,
        resume_max_pages=resume_max_pages,
        completion_marker=RESUME_COMPLETION_MARKER,
    )

    tailored_md = None
    prompt = base_prompt
    for attempt in range(2):
        try:
            raw_tailored_md = _call_claude(prompt, config)
        except OperationCancelled:
            db.close()
            raise
        if stop_requested(config):
            db.close()
            raise OperationCancelled("用户已请求停止")
        if not raw_tailored_md:
            if _last_resume_api_error:
                return fail(f"AI 服务调用失败：{_last_resume_api_error}")
            return fail("AI 服务未返回简历内容，请检查模型配置或稍后重试")

        candidate_md, marker_issue = _strip_completion_marker(raw_tailored_md)
        if not candidate_md:
            return fail(marker_issue or "生成结果为空")

        artifacts = _find_resume_artifacts(candidate_md)
        quality_issues = _find_resume_quality_issues(
            candidate_md,
            resume_text,
            job,
            max_chars=resume_max_chars,
            max_pages=resume_max_pages,
        )
        blocking_issues = _find_blocking_integrity_issues(
            candidate_md,
            resume_text,
        )
        overlong = any(issue.startswith("简历内容过长") for issue in quality_issues)
        if attempt == 0 and (overlong or blocking_issues):
            retry_issues = [*blocking_issues, *quality_issues]
            issue_text = "; ".join(dict.fromkeys(retry_issues))
            console.print(f"[yellow]生成结果校验未通过，尝试修正一次：{issue_text}[/yellow]")
            prompt = RESUME_RETRY_PROMPT.format(
                base_prompt=base_prompt,
                quality_issues=issue_text,
                resume_max_pages=resume_max_pages,
                resume_max_chars=resume_max_chars,
                completion_marker=RESUME_COMPLETION_MARKER,
            )
            continue
        if blocking_issues:
            return fail("；".join(blocking_issues))

        delivery_warnings = list(quality_issues)
        if artifacts:
            delivery_warnings.append(f"包含定制过程性措辞：{', '.join(artifacts)}")
        if marker_issue:
            delivery_warnings.append(marker_issue)
        if delivery_warnings:
            console.print(
                f"[yellow]生成结果存在质量提示，仍保留并提供下载: {'; '.join(delivery_warnings)}[/yellow]"
            )
        tailored_md = candidate_md
        break

    if not tailored_md:
        return fail("生成结果未通过校验")
    if stop_requested(config):
        db.close()
        raise OperationCancelled("用户已请求停止")

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
        pdf_pages = _pdf_page_count(pdf_path)
        if pdf_pages and pdf_pages > resume_max_pages:
            console.print(
                f"[yellow]PDF 共 {pdf_pages} 页，超过 {resume_max_pages} 页建议篇幅，仍保留并提供下载[/yellow]"
            )

        console.print(f"[green]✓ PDF 已生成: {pdf_path}[/green]")
        # Update DB
        db.execute(
            "UPDATE jobs SET resume_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
            (str(pdf_path), job_id)
        )
        db.commit()
        db.close()
        _resume_failure_reasons.pop(str(job_id), None)
        return pdf_path
    else:
        console.print(f"[yellow]Chrome PDF 渲染暂时不可用，已安全保存为 Markdown: {md_path}[/yellow]")
        console.print("[dim]  请确认 Chrome 连接后重试；系统不会生成缺少中文字体的 PDF[/dim]")
        db.execute(
            "UPDATE jobs SET resume_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
            (str(md_path), job_id)
        )
        db.commit()
        db.close()
        _resume_failure_reasons.pop(str(job_id), None)
        return md_path


def generate_all_resumes(config: dict) -> int:
    """Generate tailored resumes for all scored jobs. Returns count generated."""
    db = get_db()
    threshold = config.get("scoring", {}).get("threshold", 60)

    # Get scored jobs without resume
    rows = db.execute(
        "SELECT id FROM jobs WHERE deleted_at IS NULL AND status IN ('scored', 'ready', 'approved') AND score >= ? AND resume_path IS NULL",
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
