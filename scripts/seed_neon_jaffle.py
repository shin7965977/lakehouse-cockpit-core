"""Seed & smoke-test the Neon PostgreSQL Jaffle Shop Governance Demo (Day 0 -> 5 -> 10 -> 15 -> 20)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.neon_demo_engine import (
    fetch_demo_state,
    reset_demo,
    run_day5_normal_refresh,
    run_day10_scd_type2,
    run_day15_dirty_data_wap,
    run_day20_semantic_drift_ci,
)


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Seed or smoke-test Neon Jaffle Demo")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run full Day 0 -> Day 5 -> Day 10 -> Day 15 -> Day 20 cycle and then reset to Day 0",
    )
    args = parser.parse_args()

    print("=== Day 0: Reset & Seed Baseline ===")
    r0 = reset_demo()
    print(json.dumps(r0, indent=2))

    if args.smoke_test:
        print("\n=== Day 5: Normal Refresh (Write-Audit-Publish) ===")
        print(json.dumps(run_day5_normal_refresh(), indent=2))

        print("\n=== Day 10: SCD Type 2 Customer Upgrade ===")
        print(json.dumps(run_day10_scd_type2(), indent=2))

        state10 = fetch_demo_state()
        print("SCD1 vs SCD2 H1 2025 comparison:", json.dumps(state10["scd_comparison"], indent=2))

        print("\n=== Day 15: Dirty Data Blocked -> LKG Preserved + Quarantine ===")
        print(json.dumps(run_day15_dirty_data_wap(), indent=2))

        print("\n=== Day 20: Semantic Drift Blocked in CI ===")
        print(json.dumps(run_day20_semantic_drift_ci(), indent=2))

        print("\n=== Final Reset back to clean Day 0 ===")
        print(json.dumps(reset_demo(), indent=2))


if __name__ == "__main__":
    main()
