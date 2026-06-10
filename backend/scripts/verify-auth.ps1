# Quick Microsoft OAuth wiring check (run while API + web are up)
$ErrorActionPreference = 'Stop'
$api = 'http://localhost:3001/api/v1/health'

Write-Host 'Checking API health...'
$health = Invoke-RestMethod -Uri $api
Write-Host "  azureConfigured: $($health.azureConfigured)"
Write-Host "  oauthRedirectUri: $($health.oauthRedirectUri)"

if ($health.oauthRedirectUri -notlike '*localhost:3000*') {
  Write-Host 'FAIL: OAUTH_REDIRECT_URI must use port 3000. Restart API after updating .env' -ForegroundColor Red
  exit 1
}

Write-Host 'Checking login redirect (API)...'
$headers = curl.exe -s -D - -o NUL --max-time 10 'http://localhost:3001/api/v1/auth/microsoft/login'
$loc = ($headers | Select-String '^location:' -CaseSensitive:$false).Line -replace '^location:\s*',''
if (-not $loc) {
  Write-Host 'FAIL: no redirect from login endpoint' -ForegroundColor Red
  exit 1
}
if ($loc -notmatch 'redirect_uri=http[^&]*3000') {
  Write-Host 'FAIL: Microsoft authorize URL still uses wrong redirect_uri' -ForegroundColor Red
  Write-Host $loc
  exit 1
}

Write-Host 'OK: Auth wiring looks correct.' -ForegroundColor Green
Write-Host 'Register this redirect URI in Entra (star-bot -> Authentication):'
Write-Host "  $($health.oauthRedirectUri)"
