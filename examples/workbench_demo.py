"""Standalone Public Streamlit Demo: AI Analytics Workbench & Data Governance Lab (Neon PostgreSQL).

Reuses the exact lakehouse-cockpit-core Control Plane services (consultant_service,
sql_validator, semantic_gap_detector, dbt_asset_builder) directly in-process against a
live Neon PostgreSQL warehouse (single-company Jaffle Shop model, no tenant_id).

Run locally:
    streamlit run examples/workbench_demo.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from services.consultant_service import (
    ConsultantMessage,
    consult_freeform,
    draft_candidate_query,
)
from services.dbt_asset_builder import build_dbt_and_lightdash_bundle
from services.duckdb_executor import generate_gemini_text
from services.neon_demo_engine import (
    JAFFLE_ONE_TO_MANY_JOINS,
    build_jaffle_catalog,
    execute_neon_readonly,
    fetch_demo_state,
    reset_demo,
    run_day5_normal_refresh,
    run_day10_scd_type2,
    run_day15_dirty_data_wap,
    run_day20_semantic_drift_ci,
)
from services.query_spec import DataSourceRef, OutputSpec, QuerySpec
from services.semantic_gap_detector import classify_sql_semantic_gap
from services.sql_validator import validate_and_reconcile_sql

MAX_LLM_CALLS_PER_SESSION = 10
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
ELEMENTARY_REPORT_PATH = ROOT / "examples" / "demo_artifacts" / "elementary_report.html"


def _resolve_secret(key: str) -> str:
    try:
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return (os.getenv(key) or "").strip()


def _init_session_state() -> None:
    defaults: dict[str, Any] = {
        "llm_calls": 0,
        "chat_messages": [],
        "candidate_bundle": None,
        "editor_sql": "",
        "active_spec": None,
        "last_validation": None,
        "last_preview_df": None,
        "last_gap": None,
        "build_bundle": None,
        "build_validation_checks": None,
        "last_timeline_result": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _make_llm_callable(api_key: str):
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is required for live AI consultation. "
            "Enter a Gemini API Key in the sidebar (or configure Streamlit Secrets). "
            "Deterministic Governance Labs (Day 5/10/15/20, AST Gate, Build, Elementary) work without an API key."
        )
    if st.session_state["llm_calls"] >= MAX_LLM_CALLS_PER_SESSION:
        raise RuntimeError(
            f"Demo rate limit reached ({MAX_LLM_CALLS_PER_SESSION} LLM calls per session). "
            "Refresh the page to start a new session."
        )

    def _call(prompt: str) -> str:
        st.session_state["llm_calls"] += 1
        return generate_gemini_text(
            prompt=prompt,
            api_key=api_key,
            model=DEFAULT_GEMINI_MODEL,
        )

    return _call


def _ensure_neon_initialized(db_url: str) -> dict[str, Any]:
    state = fetch_demo_state(db_url)
    if not state.get("initialized"):
        reset_demo(db_url)
        state = fetch_demo_state(db_url)
    return state


def _render_sidebar(db_url: str, state: dict[str, Any]) -> str:
    with st.sidebar:
        st.markdown("## 🏛️ AI Analytics Workbench")
        st.caption("Single-Company Jaffle Shop · Neon PostgreSQL · dbt + Elementary")

        default_key = _resolve_secret("GEMINI_API_KEY")
        api_key = st.text_input(
            "Gemini API Key (for Tab 2 AI Chat)",
            value=default_key,
            type="password",
            help="Optional for Labs (Tab 1, AST Gate, Tab 3, Tab 4). Required for live AI consultation in Tab 2.",
        )
        st.caption(
            f"Session LLM quota: **{st.session_state['llm_calls']} / {MAX_LLM_CALLS_PER_SESSION}** · Model: `{DEFAULT_GEMINI_MODEL}`"
        )

        st.divider()
        latest = state.get("latest_run") or {}
        status = latest.get("status", "HEALTHY")
        status_badge = {
            "HEALTHY": "🟢 HEALTHY",
            "DEGRADED_LKG": "🟡 DEGRADED (LKG Preserved)",
            "PR_BLOCKED": "🔴 CI PR BLOCKED",
        }.get(status, f"⚪ {status}")

        st.markdown(f"**Warehouse Status:** {status_badge}")
        c1, c2 = st.columns(2)
        c1.metric("Gold Orders", f"{state['gold_order_count']:,}")
        c2.metric("SCD2 Versions", f"{state['snapshot_versions']:,}")
        c3, c4 = st.columns(2)
        c3.metric("Realized Rev", f"${state['gold_realized_revenue']:,.0f}")
        c4.metric("Quarantined", f"{state['quarantine_count']}")

        st.divider()
        if st.button("🔄 Reset Demo Sandbox (Day 0)", use_container_width=True):
            with st.spinner("Resetting Neon schemas to clean Day 0 baseline..."):
                res = reset_demo(db_url)
                st.session_state["last_timeline_result"] = res
            st.success("Reset to Day 0 baseline!")
            st.rerun()

    return api_key.strip()


def _render_tab1_timeline(db_url: str, state: dict[str, Any]) -> None:
    st.subheader("Interactive Data Governance & SCD Type 2 Timeline (Live Neon PostgreSQL)")
    st.markdown(
        "Click through **Day 5 → Day 10 → Day 15 → Day 20** to execute real PostgreSQL transactions on Neon "
        "demonstrating **Write-Audit-Publish**, **SCD Type 2 Point-in-Time accuracy**, **Quarantine / Last-Known-Good**, "
        "and **CI Semantic Drift blocking**."
    )

    b0, b5, b10, b15, b20 = st.columns(5)
    if b0.button("🔄 Day 0\nReset Baseline", use_container_width=True):
        with st.spinner("Seeding Day 0 baseline in Neon..."):
            st.session_state["last_timeline_result"] = reset_demo(db_url)
        st.rerun()

    if b5.button("✅ Day 5\nNormal Refresh", use_container_width=True):
        with st.spinner("Running Day 5 Write-Audit-Publish on Neon..."):
            st.session_state["last_timeline_result"] = run_day5_normal_refresh(db_url)
        st.rerun()

    if b10.button("🧬 Day 10\nSCD Type 2", use_container_width=True):
        with st.spinner("Upgrading 15 customers & running SCD Type 2 Snapshot on Neon..."):
            st.session_state["last_timeline_result"] = run_day10_scd_type2(db_url)
        st.rerun()

    if b15.button("🛡️ Day 15\nDirty Data WAP", use_container_width=True):
        with st.spinner("Injecting bad records & running WAP Gate on Neon..."):
            st.session_state["last_timeline_result"] = run_day15_dirty_data_wap(db_url)
        st.rerun()

    if b20.button("🚨 Day 20\nSemantic Drift CI", use_container_width=True):
        with st.spinner("Simulating drifted PR & running 3-layer CI Gate on Neon..."):
            st.session_state["last_timeline_result"] = run_day20_semantic_drift_ci(db_url)
        st.rerun()

    latest = state.get("latest_run")
    if latest:
        st_status = latest["status"]
        if st_status == "HEALTHY":
            st.success(f"**Day {latest['run_day']} ({latest['sim_date']}) — {latest['event_type']}**: {latest['summary']}")
        elif st_status == "DEGRADED_LKG":
            st.warning(f"**Day {latest['run_day']} ({latest['sim_date']}) — {latest['event_type']}**: {latest['summary']}")
        else:
            st.error(f"**Day {latest['run_day']} ({latest['sim_date']}) — {latest['event_type']}**: {latest['summary']}")

        details = json.loads(latest["details_json"]) if isinstance(latest.get("details_json"), str) else {}

        # Day 5 / Day 15 WAP check details
        if "wap_checks" in details:
            st.markdown("#### Write-Audit-Publish (Candidate Validation Gates)")
            st.dataframe(pd.DataFrame(details["wap_checks"]), use_container_width=True, hide_index=True)

        # Day 20 CI Semantic Drift details
        if "ci_gates" in details:
            st.markdown("#### CI/CD PR Gate Results (`feat/update-revenue-filter`)")
            st.caption(f"Simulated PR change: {details.get('drifted_rule')}")
            st.dataframe(pd.DataFrame(details["ci_gates"]), use_container_width=True, hide_index=True)
            m1, m2, m3 = st.columns(3)
            m1.metric("Prod Realized Revenue", f"${details['prod_revenue']:,.2f}")
            m2.metric("PR Candidate Revenue", f"${details['pr_revenue']:,.2f}", delta=f"+{details['delta_pct']}%", delta_color="inverse")
            m3.metric("PR Merge Status", "🚫 BLOCKED")

    # SCD Type 1 vs SCD Type 2 Point-in-Time Comparison (always visible, lights up after Day 10!)
    st.divider()
    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("#### 🧬 SCD Type 1 vs. SCD Type 2 (H1 2025 Historical Revenue)")
        st.caption(
            "After clicking **Day 10**, 15 Standard customers upgrade to VIP. "
            "Notice how naive SCD Type 1 retroactively rewrites H1 2025 history (`historical_distortion != 0`), "
            "while **Point-in-Time SCD Type 2** preserves exact historical truth."
        )
        scd_df = pd.DataFrame(state["scd_comparison"])
        st.dataframe(scd_df, use_container_width=True, hide_index=True)

    with col_r:
        st.markdown("#### 📜 SCD Type 2 Version History (`snapshots.snap_customers`)")
        if state["scd_changed_rows"]:
            st.dataframe(pd.DataFrame(state["scd_changed_rows"]), use_container_width=True, hide_index=True)
        else:
            st.info("No multi-version customers yet. Click **🧬 Day 10: SCD Type 2** above to trigger live customer upgrades.")

    # Quarantine Table (lights up after Day 15!)
    st.divider()
    st.markdown("#### 🛡️ Quarantine Table (`quarantine.rejected_orders` — For Stakeholder Clarification)")
    if state["quarantine_rows"]:
        st.caption(
            "Instead of silently deduplicating with `ROW_NUMBER() = 1` or corrupting production `gold.fct_orders`, "
            "offending rows are quarantined here so Analytics Engineers can clarify the true business rule with stakeholders."
        )
        st.dataframe(pd.DataFrame(state["quarantine_rows"]), use_container_width=True, hide_index=True)
    else:
        st.info("Quarantine is empty. Click **🛡️ Day 15: Dirty Data WAP** to inject duplicate PKs, negative amounts, and NULL FKs.")

    with st.expander("📋 Pipeline Execution Audit Log (`governance.pipeline_runs`)"):
        runs_df = pd.DataFrame(
            [
                {
                    "day": r["run_day"],
                    "sim_date": r["sim_date"],
                    "event_type": r["event_type"],
                    "status": r["status"],
                    "summary": r["summary"],
                }
                for r in state["runs"]
            ]
        )
        st.dataframe(runs_df, use_container_width=True, hide_index=True)


def _render_tab2_explore(db_url: str, api_key: str, catalog) -> None:
    st.subheader("Explore → Semantic Gap Detection → SQLGlot AST Security Gate")
    st.caption(
        "Standardizes the handoff from an ambiguous business question to a validated, read-only SQL query "
        "ready to be promoted into a governed dbt Gold model."
    )

    # Quick preset buttons
    p1, p2, p3 = st.columns(3)
    preset_question = None
    if p1.button("💡 Preset 1: High-Value Customer Concentration (NEW_SEMANTIC_METRIC)", use_container_width=True):
        preset_question = (
            "Is our revenue mainly driven by high average order value and max order size "
            "from a few key customers, or by broad order volume?"
        )
    if p2.button("📊 Preset 2: Monthly Revenue Trend (REUSE_EXISTING)", use_container_width=True):
        preset_question = "Show monthly realized revenue and order count from fct_orders."
    if p3.button("🚫 Preset 3: Test 1:N Join Fanout Block (No LLM needed)", use_container_width=True):
        fanout_sql = (
            'SELECT o.customer_id, SUM(o.order_total) AS inflated_revenue '
            'FROM fct_orders o '
            'JOIN fct_order_items i ON o.order_id = i.order_id '
            'GROUP BY o.customer_id LIMIT 20'
        )
        st.session_state["editor_sql"] = fanout_sql
        st.session_state["active_spec"] = QuerySpec(
            spec_id="qs_fanout_demo",
            question="Test 1:N join fanout protection",
            data_sources=[DataSourceRef(relation="fct_orders")],
            dialect="postgres",
        )
        st.session_state["last_validation"] = validate_and_reconcile_sql(
            fanout_sql,
            st.session_state["active_spec"],
            allowed_relations={r.name for r in catalog.relations},
            server_tenant_id=None,
            one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
        )
        st.rerun()

    user_q = st.text_input(
        "Ask a business question about Jaffle Shop:",
        value=preset_question or "",
        placeholder="e.g., Is our revenue driven by high average order value or broad order volume?",
    )

    c_ask, c_draft, c_clear = st.columns([1.2, 1.2, 0.8])
    if c_ask.button("💬 Ask AI Consultant", type="primary", use_container_width=True) or preset_question:
        q_text = (preset_question or user_q).strip()
        if not q_text:
            st.warning("Please enter a business question first.")
        else:
            try:
                llm_fn = _make_llm_callable(api_key)
                st.session_state["chat_messages"].append({"role": "user", "content": q_text})
                history = [ConsultantMessage(**m) for m in st.session_state["chat_messages"]]
                with st.spinner("Consulting dbt catalog & formulating business hypotheses..."):
                    reply = consult_freeform(
                        messages=history,
                        catalog=catalog,
                        llm_callable=llm_fn,
                    )
                st.session_state["chat_messages"].append({"role": "assistant", "content": reply.reply})
            except Exception as exc:
                st.error(f"Fail-Loud Halt: {exc}")

    if c_draft.button("📝 Draft Governed Query", use_container_width=True):
        if not st.session_state["chat_messages"] and not user_q.strip():
            st.warning("Ask a question first before drafting a governed query.")
        else:
            try:
                llm_fn = _make_llm_callable(api_key)
                if not st.session_state["chat_messages"] and user_q.strip():
                    st.session_state["chat_messages"].append({"role": "user", "content": user_q.strip()})
                history = [ConsultantMessage(**m) for m in st.session_state["chat_messages"]]
                with st.spinner("Synthesizing governed SQL candidate & running AST check..."):
                    cand = draft_candidate_query(
                        messages=history,
                        catalog=catalog,
                        dialect="postgres",
                        server_tenant_id=None,
                        one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
                        llm_callable=llm_fn,
                    )
                st.session_state["candidate_bundle"] = cand
                st.session_state["editor_sql"] = cand.sql
                base_spec = QuerySpec(
                    spec_id=f"qs_demo_{cand.version}",
                    question=st.session_state["chat_messages"][0]["content"],
                    dialect="postgres",
                    output=OutputSpec(max_rows=50, certification="VALIDATED_EXPLORATORY"),
                )
                val = validate_and_reconcile_sql(
                    cand.sql,
                    base_spec,
                    allowed_relations={r.name for r in catalog.relations},
                    server_tenant_id=None,
                    one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
                )
                st.session_state["last_validation"] = val
                st.session_state["active_spec"] = val.reconciled_spec or base_spec
                st.session_state["last_gap"] = classify_sql_semantic_gap(
                    cand.sql, catalog, is_authorized=True
                )
            except Exception as exc:
                st.error(f"Fail-Loud Halt: {exc}")

    if c_clear.button("🗑️ Clear Chat", use_container_width=True):
        st.session_state["chat_messages"] = []
        st.session_state["candidate_bundle"] = None
        st.session_state["last_preview_df"] = None
        st.rerun()

    # Render conversation history
    for msg in st.session_state["chat_messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Security Gate & SQL Editor
    st.divider()
    st.markdown("### 🔒 Advanced SQL Editor & SQLGlot AST Security Gate")

    gap = st.session_state.get("last_gap")
    if gap is not None:
        st.info(f"**Semantic Gap Classification:** `{gap.classification.value}` — {gap.rationale}")

    sql_text = st.text_area(
        "Governed SQL (try editing to `DROP TABLE` or joining `fct_order_items` to test the AST Gate!)",
        value=st.session_state["editor_sql"]
        or (
            'SELECT "customer_id", COUNT("order_id") AS "order_count", '
            'SUM("realized_revenue") AS "total_revenue", '
            'AVG("order_total") AS "average_order_value", '
            'MAX("order_total") AS "max_order_amount" '
            'FROM "fct_orders" WHERE "status" = \'Completed\' '
            'GROUP BY "customer_id" LIMIT 50'
        ),
        height=120,
    )
    st.session_state["editor_sql"] = sql_text

    g1, g2, g3 = st.columns(3)
    if g1.button("🛡️ Validate & AST Reconcile", use_container_width=True):
        base_spec = st.session_state.get("active_spec") or QuerySpec(
            spec_id="qs_manual_001",
            question=st.session_state["chat_messages"][0]["content"]
            if st.session_state["chat_messages"]
            else "Customer value analysis from completed fct_orders",
            dialect="postgres",
            output=OutputSpec(max_rows=50, certification="VALIDATED_EXPLORATORY"),
        )
        val = validate_and_reconcile_sql(
            sql_text,
            base_spec,
            allowed_relations={r.name for r in catalog.relations},
            server_tenant_id=None,
            one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
        )
        st.session_state["last_validation"] = val
        st.session_state["active_spec"] = val.reconciled_spec or base_spec
        st.session_state["last_gap"] = classify_sql_semantic_gap(
            sql_text, catalog, is_authorized=True
        )

    val = st.session_state.get("last_validation")
    spec: QuerySpec | None = st.session_state.get("active_spec")

    if g2.button("✅ Confirm spec_hash", use_container_width=True):
        if spec is None or val is None or val.status == "BLOCKED":
            st.error("Run Validate & AST Reconcile first (must not be BLOCKED).")
        else:
            st.session_state["active_spec"] = spec.confirm_intent(confirmed_by="analytics_engineer")
            spec = st.session_state["active_spec"]
            st.success(f"Confirmed immutable spec_hash: `{spec.compute_spec_hash()[:12]}`")

    if g3.button("▶️ Execute Read-Only (Neon)", type="primary", use_container_width=True):
        if val is None or val.status == "BLOCKED":
            st.error("Execution BLOCKED: SQL has not passed the AST Security Gate.")
        elif spec is None or not spec.is_confirmation_valid():
            st.warning("Human-in-the-Loop Gate: Click **Confirm spec_hash** before executing.")
        else:
            try:
                with st.spinner("Executing READ ONLY query against Neon PostgreSQL..."):
                    df = execute_neon_readonly(val.executable_sql or sql_text, max_rows=50, db_url=db_url)
                if df.empty:
                    st.warning("Query returned 0 rows (Fail-Loud: no dummy rows substituted).")
                else:
                    st.session_state["last_preview_df"] = df
            except Exception as exc:
                st.error(f"Neon Read-Only Execution Failed: {exc}")

    if val is not None:
        if val.status == "BLOCKED":
            st.error(f"**AST Gate BLOCKED** (`{', '.join(val.reason_codes)}`): {'; '.join(val.messages)}")
        elif val.status == "REQUIRES_AE_REVIEW":
            st.warning(f"**AST Gate: REQUIRES_AE_REVIEW** (`{', '.join(val.reason_codes)}`): {'; '.join(val.messages)}")
        else:
            hash_short = spec.compute_spec_hash()[:12] if spec else "n/a"
            confirmed_txt = "✅ Confirmed" if (spec and spec.is_confirmation_valid()) else "⏳ Awaiting spec_hash confirmation"
            st.success(f"**AST Gate: VALID** · `spec_hash: {hash_short}` · {confirmed_txt}")

    preview_df = st.session_state.get("last_preview_df")
    if isinstance(preview_df, pd.DataFrame) and not preview_df.empty:
        st.markdown(f"#### Live Neon Preview ({len(preview_df)} rows · `SET TRANSACTION READ ONLY`)")
        st.dataframe(preview_df, use_container_width=True, hide_index=True)
        st.info("👉 Ready to govern this query? Switch to **Tab 3: Promote to Build** to synthesize dbt Gold SQL, Data Contract & Lightdash YAML.")


def _render_tab3_build() -> None:
    st.subheader("Promote to Build: dbt Gold Model + Enforced Data Contract + BI Semantic YAML")
    spec: QuerySpec | None = st.session_state.get("active_spec")
    preview_df: pd.DataFrame | None = st.session_state.get("last_preview_df")

    if spec is None or preview_df is None or preview_df.empty:
        st.info(
            "Run and execute a validated query in **Tab 2: Explore & AST Security Gate** first, "
            "then come here to promote it into governed dbt & BI artifacts."
        )
        return

    st.success(
        f"**Linked Explore Query** · `spec_hash: {spec.compute_spec_hash()[:12]}` · "
        f"Verified {len(preview_df)} rows in Neon READ ONLY sandbox"
    )

    c1, c2 = st.columns(2)
    model_name = c1.text_input("Target Gold Model Name", value="mart_customer_value_promoted")
    default_grain = ", ".join(spec.group_by) if spec.group_by else str(preview_df.columns[0])
    grain_input = c2.text_input("Primary Key Grain (comma-separated)", value=default_grain)

    if st.button("🏗️ Build dbt / Data Contract / Lightdash & Run Isolated Validation", type="primary"):
        grain_cols = [g.strip() for g in grain_input.split(",") if g.strip()]
        if not grain_cols:
            st.error("Primary Key Grain cannot be empty.")
            return

        # Infer accurate SQL types directly from the live Neon preview DataFrame
        inferred_types: dict[str, str] = {}
        for col in preview_df.columns:
            dtype_str = str(preview_df[col].dtype).lower()
            if "int" in dtype_str:
                inferred_types[col] = "integer"
            elif "float" in dtype_str or "double" in dtype_str:
                inferred_types[col] = "double precision"
            elif "bool" in dtype_str:
                inferred_types[col] = "boolean"
            else:
                inferred_types[col] = "varchar"

        bundle = build_dbt_and_lightdash_bundle(
            query_spec=spec,
            model_name=model_name,
            grain=grain_cols,
            column_types=inferred_types,
            business_owner="analytics-engineering@jaffle.shop",
        )

        # Isolated contract validation on verified Neon preview rows
        missing_grain = [g for g in grain_cols if g not in preview_df.columns]
        if missing_grain:
            st.error(f"Grain column(s) {missing_grain} not found in query output columns {list(preview_df.columns)}.")
            return

        null_violations = int(preview_df[grain_cols].isna().any(axis=1).sum())
        dup_violations = int(preview_df.duplicated(subset=grain_cols).sum())
        checks = [
            {
                "check": "isolated_materialization",
                "passed": len(preview_df) > 0,
                "detail": f"Validated {len(preview_df)} row(s) in Neon PostgreSQL READ ONLY sandbox (zero writes).",
            },
            {
                "check": f"not_null_contract({', '.join(grain_cols)})",
                "passed": null_violations == 0,
                "detail": f"{null_violations} null violation(s) across {len(preview_df)} rows.",
            },
            {
                "check": f"grain_uniqueness({', '.join(grain_cols)})",
                "passed": dup_violations == 0,
                "detail": f"{dup_violations} duplicate key(s) across {len(preview_df)} rows.",
            },
        ]
        st.session_state["build_bundle"] = bundle
        st.session_state["build_validation_checks"] = checks

    bundle = st.session_state.get("build_bundle")
    checks = st.session_state.get("build_validation_checks")
    if bundle is not None and checks is not None:
        all_ok = all(c["passed"] for c in checks)
        if all_ok:
            st.success("✅ **Isolated Sandbox Validation: PASSED** · Status: `STAGING_LOCAL_ONLY` (ready for Draft PR)")
        else:
            st.error("❌ **Isolated Sandbox Validation: FAILED** — Fix grain/null violations before opening Draft PR.")
        st.dataframe(pd.DataFrame(checks), use_container_width=True, hide_index=True)

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### 1. Governed dbt Gold Model (`.sql`)")
            st.code(bundle.dbt_sql, language="sql")
        with col2:
            st.markdown("#### 2. Enforced Data Contract (`schema.yml`)")
            st.code(bundle.schema_yaml, language="yaml")

        st.markdown("#### 3. Standalone BI Semantic Layer (`lightdash.yml`)")
        st.caption(
            "Decoupled from `schema.yml`. For Power BI (PBIP), the same builder outputs `.tmdl` files into the same Draft PR."
        )
        st.code(bundle.lightdash_yaml, language="yaml")


def _render_tab4_elementary_catalog(catalog) -> None:
    st.subheader("Elementary Data Observability Report (Generated Live from Neon PostgreSQL)")
    if ELEMENTARY_REPORT_PATH.exists():
        st.success(
            f"Loaded real Elementary HTML report (`{ELEMENTARY_REPORT_PATH.name}`, "
            f"{ELEMENTARY_REPORT_PATH.stat().st_size / 1024:.0f} KB) built via `dbt build` + `edr report` against Neon."
        )
        html_content = ELEMENTARY_REPORT_PATH.read_text(encoding="utf-8", errors="ignore")
        components.html(html_content, height=820, scrolling=True)
    else:
        st.warning(
            f"Elementary report not found at `{ELEMENTARY_REPORT_PATH}`. "
            "Run `python scripts/run_dbt_elementary.py` to generate it from Neon."
        )

    st.divider()
    st.subheader("Governed dbt Metadata Catalog & Certified Metrics")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Certified Metrics (Single Source of Truth)")
        metrics_df = pd.DataFrame(
            [
                {
                    "metric": m.name,
                    "label": m.label,
                    "source_relation": m.source_relation,
                    "expr": f"{m.measure_agg.upper()}({m.measure_expr})" if m.measure_agg else m.measure_expr,
                    "dimensions": ", ".join(m.dimensions),
                }
                for m in catalog.metrics
            ]
        )
        st.dataframe(metrics_df, use_container_width=True, hide_index=True)
    with c2:
        st.markdown("#### Governed Gold Relations & Grains")
        rels_df = pd.DataFrame(
            [
                {
                    "relation": r.name,
                    "layer": r.layer,
                    "schema": r.schema_name,
                    "columns": len(r.columns),
                    "description": r.description,
                }
                for r in catalog.relations
            ]
        )
        st.dataframe(rels_df, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(
        page_title="AI Analytics Workbench & Data Governance Lab",
        page_icon="🏛️",
        layout="wide",
    )
    _init_session_state()

    db_url = _resolve_secret("DATABASE_URL") or _resolve_secret("NEON_DATABASE_URL")
    if not db_url:
        st.error(
            "**Fail-Loud Startup Error**: `DATABASE_URL` is not configured. "
            "Add `DATABASE_URL` to `.env` (local) or Streamlit Cloud Secrets."
        )
        st.stop()

    try:
        state = _ensure_neon_initialized(db_url)
    except Exception as exc:
        st.error(f"**Neon PostgreSQL Connection Error**: {exc}")
        st.stop()

    api_key = _render_sidebar(db_url, state)
    catalog = build_jaffle_catalog()

    st.title("🏛️ AI Analytics Workbench & Data Governance Lab")
    st.caption(
        "Standardizing the workflow from ad-hoc exploration to governed dbt Data Contracts, "
        "SCD Type 2 snapshots, Write-Audit-Publish quality gates, and CI Semantic Drift protection."
    )

    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "1️⃣ Governance Timeline (Day 5/10/15/20)",
            "2️⃣ Explore & AST Security Gate",
            "3️⃣ Promote to Build (dbt + Contract + BI)",
            "4️⃣ Elementary Observability & Catalog",
        ]
    )

    with tab1:
        _render_tab1_timeline(db_url, state)
    with tab2:
        _render_tab2_explore(db_url, api_key, catalog)
    with tab3:
        _render_tab3_build()
    with tab4:
        _render_tab4_elementary_catalog(catalog)


if __name__ == "__main__":
    main()
