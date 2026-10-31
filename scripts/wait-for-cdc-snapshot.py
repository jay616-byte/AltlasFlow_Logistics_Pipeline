#!/usr/bin/env python3
"""Wait until the initial PostgreSQL snapshot has reached the requested Iceberg layer."""
# Wait for the initial Debezium snapshot to become visible in Bronze or Silver through Trino.
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


# Submit COUNT(*) to Trino and follow its nextUri pages until the table count is returned.
def query_count(base_url: str, table: str, timeout: float) -> int:
    base = base_url.rstrip("/")
    request = Request(
        base + "/v1/statement",
        data=f"SELECT COUNT(*) FROM {table}".encode(),
        method="POST",
        headers={"X-Trino-User": "atlasflow-bootstrap", "Content-Type": "text/plain"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    deadline = time.monotonic() + timeout
    while True:
        if payload.get("error"):
            raise RuntimeError(payload["error"].get("message", str(payload["error"])))
        rows = payload.get("data", [])
        if rows:
            return int(rows[0][0])
        next_uri = payload.get("nextUri")
        if not next_uri:
            raise RuntimeError("Trino completed without returning a count row.")
        parsed = urlsplit(next_uri)
        next_target = base + parsed.path + (("?" + parsed.query) if parsed.query else "")
        with urlopen(Request(next_target, headers={"X-Trino-User": "atlasflow-bootstrap"}), timeout=timeout) as response:
            payload = json.loads(response.read())
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for Trino query results.")


# Validate the requested table and poll until its row count reaches the configured snapshot threshold.
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", default="atlasflow.bronze.cdc_events")
    parser.add_argument("--minimum-rows", type=int, default=34)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--url", default="http://localhost:18080")
    args = parser.parse_args()
    if args.table not in {"atlasflow.bronze.cdc_events", "atlasflow.silver.cdc_changes"}:
        parser.error("--table must be the known Bronze or Silver CDC table")
    # Keep retrying transient Trino/startup errors until the overall timeout expires.
    deadline = time.monotonic() + args.timeout
    last_error = "no query attempted"
    while time.monotonic() < deadline:
        try:
            count = query_count(args.url, args.table, timeout=20)
            print(f"Waiting for initial CDC snapshot: {args.table} contains {count} row(s); need {args.minimum_rows}.")
            if count >= args.minimum_rows:
                print(f"PASS: initial snapshot reached {args.table} ({count} rows).")
                return 0
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(5)
    print(f"ERROR: timed out waiting for {args.table}. Last query error: {last_error}", file=sys.stderr)
    return 1


# Expose success/failure to the calling shell through the process exit code.
if __name__ == "__main__":
    raise SystemExit(main())
