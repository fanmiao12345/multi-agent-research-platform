@echo off
rem ============================================================
rem  Agent MVP 一键体验脚本（Windows）
rem  已配置 DEEPSEEK_API_KEY 环境变量 -> 直接调用真实 DeepSeek 模型
rem  未配置 -> 自动使用离线模拟模式（不会在问答前弹窗问 Key）
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if "%DEEPSEEK_API_KEY%"=="" (
    echo [提示] 未检测到 DEEPSEEK_API_KEY，将使用离线模拟大脑演示循环。
    echo       想调用真实 DeepSeek 模型，三选一：
    echo         1. 临时:   set DEEPSEEK_API_KEY=sk-你的Key
    echo         2. 永久:   setx DEEPSEEK_API_KEY sk-你的Key   （新开窗口生效）
    echo         3. 最省事: 编辑本目录的 llm.py，把顶部 LLM_API_KEY = "" 引号里填上 Key
    echo       详细说明见 README.md 的「获取并填写 API Key」小节。
    echo.
)

echo.
echo 正在运行演示任务...
echo.
python agent.py --question "你好！现在几点了？顺便帮我算一下 12*34 + 56 等于多少？"

echo.
echo ============================================================
echo 演示结束！
echo   * 想连续对话（试试让它记备忘、再让它回忆）:
echo       python agent.py
echo   * 更多练习题目: 见 questions.txt
echo   * 完整讲解: 见 README.md
echo ============================================================
pause
