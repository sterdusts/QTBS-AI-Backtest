@echo off
cd /d "%~dp0"

rem Force UTF-8 for Python/pip: on zh-TW Windows the locale codec is cp950,
rem which cannot decode UTF-8 text (requirements comments, console output).
set PYTHONUTF8=1

if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate

pip install -r requirements.txt

python webUI.py

pause
