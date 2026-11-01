@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set PY=C:\Users\micky\.workbuddy\binaries\python\envs\default\Scripts\python.exe

if not exist "%PY%" (
    echo 找不到 Python 环境
    echo 请改用系统 Python：python run.py console
    pause
    exit /b 1
)

echo.
echo ============================================
echo   一人AI公司 - 网页控制台
echo   启动后浏览器会自动打开：
echo   http://127.0.0.1:8899/
echo ============================================
echo.
echo   关闭这个黑窗口 = 关闭控制台
echo.

"%PY%" run.py console

pause
