# Run from repo root: .\infrastructure\postgres\scripts\setup-windows.ps1
param(
  [string]$Host = "localhost",
  [int]$Port = 5432,
  [string]$User = "postgres",
  [string]$Database = "starbot"
)

$ErrorActionPreference = "Stop"
$postgresRoot = Split-Path -Parent $PSScriptRoot
$migrations = Join-Path $postgresRoot "migrations"

Write-Host "Creating database (if missing)..."
& psql -h $Host -p $Port -U $User -d postgres -f (Join-Path $PSScriptRoot "create-database.sql")

Write-Host "Running migrations on $Database..."
& psql -h $Host -p $Port -U $User -d $Database -f (Join-Path $migrations "001_auth_core.sql")
& psql -h $Host -p $Port -U $User -d $Database -f (Join-Path $migrations "002_enterprise_schema.sql")

Write-Host "Done."
