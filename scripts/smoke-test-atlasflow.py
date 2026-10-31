#!/usr/bin/env python3
"""Read-only health check for the compact AtlasFlow stack."""
# This is a read-only end-to-end health check; it inspects services and queries but does not change data.
from __future__ import annotations

import argparse
import json
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


# Shared HTTP helper for checking service endpoints and retrieving small JSON/text responses.
def read_http(url: str, timeout: float) -> tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": "AtlasFlow-YouTube-Smoke-Test/1.0", "Accept": "application/json, text/plain, */*"})
    with urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


# Retry an HTTP health endpoint briefly to tolerate services that are still starting up.
def check_http(label: str, url: str, timeout: float, retries: int) -> tuple[bool, str]:
    last = "no response"
    for attempt in range(retries + 1):
        try:
            status, _ = read_http(url, timeout)
            if 200 <= status < 300:
                return True, f"HTTP {status} {url}"
            last = f"HTTP {status} {url}"
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last = f"{url}: {exc}"
        if attempt < retries:
            time.sleep(2)
    return False, last


# Check raw TCP connectivity for services such as PostgreSQL and Kafka that expose a broker/database port.
def check_tcp(label: str, host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"TCP {host}:{port} accepts connections"
    except OSError as exc:
        return False, f"TCP {host}:{port}: {exc}"


# Confirm the expected Debezium connector is registered and that its connector and tasks are RUNNING.
def check_connector(timeout: float) -> tuple[bool, str]:
    name = "atlasflow-postgres-cdc"
    try:
        _, raw = read_http("http://localhost:8083/connectors", timeout)
        if name not in json.loads(raw):
            return False, f"connector {name} is not registered"
        _, raw = read_http(f"http://localhost:8083/connectors/{name}/status", timeout)
        status = json.loads(raw)
        bad = [f"connector={status.get('connector', {}).get('state')}" ] if status.get("connector", {}).get("state") != "RUNNING" else []
        bad.extend(f"task-{task.get('id')}={task.get('state')}" for task in status.get("tasks", []) if task.get("state") != "RUNNING")
        if bad:
            return False, ", ".join(bad)
        return True, "one Debezium connector and its task are RUNNING"
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return False, f"cannot inspect connector status: {exc}"


# Verify that the required Bronze, Silver, and Gold streaming jobs are active in Flink.
def check_flink_jobs(timeout: float) -> tuple[bool, str]:
    expected = {"atlasflow-bronze-cdc", "atlasflow-silver-cdc-normalize", "atlasflow-silver-current-state-models", "atlasflow-gold-control-tower-stream"}
    try:
        _, raw = read_http("http://localhost:8081/jobs/overview", timeout)
        jobs = json.loads(raw).get("jobs", [])
        running = {job.get("name") for job in jobs if job.get("state") == "RUNNING"}
        missing = sorted(expected - running)
        return (not missing, "Bronze, Silver CDC, Silver models, and Gold streaming jobs are RUNNING" if not missing else "missing RUNNING jobs: " + ", ".join(missing))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return False, f"cannot inspect Flink jobs: {exc}"


# Run a small read-only Trino statement and follow paginated results to verify the Gold table is queryable.
def trino_query(base_url: str, sql: str, timeout: float) -> tuple[bool, str, list[list[object]]]:
    request = Request(
        base_url.rstrip("/") + "/v1/statement", data=sql.encode("utf-8"), method="POST",
        headers={"X-Trino-User": "atlasflow-smoke", "Content-Type": "text/plain; charset=utf-8", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
        rows: list[list[object]] = []
        pages = 0
        while True:
            pages += 1
            if payload.get("error"):
                error = payload["error"]
                return False, str(error.get("message") or error), rows
            rows.extend(payload.get("data", []))
            next_uri = payload.get("nextUri")
            if not next_uri:
                break
            parsed_next = urlsplit(next_uri)
            path = parsed_next.path
            if not path.startswith("/"):
                return False, f"unexpected Trino nextUri: {next_uri}", rows
            next_target = base_url.rstrip("/") + path + (("?" + parsed_next.query) if parsed_next.query else "")
            with urlopen(Request(next_target, headers={"X-Trino-User": "atlasflow-smoke"}), timeout=timeout) as response:
                payload = json.loads(response.read())
            if pages > 60:
                return False, "Trino query exceeded 60 pages", rows
        return True, "query completed", rows
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return False, f"Trino query failed: {exc}", []


# Orchestrate service, connector, Flink-job, and Gold-table checks; print one consolidated pass/fail report.
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--skip-gold-query", action="store_true")
    parser.add_argument("--skip-stream-jobs", action="store_true")
    args = parser.parse_args()
    checks: list[tuple[str, bool, str]] = []

    # First check the database and Kafka ports, then test the HTTP APIs exposed by the rest of the stack.
    checks.append(("PostgreSQL", *check_tcp("PostgreSQL", "localhost", 55432, args.timeout)))
    checks.append(("Kafka", *check_tcp("Kafka", "localhost", 9092, args.timeout)))
    endpoints = [
        ("Kafka Connect", "http://localhost:8083/connectors"),
        ("Kafka UI", "http://localhost:8090/actuator/health"),
        ("Iceberg REST", "http://localhost:18181/v1/config"),
        ("Flink", "http://localhost:8081/overview"),
        ("Trino", "http://localhost:18080/v1/info"),
        ("Superset", "http://localhost:8088/health"),
        ("Prometheus", "http://localhost:9090/-/ready"),
        ("Grafana", "http://localhost:3000/api/health"),
    ]
    for label, url in endpoints:
        ok, message = check_http(label, url, args.timeout, args.retries)
        checks.append((label, ok, message))
    # Connector and streaming-job state checks are separate from simple endpoint availability.
    ok, message = check_connector(args.timeout)
    checks.append(("Debezium", ok, message))
    if not args.skip_stream_jobs:
        ok, message = check_flink_jobs(args.timeout)
        checks.append(("Flink jobs", ok, message))
    if not args.skip_gold_query:
        ok, message, rows = trino_query("http://localhost:18080", "SELECT COUNT(*) FROM atlasflow.gold.shipment_control_tower", args.timeout)
        checks.append(("Gold table query", ok, message + (f"; row_count={rows[0][0]}" if ok and rows else "")))

    # Print all results together and return a non-zero exit code if any required check failed.
    for label, ok, message in checks:
        print(f"{'PASS' if ok else 'FAIL'} | {label}: {message}")
    failures = [(label, message) for label, ok, message in checks if not ok]
    print(f"\nSmoke-test summary: {len(checks) - len(failures)} passed, {len(failures)} failed")
    if failures:
        print("Use docker compose ps and docker compose logs <service> to diagnose the first failing service.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
