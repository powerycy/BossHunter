"""Confirmation UI - Rich terminal display for job review."""

from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt

from bosshunter.db import get_db, get_jobs_pending_confirmation, serialize_job, update_job_status, add_history

console = Console()


def _outsourcing_badge(job: dict) -> str:
    """Return a Rich-styled outsourcing chip or empty string."""
    level = job.get("outsourcing_level") or "clean"
    if level == "confirmed":
        return " [red]外包[/red]"
    if level == "suspected":
        return " [yellow]疑似外包[/yellow]"
    return ""


def show_confirmation(config: dict) -> bool:
    """Display jobs for confirmation. Returns True if any jobs were approved."""
    db = get_db()
    jobs = [serialize_job(job) for job in get_jobs_pending_confirmation(db)]

    if not jobs:
        console.print("[yellow]没有待确认的岗位[/yellow]")
        db.close()
        return False

    # Display summary table
    console.print(f"\n[bold cyan]═══ 投递清单 ({len(jobs)} 个岗位) ═══[/bold cyan]\n")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", width=3)
    table.add_column("公司", width=22)
    table.add_column("职位", width=24)
    table.add_column("薪资", width=10)
    table.add_column("匹配分", width=6, justify="center")
    table.add_column("评分理由", width=30)

    for i, job in enumerate(jobs, 1):
        reason_preview = (job.get("score_reason", "") or "")[:28]
        if len(job.get("score_reason", "") or "") > 28:
            reason_preview += "..."

        score_style = "green" if job["score"] >= 80 else "yellow" if job["score"] >= 60 else "red"

        table.add_row(
            str(i),
            (job["company"] or "")[:14] + _outsourcing_badge(job),
            (job["title"] or "")[:22],
            job.get("salary", "") or "面议",
            f"[{score_style}]{job['score']}[/{score_style}]",
            reason_preview
        )

    console.print(table)
    console.print()

    # Confirmation options
    choice = Prompt.ask(
        "[bold]操作[/bold]",
        choices=["a", "s", "q"],
        default="s"
    )

    if choice == "q":
        console.print("[yellow]已取消[/yellow]")
        db.close()
        return False

    if choice == "a":
        # Approve all
        for job in jobs:
            update_job_status(db, job["id"], "approved")
            add_history(db, job["id"], "approved", "批量确认")
        console.print(f"[green]✓ 已确认 {len(jobs)} 个岗位[/green]")
        db.close()
        return True

    # Individual selection mode
    approved_count = 0
    for i, job in enumerate(jobs, 1):
        console.print(f"\n[bold]#{i}[/bold] {job['company']}{_outsourcing_badge(job)} - {job['title']} ({job.get('salary', '面议')})")
        console.print(f"  匹配分: {job['score']} | {job.get('score_reason', '')}")
        console.print(f"  招呼语: {job.get('greeting', '')}")

        action = Prompt.ask("  ", choices=["y", "n", "e", "q"], default="y")

        if action == "q":
            break
        elif action == "y":
            update_job_status(db, job["id"], "approved")
            add_history(db, job["id"], "approved", "逐个确认")
            approved_count += 1
        elif action == "e":
            # Edit greeting
            new_greeting = Prompt.ask("  新招呼语")
            if new_greeting:
                from bosshunter.db import update_job_greeting
                update_job_greeting(db, job["id"], new_greeting)
            update_job_status(db, job["id"], "approved")
            add_history(db, job["id"], "approved", "编辑后确认")
            approved_count += 1
        else:
            update_job_status(db, job["id"], "skipped")
            add_history(db, job["id"], "skipped", "用户跳过")

    console.print(f"\n[green]✓ 已确认 {approved_count} 个岗位[/green]")
    db.close()
    return approved_count > 0
