"""Run dbt deps + dbt build + Elementary edr report against Neon PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "examples" / "jaffle_dbt"
OUT_HTML = ROOT / "examples" / "demo_artifacts" / "elementary_report.html"


def _build_env() -> dict[str, str]:
    load_dotenv(ROOT / ".env")
    db_url = os.environ["DATABASE_URL"]
    parsed = urlparse(db_url)
    env = os.environ.copy()
    env["NEON_HOST"] = parsed.hostname or ""
    env["NEON_USER"] = parsed.username or ""
    env["NEON_PASSWORD"] = parsed.password or ""
    env["NEON_DB"] = (parsed.path or "/neondb").lstrip("/")
    env["DBT_PROFILES_DIR"] = str(DBT_DIR)
    return env


def _run(cmd: list[str], env: dict[str, str]) -> None:
    print(f"\n>>> Running: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=str(DBT_DIR), env=env, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed (exit={res.returncode}): {' '.join(cmd)}")


def main() -> None:
    env = _build_env()
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    py = sys.executable

    _run([py, "-m", "dbt.cli.main", "deps", "--profiles-dir", str(DBT_DIR)], env)
    _run([py, "-m", "dbt.cli.main", "run", "--select", "elementary", "--profiles-dir", str(DBT_DIR)], env)
    _run([py, "-m", "dbt.cli.main", "build", "--profiles-dir", str(DBT_DIR)], env)
    _run(
        [
            "edr",
            "report",
            "--profiles-dir",
            str(DBT_DIR),
            "--project-dir",
            str(DBT_DIR),
            "--file-path",
            str(OUT_HTML),
            "--open-browser",
            "false",
        ],
        env,
    )
    print(f"\nSUCCESS: Generated Elementary report at {OUT_HTML} ({OUT_HTML.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
