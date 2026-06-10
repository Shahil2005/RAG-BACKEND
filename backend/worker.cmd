@echo off
REM Celery worker + embedded beat scheduler (background ingestion sync).
REM Requires Redis to be running (REDIS_URL in .env). Windows needs --pool=solo.
cd /d "%~dp0"
set PYTHONPATH=%CD%
call .venv\Scripts\python -m celery -A app.core.celery:celery_app worker --beat --loglevel=info --pool=solo
