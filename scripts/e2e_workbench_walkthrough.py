"""End-to-End Automated Walkthrough & Audit Report Generator for examples/workbench_demo.py.

Exercises every major user workflow in exact UI order (Tab 1 -> Tab 6) against live Neon PostgreSQL,
live Gemini (`gemini-3.7-flash`), live Lightdash Cloud API, and GitHub Pages, writing a complete
structured audit report to `.scratch/e2e_audit_report.md` (gitignored).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import urllib.request

from dotenv import load_dotenv
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from integrations.lightdash_adapter import LightdashAdapter
from services.consultant_service import (
    ConsultantQueryOutcome,
    EvidenceQuerySummary,
    answer_consultant_turn,
    draft_candidate_query,
    propose_consultant_turn,
)
from services.dbt_asset_builder import build_dbt_and_lightdash_bundle
from services.duckdb_executor import generate_gemini_structured, generate_gemini_text
from services.intent_clarifier import ClarificationTurn
from services.neon_demo_engine import (
    JAFFLE_ONE_TO_MANY_JOINS,
    build_jaffle_catalog,
    execute_neon_readonly,
)
from services.query_spec import OutputSpec, QuerySpec
from services.release_orchestrator import orchestrate_draft_pr
from services.semantic_gap_detector import classify_sql_semantic_gap
from services.source_ingestion import propose_source_ingestion
from services.sql_repair import repair_sql_bounded
from services.sql_validator import validate_and_reconcile_sql
from services.warehouse_adapters import ExecutionResult, WarehouseAdapter, WarehouseAdapterError

MODEL = "gemini-3.7-flash"
TEST_QUESTION = (
    "Across our customer tiers, which segment drives the most revenue, "
    "and does it come from bigger baskets or more frequent orders?"
)
REPORT_PATH = ROOT / ".scratch" / "e2e_audit_report.md"
SNAP_PATH = ROOT / "examples" / "demo_artifacts" / "timeline_snapshots.json"
GH_PAGES_URLS = [
    "https://shin7965977.github.io/lakehouse-cockpit-core/elementary_report.html",
    "https://shin7965977.github.io/lakehouse-cockpit-core/dbt_docs_static.html",
]


class _NeonAdapter(WarehouseAdapter):
    dialect = "postgres"

    def __init__(self, db_url: str):
        self.db_url = db_url

    def explain_readonly(self, sql: str) -> tuple[bool, str]:
        try:
            execute_neon_readonly(f"EXPLAIN {sql}", max_rows=20, db_url=self.db_url)
            return True, "EXPLAIN OK"
        except Exception as exc:
            return False, str(exc)

    def execute_readonly(self, sql: str, max_rows: int = 50) -> ExecutionResult:
        try:
            df = execute_neon_readonly(sql, max_rows=max_rows, db_url=self.db_url)
            return ExecutionResult(
                columns=list(df.columns),
                rows=df.to_dict(orient="records"),
                row_count=len(df),
            )
        except Exception as exc:
            raise WarehouseAdapterError("EXECUTION_FAILED", str(exc)) from exc


def main() -> None:
    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL") or ""
    api_key = os.getenv("GEMINI_API_KEY") or ""
    if not db_url or not api_key:
        raise RuntimeError("DATABASE_URL and GEMINI_API_KEY must be set in .env")

    lines: list[str] = [
        "# E2E Workbench Automated Audit Report",
        f"- Generated UTC: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Model: `{MODEL}`",
        f"- Test Question: `{TEST_QUESTION}`",
        "",
    ]
    findings: list[str] = []

    def llm_structured(prompt: str, schema_cls: type[Any]) -> Any:
        return generate_gemini_structured(
            prompt=prompt,
            response_model=schema_cls,
            api_key=api_key,
            model=MODEL,
        )

    def llm_text(prompt: str) -> str:
        return generate_gemini_text(prompt=prompt, api_key=api_key, model=MODEL)

    # ── TAB 1: Governance Timeline Snapshots ───────────────────────────────────
    t0 = time.perf_counter()
    lines.append("## Tab 1: Governance Timeline (Pre-built Real-dbt Snapshots)")
    if not SNAP_PATH.exists():
        findings.append("Tab 1: timeline_snapshots.json missing!")
        lines.append("- ERROR: timeline_snapshots.json missing")
    else:
        snaps = json.loads(SNAP_PATH.read_text(encoding="utf-8"))
        days = snaps.get("days", {})
        for d in ("0", "5", "10", "15", "20"):
            st_d = days.get(d, {})
            latest = st_d.get("latest_run") or {}
            lines.append(
                f"- **Day {d}**: status=`{latest.get('status')}`, gold_orders=`{st_d.get('gold_order_count')}`, "
                f"scd_versions=`{st_d.get('snapshot_versions')}`, quarantine=`{st_d.get('quarantine_count')}`"
            )
    lines.append(f"- Elapsed: `{time.perf_counter() - t0:.2f}s`\n")

    # ── TAB 2: Explore & Security Gate ─────────────────────────────────────────
    catalog = build_jaffle_catalog()
    allowed_rels = {r.name for r in catalog.relations}

    lines.append("## Tab 2: Explore & Security Gate")
    t_prop = time.perf_counter()
    proposal = propose_consultant_turn(
        message=TEST_QUESTION,
        catalog=catalog,
        history=[],
        dialect="postgres",
        api_key=api_key,
        structured_llm_fn=llm_structured,
    )
    lines.append(f"### 2.1 Consult Proposal (`{time.perf_counter() - t_prop:.2f}s`)")
    lines.append(f"- `needs_data`: `{proposal.needs_data}` | `proposed_queries`: `{len(proposal.proposed_queries)}`")
    if len(proposal.proposed_queries) != 1:
        findings.append(f"Tab 2.1: Expected 1 evidence query for single-grain question, got {len(proposal.proposed_queries)}.")

    outcomes: list[ConsultantQueryOutcome] = []
    evidence_summaries: list[EvidenceQuerySummary] = []
    for pq in proposal.proposed_queries:
        v_ev = validate_and_reconcile_sql(
            pq.compiled_sql,
            pq.query_spec,
            allowed_relations=allowed_rels,
            server_tenant_id=None,
            one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
        )
        lines.append(f"- **Evidence Query**: `{pq.title}` (AST: `{v_ev.status}`, codes=`{v_ev.reason_codes}`)")
        lines.append(f"```sql\n{pq.compiled_sql}\n```")
        if v_ev.status == "BLOCKED":
            findings.append(f"Tab 2.1: Evidence query '{pq.title}' BLOCKED: {v_ev.reason_codes}")
            outcomes.append(ConsultantQueryOutcome(title=pq.title, sql=pq.compiled_sql, status="FAILED", error="; ".join(v_ev.messages)))
        else:
            df_ev = execute_neon_readonly(v_ev.executable_sql or pq.compiled_sql, max_rows=20, db_url=db_url)
            lines.append(f"- Rows returned: `{len(df_ev)}` | Columns: `{list(df_ev.columns)}`")
            outcomes.append(
                ConsultantQueryOutcome(
                    title=pq.title,
                    sql=v_ev.executable_sql or pq.compiled_sql,
                    spec_hash=pq.spec_hash,
                    status="SUCCEEDED",
                    row_count=len(df_ev),
                    columns=list(df_ev.columns),
                    rows=json.loads(df_ev.head(10).to_json(orient="records", date_format="iso")),
                )
            )
            evidence_summaries.append(
                EvidenceQuerySummary(title=pq.title, sql=v_ev.executable_sql or pq.compiled_sql, row_count=len(df_ev))
            )

    t_ans = time.perf_counter()
    ans = answer_consultant_turn(
        message=TEST_QUESTION,
        query_outcomes=outcomes,
        history=[],
        api_key=api_key,
        structured_llm_fn=llm_structured,
    )
    lines.append(f"### 2.2 Consult Answer (`{time.perf_counter() - t_ans:.2f}s`)")
    lines.append(f"> {ans.reply[:400]}...\n")

    # 2.3 Draft Candidate Governed Query
    t_cand = time.perf_counter()
    history = [
        ClarificationTurn(role="user", content=TEST_QUESTION),
        ClarificationTurn(role="assistant", content=ans.reply),
    ]
    cand = draft_candidate_query(
        history=history,
        catalog=catalog,
        dialect="postgres",
        evidence_queries=evidence_summaries,
        api_key=api_key,
        structured_llm_fn=llm_structured,
    )
    lines.append(f"### 2.3 Candidate Governed Query (`{time.perf_counter() - t_cand:.2f}s`)")
    lines.append(f"- Title: `{cand.title}` | Grain: `{cand.group_by}` | Gap: `{cand.gap.classification.value if cand.gap else None}`")
    lines.append(f"- AST Status: `{cand.validation.status if cand.validation else None}` | Codes: `{cand.validation.reason_codes if cand.validation else []}`")
    lines.append(f"- Explanation: {cand.plain_language_explanation}")
    lines.append(f"- Assumptions: `{cand.assumptions}`")
    lines.append(f"```sql\n{cand.sql}\n```")

    # 2.4 Security Gate: Validate + Execute (with Auto-Repair fallback if Neon errors)
    editor_sql = cand.sql
    spec = cand.query_spec or QuerySpec(
        spec_id="qs_e2e",
        question=TEST_QUESTION,
        dialect="postgres",
        raw_sql_override=editor_sql,
        output=OutputSpec(max_rows=50, certification="VALIDATED_EXPLORATORY"),
    )
    val = validate_and_reconcile_sql(
        editor_sql,
        spec,
        allowed_relations=allowed_rels,
        server_tenant_id=None,
        one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
    )
    gap = classify_sql_semantic_gap(editor_sql, catalog, question=spec.question, dialect="postgres")
    lines.append("### 2.4 Security Gate & Neon Execution")
    lines.append(f"- Validate status: `{val.status}` | codes: `{val.reason_codes}` | gap: `{gap.classification.value}` (`{gap.reason}`)")

    preview_df: pd.DataFrame | None = None
    try:
        preview_df = execute_neon_readonly(val.executable_sql or editor_sql, max_rows=50, db_url=db_url)
        lines.append(f"- Direct Neon execution: **SUCCEEDED** (`{len(preview_df)}` rows, cols=`{list(preview_df.columns)}`)")
    except Exception as exc:
        findings.append(f"Tab 2.4: Candidate SQL failed direct Neon execution (needed Auto-Repair): {exc}")
        lines.append(f"- Direct Neon execution FAILED: `{exc}` -> triggering **Auto-Repair (Bounded)**...")
        t_rep = time.perf_counter()
        rep = repair_sql_bounded(
            broken_sql=editor_sql,
            error_message=str(exc),
            base_spec=val.reconciled_spec or spec,
            allowed_relations=allowed_rels,
            adapter=_NeonAdapter(db_url),
            llm_callable=llm_text,
            server_tenant_id=None,
            one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
            max_attempts=2,
        )
        lines.append(
            f"- Auto-Repair (`{time.perf_counter() - t_rep:.2f}s`): repaired=`{rep.repaired}`, "
            f"attempts=`{rep.attempts}`, error_class=`{rep.error_class}`"
        )
        if rep.sql_diff:
            lines.append(f"```diff\n{rep.sql_diff}\n```")
        if rep.repaired:
            editor_sql = rep.repaired_sql
            spec = (rep.reconciled_spec or spec).with_updates(raw_sql_override=editor_sql)
            preview_df = execute_neon_readonly(editor_sql, max_rows=50, db_url=db_url)
            lines.append(f"- Post-Repair Neon execution: **SUCCEEDED** (`{len(preview_df)}` rows)")
        else:
            findings.append(f"Tab 2.4: Auto-Repair FAILED: {rep.failure_codes} {rep.messages}")

    # 2.5 1:N Fanout Guardrail Test
    fanout_sql = (
        "SELECT o.customer_id, SUM(o.order_total) AS inflated_revenue "
        "FROM fct_orders o JOIN fct_order_items oi ON o.order_id = oi.order_id "
        "GROUP BY o.customer_id LIMIT 20"
    )
    f_val = validate_and_reconcile_sql(
        fanout_sql,
        QuerySpec(spec_id="qs_fanout", question="Fanout test", dialect="postgres", raw_sql_override=fanout_sql),
        allowed_relations=allowed_rels,
        server_tenant_id=None,
        one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
    )
    lines.append(f"- 1:N Fanout Guardrail: status=`{f_val.status}`, codes=`{f_val.reason_codes}`")
    if f_val.status != "BLOCKED" or "JOIN_FANOUT_RISK" not in f_val.reason_codes:
        findings.append(f"Tab 2.5: 1:N Fanout was NOT blocked properly: {f_val.status} {f_val.reason_codes}")

    # ── TAB 4: Lightdash BI Status ─────────────────────────────────────────────
    lines.append("\n## Tab 4: Lightdash BI Status")
    t_ld = time.perf_counter()
    ld_adapter = LightdashAdapter(
        lightdash_url=os.getenv("LIGHTDASH_URL", ""),
        api_key=os.getenv("LIGHTDASH_API_KEY", ""),
        project_uuid=os.getenv("LIGHTDASH_PROJECT_UUID", ""),
    )
    ld_status = ld_adapter.check_status()
    lines.append(f"- Status (`{time.perf_counter() - t_ld:.2f}s`): `{json.dumps(ld_status, ensure_ascii=False)}`")

    # ── TAB 3: Build (dbt + Contract + Lightdash YAML) ─────────────────────────
    lines.append("\n## Tab 3: Build (dbt + Contract + Lightdash YAML)")
    if preview_df is not None and len(preview_df) > 0:
        from decimal import Decimal

        inferred_types: dict[str, str] = {}
        for col in preview_df.columns:
            dtype_str = str(preview_df[col].dtype).lower()
            if "bool" in dtype_str:
                inferred_types[col] = "boolean"
            elif "int" in dtype_str:
                inferred_types[col] = "integer"
            elif "float" in dtype_str or "double" in dtype_str:
                inferred_types[col] = "double precision"
            else:
                non_null = preview_df[col].dropna()
                sample = non_null.iloc[0] if len(non_null) > 0 else None
                inferred_types[col] = "numeric" if isinstance(sample, Decimal) else "varchar"
        grain_cols = list(spec.group_by) if spec.group_by else [str(preview_df.columns[0])]
        bundle = build_dbt_and_lightdash_bundle(
            query_spec=spec,
            model_name="mart_customer_value_promoted",
            grain=grain_cols,
            column_types=inferred_types,
            business_owner="analytics-engineering@jaffle.shop",
            tenant_policy="single_company_public_demo",
            lightdash_status=ld_status["lightdash_status"],
        )
        lines.append("### 3.1 dbt Model (.sql)")
        lines.append(f"```sql\n{bundle.dbt_sql.strip()}\n```")
        lines.append("### 3.2 Enforced Data Contract (schema.yml)")
        lines.append(f"```yaml\n{bundle.schema_yaml.strip()}\n```")
        lines.append("### 3.3 BI Asset Manifest")
        lines.append(f"```json\n{json.dumps(bundle.bi_asset_manifest.model_dump(), indent=2)}\n```")
        lines.append("### 3.4 Generated Lightdash Semantic YAML")
        lines.append(f"```yaml\n{bundle.lightdash_yaml.strip()}\n```")

        if "{{ ref(" not in bundle.dbt_sql:
            findings.append("Tab 3: dbt_sql is missing {{ ref(...) }}!")
        if not bundle.bi_asset_manifest.upstream_models:
            findings.append("Tab 3: BI Asset Manifest upstream_models is empty!")
        if bundle.bi_asset_manifest.lightdash_status != ld_status["lightdash_status"]:
            findings.append(
                f"Tab 3 vs Tab 4/5 mismatch: BIAssetManifest.lightdash_status='{bundle.bi_asset_manifest.lightdash_status}' "
                f"!= live Lightdash status '{ld_status['lightdash_status']}'"
            )

        # ── TAB 5: Review & Release ────────────────────────────────────────────
        lines.append("\n## Tab 5: Review & Release")
        m_name = bundle.contract.model_name
        pr_res = orchestrate_draft_pr(
            model_name=m_name,
            question=bundle.contract.query_spec.question or m_name,
            files={
                f"models/gold/{m_name}.sql": bundle.dbt_sql,
                f"models/gold/{m_name}_schema.yml": bundle.schema_yaml,
                f"models/gold/{m_name}_lightdash.yml": bundle.lightdash_yaml,
            },
            validation_summary=f"- PASS isolated_materialization ({len(preview_df)} rows)",
            lightdash_status=ld_status["lightdash_status"],
            github_token="",
            github_repo="",
        )
        lines.append(f"- Draft PR mode: `{pr_res.pr_mode}` | branch: `{pr_res.branch_name}`")
        lines.append(f"```markdown\n{pr_res.body_markdown.strip()}\n```")

        p_ok = propose_source_ingestion(
            source_type="approved_local_csv",
            source_locator="data/marketing_campaigns_2025.csv",
            target_bronze_table="bronze_marketing_campaigns",
        )
        p_blk = propose_source_ingestion(
            source_type="unapproved_scraper",
            source_locator="https://evil.example/scrape",
            target_bronze_table="bronze_evil",
        )
        lines.append(f"- dlt `approved_local_csv`: status=`{p_ok.status}`, scaffold_len=`{len(p_ok.dlt_scaffold_py)}`")
        lines.append(f"- dlt `unapproved_scraper`: status=`{p_blk.status}`, codes=`{p_blk.reason_codes}`")
    else:
        findings.append("Tab 3 & 5 skipped because Tab 2 Neon execution produced no preview_df.")

    # ── TAB 6: Observability & Live Tables ─────────────────────────────────────
    lines.append("\n## Tab 6: Observability (GitHub Pages + Live Neon Tables)")
    for url in GH_PAGES_URLS:
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as resp:
                lines.append(f"- `{url}` -> HTTP `{resp.status}`")
        except Exception as exc:
            findings.append(f"Tab 6: GitHub Pages URL failed ({url}): {exc}")
            lines.append(f"- `{url}` -> ERROR `{exc}`")

    tbl_df = execute_neon_readonly(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema IN ('dbt_dev_dbt_gold', 'dbt_snapshots', 'dbt_dev_dbt_test__audit', 'gold') "
        "ORDER BY table_schema, table_name",
        max_rows=100,
        db_url=db_url,
    )
    lines.append(f"- Live Neon dbt/gold tables found: `{len(tbl_df)}`")

    lines.append("\n## Automated Findings Summary")
    if findings:
        for f in findings:
            lines.append(f"- [ISSUE] {f}")
    else:
        lines.append("- ZERO automated issues detected.")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote audit report to {REPORT_PATH} ({len(findings)} finding(s)).")
    for f in findings:
        print("  [ISSUE]", f)


if __name__ == "__main__":
    main()
