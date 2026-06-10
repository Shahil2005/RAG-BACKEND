@echo off
cd /d "%~dp0"
set PYTHONPATH=%CD%
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  call .venv\Scripts\python -m pip install -r requirements.txt
)
call .venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 3001
