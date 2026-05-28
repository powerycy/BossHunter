"""AI Greeter - Generate personalized greeting messages with self-review."""

import os
import json
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from bosshunter.db import get_db, get_jobs_by_status, update_job_greeting, update_job_status

console = Console()

GREETING_PROMPT = """你是一位求职者，需要在BOSS直聘上给HR发送打招呼消息。请根据以下信息生成一条个性化、自然的招呼语。

## 我的背景
{resume_summary}

## 目标岗位
- 职位：{title}
- 公司：{company}
- 薪资：{salary}
- 岗位要求摘要：{jd_summary}
- 匹配分析：{match_reason}

## 额外亮点（适时融入，不要生硬罗列）
{extra_highlights}

## 要求
1. 字数控制在50-150字
2. 风格自然，像真人发的IM消息，不要太正式
3. 突出1-2个最匹配的优势
4. 表达对岗位的兴趣，但不要谄媚
5. 不要用"您好，我是xxx"这种模板开头，要有差异化
6. 适配手机端阅读
7. 在合适的位置自然带出作品集链接（不要每次都放，根据岗位匹配度决定）
8. 【严禁】不得捏造我没有的经历、头衔或身份，只能使用"我的背景"中明确提到的信息
9. 【严禁】不得把岗位JD中的描述（如公司头衔、项目名）当作我的经历来写
10. 严格使用"我的背景"中的原文描述，不得改写或美化
{critique_section}
请直接输出招呼语文本，不要加任何标记或解释。
"""

REVIEW_PROMPT = """请评估以下BOSS直聘招呼语的质量。

## 岗位
{title} @ {company}

## 招呼语
{greeting}

## 评估维度（每项1-10分）
1. 自然度：是否像真人发的IM消息，而非模板
2. 相关性：是否针对该岗位突出匹配点
3. 差异化：是否能从众多招呼中脱颖而出

请严格按JSON格式输出，不要输出其他内容：
{{"naturalness": 8, "relevance": 7, "differentiation": 6, "avg": 7.0, "critique": "改进建议（20字内）"}}
"""


def _get_resume_summary(config: dict) -> str:
    """Get a brief resume summary for greeting generation."""
    resume_path = Path(config.get("profile", {}).get("resume_path", "./resume.md"))
    if not resume_path.exists():
        return ""
    content = resume_path.read_text(encoding="utf-8")
    return content[:1500]


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
            max_tokens=300,
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


def _review_greeting(greeting: str, job: dict, config: dict) -> dict | None:
    """Self-evaluate a greeting. Returns scores dict or None on failure."""
    prompt = REVIEW_PROMPT.format(
        title=job["title"],
        company=job["company"],
        greeting=greeting,
    )
    response = _call_claude(prompt, config)
    if not response:
        return None
    try:
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(response[start:end])
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _generate_greeting_once(job: dict, resume_summary: str, config: dict, critique: str = "") -> str | None:
    """Generate a single greeting attempt."""
    jd_summary = job["jd"][:500] if job["jd"] else "无详细描述"
    critique_section = f"\n7. 上次生成的问题: {critique}，请避免此问题\n" if critique else ""

    # Build extra highlights from config (portfolio URL, personal strengths, etc.)
    profile_cfg = config.get("profile", {})
    highlights = profile_cfg.get("extra_highlights", [])
    portfolio_url = profile_cfg.get("portfolio_url", "")
    highlight_lines = [f"- {h}" for h in highlights]
    if portfolio_url:
        highlight_lines.append(f"- 个人作品集网址：{portfolio_url}")
    extra_highlights = "\n".join(highlight_lines) if highlight_lines else "（无额外亮点配置）"

    prompt = GREETING_PROMPT.format(
        resume_summary=resume_summary,
        title=job["title"],
        company=job["company"],
        salary=job["salary"] or "面议",
        jd_summary=jd_summary,
        match_reason=job.get("score_reason", ""),
        critique_section=critique_section,
        extra_highlights=extra_highlights,
    )

    greeting = _call_claude(prompt, config)
    if not greeting:
        return None

    # Clean up
    greeting = greeting.strip('"\'')
    # Enforce max length
    if len(greeting) > 150:
        cut = greeting[:150]
        for sep in ("。", "！", "～", "！", "\n"):
            idx = cut.rfind(sep)
            if idx > 50:
                greeting = cut[:idx + 1]
                break
        else:
            greeting = cut

    return greeting


def generate_greetings(config: dict) -> int:
    """Generate greetings for approved jobs with optional self-review. Returns count generated."""
    db = get_db()
    jobs = get_jobs_by_status(db, "approved")

    if not jobs:
        console.print("[yellow]没有需要生成招呼语的岗位[/yellow]")
        return 0

    resume_summary = _get_resume_summary(config)
    if not resume_summary:
        console.print("[red]无法读取简历[/red]")
        return 0

    ai_cfg = config.get("ai", {})
    review_threshold = ai_cfg.get("greeting_review_threshold", 7.0)
    max_iterations = ai_cfg.get("greeting_max_iterations", 2)

    count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task(f"生成招呼语 (0/{len(jobs)})", total=len(jobs))

        for job in jobs:
            # Generate with self-review loop
            best_greeting = None
            best_score = 0.0

            for iteration in range(max_iterations + 1):
                critique = ""
                if iteration > 0 and best_greeting:
                    # We have a previous attempt — review it
                    review = _review_greeting(best_greeting, job, config)
                    if review and review.get("avg", 10) >= review_threshold:
                        break  # Good enough
                    critique = review.get("critique", "") if review else ""

                greeting = _generate_greeting_once(job, resume_summary, config, critique)
                if not greeting:
                    break

                if iteration == 0:
                    best_greeting = greeting
                    # If no review iterations configured, skip review
                    if max_iterations == 0:
                        break
                else:
                    # Compare: keep the new one (it incorporates feedback)
                    best_greeting = greeting

            if not best_greeting:
                continue

            update_job_greeting(db, job["id"], best_greeting)
            update_job_status(db, job["id"], "ready")
            count += 1
            progress.update(task, advance=1, description=f"生成招呼语 ({count}/{len(jobs)})")

    db.close()
    return count
