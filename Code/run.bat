@echo off
REM One-click start for Windows.  Double-click or run:  run.bat
cd /d "%~dp0"
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv || (echo Python 3.10+ is required & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt || (echo Package install failed - check internet once & pause & exit /b 1)
start "" http://127.0.0.1:8000
python server.py
pause
