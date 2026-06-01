"""Status tracker - Show job application statistics and dashboard."""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns

from bosshunter.db import (
    get_db, get_stats, get_funnel_stats,
    get_daily_activity, get_top_companies, get_recent_history
)

console = Console()

STATUS_LABELS = {
    "pending": "待评分",
    "scored": "已评分",
    "filtered": "已过滤",
    "ready": "待确认",
    "approved": "已确认",
    "skipped": "已跳过",
    "sent": "已发送",
    "replied": "已回复",
    "resume_sent": "简历已发",
    "needs_resume": "待手动发简历",
    "follow_up_sent": "已跟进",
    "rejected": "已拒绝",
    "error": "发送失败",
}

STATUS_STYLES = {
    "pending": "dim",
    "scored": "yellow",
    "filtered": "dim red",
    "ready": "cyan",
    "approved": "blue",
    "skipped": "dim",
    "sent": "green",
    "replied": "bold green",
    "resume_sent": "bold cyan",
    "needs_resume": "bold yellow",
    "follow_up_sent": "cyan",
    "rejected": "dim red",
    "error": "red",
}


def show_status() -> None:
    """Display job application status summary (simple view)."""
    db = get_db()
    stats = get_stats(db)

    if not stats:
        console.print("[yellow]暂无数据[/yellow]")
        db.close()
        return

    total = sum(stats.values())

    table = Table(title="投递状态统计", show_header=True, header_style="bold")
    table.add_column("状态", width=12)
    table.add_column("数量", width=8, justify="right")
    table.add_column("占比", width=8, justify="right")

    for status, count in sorted(stats.items(), key=lambda x: list(STATUS_LABELS.keys()).index(x[0]) if x[0] in STATUS_LABELS else 99):
        label = STATUS_LABELS.get(status, status)
        style = STATUS_STYLES.get(status, "")
        pct = f"{count/total*100:.0f}%" if total > 0 else "0%"
        table.add_row(f"[{style}]{label}[/{style}]", str(count), pct)

    table.add_row("[bold]总计[/bold]", f"[bold]{total}[/bold]", "100%")

    console.print(table)

    # Funnel display
    sent = stats.get("sent", 0)
    replied = stats.get("replied", 0)
    if sent > 0:
        reply_rate = replied / sent * 100
        console.print(f"\n[bold]回复率: {reply_rate:.1f}%[/bold] ({replied}/{sent})")

    db.close()


def show_dashboard() -> None:
    """Display full terminal dashboard with funnel, trends, and activity."""
    db = get_db()
    stats = get_stats(db)

    if not stats:
        console.print("[yellow]暂无数据，请先运行 bosshunter run[/yellow]")
        db.close()
        return

    # === Section 1: Funnel ===
    funnel = get_funnel_stats(db)
    funnel_panel = _build_funnel_panel(funnel)

    # === Section 2: Status Table ===
    status_panel = _build_status_panel(stats)

    # Print header
    console.print(Panel("[bold cyan]BossHunter Dashboard[/bold cyan]", style="cyan"))
    console.print()

    # Funnel + Status side by side
    console.print(Columns([funnel_panel, status_panel], equal=True, expand=True))
    console.print()

    # === Section 3: Daily Activity ===
    daily = get_daily_activity(db, days=7)
    if daily:
        daily_panel = _build_daily_panel(daily)
        console.print(daily_panel)
        console.print()

    # === Section 4: Top Companies ===
    companies = get_top_companies(db, limit=5)
    if companies:
        company_panel = _build_company_panel(companies)
        console.print(company_panel)
        console.print()

    # === Section 5: Recent Activity ===
    history = get_recent_history(db, limit=10)
    if history:
        history_panel = _build_history_panel(history)
        console.print(history_panel)

    db.close()


def _build_funnel_panel(funnel: dict) -> Panel:
    """Build funnel visualization panel."""
    max_val = max(funnel.values()) if funnel.values() else 1
    bar_width = 28

    lines = []
    for label, count in funnel.items():
        bar_len = int(count / max_val * bar_width) if max_val > 0 else 0
        bar = "█" * bar_len
        lines.append(f"{label:　<4} {bar} {count}")

    text = "\n".join(lines)
    return Panel(text, title="[bold]转化漏斗[/bold]", border_style="green")


def _build_status_panel(stats: dict) -> Panel:
    """Build status summary panel."""
    total = sum(stats.values())
    lines = []
    for status in STATUS_LABELS:
        if status in stats:
            count = stats[status]
            label = STATUS_LABELS[status]
            pct = f"{count/total*100:.0f}%" if total > 0 else "0%"
            lines.append(f"{label}: {count} ({pct})")

    sent = stats.get("sent", 0)
    replied = stats.get("replied", 0)
    if sent > 0:
        lines.append(f"\n回复率: {replied/sent*100:.1f}%")

    text = "\n".join(lines)
    return Panel(text, title="[bold]状态统计[/bold]", border_style="blue")


def _build_daily_panel(daily: list[dict]) -> Panel:
    """Build 7-day activity trend panel."""
    # Aggregate by day
    day_counts: dict[str, dict[str, int]] = {}
    for row in daily:
        day = row["day"]
        action = row["action"]
        cnt = row["cnt"]
        if day not in day_counts:
            day_counts[day] = {}
        day_counts[day][action] = cnt

    lines = []
    for day in sorted(day_counts.keys(), reverse=True)[:7]:
        actions = day_counts[day]
        sent = actions.get("sent", 0)
        total_actions = sum(actions.values())
        bar = "■" * min(total_actions, 20)
        lines.append(f"{day[5:]} {bar} ({total_actions}条, 发送{sent})")

    text = "\n".join(lines) if lines else "暂无近期活动"
    return Panel(text, title="[bold]7日趋势[/bold]", border_style="yellow")


def _build_company_panel(companies: list[dict]) -> Panel:
    """Build top companies panel."""
    lines = []
    for i, row in enumerate(companies, 1):
        company = row["company"][:12]
        avg = int(row["avg_score"])
        count = row["job_count"]
        lines.append(f"{i}. {company:　<8} 均分{avg} ({count}个岗位)")

    text = "\n".join(lines)
    return Panel(text, title="[bold]Top 公司[/bold]", border_style="magenta")


def _build_history_panel(history: list[dict]) -> Panel:
    """Build recent activity panel."""
    ACTION_LABELS = {
        "sent": "[green]发送[/green]",
        "approved": "[blue]确认[/blue]",
        "error": "[red]失败[/red]",
        "skipped": "[dim]跳过[/dim]",
    }

    lines = []
    for row in history:
        ts = row["created_at"][11:16] if row["created_at"] and len(row["created_at"]) > 16 else "??:??"
        action = ACTION_LABELS.get(row["action"], row["action"])
        company = (row["company"] or "")[:8]
        title = (row["title"] or "")[:12]
        lines.append(f"[dim]{ts}[/dim] {action} → {company} {title}")

    text = "\n".join(lines)
    return Panel(text, title="[bold]近期动态[/bold]", border_style="cyan")
