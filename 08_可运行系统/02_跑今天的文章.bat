@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set PY=C:\Users\micky\.workbuddy\binaries\python\envs\default\Scripts\python.exe

if not exist "%PY%" (
    echo 找不到 Python 环境
    echo 请改用系统 Python：python run.py today
    pause
    exit /b 1
)

set TOPIC=%1
if "%TOPIC%"=="" (
    set TOPIC=21 个 AI 员工里，我最后只敢让 3 个全自动
)

echo.
echo 正在跑今日链路，选题：%TOPIC%
echo.
"%PY%" run.py today --topic "%TOPIC%"
echo.
pause
