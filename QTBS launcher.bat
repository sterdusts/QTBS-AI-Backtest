@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo =====================================
echo 正在启动 QTBS WebUI...
echo 当前目录：%cd%
echo =====================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 没找到虚拟环境 Python：
    echo .venv\Scripts\python.exe
    echo.
    echo 请确认你是不是把这个 bat 放在 QTBS 项目根目录。
    pause
    exit /b 1
)

if not exist "webUI.py" (
    echo [错误] 没找到 webUI.py
    echo.
    echo 请确认 webUI.py 是否在当前目录。
    pause
    exit /b 1
)

".venv\Scripts\python.exe" webUI.py

echo.
echo WebUI 已退出，或者启动失败。
pause