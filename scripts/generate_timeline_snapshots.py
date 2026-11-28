"""Generate pre-built real-dbt timeline snapshots (Day 0, 5, 10, 15, 20) from live Neon PostgreSQL.

Runs the authentic dbt Core lifecycle (reset_demo -> Day 5 -> Day 10 -> Day 15 -> Day 20)
against Neon, captures `fetch_demo_state(db_url)` after each milestone into
`examples/demo_artifacts/timeline_snapshots.json`, and resets Neon to clean Day 10
so Tab 2 Explore & Lightdash start with clean Gold data + SCD Type 2 history.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from services.neon_demo_engine import (
    fetch_demo_state,
    reset_demo,
    run_day5_normal_refresh,
    run_day10_scd_type2,
    run_day15_dirty_data_wap,
    run_day20_semantic_drift_ci,
)

OUT_PATH = ROOT / "examples" / "demo_artifacts" / "timeline_snapshots.json"


def main() -> None:
    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL") or ""
    if not db_url:
        raise RuntimeError("DATABASE_URL is required in .env to generate real-dbt snapshots.")

    snapshots: dict[str, dict] = {}
    steps = [
        ("0", "Day 0 Reset Baseline", reset_demo),
        ("5", "Day 5 Normal Refresh", run_day5_normal_refresh),
        ("10", "Day 10 SCD Type 2", run_day10_scd_type2),
        ("15", "Day 15 Dirty Data WAP", run_day15_dirty_data_wap),
        ("20", "Day 20 Semantic Drift CI", run_day20_semantic_drift_ci),
    ]

    for day_key, label, fn in steps:
        print(f"[snapshot] Running real dbt lifecycle: {label}...", flush=True)
        fn(db_url)
        state = fetch_demo_state(db_url)
        state["snapshot_mode"] = "PREBUILT_REAL_DBT_SNAPSHOT"
        state["snapshot_day"] = int(day_key)
        snapshots[day_key] = state
        print(
            f"  -> captured Day {day_key}: gold_orders={state['gold_order_count']}, "
            f"quarantine={state['quarantine_count']}, scd_rows={len(state['scd_comparison'])}",
            flush=True,
        )

    # Restore Neon to clean Day 10 (560 clean orders + SCD2 VIP upgrade history) for live Tab 2 / Lightdash
    print("[snapshot] Restoring live Neon baseline to clean Day 10...", flush=True)
    reset_demo(db_url)
    run_day5_normal_refresh(db_url)
    run_day10_scd_type2(db_url)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "real_dbt_core_neon_postgresql",
        "default_day": "10",
        "days": snapshots,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[snapshot] Wrote {OUT_PATH} ({OUT_PATH.stat().st_size:,} bytes).", flush=True)


if __name__ == "__main__":
    main()
