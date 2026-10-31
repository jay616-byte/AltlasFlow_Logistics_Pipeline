# Stop the script as soon as a PowerShell or Docker command reports an error.
$ErrorActionPreference = 'Stop'
# Use one consolidated business CDC topic for the Debezium change events.
$Topic = 'atlasflow.cdc.v1'
Write-Host "Creating the single business topic: $Topic" -ForegroundColor Cyan
# Create the topic only if it is missing, with three partitions, one replica, and a three-day retention window.
docker compose exec -T kafka /opt/kafka/bin/kafka-topics.sh `
  --bootstrap-server kafka:19092 --create --if-not-exists `
  --topic $Topic --partitions 3 --replication-factor 1 `
  --config retention.ms=259200000
if ($LASTEXITCODE -ne 0) { throw "Could not create Kafka topic $Topic." }
# List the broker topics afterward so the operator can verify what exists.
docker compose exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:19092 --list
if ($LASTEXITCODE -ne 0) { throw 'Could not list Kafka topics.' }
