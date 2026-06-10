@echo off
cd /d "%~dp0"
set PORT=8001
set PYTHONPATH=%CD%
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  call .venv\Scripts\pip install -r requirements.txt
)
call .venv\Scripts\uvicorn app.main:app --reload --host 127.0.0.1 --port %PORT%
