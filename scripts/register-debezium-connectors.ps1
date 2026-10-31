# Treat failed setup commands and REST requests as errors rather than continuing with a partial connector.
$ErrorActionPreference = 'Stop'
$ConnectUrl = 'http://localhost:8083'
# Read a named setting from the local .env file without printing the secret value.
function Read-EnvValue([string]$Name) {
  foreach ($line in Get-Content '.env') {
    if ($line.Trim() -match "^$([regex]::Escape($Name))=(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
  }
  throw "$Name is missing from .env. Run .\scripts\prepare-local-env.ps1 first."
}
# Load the database CDC account password and verify that it is not an unconfigured placeholder.
$Password = Read-EnvValue 'CDC_PASSWORD'
if ([string]::IsNullOrWhiteSpace($Password) -or $Password -match 'change_this') { throw 'CDC_PASSWORD is not configured. Run setup and retry.' }
try { $null = Invoke-RestMethod -Method Get -Uri "$ConnectUrl/" -TimeoutSec 10 }
catch { throw "Kafka Connect API is not ready at $ConnectUrl." }
# Load the connector definition and inject the password from .env instead of storing it in JSON.
$Doc = Get-Content -Raw 'connectors/postgres-cdc-connector.json' | ConvertFrom-Json
$Doc.config.'database.password' = $Password
$Name = [string]$Doc.name
$Names = Invoke-RestMethod -Method Get -Uri "$ConnectUrl/connectors/" -TimeoutSec 10
# Make registration repeatable: update the existing connector, or create it when it is not registered yet.
if ($Names -contains $Name) {
  Write-Host "Updating connector $Name" -ForegroundColor Cyan
  $Body = $Doc.config | ConvertTo-Json -Depth 50
  $null = Invoke-RestMethod -Method Put -Uri "$ConnectUrl/connectors/$Name/config" -ContentType 'application/json' -Body $Body -TimeoutSec 20
} else {
  Write-Host "Registering connector $Name" -ForegroundColor Cyan
  $Body = $Doc | ConvertTo-Json -Depth 50
  $null = Invoke-RestMethod -Method Post -Uri "$ConnectUrl/connectors/" -ContentType 'application/json' -Body $Body -TimeoutSec 20
}
# Poll the connector and task status for up to three minutes, stopping immediately on a FAILED state.
$Deadline = (Get-Date).AddMinutes(3)
do {
  Start-Sleep -Seconds 5
  try { $Status = Invoke-RestMethod -Method Get -Uri "$ConnectUrl/connectors/$Name/status" -TimeoutSec 10 }
  catch { continue }
  $ConnectorState = [string]$Status.connector.state
  $TaskStates = @($Status.tasks | ForEach-Object { [string]$_.state })
  if ($ConnectorState -eq 'FAILED' -or $TaskStates -contains 'FAILED') {
    throw "Debezium connector failed. Inspect: docker compose logs connect"
  }
  if ($ConnectorState -eq 'RUNNING' -and $TaskStates.Count -gt 0 -and @($TaskStates | Where-Object { $_ -ne 'RUNNING' }).Count -eq 0) { break }
} while ((Get-Date) -lt $Deadline)
if ($ConnectorState -ne 'RUNNING' -or $TaskStates.Count -eq 0 -or @($TaskStates | Where-Object { $_ -ne 'RUNNING' }).Count -gt 0) {
  throw "Debezium connector did not reach RUNNING state. Inspect: docker compose logs connect"
}
# Report success only after the connector and at least one task are confirmed RUNNING.
Write-Host 'PASS: one Debezium connector is RUNNING and routes all seven tables into atlasflow.cdc.v1.' -ForegroundColor Green
