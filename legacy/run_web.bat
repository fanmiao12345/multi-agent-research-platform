@echo off
rem ============================================================
rem  agent-mvp Web 界面一键启动
rem  后端：python web_server.py（零第三方依赖，端口 8765）
rem  前端：优先用 webui\dist 的构建产物；没构建过会提示开发方式
rem  用法：双击本文件；或 run_web.bat --force-mock（离线模拟大脑）
rem ============================================================
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 python，请先安装 Python 3.9+ 并加入 PATH
    pause
    exit /b 1
)

echo 启动 Web 界面：http://127.0.0.1:8765  （Ctrl+C 退出）
python web_server.py %*
if errorlevel 1 pause
