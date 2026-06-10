# Starbot AI service — port 8001 avoids Windows Hyper-V reservation on 8000
$ErrorActionPreference = "Stop"
$Port = if ($env:AI_SERVICE_PORT) { $env:AI_SERVICE_PORT } else { "8001" }
$BindHost = if ($env:AI_SERVICE_HOST) { $env:AI_SERVICE_HOST } else { "127.0.0.1" }

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
uvicorn app.main:app --reload --host $BindHost --port $Port
