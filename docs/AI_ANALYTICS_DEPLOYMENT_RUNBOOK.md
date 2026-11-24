# AI Analytics Workbench — Deployment & Rollback Runbook

## 1. Architecture & Trust Boundaries

```text
ERP React (/ai/analytics, SYSTEM_ADMIN_ROLES)
  └──► ERP Express Proxy (/api/analytics/*, session auth + server tenant_id + X-Request-Id)
         └──► Python AE Control Plane (FastAPI, private network, X-Internal-Token)
                ├── dbt Metadata Catalog (DBT_TARGET_DIR: manifest/catalog/run_results/semantic_manifest)
                ├── Read-Only Sandbox (DuckDB read_only=True; never direct writes to prod Supabase)
                └── Optional Lightdash Staging (docker-compose.lightdash.yml or LIGHTDASH_* creds)
```

## 2. Required Environment Variables

### Python AE Control Plane (`lakehouse-cockpit-core`)
| Variable | Required | Description |
| :--- | :---: | :--- |
| `AE_INTERNAL_TOKEN` | Yes | Shared secret for `X-Internal-Token` (fail-closed if unset) |
| `DBT_TARGET_DIR` | Yes | Path to compiled dbt `target/` directory |
| `AE_DUCKDB_PATH` | Optional | Path to read-only local DuckDB file (`read_only=True`) |
| `GEMINI_API_KEY` | Optional | Required for LLM exploratory drafting & bounded SQL repair (Fail-Loud if missing) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | Optional | Enables Langfuse masked tracing (reports `langfuse_enabled=false` when unset) |
| `LIGHTDASH_URL` / `LIGHTDASH_API_KEY` / `LIGHTDASH_PROJECT_UUID` | Optional | Enables live Lightdash status (honestly reports `STAGING_LOCAL_ONLY` when unset) |
| `GITHUB_TOKEN` / `AE_GITHUB_REPO` | Optional | Enables remote Draft PR mode (honestly reports `DRY_RUN_BUNDLE` when unset) |

### ERP Server (`artifacts/api-server`)
| Variable | Required | Description |
| :--- | :---: | :--- |
| `AE_CONTROL_PLANE_URL` | Yes | Internal URL of FastAPI service (e.g. `http://127.0.0.1:8100`) |
| `AE_INTERNAL_TOKEN` | Yes | Must match the Python control plane token |

## 3. Local / Staging Launch

```powershell
# 1. Start Python Control Plane
$env:DBT_TARGET_DIR = "<ERP_REPO>\dataops\dbt\target"
$env:AE_DUCKDB_PATH = "<ERP_REPO>\dataops\dbt\erp_analytics.duckdb"
$env:AE_INTERNAL_TOKEN = "replace-with-strong-secret"
uvicorn api.app:app --host 127.0.0.1 --port 8100

# 2. Optional: Start isolated local Staging PostgreSQL + Lightdash
docker compose -f docker-compose.lightdash.yml up -d
```

## 4. Verification & Smoke Checks

```powershell
# Health check
curl http://127.0.0.1:8100/health

# Full Lakehouse test suite including conditional live ERP read-only smoke test
$env:ERP_REPO_DIR = "<ERP_REPO>"
pytest -v tests/test_day4_acceptance.py
```

## 5. Zero-Downtime Rollback Procedure

1. **Immediate Disable (Fail-Closed)**: Remove `AE_INTERNAL_TOKEN` from ERP server environment (all `/api/analytics/*` calls immediately fail closed with HTTP `503`, while existing ERP AI Assistant and operational reports remain 100% unaffected).
2. **Code Revert**: Revert the ERP `feat/ai-analytics-workbench` merge commit and redeploy ERP.
3. **Container Stop**: Stop the `Dockerfile.api` container; no production database schema migrations were performed by the workbench.
