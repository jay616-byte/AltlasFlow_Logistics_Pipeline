#!/usr/bin/env python3
"""Execute each standalone SELECT in sql/atlasflow_queries.sql through Trino's HTTP API."""
# This utility runs the project SQL query pack against Trino using its HTTP statement API.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


# Split a multi-query SQL file safely: semicolons inside quoted strings and line comments are not separators.
def split_sql(text: str) -> list[str]:
    """Split this query pack at semicolons while respecting SQL single quotes/comments."""
    statements: list[str] = []
    current: list[str] = []
    in_quote = False
    i = 0
    while i < len(text):
        char = text[i]
        if not in_quote and char == "-" and i + 1 < len(text) and text[i + 1] == "-":
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if char == "'":
            current.append(char)
            if in_quote and i + 1 < len(text) and text[i + 1] == "'":
                current.append(text[i + 1])
                i += 2
                continue
            in_quote = not in_quote
        elif char == ";" and not in_quote:
            sql = "".join(current).strip()
            if sql:
                statements.append(sql)
            current = []
        else:
            current.append(char)
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    if in_quote:
        raise ValueError("The query pack contains an unterminated SQL string literal.")
    return statements


# Submit one statement to Trino, follow its nextUri result pages, and collect the returned columns and rows.
def execute(base_url: str, sql: str, timeout: float, user: str) -> tuple[list[str], list[list[object]]]:
    base = base_url.rstrip("/")
    req = Request(
        base + "/v1/statement",
        data=sql.encode("utf-8"),
        method="POST",
        headers={
            "X-Trino-User": user,
            "Content-Type": "text/plain; charset=utf-8",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    columns: list[str] = []
    rows: list[list[object]] = []
    pages = 0
    while True:
        pages += 1
        if payload.get("error"):
            err = payload["error"]
            raise RuntimeError(str(err.get("message") or err))
        if payload.get("columns") and not columns:
            columns = [str(col.get("name", "")) for col in payload["columns"]]
        rows.extend(payload.get("data", []))
        next_uri = payload.get("nextUri")
        if not next_uri:
            break
        parsed_next = urlsplit(next_uri)
        path = parsed_next.path
        if not path.startswith("/"):
            raise RuntimeError(f"Unexpected Trino nextUri path: {path}")
        next_target = base + path + (("?" + parsed_next.query) if parsed_next.query else "")
        with urlopen(Request(next_target, headers={"X-Trino-User": user, "Accept": "application/json"}), timeout=timeout) as resp:
            payload = json.loads(resp.read())
        if pages > 100:
            raise RuntimeError("Query exceeded 100 result pages; stopping to avoid an unbounded wait.")
    return columns, rows


# Format the query result as a readable terminal table and limit only what is printed, not what is fetched.
def print_table(columns: list[str], rows: list[list[object]], row_limit: int) -> None:
    if not columns:
        print("Query completed (no tabular columns returned).")
        return
    shown = rows[:row_limit]
    strings = [[str(v) if v is not None else "NULL" for v in row] for row in shown]
    widths = [len(c) for c in columns]
    for row in strings:
        for ix, val in enumerate(row):
            widths[ix] = min(42, max(widths[ix], len(val)))
    def fmt(row: list[str]) -> str:
        return " | ".join(val[:widths[ix]].ljust(widths[ix]) for ix, val in enumerate(row))
    print(fmt(columns))
    print("-+-".join("-" * w for w in widths))
    for row in strings:
        print(fmt(row))
    if len(rows) > len(shown):
        print(f"... {len(rows) - len(shown)} additional rows not printed")
    print(f"{len(rows)} row(s)")


# Parse options, load and split the SQL file, and optionally validate it without connecting to Trino.
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default="sql/atlasflow_queries.sql", help="path to the combined query pack")
    parser.add_argument("--url", default="http://localhost:18080", help="host-mapped Trino URL")
    parser.add_argument("--user", default="atlasflow-query-pack", help="temporary Trino session username")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request timeout in seconds")
    parser.add_argument("--row-limit", type=int, default=30, help="maximum rows printed for each query")
    parser.add_argument("--check-only", action="store_true", help="split/count SQL without connecting to Trino")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"ERROR: query pack not found: {path}", file=sys.stderr)
        return 2
    try:
        statements = split_sql(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not read/split query pack: {exc}", file=sys.stderr)
        return 2
    if not statements:
        print("ERROR: the query pack contains no SQL statements.", file=sys.stderr)
        return 2
    print(f"AtlasFlow query pack: {len(statements)} statement(s)")
    if args.check_only:
        for idx, statement in enumerate(statements, 1):
            print(f"{idx:02d}. {statement.splitlines()[0][:100]}")
        return 0

    # Execute statements in file order and stop on the first query/API error so failures are easy to isolate.
    for idx, statement in enumerate(statements, 1):
        print(f"\n=== Query {idx}/{len(statements)} ===")
        print(statement.splitlines()[0][:140])
        try:
            columns, rows = execute(args.url, statement, args.timeout, args.user)
            print_table(columns, rows, args.row_limit)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, RuntimeError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            print("Stop at the first failing query; check the Gold streaming job and Trino logs.", file=sys.stderr)
            return 1
    print("\nAll query-pack statements completed.")
    return 0


# Return the command-line result as the process exit code for PowerShell and CI callers.
if __name__ == "__main__":
    raise SystemExit(main())
