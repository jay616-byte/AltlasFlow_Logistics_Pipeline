# -StaticOnly runs repository checks only; the default path also validates Compose and live services.
param([switch]$StaticOnly)
$ErrorActionPreference = 'Stop'
# Move to the project root so all relative paths and Docker Compose commands resolve consistently.
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
  # Prepare the local secrets file and Python environment when either one is missing.
  if (-not (Test-Path '.env') -or -not (Test-Path '.venv/Scripts/python.exe')) {
    & (Join-Path $PSScriptRoot 'prepare-local-env.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Local environment preparation failed.' }
  }
  $Python = (Resolve-Path '.venv/Scripts/python.exe').Path
  # Run fast source/config validation first; do not proceed to live checks if static validation fails.
  & $Python '.\scripts\check-atlasflow-static.py'
  if ($LASTEXITCODE -ne 0) { throw 'Static validation failed.' }
  if ($StaticOnly) { Write-Host 'Static-only verification complete.' -ForegroundColor Green; return }
  # Ask Docker Compose to validate the fully resolved configuration, then run the read-only live smoke test.
  docker compose config --quiet
  if ($LASTEXITCODE -ne 0) { throw 'docker compose config failed.' }
  & $Python '.\scripts\smoke-test-atlasflow.py'
  if ($LASTEXITCODE -ne 0) { throw 'Live smoke test failed; inspect the first failing service.' }
  Write-Host 'AtlasFlow verification passed.' -ForegroundColor Green
# Report the first error and always restore the caller's original working directory.
} catch {
  Write-Error $_
  exit 1
} finally { Pop-Location }
