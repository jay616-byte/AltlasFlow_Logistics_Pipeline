#!/bin/bash
set -euo pipefail

# The CDC role is created separately so Debezium can read PostgreSQL's WAL.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 -v cdc_password="$CDC_PASSWORD" <<'SQL'
CREATE ROLE atlasflow_cdc WITH LOGIN REPLICATION PASSWORD :'cdc_password';
CREATE DATABASE atlasflow_ops;
SQL
