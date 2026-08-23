"""Pipeline - orchestrates the full BossHunter flow."""

from rich.console import Console

from bosshunter.browser import check_chrome_connection, configure, find_boss_tab

console = Console()


def run_pipeline(config: dict) -> None:
    """Run the full pipeline: scrape → score → confirm → greet → send → monitor."""
    configure(config)

    # Step 1: Check Browser Runtime connection
    console.print("[bold]Step 1/6: 检测 Browser Runtime[/bold]")
    version_info = check_chrome_connection()
    if not version_info:
        console.print("[red]✗ Browser Runtime 未连接，请先运行 bosshunter connect 查看诊断[/red]")
        return

    boss_tab = find_boss_tab()
    if not boss_tab:
        console.print("[red]✗ 未发现 BOSS直聘 页面，请先登录[/red]")
        return
    console.print("[green]  ✓ 浏览器就绪[/green]\n")

    # Step 2: Scrape jobs
    console.print("[bold]Step 2/6: 采集岗位[/bold]")
    from bosshunter.scraper.jobs import scrape_jobs
    keywords = config["search"]["keywords"]
    collected_job_ids: list[str] = []
    count = scrape_jobs(config, keywords, collected_job_ids=collected_job_ids)
    if count == 0:
        console.print("[yellow]  ! 未采集到新岗位，尝试继续处理已有岗位...[/yellow]")
    else:
        console.print(f"[green]  ✓ 采集 {count} 个新岗位[/green]\n")

    # Step 3: AI scoring (with pre-filter)
    console.print("[bold]Step 3/6: AI 评分筛选[/bold]")
    from bosshunter.ai.scorer import score_jobs
    scored, filtered = score_jobs(config)
    if scored == 0 and count > 0:
        console.print("[yellow]  ! 没有通过评分的岗位，流程结束[/yellow]")
        return
    console.print(f"[green]  ✓ {scored} 个通过, {filtered} 个过滤[/green]\n")

    # Step 4: Confirm which jobs to pursue
    console.print("[bold]Step 4/6: 确认投递清单[/bold]")
    from bosshunter.ui.confirm import show_confirmation
    approved = show_confirmation(config)
    if not approved:
        console.print("[yellow]  已取消发送[/yellow]")
        return

    # Step 5: Generate greetings for confirmed jobs, then send
    console.print("\n[bold]Step 5/6: 生成招呼语并发送[/bold]")
    from bosshunter.ai.greeter import generate_greetings
    greet_count = generate_greetings(config)
    console.print(f"[green]  ✓ 生成 {greet_count} 条招呼语[/green]")

    from bosshunter.executor.sender import send_greetings
    sent = send_greetings(config)
    console.print(f"\n[bold green]═══ 发送完成！{sent} 条招呼语 ═══[/bold green]")

    # Step 6: Auto-start monitor loop
    console.print("\n[bold]Step 6/6: 启动持续监测[/bold]")
    interval_min = config.get("monitor", {}).get("interval", 30)
    interval_sec = interval_min * 60
    console.print(f"[dim]每 {interval_min} 分钟检查一次HR回复和跟进，按 Ctrl+C 停止[/dim]\n")

    import time
    from bosshunter.executor.monitor import monitor_and_send_resumes

    try:
        raw_cooldown = config.get("monitor", {}).get("initial_cooldown_minutes", 10)
        try:
            initial_cooldown_sec = max(float(raw_cooldown), 0) * 60
        except (TypeError, ValueError):
            initial_cooldown_sec = 10 * 60
        if initial_cooldown_sec > 0:
            console.print(
                f"[dim]发送结束后先冷却 {initial_cooldown_sec / 60:g} 分钟；按 Ctrl+C 可取消[/dim]\n"
            )
            time.sleep(initial_cooldown_sec)
        while True:
            try:
                summary = monitor_and_send_resumes(config)
                parts = []
                if summary.get("replied"):
                    parts.append(f"回复{summary['replied']}条")
                if summary.get("follow_up"):
                    parts.append(f"跟进{summary['follow_up']}条")
                if summary.get("needs_resume"):
                    parts.append(f"[bold yellow]待手动发简历{summary['needs_resume']}份[/bold yellow]")
                if parts:
                    console.print(f"  本轮: {', '.join(parts)}")
                if summary.get("stop_reason"):
                    console.print("[red]监测检测到风险信号，已安全停止[/red]")
                    break
            except Exception as e:
                console.print(f"[red]  监测出错: {e}[/red]")
            console.print(f"[dim]  下次检查: {interval_min} 分钟后...[/dim]\n")
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        console.print("\n[yellow]已停止监测[/yellow]")
