@echo off
REM Windows批处理文件用于启动ChatterPal

echo 启动ChatterPal...

REM 检查是否安装了uv
uv --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到uv包管理器
    echo 请访问 https://docs.astral.sh/uv/getting-started/installation/ 安装uv
    exit /b 1
)

REM 运行应用
uv run --no-sync --project "%~dp0.." python "%~dp0run.py" %*
exit /b %errorlevel%