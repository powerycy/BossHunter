@echo off
setlocal
cd /d "%~dp0"

set "BOSSHUNTER_EXE=%CD%\.venv\Scripts\bosshunter.exe"
if not exist "%BOSSHUNTER_EXE%" (
    echo [首次启动] 正在创建 Python 环境并安装 BossHunter，请稍候...
    py -3 -m venv .venv
    if errorlevel 1 goto :setup_failed
    ".venv\Scripts\python.exe" -m pip install -e .
    if errorlevel 1 goto :setup_failed
)

echo.
echo 请选择 Chrome 启动方式：
echo   [1] 安全启动（推荐）：打开独立的 BossHunter Chrome
echo   [2] 连接已有 Chrome：由你选择 Chrome 配置档并主动授权
choice /C 12 /N /M "请输入 1 或 2"
if errorlevel 2 goto :existing_chrome

"%BOSSHUNTER_EXE%" start
goto :end

:existing_chrome
"%BOSSHUNTER_EXE%" start --existing-chrome
goto :end

:setup_failed
echo.
echo 启动失败：请先安装 Python 3.10 及以上版本，然后重新双击本文件。
pause

:end
endlocal
