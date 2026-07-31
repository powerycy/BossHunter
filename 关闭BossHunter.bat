@echo off
setlocal
chcp 65001 >nul
title BossHunter - 停止服务

echo.
echo 正在停止 BossHunter 服务...

powershell -NoProfile -ExecutionPolicy Bypass -Command "$stopped = @(); $web = Get-NetTCPConnection -LocalPort 8686 -State Listen -ErrorAction SilentlyContinue; foreach ($item in $web) { $proc = Get-Process -Id $item.OwningProcess -ErrorAction SilentlyContinue; if ($proc) { Stop-Process -Id $proc.Id -Force -ErrorAction Stop; $stopped += 'BossHunter 后端（8686）' } }; 3456..3465 | ForEach-Object { $runtime = Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue; foreach ($item in $runtime) { $proc = Get-Process -Id $item.OwningProcess -ErrorAction SilentlyContinue; if ($proc -and $proc.ProcessName -eq 'node') { Stop-Process -Id $proc.Id -Force -ErrorAction Stop; $stopped += ('浏览器运行组件（{0}）' -f $_) } } }; if ($stopped.Count -eq 0) { Write-Host '未发现正在运行的 BossHunter 服务。' } else { Write-Host '已停止：'; $stopped | Select-Object -Unique | ForEach-Object { Write-Host ('- ' + $_) }; Write-Host 'BossHunter Chrome 窗口不会被关闭。' }"

echo.
pause
endlocal
