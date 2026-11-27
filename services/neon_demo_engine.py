"""Neon PostgreSQL Real-dbt Data Governance & SCD Type 2 Demo Engine.

Executes REAL dbt Core commands via `dbt.cli.main.dbtRunner` against live Neon PostgreSQL
(single-company Jaffle Shop e-commerce model, no tenant_id):
  - Day 0  : Reset raw tables -> real `dbt snapshot` + `dbt run` + `dbt test --store-failures`
  - Day 5  : Normal orders -> `dbt run -s candidate_fct_orders` -> `dbt test --store-failures` -> publish `fct_orders_dbt`
  - Day 10 : Customer upgrades -> real `dbt snapshot` (SCD Type 2) -> `dbt run -s dim_customers_dbt mart_scd_comparison`
  - Day 15 : Dirty upstream rows -> `dbt test -s candidate_fct_orders --store-failures` FAILS ->
             Gold untouched (Last-Known-Good) + failed rows auto-stored by dbt in `dbt_dev_dbt_test__audit.*`
  - Day 20 : Semantic drift PR in `ci_pr` -> Schema Contract PASS, CI Data Diff & Reconciliation BLOCK
"""

from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
import random
from typing import Any
from urllib.parse import urlparse

from dbt.cli.main import dbtRunner
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


ROOT_DIR = Path(__file__).resolve().parents[1]
DBT_PROJECT_DIR = ROOT_DIR / "examples" / "jaffle_dbt"

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


def _set_dbt_env(db_url: str | None = None) -> None:
    parsed = urlparse(get_database_url(db_url))
    os.environ["NEON_HOST"] = parsed.hostname or ""
    os.environ["NEON_USER"] = parsed.username or ""
    os.environ["NEON_PASSWORD"] = parsed.password or ""
    os.environ["NEON_DB"] = (parsed.path or "/neondb").lstrip("/")


def _invoke_dbt(args: list[str], db_url: str | None = None) -> tuple[bool, list[dict[str, Any]]]:
    """Execute a real dbt command in-process via dbtRunner against Neon PostgreSQL."""
    _set_dbt_env(db_url)
    runner = dbtRunner()
    common = ["--project-dir", str(DBT_PROJECT_DIR), "--profiles-dir", str(DBT_PROJECT_DIR)]

    if not (DBT_PROJECT_DIR / "dbt_packages").exists() and args[0] != "deps":
        deps_res = runner.invoke(["deps", *common])
        if not deps_res.success:
            raise RuntimeError(f"dbt deps failed: {deps_res.exception}")

    res = runner.invoke([*args, *common])
    node_results: list[dict[str, Any]] = []
    if res.result is not None and hasattr(res.result, "results"):
        for r in res.result.results:
            node = getattr(r, "node", None)
            node_name = getattr(node, "name", "") or getattr(node, "unique_id", "")
            status_val = str(getattr(r, "status", ""))
            failures = getattr(r, "failures", 0)
            relation_name = getattr(node, "relation_name", "") or ""
            node_results.append(
                {
                    "test_or_model": node_name,
                    "status": status_val,
                    "passed": status_val.lower() in ("pass", "success"),
                    "violations": int(failures) if failures is not None else 0,
                    "audit_table": relation_name if "dbt_test__audit" in relation_name else "",
                }
            )
    return bool(res.success), node_results


def _generate_baseline_data() -> dict[str, list[tuple[Any, ...]]]:
    """Deterministically generate Day 0 Jaffle Shop baseline (6 months, H1 2025)."""
    rng = random.Random(42)

    customers: list[tuple[Any, ...]] = []
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
        r = rng.random()
        if r < 0.30:
            cid = rng.randint(1, 12)
        elif r < 0.55:
            cid = rng.randint(13, 40)
        elif r < 0.80:
            cid = rng.randint(41, 60)
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


def _sync_gold_explore_views(cur) -> None:
    """Point Explore-layer `gold.*` views directly at the dbt-published Gold tables."""
    cur.execute(
        """
        DROP SCHEMA IF EXISTS gold CASCADE;
        CREATE SCHEMA gold;

        CREATE VIEW gold.fct_orders AS
        SELECT * FROM dbt_dev_dbt_gold.fct_orders_dbt;

        CREATE VIEW gold.dim_customers_scd2 AS
        SELECT * FROM dbt_dev_dbt_gold.dim_customers_dbt;

        CREATE VIEW gold.dim_products AS
        SELECT
            product_id, product_name, category,
            unit_price::DOUBLE PRECISION AS unit_price,
            unit_cost::DOUBLE PRECISION AS unit_cost,
            ROUND((unit_price - unit_cost) / NULLIF(unit_price, 0), 4)::DOUBLE PRECISION AS gross_margin_pct
        FROM raw.raw_products;

        CREATE VIEW gold.fct_order_items AS
        SELECT
            order_item_id, order_id, product_id, quantity,
            unit_price::DOUBLE PRECISION AS unit_price,
            line_total::DOUBLE PRECISION AS line_total
        FROM raw.raw_order_items;

        CREATE VIEW gold.mart_customer_value AS
        SELECT
            c.customer_id, c.customer_name, c.customer_tier, c.region,
            COUNT(o.order_id) AS order_count,
            COALESCE(SUM(o.realized_revenue), 0.0)::DOUBLE PRECISION AS total_revenue,
            COALESCE(AVG(CASE WHEN o.is_completed THEN o.order_total END), 0.0)::DOUBLE PRECISION AS average_order_value,
            COALESCE(MAX(CASE WHEN o.is_completed THEN o.order_total END), 0.0)::DOUBLE PRECISION AS max_order_amount,
            COALESCE(SUM(o.gross_profit), 0.0)::DOUBLE PRECISION AS total_gross_profit
        FROM dbt_dev_dbt_gold.dim_customers_dbt c
        LEFT JOIN dbt_dev_dbt_gold.fct_orders_dbt o ON c.customer_id = o.customer_id
        WHERE c.is_current = TRUE
        GROUP BY c.customer_id, c.customer_name, c.customer_tier, c.region;
        """
    )


def _log_state(
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
        CREATE SCHEMA IF NOT EXISTS governance;
        CREATE TABLE IF NOT EXISTS governance.pipeline_runs (
            run_id SERIAL PRIMARY KEY,
            run_day INTEGER NOT NULL,
            sim_date DATE NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            status VARCHAR(48) NOT NULL,
            summary TEXT NOT NULL,
            details_json TEXT NOT NULL,
            executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO governance.pipeline_runs (run_day, sim_date, event_type, status, summary, details_json)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (run_day, sim_date, event_type, status, summary, json.dumps(details, default=str)),
    )


def reset_demo(db_url: str | None = None) -> dict[str, Any]:
    """Day 0: Reset raw tables & dbt schemas in Neon, then run real dbt snapshot + run + test."""
    seed = _generate_baseline_data()
    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE SCHEMA IF NOT EXISTS raw;
                CREATE SCHEMA IF NOT EXISTS ci_pr;
                CREATE SCHEMA IF NOT EXISTS governance;

                DROP SCHEMA IF EXISTS dbt_snapshots CASCADE;
                DROP SCHEMA IF EXISTS dbt_dev_dbt_test__audit CASCADE;
                DROP SCHEMA IF EXISTS dbt_dev_dbt_staging CASCADE;
                DROP SCHEMA IF EXISTS dbt_dev_dbt_gold CASCADE;
                DROP TABLE IF EXISTS ci_pr.fct_orders_pr CASCADE;
                DROP TABLE IF EXISTS governance.pipeline_runs CASCADE;

                DROP TABLE IF EXISTS raw.raw_order_items CASCADE;
                DROP TABLE IF EXISTS raw.raw_orders CASCADE;
                DROP TABLE IF EXISTS raw.raw_customers CASCADE;
                DROP TABLE IF EXISTS raw.raw_products CASCADE;

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
                """
            )
            execute_values(cur, "INSERT INTO raw.raw_products VALUES %s", seed["products"])
            execute_values(cur, "INSERT INTO raw.raw_customers VALUES %s", seed["customers"])
            execute_values(cur, "INSERT INTO raw.raw_orders VALUES %s", seed["orders"])
            execute_values(cur, "INSERT INTO raw.raw_order_items VALUES %s", seed["order_items"])
        conn.commit()

    # Run REAL dbt snapshot + run + test
    ok_silver, _ = _invoke_dbt(["run", "--select", "stg_orders", "stg_customers"], db_url)
    ok_snap, _ = _invoke_dbt(["snapshot"], db_url)
    ok_cand, _ = _invoke_dbt(["run", "--select", "candidate_fct_orders", "dim_customers_dbt"], db_url)
    ok_test, test_rows = _invoke_dbt(["test", "--select", "candidate_fct_orders", "--store-failures"], db_url)
    if not (ok_silver and ok_snap and ok_cand and ok_test):
        raise RuntimeError(f"Day 0 dbt baseline failed: {test_rows}")
    ok_gold, _ = _invoke_dbt(["run", "--select", "fct_orders_dbt", "mart_scd_comparison"], db_url)
    if not ok_gold:
        raise RuntimeError("Day 0 dbt Gold materialization failed.")

    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            _sync_gold_explore_views(cur)
            cur.execute("SELECT COUNT(*), ROUND(SUM(realized_revenue)::numeric, 2) FROM dbt_dev_dbt_gold.fct_orders_dbt")
            order_cnt, total_rev = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM dbt_snapshots.snap_customers_dbt")
            snap_cnt = int(cur.fetchone()[0])
            details = {
                "dbt_commands": [
                    "dbt snapshot",
                    "dbt run --select stg_orders stg_customers candidate_fct_orders dim_customers_dbt",
                    "dbt test --select candidate_fct_orders --store-failures",
                    "dbt run --select fct_orders_dbt mart_scd_comparison",
                ],
                "customers": len(seed["customers"]),
                "orders": int(order_cnt),
                "order_items": len(seed["order_items"]),
                "realized_revenue": float(total_rev or 0.0),
                "snapshot_versions": snap_cnt,
            }
            _log_state(
                cur,
                run_day=0,
                sim_date=DEMO_H1_END,
                event_type="RESET_BASELINE",
                status="HEALTHY",
                summary=f"Day 0 real dbt baseline built: {order_cnt} orders, {snap_cnt} initial dbt snapshot rows.",
                details=details,
            )
        conn.commit()
    return {"run_day": 0, "status": "HEALTHY", **details}


def run_day5_normal_refresh(db_url: str | None = None) -> dict[str, Any]:
    """Day 5: Insert 25 clean orders -> real dbt run candidate -> real dbt test --store-failures -> publish Gold."""
    sim_date = date(2025, 7, 5)
    rng = random.Random(505)

    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM raw.raw_order_items WHERE order_id BETWEEN 5001 AND 5099")
            cur.execute("DELETE FROM raw.raw_orders WHERE order_id BETWEEN 5001 AND 5099")
            new_orders, new_items = [], []
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
        conn.commit()

    # Real dbt Write-Audit-Publish
    _invoke_dbt(["run", "--select", "stg_orders", "candidate_fct_orders"], db_url)
    all_passed, wap_checks = _invoke_dbt(["test", "--select", "candidate_fct_orders", "--store-failures"], db_url)
    if all_passed:
        _invoke_dbt(["run", "--select", "fct_orders_dbt", "mart_scd_comparison"], db_url)

    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            if all_passed:
                _sync_gold_explore_views(cur)
            cur.execute("SELECT COUNT(*), ROUND(SUM(realized_revenue)::numeric, 2) FROM dbt_dev_dbt_gold.fct_orders_dbt")
            total_orders, total_rev = cur.fetchone()
            details = {
                "dbt_commands": [
                    "dbt run --select stg_orders candidate_fct_orders",
                    "dbt test --select candidate_fct_orders --store-failures",
                    "dbt run --select fct_orders_dbt mart_scd_comparison" if all_passed else "(SKIPPED Gold publish — tests failed)",
                ],
                "new_orders_added": len(new_orders),
                "wap_checks": wap_checks,
                "published_to_gold": all_passed,
                "gold_order_count": int(total_orders),
                "gold_realized_revenue": float(total_rev or 0.0),
            }
            _log_state(
                cur,
                run_day=5,
                sim_date=sim_date,
                event_type="DAY5_NORMAL_REFRESH",
                status="HEALTHY" if all_passed else "DEGRADED_LKG",
                summary=(
                    f"Day 5 real dbt WAP PASSED ({len(wap_checks)}/{len(wap_checks)} tests): +{len(new_orders)} orders published to dbt_gold.fct_orders_dbt."
                    if all_passed
                    else "Day 5 dbt test FAILED (upstream still contains unresolved dirty rows from Day 15 — Gold preserved at LKG)."
                ),
                details=details,
            )
        conn.commit()
    return {"run_day": 5, "sim_date": str(sim_date), "status": "HEALTHY" if all_passed else "DEGRADED_LKG", **details}


def run_day10_scd_type2(db_url: str | None = None) -> dict[str, Any]:
    """Day 10: Update 15 customers in raw -> real `dbt snapshot` (SCD Type 2) -> rebuild mart_scd_comparison."""
    sim_date = date(2025, 7, 10)
    upgrade_ids = list(range(41, 56))

    with _connect(db_url) as conn:
        with conn.cursor() as cur:
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
            cur.execute("DELETE FROM raw.raw_order_items WHERE order_id BETWEEN 10001 AND 10099")
            cur.execute("DELETE FROM raw.raw_orders WHERE order_id BETWEEN 10001 AND 10099")
            rng = random.Random(1010)
            post_orders, post_items = [], []
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
        conn.commit()

    # Run REAL dbt snapshot + candidate WAP + Gold SCD comparison mart
    _invoke_dbt(["snapshot"], db_url)
    _invoke_dbt(["run", "--select", "dim_customers_dbt", "candidate_fct_orders"], db_url)
    all_passed, _ = _invoke_dbt(["test", "--select", "candidate_fct_orders", "--store-failures"], db_url)
    if all_passed:
        _invoke_dbt(["run", "--select", "fct_orders_dbt", "mart_scd_comparison"], db_url)
    else:
        _invoke_dbt(["run", "--select", "mart_scd_comparison"], db_url)

    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            _sync_gold_explore_views(cur)
            cur.execute("SELECT COUNT(*) FROM dbt_snapshots.snap_customers_dbt")
            total_snap_rows = int(cur.fetchone()[0])
            details = {
                "dbt_commands": [
                    "dbt snapshot",
                    "dbt run --select dim_customers_dbt candidate_fct_orders",
                    "dbt test --select candidate_fct_orders --store-failures",
                    "dbt run --select fct_orders_dbt mart_scd_comparison",
                ],
                "upgraded_customers": len(upgrade_ids),
                "total_snapshot_rows": total_snap_rows,
                "post_upgrade_orders_added": len(post_orders),
            }
            _log_state(
                cur,
                run_day=10,
                sim_date=sim_date,
                event_type="DAY10_SCD_TYPE2_SNAPSHOT",
                status="HEALTHY",
                summary=f"Day 10 real `dbt snapshot` executed: 15 Standard customers upgraded to VIP ({total_snap_rows} total SCD2 rows in dbt_snapshots.snap_customers_dbt).",
                details=details,
            )
        conn.commit()
    return {"run_day": 10, "sim_date": str(sim_date), "status": "HEALTHY", **details}


def run_day15_dirty_data_wap(db_url: str | None = None) -> dict[str, Any]:
    """Day 15: Inject dirty rows -> real `dbt test --store-failures` FAILS -> Gold preserved + audit tables populated."""
    sim_date = date(2025, 7, 15)

    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), ROUND(SUM(realized_revenue)::numeric, 2) FROM dbt_dev_dbt_gold.fct_orders_dbt")
            lkg_count, lkg_rev = cur.fetchone()

            cur.execute("DELETE FROM raw.raw_orders WHERE order_id IN (1001, 1002) AND order_date = %s", (sim_date,))
            cur.execute("DELETE FROM raw.raw_orders WHERE order_id BETWEEN 15001 AND 15099 OR order_id IS NULL")

            dirty_rows = [
                (1001, 5, sim_date, "Completed", 420.00, 120.00),     # Duplicate PK
                (1002, 8, sim_date, "Completed", 180.00, 60.00),      # Duplicate PK
                (15001, 12, sim_date, "Completed", -350.00, 50.00),   # Negative order_total
                (15002, 19, sim_date, "Completed", -95.00, 20.00),    # Negative order_total
                (15003, None, sim_date, "Completed", 210.00, 70.00),  # NULL customer_id
            ]
            execute_values(cur, "INSERT INTO raw.raw_orders VALUES %s", dirty_rows)
        conn.commit()

    # Real dbt WAP: build candidate -> test with --store-failures -> DO NOT publish Gold!
    _invoke_dbt(["run", "--select", "stg_orders", "candidate_fct_orders"], db_url)
    all_passed, wap_checks = _invoke_dbt(["test", "--select", "candidate_fct_orders", "--store-failures"], db_url)
    failed_checks = [c for c in wap_checks if not c["passed"]]

    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            details = {
                "dbt_commands": [
                    "dbt run --select stg_orders candidate_fct_orders",
                    "dbt test --select candidate_fct_orders --store-failures  # FAILED -> rows stored in dbt_dev_dbt_test__audit.*",
                    "# SKIPPED: dbt run --select fct_orders_dbt  (Production Gold preserved at Last-Known-Good)",
                ],
                "injected_dirty_rows": len(dirty_rows),
                "wap_checks": wap_checks,
                "failed_check_count": len(failed_checks),
                "published_to_gold": all_passed,
                "lkg_preserved": not all_passed,
                "lkg_order_count": int(lkg_count),
                "lkg_realized_revenue": float(lkg_rev or 0.0),
                "staleness_hours": 14,
                "max_staleness_hours": 120,
            }
            _log_state(
                cur,
                run_day=15,
                sim_date=sim_date,
                event_type="DAY15_DIRTY_DATA_WAP_BLOCK",
                status="DEGRADED_LKG",
                summary=(
                    f"Day 15 real `dbt test --store-failures` FAILED ({len(failed_checks)} failed dbt tests). "
                    f"Production `dbt_gold.fct_orders_dbt` preserved at Last-Known-Good ({lkg_count} rows); offending rows auto-stored in `dbt_dev_dbt_test__audit`."
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
            cur.execute(
                """
                DROP TABLE IF EXISTS ci_pr.fct_orders_pr;
                CREATE TABLE ci_pr.fct_orders_pr AS
                SELECT
                    order_id, customer_id, order_date, order_month, status, is_completed,
                    order_total,
                    order_total AS realized_revenue,  -- DRIFTED: includes Pending & Returned!
                    cost_total,
                    (order_total - cost_total) AS gross_profit
                FROM dbt_dev_dbt_gold.fct_orders_dbt;
                """
            )
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'dbt_dev_dbt_gold' AND table_name = 'fct_orders_dbt' ORDER BY ordinal_position"
            )
            prod_cols = cur.fetchall()
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'ci_pr' AND table_name = 'fct_orders_pr' ORDER BY ordinal_position"
            )
            pr_cols = cur.fetchall()
            schema_match = prod_cols == pr_cols

            cur.execute(
                "SELECT COUNT(*), ROUND(SUM(realized_revenue)::numeric, 2), ROUND(SUM(gross_profit)::numeric, 2) "
                "FROM dbt_dev_dbt_gold.fct_orders_dbt"
            )
            prod_rows, prod_rev, prod_gp = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*), ROUND(SUM(realized_revenue)::numeric, 2), ROUND(SUM(gross_profit)::numeric, 2) "
                "FROM ci_pr.fct_orders_pr"
            )
            _pr_rows, pr_rev, pr_gp = cur.fetchone()

            prod_rev_f = float(prod_rev or 0.0)
            pr_rev_f = float(pr_rev or 0.0)
            delta_rev = round(pr_rev_f - prod_rev_f, 2)
            delta_pct = round((delta_rev / prod_rev_f * 100.0), 2) if prod_rev_f > 0 else 0.0
            drift_blocked = abs(delta_pct) > 5.0

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
                    "status": "PASS" if abs(delta_rev) < 0.01 else "FAIL",
                    "detail": f"Unexplained variance of ${delta_rev:,.2f} caused by including Pending/Returned orders.",
                },
            ]

            details = {
                "dbt_commands": [
                    "dbt build --select state:modified+ --defer --target ci_pr",
                    "dbt run-operation audit_helper.compare_relation_results  # BLOCKED: +25% KPI drift",
                ],
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
            _log_state(
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


def _fetch_dbt_audit_failures(cur) -> list[dict[str, Any]]:
    """Dynamically read real failed rows stored by `dbt test --store-failures` in `dbt_dev_dbt_test__audit`."""
    cur.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'dbt_dev_dbt_test__audit'
        ORDER BY table_name
        """
    )
    audit_tables = [r["table_name"] for r in cur.fetchall()]
    all_failures: list[dict[str, Any]] = []
    for tbl in audit_tables:
        cur.execute(f'SELECT * FROM dbt_dev_dbt_test__audit."{tbl}" LIMIT 25')
        rows = cur.fetchall()
        for row in rows:
            row_dict = dict(row)
            all_failures.append(
                {
                    "dbt_audit_table": tbl,
                    "failed_record_details": json.dumps(row_dict, default=str),
                }
            )
    return all_failures


def fetch_demo_state(db_url: str | None = None) -> dict[str, Any]:
    """Read live state directly from dbt-materialized Neon tables (zero hardcoded quarantine)."""
    with _connect(db_url) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'dbt_dev_dbt_gold' AND table_name = 'mart_scd_comparison'
                ) AS initialized
                """
            )
            if not cur.fetchone()["initialized"]:
                return {"initialized": False}

            cur.execute(
                "SELECT run_day, sim_date, event_type, status, summary, details_json, executed_at "
                "FROM governance.pipeline_runs ORDER BY run_id DESC LIMIT 1"
            )
            latest = dict(cur.fetchone()) if cur.rowcount else None

            cur.execute(
                "SELECT COUNT(*) AS cnt, COALESCE(ROUND(SUM(realized_revenue)::numeric, 2), 0) AS rev "
                "FROM dbt_dev_dbt_gold.fct_orders_dbt"
            )
            gold_stats = dict(cur.fetchone())

            cur.execute(
                "SELECT COUNT(*) AS total_versions, COUNT(DISTINCT customer_id) AS unique_customers "
                "FROM dbt_snapshots.snap_customers_dbt"
            )
            snap_stats = dict(cur.fetchone())

            # Real dbt Gold model: mart_scd_comparison
            cur.execute("SELECT * FROM dbt_dev_dbt_gold.mart_scd_comparison ORDER BY customer_tier")
            scd_comparison = [dict(r) for r in cur.fetchall()]

            # Real dbt snapshot table: dbt_snapshots.snap_customers_dbt
            cur.execute(
                """
                SELECT customer_id, customer_name, customer_tier, region,
                       dbt_valid_from, dbt_valid_to, (dbt_valid_to IS NULL) AS is_current
                FROM dbt_snapshots.snap_customers_dbt
                WHERE customer_id IN (
                    SELECT customer_id FROM dbt_snapshots.snap_customers_dbt GROUP BY customer_id HAVING COUNT(*) > 1
                )
                ORDER BY customer_id, dbt_valid_from
                LIMIT 20
                """
            )
            scd_changed_rows = [dict(r) for r in cur.fetchall()]

            # Real dbt --store-failures audit tables (zero hardcoded quarantine!)
            dbt_audit_failures = _fetch_dbt_audit_failures(cur)

    return {
        "initialized": True,
        "latest_run": latest,
        "gold_order_count": int(gold_stats["cnt"]),
        "gold_realized_revenue": float(gold_stats["rev"]),
        "snapshot_versions": int(snap_stats["total_versions"]),
        "unique_customers": int(snap_stats["unique_customers"]),
        "quarantine_count": len(dbt_audit_failures),
        "scd_comparison": scd_comparison,
        "scd_changed_rows": scd_changed_rows,
        "quarantine_rows": dbt_audit_failures,
    }


def execute_neon_readonly(sql: str, *, max_rows: int = 100, db_url: str | None = None) -> pd.DataFrame:
    """Execute a validated read-only SELECT query against Neon gold/snapshots schemas."""
    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL search_path TO gold, dbt_dev_dbt_gold, dbt_snapshots, public")
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
            description="SCD Type 2 customer dimension built via dbt snapshot (grain: dbt_scd_id).",
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
