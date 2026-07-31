@echo off
setlocal
title BossHunter - Stop
echo Stopping BossHunter services...

powershell -NoProfile -ExecutionPolicy Bypass -Command "$stopped = 0; $web = Get-NetTCPConnection -LocalPort 8686 -State Listen -ErrorAction SilentlyContinue; foreach ($item in $web) { $proc = Get-Process -Id $item.OwningProcess -ErrorAction SilentlyContinue; if ($proc) { Stop-Process -Id $proc.Id -Force -ErrorAction Stop; $stopped++ } }; foreach ($port in 3456..3465) { $runtime = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue; foreach ($item in $runtime) { $proc = Get-Process -Id $item.OwningProcess -ErrorAction SilentlyContinue; if ($proc -and $proc.ProcessName -eq 'node') { Stop-Process -Id $proc.Id -Force -ErrorAction Stop; $stopped++ } } }; if ($stopped -eq 0) { Write-Host 'No running BossHunter service was found.' } else { Write-Host ('BossHunter stopped. Closed processes: ' + $stopped) }"

pause
endlocal
