# Stop on errors; this script prepares and starts the continuous Gold control-tower transformation.
$ErrorActionPreference = 'Stop'
$Root = Get-Location
# Read private object-store credentials from .env without embedding them in the SQL templates.
function Read-EnvValue([string]$Name) {
  foreach ($line in Get-Content '.env') {
    if ($line.Trim() -match "^$([regex]::Escape($Name))=(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
  }
  throw "$Name is missing from .env. Run .\scripts\prepare-local-env.ps1 first."
}
# Render a SQL template with the local RustFS keys and reject any placeholder that was not replaced.
function Render-Template([string]$SourcePath, [string]$TargetPath, [string]$AccessKey, [string]$SecretKey) {
  $Text = Get-Content -Raw $SourcePath
  $Text = $Text.Replace('__RUSTFS_ACCESS_KEY__', $AccessKey).Replace('__RUSTFS_SECRET_KEY__', $SecretKey)
  if ($Text -match '__[A-Z0-9_]+__') { throw "Unresolved placeholders remain in $SourcePath." }
  [System.IO.File]::WriteAllText($TargetPath, $Text, [System.Text.UTF8Encoding]::new($false))
}
# Validate the object-store credentials and check whether the Gold job is already running.
$AccessKey = Read-EnvValue 'RUSTFS_ACCESS_KEY'
$SecretKey = Read-EnvValue 'RUSTFS_SECRET_KEY'
if ($AccessKey -notmatch '^[A-Za-z0-9_-]{8,80}$' -or $SecretKey -notmatch '^[A-Za-z0-9_-]{8,80}$') {
  throw 'RUSTFS credentials must each be 8-80 letters, digits, underscores, or hyphens.'
}
$JobsUrl = 'http://localhost:8081/jobs/overview'
$Overview = Invoke-RestMethod -Uri $JobsUrl -TimeoutSec 10
if (@($Overview.jobs | Where-Object { $_.name -eq 'atlasflow-gold-control-tower-stream' -and $_.state -eq 'RUNNING' }).Count -gt 0) {
  Write-Host 'Gold streaming job is already RUNNING; not creating a duplicate.' -ForegroundColor Green
  exit 0
}
# Gold depends on the Silver CDC normalization job, so refuse to start when that upstream job is absent.
$RequiredJobs = @('atlasflow-silver-cdc-normalize')
$RunningNames = @($Overview.jobs | Where-Object { $_.state -eq 'RUNNING' } | ForEach-Object { $_.name })
$MissingJobs = @($RequiredJobs | Where-Object { $_ -notin $RunningNames })
if ($MissingJobs.Count -gt 0) { throw "Start Silver first. Required RUNNING jobs missing: $($MissingJobs -join ', ')" }
$RuntimeDir = Join-Path $Root 'runtime/gold'
$GoldDir = Join-Path $Root 'sql/gold'
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
$BootstrapPath = Join-Path $RuntimeDir 'bootstrap_gold_tables.sql'
$GoldStreamPath = Join-Path $RuntimeDir 'gold_control_tower_stream.sql'
# Create the keyed Gold Iceberg sink table first, then submit the continuous streaming SQL query.
try {
  Render-Template (Join-Path $GoldDir 'bootstrap_gold_tables.sql.template') $BootstrapPath $AccessKey $SecretKey
  Write-Host 'Creating the keyed Gold control-tower table...' -ForegroundColor Cyan
  docker compose exec -T flink-jobmanager /opt/flink/bin/sql-client.sh -f '/opt/atlasflow/runtime/gold/bootstrap_gold_tables.sql'
  if ($LASTEXITCODE -ne 0) { throw 'Gold upsert table bootstrap failed.' }

  Render-Template (Join-Path $GoldDir 'stream_shipment_control_tower.sql.template') $GoldStreamPath $AccessKey $SecretKey
  $GoldShell = "nohup /opt/flink/bin/sql-client.sh -f '/opt/atlasflow/runtime/gold/gold_control_tower_stream.sql' > '/tmp/atlasflow-lite-gold-stream.log' 2>&1 < /dev/null &"
  Write-Host 'Submitting atlasflow-gold-control-tower-stream...' -ForegroundColor Cyan
  docker compose exec -d -T flink-jobmanager /bin/sh -lc $GoldShell
  if ($LASTEXITCODE -ne 0) { throw 'Could not submit the Gold streaming query.' }

  # Wait for Flink to report the expected job as RUNNING; print its SQL-client log if startup fails.
  $Deadline = (Get-Date).AddSeconds(150)
  do {
    Start-Sleep -Seconds 4
    $Overview = Invoke-RestMethod -Uri $JobsUrl -TimeoutSec 10
    $Running = @($Overview.jobs | Where-Object { $_.name -eq 'atlasflow-gold-control-tower-stream' -and $_.state -eq 'RUNNING' })
    if ($Running.Count -gt 0) { break }
  } while ((Get-Date) -lt $Deadline)
  if ($Running.Count -eq 0) {
    docker compose exec -T flink-jobmanager /bin/sh -lc "tail -n 120 '/tmp/atlasflow-lite-gold-stream.log'"
    throw 'Gold streaming job did not reach RUNNING state. Fix the first Flink SQL error in the log above.'
  }
  Write-Host 'PASS: atlasflow-gold-control-tower-stream is RUNNING.' -ForegroundColor Green
  Write-Host 'Gold will keep updating as CDC changes arrive. Run the Trino query pack to verify results.' -ForegroundColor Cyan
}
# Remove rendered SQL files containing injected credentials, whether startup succeeds or fails.
finally {
  foreach ($Path in @($BootstrapPath, $GoldStreamPath)) { if (Test-Path $Path) { Remove-Item -Force $Path -ErrorAction SilentlyContinue } }
}
