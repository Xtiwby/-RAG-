@echo off
setlocal
cd /d "%~dp0backend"
if not exist ".venv\Scripts\python.exe" (
  echo Backend virtual environment is missing.
  exit /b 1
)
".venv\Scripts\python.exe" -m app.worker
