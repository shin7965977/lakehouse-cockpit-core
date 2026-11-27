"""Neon PostgreSQL Interactive Data Governance & SCD Type 2 Demo Engine.

Powers the Day 0 / Day 5 / Day 10 / Day 15 / Day 20 interactive lifecycle on a real
Neon PostgreSQL database (single-company Jaffle Shop e-commerce model, no tenant_id):
  - Day 0  : Reset & seed deterministic Jaffle Shop baseline + initial SCD2 snapshot
  - Day 5  : Normal daily refresh via Write-Audit-Publish (Candidate -> Validate -> Publish)
  - Day 10 : Customer tier/region changes via SCD Type 2 snapshot + Point-in-Time comparison
  - Day 15 : Dirty upstream data blocked by WAP -> Last-Known-Good preserved + Quarantine table
  - Day 20 : Semantic drift in CI PR -> Schema Contract PASS, Data Diff & Reconciliation BLOCK
"""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
import os
import random
from typing import Any

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

try:
    from .dbt_catalog import (
        CatalogColumn,
        CatalogMetric,
        CatalogRelation,
        DbtMetadataCatalog,
    )
except (ImportError, ValueError):
    try:
        from services.dbt_catalog import (
            CatalogColumn,
            CatalogMetric,
            CatalogRelation,
            DbtMetadataCatalog,
        )
    except (ImportError, ValueError):
        from cockpit_core.services.dbt_catalog import (
            CatalogColumn,
            CatalogMetric,
            CatalogRelation,
            DbtMetadataCatalog,
        )


DEMO_BASE_DATE = date(2025, 1, 1)
DEMO_H1_END = date(2025, 6, 30)

PRODUCTS_SEED = [
    (1, "Classic Cheese Jaffle", "Jaffle", 12.00, 4.20),
    (2, "Truffle Mushroom Jaffle", "Jaffle", 16.50, 6.10),
    (3, "Smoked Brisket Jaffle", "Jaffle", 18.00, 7.40),
    (4, "Spicy Kimchi Jaffle", "Jaffle", 15.00, 5.30),
    (5, "Oat Flat White", "Beverage", 5.50, 1.40),
    (6, "Cold Brew Tonic", "Beverage", 6.50, 1.75),
    (7, "Matcha Latte", "Beverage", 6.00, 1.90),
    (8, "Craft Ginger Beer", "Beverage", 5.00, 1.60),
    (9, "Salted Caramel Brownie", "Dessert", 7.50, 2.30),
    (10, "Cinnamon Sugar Churros", "Dessert", 8.00, 2.40),
    (11, "Morning Office Box (10x)", "Bundle", 135.00, 48.00),
    (12, "Team Lunch Catering Pack", "Bundle", 240.00, 88.00),
]

REGIONS = ["DACH", "Benelux", "Nordics", "Southern EU"]
FIRST_NAMES = [
    "Lukas", "Hannah", "Felix", "Sophie", "Jonas", "Mia", "Leon", "Emma",
    "Noah", "Clara", "Paul", "Laura", "Finn", "Anna", "Elias", "Julia",
    "Ben", "Lea", "Maximilian", "Lena", "Tiago", "Ines", "Diogo", "Sofia",
]
LAST_NAMES = [
    "Mueller", "Schmidt", "Weber", "Fischer", "Meyer", "Wagner", "Becker",
    "Hoffmann", "Schaefer", "Koch", "Silva", "Santos", "Ferreira", "Pereira",
    "De Vries", "Bakker", "Lindqvist", "Bergstrom", "Rossi", "Bianchi",
]


def get_database_url(explicit_url: str | None = None) -> str:
    """Resolve Neon PostgreSQL URL from explicit argument or environment."""
    url = (
        explicit_url
        or os.getenv("DATABASE_URL")
        or os.getenv("NEON_DATABASE_URL")
        or ""
    ).strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is missing. Set DATABASE_URL in .env or Streamlit Secrets."
        )
    return url


def _connect(db_url: str | None = None):
    return psycopg2.connect(get_database_url(db_url))


def _scd_id(customer_id: int, valid_from: str, tier: str, region: str) -> str:
    raw = f"{customer_id}|{valid_from}|{tier}|{region}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _generate_baseline_data() -> dict[str, list[tuple[Any, ...]]]:
    """Deterministically generate Day 0 Jaffle Shop baseline (6 months, H1 2025)."""
    rng = random.Random(42)

    customers: list[tuple[Any, ...]] = []
    # 120 customers: 12 VIP (Pareto top accounts), 28 Silver, 80 Standard
    for cid in range(1, 121):
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        if cid <= 12:
            tier = "VIP"
        elif cid <= 40:
            tier = "Silver"
        else:
            tier = "Standard"
        region = REGIONS[cid % len(REGIONS)]
        customers.append((cid, name, tier, region, DEMO_BASE_DATE))

    orders: list[tuple[Any, ...]] = []
    order_items: list[tuple[Any, ...]] = []
    order_id = 1001
    item_id = 1

    total_days = (DEMO_H1_END - DEMO_BASE_DATE).days + 1
    for _ in range(520):
        # Pareto skew: VIPs & top Standard accounts order more frequently
        r = rng.random()
        if r < 0.30:
            cid = rng.randint(1, 12)  # VIP
        elif r < 0.55:
            cid = rng.randint(13, 40)  # Silver
        elif r < 0.80:
            cid = rng.randint(41, 60)  # High-potential Standard (upgrade candidates on Day 10!)
        else:
            cid = rng.randint(61, 120)

        odate = DEMO_BASE_DATE + timedelta(days=rng.randint(0, total_days - 1))
        status_r = rng.random()
        if status_r < 0.84:
            status = "Completed"
        elif status_r < 0.93:
            status = "Pending"
        else:
            status = "Returned"

        n_items = rng.randint(1, 4)
        order_total = 0.0
        cost_total = 0.0
        for _ in range(n_items):
            # VIP & high-potential Standard sometimes buy Bundles
            if cid <= 12 or (41 <= cid <= 55 and rng.random() < 0.35):
                prod = rng.choice(PRODUCTS_SEED)
            else:
                prod = rng.choice(PRODUCTS_SEED[:10])
            pid, _pname, _cat, uprice, ucost = prod
            qty = rng.randint(1, 3)
            line_tot = round(uprice * qty, 2)
            order_total = round(order_total + line_tot, 2)
            cost_total = round(cost_total + round(ucost * qty, 2), 2)
            order_items.append((item_id, order_id, pid, qty, uprice, line_tot))
            item_id += 1

        orders.append((order_id, cid, odate, status, order_total, cost_total))
        order_id += 1

    return {
        "products": PRODUCTS_SEED,
        "customers": customers,
        "orders": orders,
        "order_items": order_items,
    }


def _create_schemas_and_tables(cur) -> None:
    cur.execute(
        """
        CREATE SCHEMA IF NOT EXISTS raw;
        CREATE SCHEMA IF NOT EXISTS snapshots;
        CREATE SCHEMA IF NOT EXISTS gold;
        CREATE SCHEMA IF NOT EXISTS quarantine;
        CREATE SCHEMA IF NOT EXISTS ci_pr;
        CREATE SCHEMA IF NOT EXISTS governance;
        """
    )

    cur.execute(
        """
        DROP TABLE IF EXISTS raw.raw_order_items CASCADE;
        DROP TABLE IF EXISTS raw.raw_orders CASCADE;
        DROP TABLE IF EXISTS raw.raw_customers CASCADE;
        DROP TABLE IF EXISTS raw.raw_products CASCADE;

        DROP TABLE IF EXISTS snapshots.snap_customers CASCADE;
        DROP TABLE IF EXISTS gold.candidate_fct_orders CASCADE;
        DROP TABLE IF EXISTS gold.fct_order_items CASCADE;
        DROP TABLE IF EXISTS gold.fct_orders CASCADE;
        DROP TABLE IF EXISTS gold.dim_customers_scd2 CASCADE;
        DROP TABLE IF EXISTS gold.dim_products CASCADE;
        DROP TABLE IF EXISTS gold.mart_customer_value CASCADE;

        DROP TABLE IF EXISTS quarantine.rejected_orders CASCADE;
        DROP TABLE IF EXISTS ci_pr.fct_orders_pr CASCADE;
        DROP TABLE IF EXISTS governance.pipeline_runs CASCADE;
        """
    )

    cur.execute(
        """
        CREATE TABLE raw.raw_products (
            product_id INTEGER PRIMARY KEY,
            product_name VARCHAR(120) NOT NULL,
            category VARCHAR(60) NOT NULL,
            unit_price NUMERIC(12, 2) NOT NULL,
            unit_cost NUMERIC(12, 2) NOT NULL
        );

        CREATE TABLE raw.raw_customers (
            customer_id INTEGER PRIMARY KEY,
            customer_name VARCHAR(120) NOT NULL,
            customer_tier VARCHAR(32) NOT NULL,
            region VARCHAR(64) NOT NULL,
            updated_at DATE NOT NULL
        );

        -- Intentionally no PK/NOT NULL constraint on raw_orders so Day 15 can simulate upstream bad data!
        CREATE TABLE raw.raw_orders (
            order_id INTEGER,
            customer_id INTEGER,
            order_date DATE,
            status VARCHAR(32),
            order_total NUMERIC(12, 2),
            cost_total NUMERIC(12, 2)
        );

        CREATE TABLE raw.raw_order_items (
            order_item_id INTEGER,
            order_id INTEGER,
            product_id INTEGER,
            quantity INTEGER,
            unit_price NUMERIC(12, 2),
            line_total NUMERIC(12, 2)
        );

        CREATE TABLE snapshots.snap_customers (
            dbt_scd_id VARCHAR(64) PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            customer_name VARCHAR(120) NOT NULL,
            customer_tier VARCHAR(32) NOT NULL,
            region VARCHAR(64) NOT NULL,
            dbt_updated_at DATE NOT NULL,
            dbt_valid_from DATE NOT NULL,
            dbt_valid_to DATE,
            is_current BOOLEAN NOT NULL
        );

        CREATE TABLE quarantine.rejected_orders (
            quarantine_id SERIAL PRIMARY KEY,
            run_day INTEGER NOT NULL,
            order_id INTEGER,
            customer_id INTEGER,
            order_date DATE,
            status VARCHAR(32),
            order_total NUMERIC(12, 2),
            rejection_reason VARCHAR(255) NOT NULL,
            quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE governance.pipeline_runs (
            run_id SERIAL PRIMARY KEY,
            run_day INTEGER NOT NULL,
            sim_date DATE NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            status VARCHAR(48) NOT NULL,
            summary TEXT NOT NULL,
            details_json TEXT NOT NULL,
            executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def _materialize_gold_from_raw(cur) -> None:
    """Publish validated Gold tables from raw & snapshots."""
    cur.execute(
        """
        DROP TABLE IF EXISTS gold.dim_products CASCADE;
        CREATE TABLE gold.dim_products AS
        SELECT
            product_id,
            product_name,
            category,
            unit_price::DOUBLE PRECISION AS unit_price,
            unit_cost::DOUBLE PRECISION AS unit_cost,
            ROUND((unit_price - unit_cost) / NULLIF(unit_price, 0), 4)::DOUBLE PRECISION AS gross_margin_pct
        FROM raw.raw_products;

        DROP TABLE IF EXISTS gold.dim_customers_scd2 CASCADE;
        CREATE TABLE gold.dim_customers_scd2 AS
        SELECT
            dbt_scd_id,
            customer_id,
            customer_name,
            customer_tier,
            region,
            dbt_valid_from,
            dbt_valid_to,
            is_current
        FROM snapshots.snap_customers;

        DROP TABLE IF EXISTS gold.fct_orders CASCADE;
        CREATE TABLE gold.fct_orders AS
        SELECT
            o.order_id,
            o.customer_id,
            o.order_date,
            TO_CHAR(o.order_date, 'YYYY-MM') AS order_month,
            o.status,
            (o.status = 'Completed') AS is_completed,
            o.order_total::DOUBLE PRECISION AS order_total,
            CASE WHEN o.status = 'Completed' THEN o.order_total::DOUBLE PRECISION ELSE 0.0 END AS realized_revenue,
            o.cost_total::DOUBLE PRECISION AS cost_total,
            CASE WHEN o.status = 'Completed' THEN (o.order_total - o.cost_total)::DOUBLE PRECISION ELSE 0.0 END AS gross_profit
        FROM raw.raw_orders o;

        DROP TABLE IF EXISTS gold.fct_order_items CASCADE;
        CREATE TABLE gold.fct_order_items AS
        SELECT
            i.order_item_id,
            i.order_id,
            i.product_id,
            i.quantity,
            i.unit_price::DOUBLE PRECISION AS unit_price,
            i.line_total::DOUBLE PRECISION AS line_total
        FROM raw.raw_order_items i;

        DROP TABLE IF EXISTS gold.mart_customer_value CASCADE;
        CREATE TABLE gold.mart_customer_value AS
        SELECT
            c.customer_id,
            c.customer_name,
            c.customer_tier,
            c.region,
            COUNT(o.order_id) AS order_count,
            COALESCE(SUM(o.realized_revenue), 0.0)::DOUBLE PRECISION AS total_revenue,
            COALESCE(AVG(CASE WHEN o.is_completed THEN o.order_total END), 0.0)::DOUBLE PRECISION AS average_order_value,
            COALESCE(MAX(CASE WHEN o.is_completed THEN o.order_total END), 0.0)::DOUBLE PRECISION AS max_order_amount,
            COALESCE(SUM(o.gross_profit), 0.0)::DOUBLE PRECISION AS total_gross_profit
        FROM gold.dim_customers_scd2 c
        LEFT JOIN gold.fct_orders o
          ON c.customer_id = o.customer_id
        WHERE c.is_current = TRUE
        GROUP BY c.customer_id, c.customer_name, c.customer_tier, c.region;
        """
    )


def _log_run(
    cur,
    *,
    run_day: int,
    sim_date: date,
    event_type: str,
    status: str,
    summary: str,
    details: dict[str, Any],
) -> None:
    cur.execute(
        """
        INSERT INTO governance.pipeline_runs (run_day, sim_date, event_type, status, summary, details_json)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (run_day, sim_date, event_type, status, summary, json.dumps(details, default=str)),
    )


def reset_demo(db_url: str | None = None) -> dict[str, Any]:
    """Day 0: Reset all demo schemas in Neon and seed deterministic Jaffle Shop baseline."""
    seed = _generate_baseline_data()
    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            _create_schemas_and_tables(cur)
            execute_values(
                cur,
                "INSERT INTO raw.raw_products VALUES %s",
                seed["products"],
            )
            execute_values(
                cur,
                "INSERT INTO raw.raw_customers VALUES %s",
                seed["customers"],
            )
            execute_values(
                cur,
                "INSERT INTO raw.raw_orders VALUES %s",
                seed["orders"],
            )
            execute_values(
                cur,
                "INSERT INTO raw.raw_order_items VALUES %s",
                seed["order_items"],
            )

            snap_rows = [
                (
                    _scd_id(cid, str(updated_at), tier, region),
                    cid,
                    name,
                    tier,
                    region,
                    updated_at,
                    updated_at,
                    None,
                    True,
                )
                for (cid, name, tier, region, updated_at) in seed["customers"]
            ]
            execute_values(
                cur,
                """
                INSERT INTO snapshots.snap_customers (
                    dbt_scd_id, customer_id, customer_name, customer_tier, region,
                    dbt_updated_at, dbt_valid_from, dbt_valid_to, is_current
                ) VALUES %s
                """,
                snap_rows,
            )

            _materialize_gold_from_raw(cur)

            cur.execute("SELECT COUNT(*), ROUND(SUM(realized_revenue)::numeric, 2) FROM gold.fct_orders")
            order_cnt, total_rev = cur.fetchone()
            details = {
                "customers": len(seed["customers"]),
                "orders": int(order_cnt),
                "order_items": len(seed["order_items"]),
                "realized_revenue": float(total_rev or 0.0),
                "snapshot_versions": len(snap_rows),
            }
            _log_run(
                cur,
                run_day=0,
                sim_date=DEMO_H1_END,
                event_type="RESET_BASELINE",
                status="HEALTHY",
                summary=f"Day 0 baseline seeded: {order_cnt} orders, {len(seed['customers'])} customers (initial SCD2 v1).",
                details=details,
            )
        conn.commit()
    return {"run_day": 0, "status": "HEALTHY", **details}


def _validate_candidate_orders(cur) -> list[dict[str, Any]]:
    """Run deterministic Write-Audit-Publish checks on gold.candidate_fct_orders."""
    checks: list[dict[str, Any]] = []

    cur.execute("SELECT COUNT(*) FROM gold.candidate_fct_orders WHERE order_id IS NULL OR customer_id IS NULL")
    null_cnt = int(cur.fetchone()[0])
    checks.append(
        {
            "check": "not_null(order_id, customer_id)",
            "passed": null_cnt == 0,
            "violations": null_cnt,
        }
    )

    cur.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT order_id FROM gold.candidate_fct_orders
            WHERE order_id IS NOT NULL
            GROUP BY order_id HAVING COUNT(*) > 1
        ) dup
        """
    )
    dup_cnt = int(cur.fetchone()[0])
    checks.append(
        {
            "check": "unique(order_id)",
            "passed": dup_cnt == 0,
            "violations": dup_cnt,
        }
    )

    cur.execute("SELECT COUNT(*) FROM gold.candidate_fct_orders WHERE order_total < 0 OR cost_total < 0")
    neg_cnt = int(cur.fetchone()[0])
    checks.append(
        {
            "check": "non_negative(order_total, cost_total)",
            "passed": neg_cnt == 0,
            "violations": neg_cnt,
        }
    )

    cur.execute(
        """
        SELECT COUNT(*) FROM gold.candidate_fct_orders o
        LEFT JOIN raw.raw_customers c ON o.customer_id = c.customer_id
        WHERE o.customer_id IS NOT NULL AND c.customer_id IS NULL
        """
    )
    orphan_cnt = int(cur.fetchone()[0])
    checks.append(
        {
            "check": "referential_integrity(customer_id)",
            "passed": orphan_cnt == 0,
            "violations": orphan_cnt,
        }
    )
    return checks


def run_day5_normal_refresh(db_url: str | None = None) -> dict[str, Any]:
    """Day 5: Normal incremental orders arrive -> Write-Audit-Publish passes -> Gold updated."""
    sim_date = date(2025, 7, 5)
    rng = random.Random(505)

    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            # Remove any prior Day 5 synthetic rows for idempotency if re-clicked
            cur.execute("DELETE FROM raw.raw_order_items WHERE order_id BETWEEN 5001 AND 5099")
            cur.execute("DELETE FROM raw.raw_orders WHERE order_id BETWEEN 5001 AND 5099")

            new_orders = []
            new_items = []
            item_id = 50001
            for idx in range(25):
                oid = 5001 + idx
                cid = rng.randint(1, 100)
                odate = date(2025, 7, 1) + timedelta(days=idx % 5)
                status = "Completed" if idx % 6 != 0 else "Pending"
                prod = rng.choice(PRODUCTS_SEED[:10])
                qty = rng.randint(1, 3)
                otot = round(prod[3] * qty, 2)
                ctot = round(prod[4] * qty, 2)
                new_orders.append((oid, cid, odate, status, otot, ctot))
                new_items.append((item_id, oid, prod[0], qty, prod[3], otot))
                item_id += 1

            execute_values(cur, "INSERT INTO raw.raw_orders VALUES %s", new_orders)
            execute_values(cur, "INSERT INTO raw.raw_order_items VALUES %s", new_items)

            # Write-Audit-Publish: build candidate first
            cur.execute(
                """
                DROP TABLE IF EXISTS gold.candidate_fct_orders;
                CREATE TABLE gold.candidate_fct_orders AS
                SELECT
                    order_id, customer_id, order_date,
                    TO_CHAR(order_date, 'YYYY-MM') AS order_month,
                    status,
                    (status = 'Completed') AS is_completed,
                    order_total::DOUBLE PRECISION AS order_total,
                    CASE WHEN status = 'Completed' THEN order_total::DOUBLE PRECISION ELSE 0.0 END AS realized_revenue,
                    cost_total::DOUBLE PRECISION AS cost_total,
                    CASE WHEN status = 'Completed' THEN (order_total - cost_total)::DOUBLE PRECISION ELSE 0.0 END AS gross_profit
                FROM raw.raw_orders;
                """
            )
            checks = _validate_candidate_orders(cur)
            all_passed = all(c["passed"] for c in checks)
            if all_passed:
                _materialize_gold_from_raw(cur)

            cur.execute("SELECT COUNT(*), ROUND(SUM(realized_revenue)::numeric, 2) FROM gold.fct_orders")
            total_orders, total_rev = cur.fetchone()
            details = {
                "new_orders_added": len(new_orders),
                "wap_checks": checks,
                "published_to_gold": all_passed,
                "gold_order_count": int(total_orders),
                "gold_realized_revenue": float(total_rev or 0.0),
            }
            _log_run(
                cur,
                run_day=5,
                sim_date=sim_date,
                event_type="DAY5_NORMAL_REFRESH",
                status="HEALTHY" if all_passed else "FAILED",
                summary=f"Day 5 Write-Audit-Publish PASSED: +{len(new_orders)} clean orders published to gold.fct_orders.",
                details=details,
            )
        conn.commit()
    return {"run_day": 5, "sim_date": str(sim_date), "status": "HEALTHY", **details}


def run_day10_scd_type2(db_url: str | None = None) -> dict[str, Any]:
    """Day 10: Customer tier & region changes -> SCD Type 2 snapshot + post-upgrade orders."""
    sim_date = date(2025, 7, 10)
    upgrade_ids = list(range(41, 56))  # 15 high-potential Standard customers -> VIP

    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            # 1. Update raw.raw_customers (source overwrites latest state, just like real ERP)
            cur.execute(
                """
                UPDATE raw.raw_customers
                SET customer_tier = 'VIP',
                    region = CASE WHEN MOD(customer_id, 2) = 0 THEN 'DACH' ELSE region END,
                    updated_at = %s
                WHERE customer_id = ANY(%s)
                """,
                (sim_date, upgrade_ids),
            )

            # 2. Execute dbt-equivalent SCD Type 2 snapshot merge on snapshots.snap_customers
            cur.execute(
                """
                UPDATE snapshots.snap_customers s
                SET dbt_valid_to = %s,
                    is_current = FALSE
                FROM raw.raw_customers r
                WHERE s.customer_id = r.customer_id
                  AND s.is_current = TRUE
                  AND (s.customer_tier <> r.customer_tier OR s.region <> r.region)
                """,
                (sim_date,),
            )
            closed_rows = cur.rowcount

            cur.execute(
                """
                SELECT r.customer_id, r.customer_name, r.customer_tier, r.region, r.updated_at
                FROM raw.raw_customers r
                LEFT JOIN snapshots.snap_customers s
                  ON r.customer_id = s.customer_id AND s.is_current = TRUE
                WHERE s.customer_id IS NULL
                """
            )
            new_versions = cur.fetchall()
            if new_versions:
                snap_inserts = [
                    (
                        _scd_id(cid, str(updated_at), tier, region),
                        cid,
                        name,
                        tier,
                        region,
                        updated_at,
                        updated_at,
                        None,
                        True,
                    )
                    for (cid, name, tier, region, updated_at) in new_versions
                ]
                execute_values(
                    cur,
                    """
                    INSERT INTO snapshots.snap_customers (
                        dbt_scd_id, customer_id, customer_name, customer_tier, region,
                        dbt_updated_at, dbt_valid_from, dbt_valid_to, is_current
                    ) VALUES %s
                    """,
                    snap_inserts,
                )

            # 3. Add post-upgrade orders on 2025-07-10 for upgraded VIP customers
            cur.execute("DELETE FROM raw.raw_order_items WHERE order_id BETWEEN 10001 AND 10099")
            cur.execute("DELETE FROM raw.raw_orders WHERE order_id BETWEEN 10001 AND 10099")
            rng = random.Random(1010)
            post_orders = []
            post_items = []
            item_id = 100001
            for idx, cid in enumerate(upgrade_ids):
                oid = 10001 + idx
                prod = PRODUCTS_SEED[10] if idx % 2 == 0 else PRODUCTS_SEED[2]
                qty = rng.randint(1, 2)
                otot = round(prod[3] * qty, 2)
                ctot = round(prod[4] * qty, 2)
                post_orders.append((oid, cid, sim_date, "Completed", otot, ctot))
                post_items.append((item_id, oid, prod[0], qty, prod[3], otot))
                item_id += 1

            execute_values(cur, "INSERT INTO raw.raw_orders VALUES %s", post_orders)
            execute_values(cur, "INSERT INTO raw.raw_order_items VALUES %s", post_items)

            # Refresh Gold dim_customers_scd2 & fct_orders (only if no dirty rows exist)
            cur.execute(
                """
                DROP TABLE IF EXISTS gold.candidate_fct_orders;
                CREATE TABLE gold.candidate_fct_orders AS
                SELECT
                    order_id, customer_id, order_date,
                    TO_CHAR(order_date, 'YYYY-MM') AS order_month,
                    status, (status = 'Completed') AS is_completed,
                    order_total::DOUBLE PRECISION AS order_total,
                    CASE WHEN status = 'Completed' THEN order_total::DOUBLE PRECISION ELSE 0.0 END AS realized_revenue,
                    cost_total::DOUBLE PRECISION AS cost_total,
                    CASE WHEN status = 'Completed' THEN (order_total - cost_total)::DOUBLE PRECISION ELSE 0.0 END AS gross_profit
                FROM raw.raw_orders;
                """
            )
            if all(c["passed"] for c in _validate_candidate_orders(cur)):
                _materialize_gold_from_raw(cur)

            cur.execute("SELECT COUNT(*) FROM snapshots.snap_customers")
            total_snap_rows = int(cur.fetchone()[0])

            details = {
                "upgraded_customers": len(upgrade_ids),
                "closed_old_versions": closed_rows,
                "inserted_new_versions": len(new_versions),
                "total_snapshot_rows": total_snap_rows,
                "post_upgrade_orders_added": len(post_orders),
            }
            _log_run(
                cur,
                run_day=10,
                sim_date=sim_date,
                event_type="DAY10_SCD_TYPE2_SNAPSHOT",
                status="HEALTHY",
                summary=f"Day 10 SCD Type 2 Snapshot: {len(upgrade_ids)} Standard customers upgraded to VIP ({closed_rows} old rows closed, {len(new_versions)} new versions inserted).",
                details=details,
            )
        conn.commit()
    return {"run_day": 10, "sim_date": str(sim_date), "status": "HEALTHY", **details}


def run_day15_dirty_data_wap(db_url: str | None = None) -> dict[str, Any]:
    """Day 15: Inject dirty upstream rows -> WAP rejects publish -> LKG preserved + Quarantine populated."""
    sim_date = date(2025, 7, 15)

    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            # Record current Last-Known-Good Gold metrics before dirty injection
            cur.execute("SELECT COUNT(*), ROUND(SUM(realized_revenue)::numeric, 2) FROM gold.fct_orders")
            lkg_count, lkg_rev = cur.fetchone()

            # Clean prior Day 15 injection if re-clicked
            cur.execute("DELETE FROM raw.raw_orders WHERE order_id IN (1001, 1002) AND order_date = %s", (sim_date,))
            cur.execute("DELETE FROM raw.raw_orders WHERE order_id BETWEEN 15001 AND 15099 OR order_id IS NULL")
            cur.execute("DELETE FROM quarantine.rejected_orders WHERE run_day = 15")

            # Inject 5 dirty rows into raw.raw_orders:
            # 2 duplicate PKs (1001, 1002), 2 negative amounts, 1 NULL customer_id
            dirty_rows = [
                (1001, 5, sim_date, "Completed", 420.00, 120.00),     # Duplicate PK
                (1002, 8, sim_date, "Completed", 180.00, 60.00),      # Duplicate PK
                (15001, 12, sim_date, "Completed", -350.00, 50.00),   # Negative order_total
                (15002, 19, sim_date, "Completed", -95.00, 20.00),    # Negative order_total
                (15003, None, sim_date, "Completed", 210.00, 70.00),  # NULL customer_id
            ]
            execute_values(cur, "INSERT INTO raw.raw_orders VALUES %s", dirty_rows)

            # Build candidate table & validate BEFORE publishing
            cur.execute(
                """
                DROP TABLE IF EXISTS gold.candidate_fct_orders;
                CREATE TABLE gold.candidate_fct_orders AS
                SELECT
                    order_id, customer_id, order_date,
                    TO_CHAR(order_date, 'YYYY-MM') AS order_month,
                    status, (status = 'Completed') AS is_completed,
                    order_total::DOUBLE PRECISION AS order_total,
                    CASE WHEN status = 'Completed' THEN order_total::DOUBLE PRECISION ELSE 0.0 END AS realized_revenue,
                    cost_total::DOUBLE PRECISION AS cost_total,
                    CASE WHEN status = 'Completed' THEN (order_total - cost_total)::DOUBLE PRECISION ELSE 0.0 END AS gross_profit
                FROM raw.raw_orders;
                """
            )
            checks = _validate_candidate_orders(cur)
            failed_checks = [c for c in checks if not c["passed"]]

            # Quarantine offending rows instead of silently deduplicating or publishing bad data
            quarantine_rows = [
                (15, 1001, 5, sim_date, "Completed", 420.00, "DUPLICATE_PRIMARY_KEY: order_id=1001 already exists"),
                (15, 1002, 8, sim_date, "Completed", 180.00, "DUPLICATE_PRIMARY_KEY: order_id=1002 already exists"),
                (15, 15001, 12, sim_date, "Completed", -350.00, "NEGATIVE_AMOUNT: order_total < 0 (possible unflagged refund)"),
                (15, 15002, 19, sim_date, "Completed", -95.00, "NEGATIVE_AMOUNT: order_total < 0 (possible unflagged refund)"),
                (15, 15003, None, sim_date, "Completed", 210.00, "NULL_FOREIGN_KEY: customer_id IS NULL"),
            ]
            execute_values(
                cur,
                """
                INSERT INTO quarantine.rejected_orders (
                    run_day, order_id, customer_id, order_date, status, order_total, rejection_reason
                ) VALUES %s
                """,
                quarantine_rows,
            )

            # DO NOT overwrite gold.fct_orders -> Last-Known-Good preserved!
            details = {
                "injected_dirty_rows": len(dirty_rows),
                "wap_checks": checks,
                "failed_check_count": len(failed_checks),
                "published_to_gold": False,
                "lkg_preserved": True,
                "lkg_order_count": int(lkg_count),
                "lkg_realized_revenue": float(lkg_rev or 0.0),
                "quarantined_rows": len(quarantine_rows),
                "staleness_hours": 14,
                "max_staleness_hours": 120,
            }
            _log_run(
                cur,
                run_day=15,
                sim_date=sim_date,
                event_type="DAY15_DIRTY_DATA_WAP_BLOCK",
                status="DEGRADED_LKG",
                summary=(
                    f"Day 15 Candidate REJECTED ({len(failed_checks)} failed WAP gates, {len(quarantine_rows)} rows quarantined). "
                    f"Production gold.fct_orders preserved at Last-Known-Good ({lkg_count} rows). Clarify with stakeholder before fixing."
                ),
                details=details,
            )
        conn.commit()
    return {"run_day": 15, "sim_date": str(sim_date), "status": "DEGRADED_LKG", **details}


def run_day20_semantic_drift_ci(db_url: str | None = None) -> dict[str, Any]:
    """Day 20: Simulate PR introducing semantic drift -> Schema Contract PASS, Data Diff & Reconciliation BLOCK."""
    sim_date = date(2025, 7, 20)

    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            # Build PR candidate model in ci_pr schema with IDENTICAL schema/types,
            # but drifted business logic: silently counts 'Pending' & 'Returned' orders as realized_revenue!
            cur.execute(
                """
                DROP TABLE IF EXISTS ci_pr.fct_orders_pr;
                CREATE TABLE ci_pr.fct_orders_pr AS
                SELECT
                    order_id,
                    customer_id,
                    order_date,
                    order_month,
                    status,
                    is_completed,
                    order_total,
                    -- DRIFTED LOGIC: includes Pending & Returned orders in realized_revenue!
                    order_total AS realized_revenue,
                    cost_total,
                    (order_total - cost_total) AS gross_profit
                FROM gold.fct_orders;
                """
            )

            # 1. Schema Contract Check (compare columns & types between gold.fct_orders and ci_pr.fct_orders_pr)
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'gold' AND table_name = 'fct_orders'
                ORDER BY ordinal_position
                """
            )
            prod_cols = cur.fetchall()
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'ci_pr' AND table_name = 'fct_orders_pr'
                ORDER BY ordinal_position
                """
            )
            pr_cols = cur.fetchall()
            schema_match = prod_cols == pr_cols

            # 2. Staging-vs-Prod Data Diff on realized_revenue
            cur.execute(
                """
                SELECT
                    COUNT(*) AS rows,
                    ROUND(SUM(realized_revenue)::numeric, 2) AS realized_revenue,
                    ROUND(SUM(gross_profit)::numeric, 2) AS gross_profit
                FROM gold.fct_orders
                """
            )
            prod_rows, prod_rev, prod_gp = cur.fetchone()
            cur.execute(
                """
                SELECT
                    COUNT(*) AS rows,
                    ROUND(SUM(realized_revenue)::numeric, 2) AS realized_revenue,
                    ROUND(SUM(gross_profit)::numeric, 2) AS gross_profit
                FROM ci_pr.fct_orders_pr
                """
            )
            pr_rows, pr_rev, pr_gp = cur.fetchone()

            prod_rev_f = float(prod_rev or 0.0)
            pr_rev_f = float(pr_rev or 0.0)
            delta_rev = round(pr_rev_f - prod_rev_f, 2)
            delta_pct = round((delta_rev / prod_rev_f * 100.0), 2) if prod_rev_f > 0 else 0.0
            drift_blocked = abs(delta_pct) > 5.0

            # 3. Reconciliation check against Completed-only GL baseline
            reconciliation_ok = abs(delta_rev) < 0.01

            ci_gates = [
                {
                    "gate": "1. dbt Schema Contract (columns & data_types)",
                    "status": "PASS" if schema_match else "FAIL",
                    "detail": f"{len(pr_cols)}/{len(prod_cols)} columns match exact types — proves Schema Contracts alone CANNOT catch semantic drift!",
                },
                {
                    "gate": "2. CI Data Diff (Staging vs. Prod KPI <= 5% threshold)",
                    "status": "BLOCKED" if drift_blocked else "PASS",
                    "detail": f"realized_revenue drifted by +{delta_pct}% (+${delta_rev:,.2f}): Prod=${prod_rev_f:,.2f} vs PR=${pr_rev_f:,.2f}.",
                },
                {
                    "gate": "3. GL Reconciliation Test (realized_revenue == Completed orders)",
                    "status": "PASS" if reconciliation_ok else "FAIL",
                    "detail": f"Unexplained variance of ${delta_rev:,.2f} caused by including Pending/Returned orders.",
                },
            ]

            details = {
                "pr_branch": "feat/update-revenue-filter",
                "drifted_rule": "Removed `WHERE status = 'Completed'` guard in realized_revenue projection",
                "ci_gates": ci_gates,
                "merge_allowed": False,
                "prod_revenue": prod_rev_f,
                "pr_revenue": pr_rev_f,
                "delta_revenue": delta_rev,
                "delta_pct": delta_pct,
                "prod_gross_profit": float(prod_gp or 0.0),
                "pr_gross_profit": float(pr_gp or 0.0),
                "row_count": int(prod_rows),
            }
            _log_run(
                cur,
                run_day=20,
                sim_date=sim_date,
                event_type="DAY20_SEMANTIC_DRIFT_CI_BLOCK",
                status="PR_BLOCKED",
                summary=(
                    f"Day 20 CI BLOCKED PR merge: Schema Contract PASSED, but Data Diff caught +{delta_pct}% "
                    f"semantic drift (+${delta_rev:,.2f}) on realized_revenue."
                ),
                details=details,
            )
        conn.commit()
    return {"run_day": 20, "sim_date": str(sim_date), "status": "PR_BLOCKED", **details}


def fetch_demo_state(db_url: str | None = None) -> dict[str, Any]:
    """Read current Neon demo state, pipeline audit log, SCD comparison, and quarantine rows."""
    with _connect(db_url) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'governance' AND table_name = 'pipeline_runs'
                ) AS initialized
                """
            )
            if not cur.fetchone()["initialized"]:
                return {"initialized": False}

            cur.execute(
                "SELECT run_day, sim_date, event_type, status, summary, details_json, executed_at "
                "FROM governance.pipeline_runs ORDER BY run_id DESC LIMIT 12"
            )
            runs = [dict(r) for r in cur.fetchall()]
            latest = runs[0] if runs else None

            cur.execute("SELECT COUNT(*) AS cnt, COALESCE(ROUND(SUM(realized_revenue)::numeric, 2), 0) AS rev FROM gold.fct_orders")
            gold_stats = dict(cur.fetchone())

            cur.execute("SELECT COUNT(*) AS total_versions, COUNT(DISTINCT customer_id) AS unique_customers FROM snapshots.snap_customers")
            snap_stats = dict(cur.fetchone())

            cur.execute("SELECT COUNT(*) AS cnt FROM quarantine.rejected_orders")
            quarantine_cnt = int(cur.fetchone()["cnt"])

            # SCD Type 1 (naive current-tier join) vs SCD Type 2 (point-in-time join) on H1 2025 historical orders
            cur.execute(
                """
                WITH scd1 AS (
                    SELECT c.customer_tier, ROUND(SUM(o.realized_revenue)::numeric, 2) AS scd1_h1_revenue
                    FROM gold.fct_orders o
                    JOIN gold.dim_customers_scd2 c ON o.customer_id = c.customer_id AND c.is_current = TRUE
                    WHERE o.order_date <= '2025-06-30'
                    GROUP BY c.customer_tier
                ),
                scd2 AS (
                    SELECT c.customer_tier, ROUND(SUM(o.realized_revenue)::numeric, 2) AS scd2_h1_revenue
                    FROM gold.fct_orders o
                    JOIN gold.dim_customers_scd2 c
                      ON o.customer_id = c.customer_id
                     AND o.order_date >= c.dbt_valid_from
                     AND (o.order_date < c.dbt_valid_to OR c.dbt_valid_to IS NULL)
                    WHERE o.order_date <= '2025-06-30'
                    GROUP BY c.customer_tier
                )
                SELECT
                    COALESCE(s1.customer_tier, s2.customer_tier) AS customer_tier,
                    COALESCE(s1.scd1_h1_revenue, 0)::DOUBLE PRECISION AS scd1_naive_latest_tier_rev,
                    COALESCE(s2.scd2_h1_revenue, 0)::DOUBLE PRECISION AS scd2_point_in_time_rev,
                    (COALESCE(s1.scd1_h1_revenue, 0) - COALESCE(s2.scd2_h1_revenue, 0))::DOUBLE PRECISION AS historical_distortion
                FROM scd1 s1
                FULL OUTER JOIN scd2 s2 ON s1.customer_tier = s2.customer_tier
                ORDER BY customer_tier
                """
            )
            scd_comparison = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT customer_id, customer_name, customer_tier, region, dbt_valid_from, dbt_valid_to, is_current
                FROM snapshots.snap_customers
                WHERE customer_id IN (
                    SELECT customer_id FROM snapshots.snap_customers GROUP BY customer_id HAVING COUNT(*) > 1
                )
                ORDER BY customer_id, dbt_valid_from
                LIMIT 20
                """
            )
            scd_changed_rows = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT order_id, customer_id, order_date, status, order_total, rejection_reason
                FROM quarantine.rejected_orders
                ORDER BY quarantine_id DESC
                LIMIT 20
                """
            )
            quarantine_rows = [dict(r) for r in cur.fetchall()]

    return {
        "initialized": True,
        "latest_run": latest,
        "runs": runs,
        "gold_order_count": int(gold_stats["cnt"]),
        "gold_realized_revenue": float(gold_stats["rev"]),
        "snapshot_versions": int(snap_stats["total_versions"]),
        "unique_customers": int(snap_stats["unique_customers"]),
        "quarantine_count": quarantine_cnt,
        "scd_comparison": scd_comparison,
        "scd_changed_rows": scd_changed_rows,
        "quarantine_rows": quarantine_rows,
    }


def execute_neon_readonly(sql: str, *, max_rows: int = 100, db_url: str | None = None) -> pd.DataFrame:
    """Execute a validated read-only SELECT query against Neon gold/snapshots schemas."""
    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL search_path TO gold, snapshots, public")
            cur.execute("SET LOCAL statement_timeout = '8000'")
            cur.execute(sql)
            cols = [desc[0] for desc in (cur.description or [])]
            rows = cur.fetchmany(max_rows)
            return pd.DataFrame(rows, columns=cols)


def build_jaffle_catalog() -> DbtMetadataCatalog:
    """Build the governed DbtMetadataCatalog for the single-company Jaffle Shop Neon warehouse."""
    relations = [
        CatalogRelation(
            unique_id="model.jaffle_governance_demo.fct_orders",
            name="fct_orders",
            schema_name="gold",
            layer="gold",
            last_run_status="pass",
            description="Order-level fact table (grain: order_id). Use realized_revenue for Completed orders only.",
            columns=[
                CatalogColumn(name="order_id", data_type="integer", description="Primary key of order", tests=["not_null", "unique"]),
                CatalogColumn(name="customer_id", data_type="integer", description="Customer foreign key", tests=["not_null"]),
                CatalogColumn(name="order_date", data_type="date", description="Order date", tests=["not_null"]),
                CatalogColumn(name="order_month", data_type="varchar", description="Order YYYY-MM"),
                CatalogColumn(name="status", data_type="varchar", description="Completed | Pending | Returned"),
                CatalogColumn(name="is_completed", data_type="boolean", description="True when status = Completed"),
                CatalogColumn(name="order_total", data_type="double", description="Gross order amount"),
                CatalogColumn(name="realized_revenue", data_type="double", description="Completed order revenue"),
                CatalogColumn(name="cost_total", data_type="double", description="Order COGS"),
                CatalogColumn(name="gross_profit", data_type="double", description="Completed revenue minus COGS"),
            ],
        ),
        CatalogRelation(
            unique_id="model.jaffle_governance_demo.fct_order_items",
            name="fct_order_items",
            schema_name="gold",
            layer="gold",
            last_run_status="pass",
            description="Order line-item fact table (grain: order_item_id). 1:N child of fct_orders.",
            columns=[
                CatalogColumn(name="order_item_id", data_type="integer", description="Primary key of line item", tests=["not_null", "unique"]),
                CatalogColumn(name="order_id", data_type="integer", description="Foreign key to fct_orders", tests=["not_null"]),
                CatalogColumn(name="product_id", data_type="integer", description="Foreign key to dim_products", tests=["not_null"]),
                CatalogColumn(name="quantity", data_type="integer", description="Units purchased"),
                CatalogColumn(name="unit_price", data_type="double", description="Unit selling price"),
                CatalogColumn(name="line_total", data_type="double", description="Line subtotal"),
            ],
        ),
        CatalogRelation(
            unique_id="model.jaffle_governance_demo.dim_customers_scd2",
            name="dim_customers_scd2",
            schema_name="gold",
            layer="gold",
            last_run_status="pass",
            description="SCD Type 2 customer dimension (grain: dbt_scd_id). Filter is_current=TRUE for latest or join on dbt_valid_from/dbt_valid_to for point-in-time.",
            columns=[
                CatalogColumn(name="dbt_scd_id", data_type="varchar", description="SCD Type 2 surrogate key", tests=["not_null", "unique"]),
                CatalogColumn(name="customer_id", data_type="integer", description="Natural customer key", tests=["not_null"]),
                CatalogColumn(name="customer_name", data_type="varchar", description="Customer full name"),
                CatalogColumn(name="customer_tier", data_type="varchar", description="Standard | Silver | VIP"),
                CatalogColumn(name="region", data_type="varchar", description="DACH | Benelux | Nordics | Southern EU"),
                CatalogColumn(name="dbt_valid_from", data_type="date", description="Version effective start date"),
                CatalogColumn(name="dbt_valid_to", data_type="date", description="Version effective end date (NULL if current)"),
                CatalogColumn(name="is_current", data_type="boolean", description="True for active version"),
            ],
        ),
        CatalogRelation(
            unique_id="model.jaffle_governance_demo.dim_products",
            name="dim_products",
            schema_name="gold",
            layer="gold",
            last_run_status="pass",
            description="Conformed product catalog dimension (grain: product_id).",
            columns=[
                CatalogColumn(name="product_id", data_type="integer", description="Primary key", tests=["not_null", "unique"]),
                CatalogColumn(name="product_name", data_type="varchar", description="Product display name"),
                CatalogColumn(name="category", data_type="varchar", description="Jaffle | Beverage | Dessert | Bundle"),
                CatalogColumn(name="unit_price", data_type="double", description="Selling price"),
                CatalogColumn(name="unit_cost", data_type="double", description="Unit COGS"),
                CatalogColumn(name="gross_margin_pct", data_type="double", description="Gross margin ratio"),
            ],
        ),
        CatalogRelation(
            unique_id="model.jaffle_governance_demo.mart_customer_value",
            name="mart_customer_value",
            schema_name="gold",
            layer="gold",
            last_run_status="pass",
            description="Pre-aggregated customer value mart (grain: customer_id). Eliminates ad-hoc 1:N joins.",
            columns=[
                CatalogColumn(name="customer_id", data_type="integer", description="Primary key", tests=["not_null", "unique"]),
                CatalogColumn(name="customer_name", data_type="varchar", description="Customer name"),
                CatalogColumn(name="customer_tier", data_type="varchar", description="Current customer tier"),
                CatalogColumn(name="region", data_type="varchar", description="Current region"),
                CatalogColumn(name="order_count", data_type="integer", description="Total orders"),
                CatalogColumn(name="total_revenue", data_type="double", description="Completed revenue"),
                CatalogColumn(name="average_order_value", data_type="double", description="Mean completed order value"),
                CatalogColumn(name="max_order_amount", data_type="double", description="Largest single completed order"),
                CatalogColumn(name="total_gross_profit", data_type="double", description="Total gross profit"),
            ],
        ),
    ]

    metrics = [
        CatalogMetric(
            name="total_revenue",
            label="Total Realized Revenue",
            description="Sum of realized_revenue from Completed orders in fct_orders.",
            semantic_model="orders",
            source_relation="fct_orders",
            measure_agg="sum",
            measure_expr="realized_revenue",
            dimensions=["order_month", "status", "customer_id"],
        ),
        CatalogMetric(
            name="order_count",
            label="Order Count",
            description="Count of orders in fct_orders.",
            semantic_model="orders",
            source_relation="fct_orders",
            measure_agg="count",
            measure_expr="order_id",
            dimensions=["order_month", "status", "customer_id"],
        ),
        CatalogMetric(
            name="gross_profit",
            label="Gross Profit",
            description="Sum of gross_profit (realized_revenue - cost_total) in fct_orders.",
            semantic_model="orders",
            source_relation="fct_orders",
            measure_agg="sum",
            measure_expr="gross_profit",
            dimensions=["order_month", "customer_id"],
        ),
    ]

    return DbtMetadataCatalog(relations=relations, metrics=metrics)


JAFFLE_ONE_TO_MANY_JOINS: set[tuple[str, str]] = {
    ("fct_orders", "fct_order_items"),
    ("dim_customers_scd2", "fct_orders"),
    ("dim_products", "fct_order_items"),
}
