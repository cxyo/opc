@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set PY=C:\Users\micky\.workbuddy\binaries\python\envs\default\Scripts\python.exe

if not exist "%PY%" (
    echo 找不到 Python 环境
    echo 请改用系统 Python：python run.py doctor
    pause
    exit /b 1
)

echo.
echo 正在做系统体检...
echo.
"%PY%" run.py doctor
echo.
pause
