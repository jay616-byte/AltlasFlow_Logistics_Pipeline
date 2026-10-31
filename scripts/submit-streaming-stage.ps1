# Accept only the Bronze or Silver layer; the selected value determines which SQL files and job name are used.
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('Bronze', 'Silver')]
  [string]$Stage
)
# Stop on the first error so a failed bootstrap cannot be mistaken for a running stream.
$ErrorActionPreference = 'Stop'
$ExpectedJob = if ($Stage -eq 'Bronze') { 'atlasflow-bronze-cdc' } else { 'atlasflow-silver-cdc-normalize' }
$Layer = if ($Stage -eq 'Bronze') { 'bronze' } else { 'silver' }
$TemplateDir = Join-Path (Get-Location) "sql/$Layer"
$RuntimeDir = Join-Path (Get-Location) "runtime/$Layer"
$TemplateName = if ($Stage -eq 'Bronze') { '01_cdc_to_bronze.sql.template' } else { '01_cdc_to_silver.sql.template' }
$BootstrapName = if ($Stage -eq 'Bronze') { 'bootstrap_bronze_tables.sql.template' } else { 'bootstrap_silver_tables.sql.template' }
$TransformName = if ($Stage -eq 'Bronze') { '01_cdc_to_bronze.sql' } else { '01_cdc_to_silver.sql' }
$BootstrapRendered = if ($Stage -eq 'Bronze') { 'bootstrap_bronze_tables.sql' } else { 'bootstrap_silver_tables.sql' }
$LogPath = if ($Stage -eq 'Bronze') { '/tmp/atlasflow-lite-bronze.log' } else { '/tmp/atlasflow-lite-silver.log' }
$JobsUrl = 'http://localhost:8081/jobs/overview'

# Read the local RustFS credentials from .env for secure SQL-template rendering.
function Read-EnvValue([string]$Name) {
  foreach ($line in Get-Content '.env') {
    if ($line.Trim() -match "^$([regex]::Escape($Name))=(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
  }
  throw "$Name is missing from .env. Run .\scripts\prepare-local-env.ps1 first."
}
# Replace object-store placeholders, reject unresolved tokens, and write the rendered SQL into the layer runtime folder.
function Render-Sql([string]$SourceName, [string]$TargetName) {
  $Text = Get-Content -Raw (Join-Path $TemplateDir $SourceName)
  $Text = $Text.Replace('__RUSTFS_ACCESS_KEY__', $AccessKey).Replace('__RUSTFS_SECRET_KEY__', $SecretKey)
  if ($Text -match '__[A-Z0-9_]+__') { throw "Unresolved placeholders remain in $SourceName." }
  $Target = Join-Path $RuntimeDir $TargetName
  [System.IO.File]::WriteAllText($Target, $Text, [System.Text.UTF8Encoding]::new($false))
  return $Target
}

# Validate credentials and query Flink so an already-running job is not submitted a second time.
$AccessKey = Read-EnvValue 'RUSTFS_ACCESS_KEY'
$SecretKey = Read-EnvValue 'RUSTFS_SECRET_KEY'
if ($AccessKey -notmatch '^[A-Za-z0-9_-]{8,80}$' -or $SecretKey -notmatch '^[A-Za-z0-9_-]{8,80}$') {
  throw 'RUSTFS_ACCESS_KEY and RUSTFS_SECRET_KEY must each be 8-80 letters, digits, underscores, or hyphens.'
}
$Overview = Invoke-RestMethod -Uri $JobsUrl -TimeoutSec 10
$Existing = @($Overview.jobs | Where-Object { $_.name -eq $ExpectedJob -and $_.state -eq 'RUNNING' })
if ($Existing.Count -gt 0) {
  Write-Host "$ExpectedJob is already RUNNING; not creating a duplicate." -ForegroundColor Green
  exit 0
}
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
# Track temporary rendered files so the finally block can remove them even if a later step fails.
$RenderedPaths = @()
try {
  # First create the target Iceberg table/catalog metadata, then start the layer-specific streaming transformation.
  $BootstrapPath = Render-Sql $BootstrapName $BootstrapRendered
  $RenderedPaths += $BootstrapPath
  Write-Host "Bootstrapping the $Stage Iceberg table..." -ForegroundColor Cyan
  docker compose exec -T flink-jobmanager /opt/flink/bin/sql-client.sh -f "/opt/atlasflow/runtime/$Layer/$BootstrapRendered"
  if ($LASTEXITCODE -ne 0) { throw "$Stage Iceberg bootstrap failed. Inspect Flink and Iceberg REST logs." }

  $TransformPath = Render-Sql $TemplateName $TransformName
  $RenderedPaths += $TransformPath
  $ContainerPath = "/opt/atlasflow/runtime/$Layer/$TransformName"
  $ShellCommand = "nohup /opt/flink/bin/sql-client.sh -f '$ContainerPath' > '$LogPath' 2>&1 < /dev/null &"
  Write-Host "Submitting the single $Stage transformation: $ExpectedJob" -ForegroundColor Cyan
  docker compose exec -d -T flink-jobmanager /bin/sh -lc $ShellCommand
  if ($LASTEXITCODE -ne 0) { throw "Could not start the $Stage SQL client." }

  # Poll the Flink REST API for the expected RUNNING job and show the SQL-client log if it never starts.
  $Deadline = (Get-Date).AddSeconds(150)
  do {
    Start-Sleep -Seconds 4
    $Overview = Invoke-RestMethod -Uri $JobsUrl -TimeoutSec 10
    $Running = @($Overview.jobs | Where-Object { $_.name -eq $ExpectedJob -and $_.state -eq 'RUNNING' })
    if ($Running.Count -gt 0) { break }
  } while ((Get-Date) -lt $Deadline)
  if ($Running.Count -eq 0) {
    Write-Host "SQL client log ($LogPath):" -ForegroundColor Yellow
    docker compose exec -T flink-jobmanager /bin/sh -lc "tail -n 100 '$LogPath'"
    throw "$ExpectedJob did not reach RUNNING state. Fix the first Flink SQL error in the log above."
  }
  Write-Host "PASS: $ExpectedJob is RUNNING." -ForegroundColor Green
}
# Delete the temporary SQL files after submission so rendered credentials are not left in the runtime directory.
finally {
  foreach ($Path in $RenderedPaths) { if (Test-Path $Path) { Remove-Item -Force $Path -ErrorAction SilentlyContinue } }
}
