#!/usr/bin/env python3
"""Generate coherent logistics changes in PostgreSQL for the AtlasFlow demo.

All writes stay in PostgreSQL. Debezium captures the existing seven-table
schema and publishes the same CDC record shape used by the rest of the pipeline.
"""
from __future__ import annotations

import argparse
import os
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

ROOT = Path(__file__).resolve().parents[1]
FAULT_SHIPMENT = "SHP-008"
LOCATIONS_BY_WAREHOUSE = {
    "WH-US-EAST": ["East Coast Hub", "Regional Delivery Center"],
    "WH-EU-WEST": ["Port of Rotterdam", "Regional Delivery Center"],
    "WH-APAC-SG": ["Singapore Gateway", "Regional Delivery Center"],
}
FALLBACK_LOCATIONS = [
    "East Coast Hub",
    "Port of Rotterdam",
    "Singapore Gateway",
    "Regional Delivery Center",
]
DEMO_DISRUPTION_ID = "DSP-DEMO-SHP-008"
DEMO_DISRUPTION_DESCRIPTION = "Synthetic port congestion scenario for the control-tower demo"

# Keep these quantities mirrored in docker/postgres/init/10-atlasflow-schema.sql.
# They intentionally demonstrate inventory risk in all three warehouse regions.
INVENTORY_TARGETS = (
    ("WH-US-EAST", "SKU-LAPTOP-A", 120, 85, 2),   # ATP 33; SHP-001 needs 40
    ("WH-US-EAST", "SKU-KEYBOARD-A", 200, 145, 1),  # ATP 54; SHP-006 needs 60
    ("WH-EU-WEST", "SKU-LAPTOP-A", 100, 20, 5),  # ATP 75; covers delivered SHP-004 and SHP-008
    ("WH-EU-WEST", "SKU-DOCK-A", 160, 90, 4),   # ATP 66; SHP-002 needs 80
    ("WH-EU-WEST", "SKU-HEADSET-A", 90, 35, 10), # ATP 45; SHP-005 needs 50
    ("WH-APAC-SG", "SKU-MONITOR-A", 70, 42, 3),  # ATP 25; SHP-003 needs 30
)


def load_dotenv() -> None:
    """Load simple KEY=VALUE settings without needing a separate dotenv package."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        raise FileNotFoundError(
            ".env is missing. Copy .env.example to .env and set the required local development values first."
        )
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect() -> psycopg.Connection:
    load_dotenv()
    return psycopg.connect(
        host="localhost",
        port=int(os.environ.get("POSTGRES_PORT", "55432")),
        dbname="atlasflow_ops",
        user=os.environ.get("POSTGRES_USER", "atlasflow_admin"),
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=10,
    )


def event_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16].upper()}"


def ensure_demo_inventory(cursor: psycopg.Cursor[Any]) -> int:
    """Align seeded inventory values even when an existing Docker volume is reused.

    This changes source values only; it does not add or alter columns or tables.
    No update is emitted when a row already has the target quantities.
    """
    updated_rows = 0
    for warehouse_id, sku, on_hand, reserved, damaged in INVENTORY_TARGETS:
        cursor.execute(
            "SELECT 1 FROM public.inventory_balances WHERE warehouse_id = %s AND product_sku = %s",
            (warehouse_id, sku),
        )
        if cursor.fetchone() is None:
            raise RuntimeError(
                f"Expected inventory seed row not found: {warehouse_id}/{sku}. "
                "Initialize the seven-table PostgreSQL seed data first."
            )
        cursor.execute(
            """UPDATE public.inventory_balances
               SET on_hand_quantity = %s,
                   reserved_quantity = %s,
                   damaged_quantity = %s,
                   updated_at = now()
               WHERE warehouse_id = %s AND product_sku = %s
                 AND (on_hand_quantity IS DISTINCT FROM %s
                   OR reserved_quantity IS DISTINCT FROM %s
                   OR damaged_quantity IS DISTINCT FROM %s)""",
            (
                on_hand, reserved, damaged, warehouse_id, sku,
                on_hand, reserved, damaged,
            ),
        )
        updated_rows += cursor.rowcount
    return updated_rows


def load_shipments(cursor: psycopg.Cursor[Any]) -> list[dict[str, str]]:
    cursor.execute(
        """SELECT s.shipment_id, s.status, s.origin_warehouse_id,
                  CASE
                    WHEN inv.warehouse_id IS NOT NULL
                     AND (inv.on_hand_quantity - inv.reserved_quantity - inv.damaged_quantity) < o.quantity
                    THEN TRUE ELSE FALSE
                  END AS has_inventory_shortage
           FROM public.shipments AS s
           JOIN public.orders AS o ON o.order_id = s.order_id
           LEFT JOIN public.inventory_balances AS inv
             ON inv.warehouse_id = s.origin_warehouse_id
            AND inv.product_sku = o.product_sku
           ORDER BY s.shipment_id"""
    )
    rows = cursor.fetchall()
    if not rows:
        raise RuntimeError("No seeded shipments were found in public.shipments.")
    return [
        {
            "shipment_id": row[0],
            "status": row[1],
            "origin_warehouse_id": row[2],
            "has_inventory_shortage": bool(row[3]),
        }
        for row in rows
    ]


def choose_event_type(
    shipment_id: str, status: str, has_inventory_shortage: bool, rng: random.Random
) -> str:
    """Choose an event compatible with the shipment's current state.

    Delivered shipments remain delivered; delayed shipments continue to report
    delays; ordinary shipments may progress, become delayed, or be delivered.
    SHP-008 is kept non-terminal so the fault-bundle scenario remains reusable.
    """
    current = (status or "").strip().lower()
    if current == "delivered":
        return "delivered"
    if current in {"delayed", "exception"}:
        return "delay"
    if shipment_id == FAULT_SHIPMENT:
        return rng.choices(["in_transit", "arrived_facility", "delay"], weights=[5, 3, 2])[0]
    # Do not generate a delivered event for a shipment that Gold currently flags
    # for a stock shortfall; that would make a completed shipment look actionable.
    if has_inventory_shortage:
        if current in {"planned", "booked", "confirmed"}:
            return rng.choices(
                ["picked_up", "in_transit", "arrived_facility", "delay"],
                weights=[3, 3, 2, 2],
            )[0]
        return rng.choices(
            ["in_transit", "arrived_facility", "delay"], weights=[4, 3, 2]
        )[0]
    if current in {"planned", "booked", "confirmed"}:
        return rng.choices(
            ["picked_up", "in_transit", "arrived_facility", "delay", "delivered"],
            weights=[3, 3, 2, 2, 1],
        )[0]
    return rng.choices(
        ["in_transit", "arrived_facility", "delay", "delivered"],
        weights=[4, 3, 2, 1],
    )[0]


def status_for_event(event_type: str) -> str:
    """Translate the latest tracking event into the compatible shipment state."""
    if event_type == "delay":
        return "delayed"
    if event_type == "delivered":
        return "delivered"
    # picked_up, in_transit and arrived_facility are all active transit states.
    return "in_transit"


def synchronize_shipment_status(cursor: psycopg.Cursor[Any], shipment_id: str, event_type: str) -> str:
    """Keep shipment state and the newest generated tracking event coherent."""
    new_status = status_for_event(event_type)
    cursor.execute(
        """UPDATE public.shipments
           SET status = %s,
               actual_delivery_at = CASE
                   WHEN %s = 'delivered' THEN COALESCE(actual_delivery_at, now())
                   ELSE NULL
               END,
               source_version = source_version + 1,
               updated_at = now()
           WHERE shipment_id = %s
             AND (status IS DISTINCT FROM %s
               OR (%s = 'delivered' AND actual_delivery_at IS NULL)
               OR (%s <> 'delivered' AND actual_delivery_at IS NOT NULL))""",
        (new_status, new_status, shipment_id, new_status, new_status, new_status),
    )
    if cursor.rowcount not in (0, 1):
        raise RuntimeError(f"Unexpected update count while synchronizing {shipment_id}: {cursor.rowcount}")
    return new_status


def insert_tracking(
    cursor: psycopg.Cursor[Any],
    shipment_id: str,
    event_type: str,
    location: str,
    delay_minutes: int = 0,
) -> None:
    cursor.execute(
        """INSERT INTO public.tracking_events
           (event_id, shipment_id, event_type, event_time, location, delay_minutes)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (event_id("TRK"), shipment_id, event_type, datetime.now(timezone.utc), location, delay_minutes),
    )


def run_baseline(cursor: psycopg.Cursor[Any], count: int, rng: random.Random) -> int:
    """Create ordinary tracking updates spread across all existing shipments."""
    shipments = load_shipments(cursor)
    for index in range(count):
        shipment = shipments[index % len(shipments)]
        shipment_id = shipment["shipment_id"]
        event_type = choose_event_type(
            shipment_id,
            shipment["status"],
            shipment["has_inventory_shortage"],
            rng,
        )
        new_status = status_for_event(event_type)
        delay = rng.choice([15, 30, 45, 90]) if event_type == "delay" else 0
        locations = LOCATIONS_BY_WAREHOUSE.get(
            shipment["origin_warehouse_id"], FALLBACK_LOCATIONS
        )
        location = rng.choice(locations)

        # Both writes use the same PostgreSQL transaction and the same source schema.
        synchronize_shipment_status(cursor, shipment_id, event_type)
        insert_tracking(cursor, shipment_id, event_type, location, delay)
        shipment["status"] = new_status
    return count


def run_fault_bundle(cursor: psycopg.Cursor[Any]) -> tuple[int, int]:
    """Produce the repeatable SHP-008 delay, exception and port disruption."""
    shipment = FAULT_SHIPMENT
    cursor.execute(
        """UPDATE public.shipments
           SET status = 'delayed', actual_delivery_at = NULL,
               source_version = source_version + 1, updated_at = now()
           WHERE shipment_id = %s""",
        (shipment,),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"Expected one row for {shipment}; found {cursor.rowcount}.")

    insert_tracking(cursor, shipment, "exception", "Port of Rotterdam", 180)

    # Deactivate duplicates created by earlier versions of the generator.
    cursor.execute(
        """UPDATE public.disruptions
           SET active = FALSE
           WHERE shipment_id = %s
             AND disruption_type = 'port_congestion'
             AND description = %s
             AND active = TRUE
             AND disruption_id <> %s""",
        (shipment, DEMO_DISRUPTION_DESCRIPTION, DEMO_DISRUPTION_ID),
    )
    # Use a stable key so rerunning the demo does not accumulate active duplicates.
    cursor.execute(
        """INSERT INTO public.disruptions
           (disruption_id, shipment_id, disruption_type, severity, location, started_at,
            expected_delay_minutes, active, description)
           VALUES (%s, %s, 'port_congestion', 'high', 'Port of Rotterdam', now(), 180, TRUE, %s)
           ON CONFLICT (disruption_id) DO UPDATE SET
             shipment_id = EXCLUDED.shipment_id,
             disruption_type = EXCLUDED.disruption_type,
             severity = EXCLUDED.severity,
             location = EXCLUDED.location,
             started_at = EXCLUDED.started_at,
             expected_delay_minutes = EXCLUDED.expected_delay_minutes,
             active = TRUE,
             description = EXCLUDED.description""",
        (DEMO_DISRUPTION_ID, shipment, DEMO_DISRUPTION_DESCRIPTION),
    )
    return 1, 1


def execute_once(scenario: str, count: int, rng: random.Random) -> tuple[int, int, int]:
    """Run one committed batch and return tracking, disruption and inventory counts."""
    with connect() as connection, connection.cursor() as cursor:
        inventory_updates = ensure_demo_inventory(cursor)
        if scenario == "baseline":
            tracking_count = run_baseline(cursor, count, rng)
            disruption_count = 0
        else:
            tracking_count, disruption_count = run_fault_bundle(cursor)
    return tracking_count, disruption_count, inventory_updates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", choices=("baseline", "fault-bundle"), default="baseline",
        help="baseline creates tracking updates; fault-bundle creates the SHP-008 disruption demo",
    )
    parser.add_argument(
        "--count", type=int, default=12,
        help="tracking rows per baseline run (or per continuous batch)",
    )
    parser.add_argument("--seed", type=int, default=7, help="repeatable random choices for event selection")
    parser.add_argument(
        "--continuous", action="store_true",
        help="repeat baseline batches until Ctrl+C; disabled by default",
    )
    parser.add_argument(
        "--interval-seconds", type=float, default=30.0,
        help="pause between continuous baseline batches (minimum 1 second; default: 30)",
    )
    args = parser.parse_args()
    if args.count < 1 or args.count > 500:
        parser.error("--count must be between 1 and 500")
    if args.interval_seconds < 1:
        parser.error("--interval-seconds must be at least 1")
    if args.continuous and args.scenario != "baseline":
        parser.error("--continuous is supported only with --scenario baseline; fault-bundle is a one-shot scenario")

    rng = random.Random(args.seed)
    batch_number = 0
    try:
        while True:
            batch_number += 1
            tracking_count, disruption_count, inventory_updates = execute_once(
                args.scenario, args.count, rng
            )
            print(f"Scenario: {args.scenario}" + (f" (batch {batch_number})" if args.continuous else ""))
            print(f"Inserted tracking events: {tracking_count}")
            print(f"Inserted disruption events: {disruption_count}")
            if inventory_updates:
                print(f"Aligned existing inventory rows for the demo: {inventory_updates}")
            print("All generated data was committed to PostgreSQL first.")
            print("Debezium captures those database changes and publishes them to Kafka.")
            if not args.continuous:
                return 0
            print(
                f"Next batch in {args.interval_seconds:g} seconds. "
                "Press Ctrl+C to stop continuous generation."
            )
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        print("\nContinuous baseline generation stopped cleanly.")
        return 0
    except (KeyError, OSError, psycopg.Error, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
