@echo off
setlocal
cd /d "%~dp0backend"
if not exist ".venv\Scripts\python.exe" (
  echo Backend virtual environment is missing. Run python -m venv .venv and install requirements.txt.
  pause
  exit /b 1
)
echo Backend: http://127.0.0.1:8000/docs
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
