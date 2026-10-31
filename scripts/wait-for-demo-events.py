#!/usr/bin/env python3
"""Wait until the generated PostgreSQL changes reach the Silver Iceberg table."""
# Wait for generated demo changes to flow from PostgreSQL through Debezium and the streaming layers into Silver.
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

# This verification query counts tracking and disruption changes and checks for the expected SHP-008 delayed update.
QUERY = """
SELECT
  SUM(CASE WHEN source_table = 'tracking_events' THEN 1 ELSE 0 END) AS tracking_rows,
  SUM(CASE WHEN source_table = 'disruptions' THEN 1 ELSE 0 END) AS disruption_rows,
  SUM(CASE WHEN source_table = 'shipments' AND entity_id = 'SHP-008'
            AND operation = 'u' AND LOWER(shipment_status) = 'delayed'
       THEN 1 ELSE 0 END) AS delayed_shipment_updates
FROM atlasflow.silver.cdc_changes
""".strip()


# Submit the verification query to Trino and follow paginated responses until the three counts are available.
def execute_query(base_url: str, timeout: float) -> list[object]:
    base = base_url.rstrip("/")
    request = Request(
        f"{base}/v1/statement",
        data=QUERY.encode("utf-8"),
        method="POST",
        headers={"X-Trino-User": "atlasflow-event-wait", "Content-Type": "text/plain; charset=utf-8"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    deadline = time.monotonic() + timeout
    while True:
        if payload.get("error"):
            error = payload["error"]
            raise RuntimeError(str(error.get("message") or error))
        rows = payload.get("data", [])
        if rows:
            return [int(value or 0) for value in rows[0]]
        next_uri = payload.get("nextUri")
        if not next_uri:
            raise RuntimeError("Trino completed without returning the event counts.")
        parsed_next = urlsplit(next_uri)
        path = parsed_next.path
        if not path.startswith("/"):
            raise RuntimeError(f"Unexpected Trino nextUri path: {next_uri}")
        next_target = base + path + (("?" + parsed_next.query) if parsed_next.query else "")
        with urlopen(Request(next_target, headers={"X-Trino-User": "atlasflow-event-wait"}), timeout=timeout) as response:
            payload = json.loads(response.read())
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out reading Trino event-count results.")


# Validate polling options and keep checking Silver until every expected type of generated change is present.
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:18080")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args()
    if args.timeout < 1 or args.poll_seconds < 1:
        parser.error("--timeout and --poll-seconds must be positive")

    # Poll periodically; keep the most recent error or row counts to explain a timeout clearly.
    deadline = time.monotonic() + args.timeout
    last_error = "no query attempted"
    while time.monotonic() < deadline:
        try:
            tracking, disruptions, delayed_updates = execute_query(args.url, timeout=20)
            if tracking > 0 and disruptions > 0 and delayed_updates > 0:
                print("PASS: generated PostgreSQL changes have reached Silver.")
                print(f"Tracking CDC rows: {tracking}; disruption CDC rows: {disruptions}; SHP-008 delayed updates: {delayed_updates}")
                return 0
            last_error = (
                f"tracking_rows={tracking}, disruption_rows={disruptions}, "
                f"delayed_shipment_updates={delayed_updates}"
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(args.poll_seconds)
    print(f"ERROR: generated changes did not reach Silver within {args.timeout}s. Last result: {last_error}", file=sys.stderr)
    return 1


# Return a process exit code so automation can detect whether the generated events reached Silver.
if __name__ == "__main__":
    raise SystemExit(main())
