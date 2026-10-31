# Stop immediately if PostgreSQL cannot be queried or the publication cannot be created.
$ErrorActionPreference = 'Stop'
# Read the PostgreSQL administrator setting from .env so credentials are not hard-coded here.
function Read-EnvValue([string]$Name) {
  foreach ($line in Get-Content '.env') {
    if ($line.Trim() -match "^$([regex]::Escape($Name))=(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
  }
  throw "$Name is missing from .env. Run .\scripts\prepare-local-env.ps1 first."
}
# Define one publication containing the seven tables that Debezium is allowed to capture.
$DbAdmin = Read-EnvValue 'POSTGRES_USER'
$Publication = 'atlasflow_publication'
$Tables = 'public.customers, public.orders, public.inventory_balances, public.carriers, public.shipments, public.tracking_events, public.disruptions'
# Check whether the publication already exists so this setup can safely be run more than once.
$Exists = docker compose exec -T postgres psql -X -qAt -U $DbAdmin -d atlasflow_ops -c "SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_publication WHERE pubname = '$Publication') THEN 'yes' ELSE 'no' END;"
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the CDC publication. Confirm PostgreSQL is healthy.' }
if ("$Exists".Trim() -eq 'no') {
  docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U $DbAdmin -d atlasflow_ops -c "CREATE PUBLICATION $Publication FOR TABLE $Tables;"
  if ($LASTEXITCODE -ne 0) { throw 'Could not create the PostgreSQL publication.' }
}
# Read back the publication membership and compare it with the exact expected table list.
$Actual = docker compose exec -T postgres psql -X -qAt -U $DbAdmin -d atlasflow_ops -c "SELECT schemaname || '.' || tablename FROM pg_publication_tables WHERE pubname = '$Publication' ORDER BY 1;"
if ($LASTEXITCODE -ne 0) { throw 'Could not verify the publication table list.' }
$Expected = @('public.carriers','public.customers','public.disruptions','public.inventory_balances','public.orders','public.shipments','public.tracking_events') | Sort-Object
$Got = @($Actual | ForEach-Object { "$($_)".Trim() } | Where-Object { $_ } | Sort-Object)
if (($Expected -join '|') -ne ($Got -join '|')) {
  throw "Publication has an unexpected table list. Expected seven simplified tables; found: $($Got -join ', ')"
}
Write-Host 'PASS: one PostgreSQL publication covers exactly the seven operational tables.' -ForegroundColor Green
