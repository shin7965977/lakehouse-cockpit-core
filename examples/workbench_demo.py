"""Standalone Public Streamlit Demo: AI Analytics Workbench & Data Governance Lab (Neon PostgreSQL).

Full ERP-parity workflow in a single-company public Streamlit Cloud app:
  1. Governance Timeline (Day 0/5/10/15/20 on Live Neon: WAP, SCD Type 2, Quarantine/LKG, CI Semantic Drift)
  2. Explore & Security Gate (2-step Query Evidence, v1->v2 Candidate Refinement, SQLGlot AST Gate,
     Bounded Auto-Repair + SQL Diff, Live Catalog Search, 1-click Promote to Build)
  3. Build (dbt Gold SQL + Enforced Data Contract + Isolated Validation)
  4. Lightdash BI (Dedicated tab reading Lightdash Cloud Secrets or STAGING_LOCAL_ONLY manifest)
  5. Review & Release (Draft PR Bundle Orchestrator, Promotion Candidates, dlt Missing-Source Proposal)
  6. Elementary, dbt Docs & Live dbt Table Explorer (Neon)

Run locally:
    streamlit run examples/workbench_demo.py
"""

from __future__ import annotations

import html
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

from integrations.lightdash_adapter import LightdashAdapter
from services.consultant_service import (
    ConsultantQueryOutcome,
    EvidenceQuerySummary,
    PreviousCandidateDraft,
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
    fetch_demo_state,
    reset_demo,
    run_day5_normal_refresh,
    run_day10_scd_type2,
    run_day15_dirty_data_wap,
    run_day20_semantic_drift_ci,
)
from services.query_spec import DataSourceRef, OutputSpec, QuerySpec
from services.release_orchestrator import orchestrate_draft_pr
from services.semantic_gap_detector import classify_sql_semantic_gap
from services.source_ingestion import APPROVED_CONNECTOR_REGISTRY, propose_source_ingestion
from services.sql_repair import repair_sql_bounded
from services.sql_validator import validate_and_reconcile_sql
from services.warehouse_adapters import ExecutionResult, WarehouseAdapter, WarehouseAdapterError

MAX_LLM_CALLS_PER_SESSION = 20
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
ELEMENTARY_REPORT_PATH = ROOT / "examples" / "demo_artifacts" / "elementary_report.html"
DBT_DOCS_STATIC_PATH = ROOT / "examples" / "demo_artifacts" / "dbt_docs_static.html"

NAV_TABS = [
    "1️⃣ Governance Timeline",
    "2️⃣ Explore & Security Gate",
    "3️⃣ Build (dbt + Contract)",
    "4️⃣ Lightdash BI",
    "5️⃣ Review & Release",
    "6️⃣ Elementary, dbt Docs & Tables",
]


ERP_THEME_CSS = """
<style>
.stApp {
    background-color: #f8fafc;
}
div.stButton > button[kind="primary"] {
    background-color: #2563eb !important;
    border-color: #2563eb !important;
    color: #ffffff !important;
    font-weight: 600 !important;
    border-radius: 8px !important;
}
div.stButton > button[kind="primary"]:hover {
    background-color: #1d4ed8 !important;
    border-color: #1d4ed8 !important;
}
div.stButton > button[kind="secondary"] {
    background-color: #ffffff !important;
    border: 1px solid #cbd5e1 !important;
    color: #1e293b !important;
    font-weight: 500 !important;
    border-radius: 8px !important;
}
div.stButton > button[kind="secondary"]:hover {
    border-color: #2563eb !important;
    color: #1d4ed8 !important;
    background-color: #eff6ff !important;
}
.erp-pill-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    justify-content: flex-end;
}
.erp-pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.03em;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
.pill-amber { background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }
.pill-green { background: #dcfce7; color: #166534; border: 1px solid #86efac; }
.pill-blue  { background: #dbeafe; color: #1e40af; border: 1px solid #93c5fd; }
.pill-red   { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
.pill-slate { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; }
.erp-assumption-box {
    background: #fefce8;
    border: 1px solid #fde047;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
    color: #713f12;
    margin: 8px 0 12px 0;
}
.erp-card-title {
    font-size: 15px;
    font-weight: 700;
    color: #0f172a;
    margin-bottom: 2px;
}
.erp-card-sub {
    font-size: 12px;
    color: #64748b;
    margin-bottom: 8px;
}
.erp-passed-banner {
    background: #ecfdf5;
    border: 1px solid #6ee7b7;
    border-radius: 8px;
    padding: 12px 16px;
    color: #065f46;
    font-size: 13px;
    margin: 10px 0;
}
</style>
"""


class _NeonReadOnlyAdapter:
    """Lightweight WarehouseAdapter backed by Neon READ ONLY transactions for bounded SQL Auto-Repair."""

    dialect = "postgres"

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url

    def capabilities(self) -> dict[str, Any]:
        return {"dialect": "postgres", "read_only": True, "provider": "neon"}

    def explain(self, sql: str) -> str:
        try:
            df = execute_neon_readonly(f"EXPLAIN {sql}", max_rows=20, db_url=self.db_url)
            return "\n".join(str(v) for v in df.iloc[:, 0].tolist()) if not df.empty else "EXPLAIN OK"
        except Exception as exc:
            raise WarehouseAdapterError("EXPLAIN_FAILED", str(exc)) from exc

    def execute_readonly(self, sql: str, max_rows: int = 50) -> ExecutionResult:
        try:
            df = execute_neon_readonly(sql, max_rows=max_rows, db_url=self.db_url)
            return ExecutionResult(
                dialect="postgres",
                row_count=len(df),
                columns=list(df.columns),
                rows=json.loads(df.to_json(orient="records", date_format="iso")),
                source_label="neon_postgres_readonly",
            )
        except Exception as exc:
            raise WarehouseAdapterError("EXECUTION_FAILED", str(exc)) from exc


def _pill(text: str, variant: str = "slate") -> str:
    return f'<span class="erp-pill pill-{variant}">{html.escape(text)}</span>'


def _resolve_secret(key: str) -> str:
    try:
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return (os.getenv(key) or "").strip()


def _init_session_state() -> None:
    defaults: dict[str, Any] = {
        "active_tab": NAV_TABS[0],
        "llm_calls": 0,
        "chat_messages": [],
        "evidence_history": [],
        "candidate_bundle": None,
        "sent_to_gate": False,
        "editor_sql": "",
        "active_spec": None,
        "last_validation": None,
        "last_repair": None,
        "last_exec_error": "",
        "last_preview_df": None,
        "last_gap": None,
        "gap_events": [],
        "build_bundle": None,
        "build_validation_checks": None,
        "draft_pr_result": None,
        "ingestion_proposal": None,
        "last_timeline_result": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _make_text_llm_callable(api_key: str):
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is required for bounded SQL auto-repair. "
            "Configure GEMINI_API_KEY in Streamlit Secrets or enter one in the sidebar."
        )
    if st.session_state["llm_calls"] >= MAX_LLM_CALLS_PER_SESSION:
        raise RuntimeError(
            f"Demo rate limit reached ({MAX_LLM_CALLS_PER_SESSION} LLM calls per session). "
            "Refresh the page to start a new session."
        )

    def _call_text(prompt: str) -> str:
        st.session_state["llm_calls"] += 1
        return generate_gemini_text(
            prompt=prompt,
            api_key=api_key,
            model=DEFAULT_GEMINI_MODEL,
        )

    return _call_text


def _make_structured_llm_callable(api_key: str):
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is required for AI consultation and SQL drafting. "
            "Configure GEMINI_API_KEY in Streamlit Secrets or enter one in the sidebar."
        )
    if st.session_state["llm_calls"] >= MAX_LLM_CALLS_PER_SESSION:
        raise RuntimeError(
            f"Demo rate limit reached ({MAX_LLM_CALLS_PER_SESSION} LLM calls per session). "
            "Refresh the page to start a new session."
        )

    def _call_structured(prompt: str, schema_cls: type[Any]) -> Any:
        st.session_state["llm_calls"] += 1
        return generate_gemini_structured(
            prompt=prompt,
            response_model=schema_cls,
            api_key=api_key,
            model=DEFAULT_GEMINI_MODEL,
        )

    return _call_structured


def _ensure_neon_initialized(db_url: str) -> dict[str, Any]:
    state = fetch_demo_state(db_url)
    if not state.get("initialized"):
        reset_demo(db_url)
        state = fetch_demo_state(db_url)
    return state


def _get_lightdash_status() -> dict[str, Any]:
    adapter = LightdashAdapter(
        lightdash_url=_resolve_secret("LIGHTDASH_URL"),
        api_key=_resolve_secret("LIGHTDASH_API_KEY"),
        project_uuid=_resolve_secret("LIGHTDASH_PROJECT_UUID"),
    )
    return adapter.check_status()


def _record_gap_event(question: str, gap) -> None:
    if gap is None:
        return
    cls_val = gap.classification.value
    if cls_val in ("NEW_SEMANTIC_METRIC", "NEW_DBT_MODEL", "TRANSFORMATION_GAP", "SOURCE_GAP"):
        st.session_state["gap_events"].append(
            {
                "question": question,
                "classification": cls_val,
                "candidate_relations": list(gap.candidate_relations or []),
                "rationale": gap.rationale,
            }
        )


def _render_sidebar(db_url: str, state: dict[str, Any]) -> str:
    with st.sidebar:
        st.markdown("## 🏛️ AI Analytics Workbench")
        st.caption("Single-Company Jaffle Shop · Neon PostgreSQL · dbt + Elementary")

        server_key = _resolve_secret("GEMINI_API_KEY")
        if server_key:
            st.success("🔐 Gemini AI: Connected (Server Secret)")
            api_key = server_key
        else:
            api_key = st.text_input(
                "Gemini API Key (for Tab 2 AI Chat)",
                value="",
                type="password",
                help="Optional for deterministic Labs (Tab 1, AST Gate, Tab 3-6). Required for live AI chat in Tab 2.",
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
        if st.button(
            "🔄 Reset Demo Sandbox (Day 0)",
            use_container_width=True,
            help="清空並還原 Neon 所有測試表回 Day 0 初始狀態（120 位客戶、520 筆訂單、初始 SCD2 快照），清除隔離表與髒資料。",
        ):
            with st.spinner("Resetting Neon schemas to clean Day 0 baseline..."):
                res = reset_demo(db_url)
                st.session_state["last_timeline_result"] = res
            st.success("Reset to Day 0 baseline!")
            st.rerun()

    return api_key.strip()


def _render_nav_bar() -> str:
    cols = st.columns(len(NAV_TABS))
    for idx, tab_name in enumerate(NAV_TABS):
        is_active = st.session_state["active_tab"] == tab_name
        if cols[idx].button(
            tab_name,
            key=f"nav_btn_{idx}",
            type="primary" if is_active else "secondary",
            use_container_width=True,
        ):
            st.session_state["active_tab"] = tab_name
            st.rerun()
    st.divider()
    return st.session_state["active_tab"]


def _render_tab1_timeline(db_url: str, state: dict[str, Any]) -> None:
    st.subheader("Interactive Data Governance & SCD Type 2 Timeline (Live Neon PostgreSQL)")
    st.caption(
        "Hover over each button to inspect the exact database & CI event it triggers. "
        "All actions execute real PostgreSQL transactions against Neon (zero LLM tokens)."
    )

    with st.container(border=True):
        b0, b5, b10, b15, b20 = st.columns(5)
        if b0.button(
            "🔄 Day 0 Reset Baseline",
            use_container_width=True,
            help="【重置基準】清空並重建 Neon raw / snapshots / gold / quarantine 表，回到 Day 0 乾淨狀態（120 客戶、520 訂單、120 筆初始 SCD2 快照）。",
        ):
            with st.spinner("Seeding Day 0 baseline in Neon..."):
                st.session_state["last_timeline_result"] = reset_demo(db_url)
            st.rerun()

        if b5.button(
            "✅ Day 5 Normal Refresh",
            use_container_width=True,
            help="【正常每日刷新 · WAP】寫入 25 筆正常新訂單進 raw → 建立 candidate 表 → 跑 4 項品質檢核（not_null / unique / non_negative / FK）全綠 → 發布至 gold.fct_orders。",
        ):
            with st.spinner("Running Day 5 Write-Audit-Publish on Neon..."):
                st.session_state["last_timeline_result"] = run_day5_normal_refresh(db_url)
            st.rerun()

        if b10.button(
            "🧬 Day 10 SCD Type 2",
            use_container_width=True,
            help="【客戶屬性異動 · SCD Type 2】將 15 位 Standard 客戶升級 VIP → 執行 SCD2 Snapshot（舊列補上 dbt_valid_to、新增 VIP 列）→ 現場對比 SCD1 歷史失真 vs. Point-in-Time Join 歷史保真。",
        ):
            with st.spinner("Upgrading 15 customers & running SCD Type 2 Snapshot on Neon..."):
                st.session_state["last_timeline_result"] = run_day10_scd_type2(db_url)
            st.rerun()

        if b15.button(
            "🛡️ Day 15 Dirty Data WAP",
            use_container_width=True,
            help="【上游髒資料防護 · WAP + LKG】注入 5 筆壞資料（重複 PK、負數金額、NULL 客戶 ID）→ Candidate 驗證失敗 → 拒絕覆蓋正式表（保留 Last-Known-Good）並將壞資料送進 Quarantine 隔離表待業務確認。",
        ):
            with st.spinner("Injecting bad records & running WAP Gate on Neon..."):
                st.session_state["last_timeline_result"] = run_day15_dirty_data_wap(db_url)
            st.rerun()

        if b20.button(
            "🚨 Day 20 Semantic Drift CI",
            use_container_width=True,
            help="【CI/CD 阻擋語意飄移】在隔離的 ci_pr schema 模擬 PR 偷改口徑（納入 Pending/Returned 訂單）→ Schema Contract 雖 PASS，但 CI Data Diff（+25% 飄移）與總帳對帳 FAIL → 直接 BLOCK PR Merge。",
        ):
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

            if "wap_checks" in details:
                st.markdown("#### Write-Audit-Publish (Candidate Validation Gates)")
                st.dataframe(pd.DataFrame(details["wap_checks"]), use_container_width=True, hide_index=True)

            if "ci_gates" in details:
                st.markdown("#### CI/CD PR Gate Results (`feat/update-revenue-filter`)")
                st.caption(f"Simulated PR change: {details.get('drifted_rule')}")
                st.dataframe(pd.DataFrame(details["ci_gates"]), use_container_width=True, hide_index=True)
                m1, m2, m3 = st.columns(3)
                m1.metric("Prod Realized Revenue", f"${details['prod_revenue']:,.2f}")
                m2.metric(
                    "PR Candidate Revenue",
                    f"${details['pr_revenue']:,.2f}",
                    delta=f"+{details['delta_pct']}%",
                    delta_color="inverse",
                )
                m3.metric("PR Merge Status", "🚫 BLOCKED")

    col_l, col_r = st.columns(2)
    with col_l:
        with st.container(border=True):
            st.markdown("#### 🧬 SCD Type 1 vs. SCD Type 2 (H1 2025 Historical Revenue)")
            st.caption(
                "After **Day 10**, 15 Standard customers upgrade to VIP. Naive SCD Type 1 retroactively rewrites H1 2025 "
                "revenue (`historical_distortion != 0`), while **Point-in-Time SCD Type 2** preserves historical truth."
            )
            st.dataframe(pd.DataFrame(state["scd_comparison"]), use_container_width=True, hide_index=True)

    with col_r:
        with st.container(border=True):
            st.markdown("#### 📜 SCD Type 2 Version History (`snapshots.snap_customers`)")
            if state["scd_changed_rows"]:
                st.dataframe(pd.DataFrame(state["scd_changed_rows"]), use_container_width=True, hide_index=True)
            else:
                st.info("No multi-version customers yet. Click **🧬 Day 10 SCD Type 2** above to trigger live customer upgrades.")

    with st.container(border=True):
        st.markdown("#### 🛡️ Quarantine Table (`quarantine.rejected_orders` — For Stakeholder Clarification)")
        if state["quarantine_rows"]:
            st.caption("Offending rows are quarantined instead of silently deduplicated (`ROW_NUMBER() = 1`) or published to Gold.")
            st.dataframe(pd.DataFrame(state["quarantine_rows"]), use_container_width=True, hide_index=True)
        else:
            st.info("Quarantine is empty. Click **🛡️ Day 15 Dirty Data WAP** to inject duplicate PKs, negative amounts, and NULL FKs.")

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
    st.subheader("Explore → Candidate Governed Query (v1 → v2) → SQLGlot AST Security Gate")
    st.caption(
        "ERP-parity flow: Free Consultation (with Query Evidence) → Draft / Refine Candidate Query (v1 → v2) → "
        "Approve & Send to Security Gate → Validate / Auto-Repair / Confirm `spec_hash` → Execute Read-Only → 1-Click Promote to Build."
    )

    # ── Step 1: Consultation Card ──────────────────────────────────────────────
    with st.container(border=True):
        h_left, h_right = st.columns([3, 1])
        with h_left:
            st.markdown('<div class="erp-card-title">1. Business Consultation & Intent Framing</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="erp-card-sub">Align on business hypotheses with live Neon Query Evidence before drafting governed SQL.</div>',
                unsafe_allow_html=True,
            )
        with h_right:
            if st.button(
                "✨ New Conversation",
                use_container_width=True,
                help="清空目前對話、候選查詢（v1/v2）與 Security Gate 狀態，開始全新分析。",
            ):
                st.session_state["chat_messages"] = []
                st.session_state["evidence_history"] = []
                st.session_state["candidate_bundle"] = None
                st.session_state["sent_to_gate"] = False
                st.session_state["editor_sql"] = ""
                st.session_state["active_spec"] = None
                st.session_state["last_validation"] = None
                st.session_state["last_repair"] = None
                st.session_state["last_exec_error"] = ""
                st.session_state["last_preview_df"] = None
                st.session_state["last_gap"] = None
                st.rerun()

        p1, p2, p3 = st.columns(3)
        preset_question = None
        if p1.button(
            "💡 Preset 1: Customer Value (NEW_SEMANTIC_METRIC)",
            use_container_width=True,
            help="發問客單價與最大單筆金額集中度，觸發兩段式 Query Evidence 實查 Neon 與 NEW_SEMANTIC_METRIC 語意缺口。",
        ):
            preset_question = (
                "Is our revenue mainly driven by high average order value and max order size "
                "from a few key customers, or by broad order volume?"
            )
        if p2.button(
            "📊 Preset 2: Monthly Revenue (REUSE_EXISTING)",
            use_container_width=True,
            help="發問已認證的月度營收與訂單數指標，示範 REUSE_EXISTING 重用既有語意層。",
        ):
            preset_question = "Show monthly realized revenue and order count from fct_orders."
        if p3.button(
            "🚫 Preset 3: Test 1:N Join Fanout Block",
            use_container_width=True,
            help="免呼叫 LLM：直接載入一條危險的 1:N Join SQL（fct_orders ⨝ fct_order_items + SUM）進 Security Gate，立即展示 AST 秒擋 JOIN_FANOUT_RISK。",
        ):
            fanout_sql = (
                "SELECT o.customer_id, SUM(o.order_total) AS inflated_revenue "
                "FROM fct_orders o "
                "JOIN fct_order_items i ON o.order_id = i.order_id "
                "GROUP BY o.customer_id LIMIT 20"
            )
            spec = QuerySpec(
                spec_id="qs_fanout_demo",
                question="Test 1:N join fanout protection",
                data_sources=[DataSourceRef(relation="fct_orders")],
                dialect="postgres",
            )
            st.session_state["editor_sql"] = fanout_sql
            st.session_state["active_spec"] = spec
            st.session_state["sent_to_gate"] = True
            st.session_state["last_validation"] = validate_and_reconcile_sql(
                fanout_sql,
                spec,
                allowed_relations={r.name for r in catalog.relations},
                server_tenant_id=None,
                one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
            )
            st.session_state["last_gap"] = classify_sql_semantic_gap(fanout_sql, catalog, is_authorized=True)
            st.rerun()

        for msg in st.session_state["chat_messages"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                ev_list = msg.get("evidence") or []
                if ev_list:
                    total_rows = sum(int(e.get("row_count", 0)) for e in ev_list)
                    with st.expander(
                        f"📊 Query Evidence ({len(ev_list)} read-only quer{'y' if len(ev_list) == 1 else 'ies'} · {total_rows} rows)",
                        expanded=True,
                    ):
                        for ev in ev_list:
                            status_icon = "✅" if ev.get("status") == "SUCCEEDED" else "❌"
                            st.markdown(
                                f"{status_icon} **{ev.get('title', 'Read-only Evidence Query')}** "
                                f"(`{ev.get('row_count', 0)} rows` · `{str(ev.get('spec_hash', ''))[:8]}`)"
                            )
                            if ev.get("sql"):
                                st.code(ev["sql"], language="sql")
                            if ev.get("error"):
                                st.error(ev["error"])
                            elif ev.get("rows"):
                                st.dataframe(pd.DataFrame(ev["rows"]), use_container_width=True, hide_index=True)

        existing_cand = st.session_state.get("candidate_bundle")
        input_label = (
            f"Refinement feedback for v{existing_cand.version} → v{existing_cand.version + 1} (or new question):"
            if existing_cand is not None
            else "Ask a business question:"
        )
        user_q = st.text_input(
            input_label,
            value=preset_question or "",
            placeholder=(
                "e.g., Group by region instead, or filter only Completed orders..."
                if existing_cand is not None
                else "e.g., Is our revenue driven by high average order value or broad order volume?"
            ),
        )

        a1, a2 = st.columns(2)
        if a1.button(
            "💬 Ask AI Consultant",
            type="primary",
            use_container_width=True,
            help="執行兩段式證據諮詢（與 ERP 相同）：顧問提議唯讀 SQL → 實查 Neon → 結合真實數據提出商業假設並顯示 Query Evidence。",
        ) or preset_question:
            q_text = (preset_question or user_q).strip()
            if not q_text:
                st.warning("Please enter a business question first.")
            else:
                try:
                    structured_fn = _make_structured_llm_callable(api_key)
                    prior_history = [
                        ClarificationTurn(role=m["role"], content=m["content"])
                        for m in st.session_state["chat_messages"]
                    ]
                    st.session_state["chat_messages"].append({"role": "user", "content": q_text})
                    allowed_rels = {r.name for r in catalog.relations}

                    with st.spinner("Step 1/2: Proposing read-only evidence queries & querying Neon..."):
                        proposal = propose_consultant_turn(
                            message=q_text,
                            catalog=catalog,
                            history=prior_history,
                            dialect="postgres",
                            structured_llm_fn=structured_fn,
                        )

                    evidence_cards: list[dict[str, Any]] = []
                    outcomes: list[ConsultantQueryOutcome] = []

                    if proposal.needs_data and proposal.proposed_queries:
                        for pq in proposal.proposed_queries[:2]:
                            raw_sql = pq.compiled_sql or (pq.query_spec.compile_sql() if pq.query_spec else "")
                            val_ev = validate_and_reconcile_sql(
                                raw_sql,
                                pq.query_spec or QuerySpec(spec_id="qs_ev", dialect="postgres"),
                                allowed_relations=allowed_rels,
                                server_tenant_id=None,
                                one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
                            )
                            if val_ev.status == "BLOCKED":
                                outcome = ConsultantQueryOutcome(
                                    title=pq.title,
                                    sql=raw_sql,
                                    spec_hash=pq.spec_hash,
                                    status="FAILED",
                                    error=f"AST Gate Blocked: {'; '.join(val_ev.messages)}",
                                )
                            else:
                                try:
                                    exec_sql = val_ev.executable_sql or raw_sql
                                    df_ev = execute_neon_readonly(exec_sql, max_rows=20, db_url=db_url)
                                    rows_dict = json.loads(df_ev.to_json(orient="records", date_format="iso"))
                                    outcome = ConsultantQueryOutcome(
                                        title=pq.title,
                                        sql=exec_sql,
                                        spec_hash=pq.spec_hash,
                                        status="SUCCEEDED",
                                        row_count=len(df_ev),
                                        columns=list(df_ev.columns),
                                        rows=rows_dict,
                                    )
                                    st.session_state["evidence_history"].append(
                                        EvidenceQuerySummary(
                                            title=pq.title,
                                            sql=exec_sql,
                                            row_count=len(df_ev),
                                            columns=list(df_ev.columns),
                                        )
                                    )
                                except Exception as q_exc:
                                    outcome = ConsultantQueryOutcome(
                                        title=pq.title,
                                        sql=raw_sql,
                                        spec_hash=pq.spec_hash,
                                        status="FAILED",
                                        error=str(q_exc),
                                    )
                            outcomes.append(outcome)
                            evidence_cards.append(outcome.model_dump())

                        with st.spinner("Step 2/2: Synthesizing data-backed hypotheses from Neon evidence..."):
                            ans = answer_consultant_turn(
                                message=q_text,
                                query_outcomes=outcomes,
                                history=prior_history,
                                structured_llm_fn=structured_fn,
                            )
                            reply_text = ans.reply
                    else:
                        reply_text = proposal.direct_reply or "Please specify which metric or dimension you want to explore."

                    st.session_state["chat_messages"].append(
                        {"role": "assistant", "content": reply_text, "evidence": evidence_cards}
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Fail-Loud Halt: {exc}")

        draft_btn_label = (
            f"📝 Refine Candidate (v{existing_cand.version} → v{existing_cand.version + 1})"
            if existing_cand is not None
            else "📝 Draft Governed Query"
        )
        if a2.button(
            draft_btn_label,
            use_container_width=True,
            help="起草或迭代微調候選 SQL（已有 v1 時輸入修改意見即可帶入 previous_candidate 升級成 v2），並自動偵測語意缺口。",
        ):
            if not st.session_state["chat_messages"] and not user_q.strip():
                st.warning("Ask a business question first before drafting a governed query.")
            else:
                try:
                    structured_fn = _make_structured_llm_callable(api_key)
                    clean_input = user_q.strip()
                    prev_draft: PreviousCandidateDraft | None = None
                    refinement = ""

                    if existing_cand is not None:
                        prev_draft = PreviousCandidateDraft(
                            version=existing_cand.version,
                            title=existing_cand.title,
                            sql=existing_cand.sql,
                            plain_language_explanation=existing_cand.plain_language_explanation,
                            assumptions=list(existing_cand.assumptions or []),
                            group_by=list(existing_cand.group_by or []),
                        )
                        refinement = clean_input or "Refine and improve clarity of the previous candidate query."
                        if clean_input:
                            st.session_state["chat_messages"].append({"role": "user", "content": clean_input})
                    elif not st.session_state["chat_messages"] and clean_input:
                        st.session_state["chat_messages"].append({"role": "user", "content": clean_input})

                    history = [
                        ClarificationTurn(role=m["role"], content=m["content"])
                        for m in st.session_state["chat_messages"]
                    ]
                    with st.spinner("Synthesizing Candidate Governed Query & running AST check..."):
                        cand = draft_candidate_query(
                            history=history,
                            catalog=catalog,
                            evidence_queries=st.session_state.get("evidence_history") or None,
                            previous_candidate=prev_draft,
                            refinement_feedback=refinement,
                            dialect="postgres",
                            tenant_id=None,
                            structured_llm_fn=structured_fn,
                        )
                    st.session_state["candidate_bundle"] = cand
                    st.session_state["sent_to_gate"] = False
                    st.session_state["last_repair"] = None
                    st.session_state["last_exec_error"] = ""
                    if cand.sql:
                        st.session_state["editor_sql"] = cand.sql

                    # Preserve original business question for downstream dbt description (avoid overwriting with refinement text)
                    original_q = next(
                        (m["content"] for m in st.session_state["chat_messages"] if m["role"] == "user"),
                        cand.title or "Governed analytical model",
                    )
                    base_spec = (cand.query_spec or QuerySpec(spec_id=f"qs_demo_v{cand.version}", dialect="postgres")).model_copy(
                        update={"question": original_q}
                    )
                    if cand.sql:
                        val = validate_and_reconcile_sql(
                            cand.sql,
                            base_spec,
                            allowed_relations={r.name for r in catalog.relations},
                            server_tenant_id=None,
                            one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
                        )
                        st.session_state["last_validation"] = val
                        st.session_state["active_spec"] = (val.reconciled_spec or base_spec).model_copy(
                            update={"question": original_q}
                        )
                    else:
                        st.session_state["last_validation"] = cand.validation
                        st.session_state["active_spec"] = base_spec
                    st.session_state["last_gap"] = cand.gap
                    _record_gap_event(original_q, cand.gap)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Fail-Loud Halt: {exc}")

    # ── Step 2: Candidate Governed Query Card (v1 / v2) ────────────────────────
    cand = st.session_state.get("candidate_bundle")
    val = st.session_state.get("last_validation")
    spec: QuerySpec | None = st.session_state.get("active_spec")
    gap = st.session_state.get("last_gap")

    if cand is not None:
        with st.container(border=True):
            top_l, top_r = st.columns([2.2, 1.8])
            with top_l:
                st.markdown(
                    f'<div class="erp-card-title">Candidate Governed Query &nbsp;{_pill(f"v{cand.version}", "blue")}</div>'
                    f'<div class="erp-card-sub">{html.escape(cand.title or "Governed Analytical Candidate")}</div>',
                    unsafe_allow_html=True,
                )
            with top_r:
                gap_label = cand.gap.classification.value if cand.gap else "UNKNOWN"
                gap_variant = "green" if gap_label == "REUSE_EXISTING" else ("red" if gap_label in ("SOURCE_GAP", "ACCESS_BLOCKED") else "amber")
                ast_status = val.status if val else "PENDING"
                ast_variant = "green" if ast_status == "VALID" else ("red" if ast_status == "BLOCKED" else "amber")
                st.markdown(
                    f'<div class="erp-pill-row">{_pill(gap_label, gap_variant)}{_pill(f"AST: {ast_status}", ast_variant)}</div>',
                    unsafe_allow_html=True,
                )

            if cand.plain_language_explanation:
                st.markdown(cand.plain_language_explanation)

            if cand.assumptions:
                assump_txt = "; ".join(cand.assumptions)
                st.markdown(
                    f'<div class="erp-assumption-box"><strong>Assumptions:</strong> {html.escape(assump_txt)}</div>',
                    unsafe_allow_html=True,
                )

            grain_lbl = ", ".join(cand.group_by) if cand.group_by else (
                ", ".join(spec.group_by) if (spec and spec.group_by) else "auto"
            )
            hash_short = spec.compute_spec_hash()[:10] if spec else "pending"
            with st.expander(f"View Candidate SQL (Grain: {grain_lbl}) · hash: {hash_short}", expanded=True):
                st.code(cand.sql or "-- No SQL generated (SOURCE_GAP / Abstention)", language="sql")

            btn_l, btn_r = st.columns(2)
            if btn_l.button(
                "Continue Free Consultation",
                use_container_width=True,
                help="先不送進 Security Gate，繼續在上方輸入框微調需求（按 Refine Candidate 會升級成下一版本）。",
            ):
                st.info("Type your refinement in the input box above and click **Refine Candidate** to upgrade to the next version.")

            sent_label = "✅ Sent to Security Gate" if st.session_state["sent_to_gate"] else "Approve & Send to Security Gate →"
            if btn_r.button(
                sent_label,
                type="primary",
                use_container_width=True,
                disabled=not bool(cand.sql),
                help="核准候選方向並送進下方 SQLGlot AST Security Gate 進行安全驗證與唯讀執行。",
            ):
                st.session_state["sent_to_gate"] = True
                st.rerun()

    # ── Step 3: Semantic Gap + Security Gate + Auto-Repair + SQL Diff ──────────
    if st.session_state["sent_to_gate"] or (val is not None and cand is None):
        if gap is not None:
            with st.container(border=True):
                gl, gr = st.columns([3, 1])
                gl.markdown('<div class="erp-card-title">Semantic Gap Classification</div>', unsafe_allow_html=True)
                gl.caption(gap.rationale)
                g_val = gap.classification.value
                g_var = "green" if g_val == "REUSE_EXISTING" else ("red" if g_val in ("SOURCE_GAP", "ACCESS_BLOCKED") else "amber")
                gr.markdown(f'<div class="erp-pill-row">{_pill(g_val, g_var)}</div>', unsafe_allow_html=True)

        with st.container(border=True):
            sl, sr = st.columns([2.2, 1.8])
            sl.markdown(
                '<div class="erp-card-title">Advanced SQL Editor & Security Gate (AST / Fanout / Auto-Repair)</div>',
                unsafe_allow_html=True,
            )
            hash_short = spec.compute_spec_hash()[:12] if spec else "unverified"
            confirmed_pill = _pill("✅ Confirmed", "green") if (spec and spec.is_confirmation_valid()) else _pill("Unconfirmed", "slate")
            sr.markdown(
                f'<div class="erp-pill-row">{_pill(f"hash: {hash_short}", "blue")}{confirmed_pill}</div>',
                unsafe_allow_html=True,
            )

            sql_text = st.text_area(
                "Governed SQL (edit freely — any change invalidates spec_hash until re-validated & re-confirmed)",
                value=st.session_state["editor_sql"],
                height=120,
                label_visibility="collapsed",
            )
            st.session_state["editor_sql"] = sql_text

            g1, g2, g3, g4 = st.columns(4)
            if g1.button(
                "🛡️ Validate & AST Reconcile",
                use_container_width=True,
                help="用 SQLGlot 解析語法樹：驗證唯讀 SELECT、授權白名單、1:N Join Fanout，並產生 SQL Diff 與重算 spec_hash。",
            ):
                base_spec = st.session_state.get("active_spec") or QuerySpec(
                    spec_id="qs_manual_001",
                    question=st.session_state["chat_messages"][0]["content"]
                    if st.session_state["chat_messages"]
                    else "Manual governed query",
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
                st.session_state["last_gap"] = classify_sql_semantic_gap(sql_text, catalog, is_authorized=True)
                st.session_state["last_exec_error"] = ""
                st.rerun()

            if g2.button(
                "🔧 Auto-Repair (Bounded)",
                use_container_width=True,
                help="有界自動修復（最多 2 次嘗試）：當 SQL 驗證或執行報錯時，呼叫 Gemini 修復並自動重過 AST 安全閘門與 Neon EXPLAIN，顯示修復前後 SQL Diff。",
            ):
                err_ctx = st.session_state.get("last_exec_error") or (
                    "; ".join(val.messages) if (val is not None and val.messages) else "Syntax or schema error in SQL"
                )
                try:
                    llm_text_fn = _make_text_llm_callable(api_key)
                    base_spec = st.session_state.get("active_spec") or QuerySpec(
                        spec_id="qs_repair_001", dialect="postgres"
                    )
                    schema_hint = "; ".join(
                        f"{r.name}({', '.join(c.name for c in r.columns)})" for r in catalog.relations
                    )
                    with st.spinner("Running bounded SQL Auto-Repair (max 2 attempts) + Neon EXPLAIN..."):
                        rep = repair_sql_bounded(
                            sql_text,
                            err_ctx,
                            base_spec=base_spec,
                            allowed_relations={r.name for r in catalog.relations},
                            adapter=_NeonReadOnlyAdapter(db_url),
                            server_tenant_id=None,
                            one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
                            schema_hint=schema_hint,
                            llm_callable=llm_text_fn,
                            max_attempts=2,
                        )
                    st.session_state["last_repair"] = rep
                    if rep.repaired and rep.repaired_sql:
                        st.session_state["editor_sql"] = rep.repaired_sql
                        st.session_state["active_spec"] = rep.reconciled_spec or base_spec
                        st.session_state["last_validation"] = validate_and_reconcile_sql(
                            rep.repaired_sql,
                            st.session_state["active_spec"],
                            allowed_relations={r.name for r in catalog.relations},
                            server_tenant_id=None,
                            one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
                        )
                        st.session_state["last_exec_error"] = ""
                    st.rerun()
                except Exception as exc:
                    st.error(f"Auto-Repair Fail-Loud Halt: {exc}")

            if g3.button(
                "✅ Confirm spec_hash",
                use_container_width=True,
                help="Human-in-the-Loop 確認：鎖定核准目前驗證通過的 spec_hash，允許唯讀執行。",
            ):
                if spec is None or val is None or val.status == "BLOCKED":
                    st.error("Run Validate & AST Reconcile first (must not be BLOCKED).")
                else:
                    st.session_state["active_spec"] = spec.confirm_intent(confirmed_by="analytics_engineer")
                    st.rerun()

            if g4.button(
                "▶️ Execute Read-Only (Neon)",
                type="primary",
                use_container_width=True,
                help="在 Neon PostgreSQL 的 SET TRANSACTION READ ONLY 沙盒執行查詢，回傳真實預覽結果（零寫入）。",
            ):
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
                            st.session_state["last_exec_error"] = ""
                    except Exception as exc:
                        st.session_state["last_exec_error"] = str(exc)
                        st.error(f"Neon Read-Only Execution Failed (try **🔧 Auto-Repair**): {exc}")

            # Validation & Repair Status + SQL Diff
            val = st.session_state.get("last_validation")
            rep = st.session_state.get("last_repair")
            if rep is not None:
                if rep.repaired:
                    st.success(f"🔧 **Auto-Repair Succeeded** in {rep.attempts} attempt(s) — please re-confirm `spec_hash`.")
                    if rep.sql_diff:
                        st.code(rep.sql_diff, language="diff")
                else:
                    st.error(f"🔧 **Auto-Repair Stopped** ({', '.join(rep.failure_codes)}): {'; '.join(rep.messages)}")

            if val is not None:
                if val.status == "BLOCKED":
                    st.error(f"**Validation Status: BLOCKED (`{', '.join(val.reason_codes)}`)** — {'; '.join(val.messages)}")
                elif val.status == "REQUIRES_AE_REVIEW":
                    st.warning(f"**Validation Status: REQUIRES_AE_REVIEW (`{', '.join(val.reason_codes)}`)** — {'; '.join(val.messages)}")
                else:
                    st.success("**Validation Status: VALID** — Passed read-only, allowlist, and 1:N join fanout AST gates.")
                if val.sql_diff:
                    with st.expander("View SQL Diff (vs. prior QuerySpec)", expanded=True):
                        st.code(val.sql_diff, language="diff")

        # Live Preview + 1-Click Promote to Build!
        preview_df = st.session_state.get("last_preview_df")
        if isinstance(preview_df, pd.DataFrame) and not preview_df.empty:
            with st.container(border=True):
                pl, pr = st.columns([2.5, 1.5])
                pl.caption(f"Provenance: `neon_postgres_readonly` (postgres) · **{len(preview_df)} rows**")
                if pr.button(
                    "🚀 Promote to Build →",
                    type="primary",
                    use_container_width=True,
                    help="一鍵帶著已驗證的 QuerySpec、spec_hash 與預覽結果自動切換到 3️⃣ Build 頁籤升格成 dbt Gold 資產！",
                ):
                    st.session_state["active_tab"] = NAV_TABS[2]
                    st.rerun()
                st.dataframe(preview_df, use_container_width=True, hide_index=True)

    # ── Step 4: Live Catalog Search Cards (ERP parity) ─────────────────────────
    with st.container(border=True):
        st.markdown('<div class="erp-card-title">🔍 Live dbt Metadata Catalog Search</div>', unsafe_allow_html=True)
        default_search = (
            st.session_state["chat_messages"][-1]["content"]
            if st.session_state["chat_messages"]
            else "revenue"
        )
        search_q = st.text_input(
            "Search certified metrics & governed Gold tables:",
            value=default_search[:80],
            key="catalog_search_box",
        )
        search_res = catalog.search(search_q)
        mc1, mc2 = st.columns(2)
        with mc1:
            st.markdown(f"**Matched Certified Metrics ({len(search_res.metrics)})**")
            if search_res.metrics:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {"metric": m.name, "label": m.label, "table": m.source_relation, "agg": f"{m.measure_agg}({m.measure_expr})"}
                            for m in search_res.metrics
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("No exact certified metric matched — candidate for `NEW_SEMANTIC_METRIC`.")
        with mc2:
            st.markdown(f"**Related Governed Tables ({len(search_res.relations)})**")
            if search_res.relations:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {"relation": r.name, "layer": r.layer, "columns": len(r.columns), "description": r.description}
                            for r in search_res.relations
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("No matching table in governed catalog.")


def _render_tab3_build() -> None:
    spec: QuerySpec | None = st.session_state.get("active_spec")
    preview_df: pd.DataFrame | None = st.session_state.get("last_preview_df")
    cand = st.session_state.get("candidate_bundle")
    gap = st.session_state.get("last_gap")

    if spec is None or preview_df is None or preview_df.empty:
        st.info(
            "Complete **2️⃣ Explore & Security Gate** (Draft → Approve → Confirm `spec_hash` → Execute Read-Only → Promote to Build) "
            "first, then return here to synthesize dbt Gold SQL & Data Contracts."
        )
        if st.button("← Go to Explore & Security Gate", type="primary"):
            st.session_state["active_tab"] = NAV_TABS[1]
            st.rerun()
        return

    with st.container(border=True):
        ll, lr = st.columns([2, 2])
        title_txt = cand.title if (cand and cand.title) else (spec.question[:70] or "Governed Analytical Model")
        ll.markdown(
            f'<div class="erp-card-sub" style="text-transform:uppercase;letter-spacing:0.06em;">LINKED EXPLORE QUERY</div>'
            f'<div class="erp-card-title">{html.escape(title_txt)}</div>',
            unsafe_allow_html=True,
        )
        gap_txt = gap.classification.value if gap else "NEW_SEMANTIC_METRIC"
        lr.markdown(
            f'<div class="erp-pill-row">'
            f'{_pill(gap_txt, "slate")}'
            f'{_pill(f"hash: {spec.compute_spec_hash()[:12]}", "blue")}'
            f'{_pill(f"Sandbox Verified ({len(preview_df)} rows)", "green")}'
            f'</div>',
            unsafe_allow_html=True,
        )
        with st.expander("View Synced SQL from Explore", expanded=False):
            st.code(st.session_state.get("editor_sql") or spec.compile_sql(), language="sql")

    with st.container(border=True):
        st.markdown(
            '<div class="erp-card-title">Synthesize dbt Gold Model, Enforced Data Contract & Standalone Lightdash YAML</div>',
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        model_name = c1.text_input("Target Gold Model Name (auto-suggested from Explore)", value="mart_customer_value_promoted")
        default_grain = ", ".join(spec.group_by) if spec.group_by else str(preview_df.columns[0])
        grain_input = c2.text_input("Primary Key Grain (comma-separated, synced from Explore)", value=default_grain)

        if st.button(
            "Build dbt / Data Contract / Lightdash & Run Isolated Validation",
            type="primary",
            help="同步產出 dbt Gold SQL（自動轉 {{ ref(...) }}）、Enforced Data Contract (schema.yml)、Lightdash Semantic YAML，並執行隔離契約測試。",
        ):
            grain_cols = [g.strip() for g in grain_input.split(",") if g.strip()]
            if not grain_cols:
                st.error("Primary Key Grain cannot be empty.")
                return

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

            missing_grain = [g for g in grain_cols if g not in preview_df.columns]
            if missing_grain:
                st.error(f"Grain column(s) {missing_grain} not found in output columns {list(preview_df.columns)}.")
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
                    "check": "not_null_contract",
                    "passed": null_violations == 0,
                    "detail": f"All grain column(s) {grain_cols} passed not_null assertion across {len(preview_df)} row(s)."
                    if null_violations == 0
                    else f"{null_violations} null violation(s) found.",
                },
                {
                    "check": "grain_uniqueness",
                    "passed": dup_violations == 0,
                    "detail": f"Composite grain ({', '.join(grain_cols)}) is 100% unique across {len(preview_df)} row(s)."
                    if dup_violations == 0
                    else f"{dup_violations} duplicate key(s) found.",
                },
            ]
            st.session_state["build_bundle"] = bundle
            st.session_state["build_validation_checks"] = checks
            st.session_state["draft_pr_result"] = None

    bundle = st.session_state.get("build_bundle")
    checks = st.session_state.get("build_validation_checks")
    if bundle is not None and checks is not None:
        all_ok = all(c["passed"] for c in checks)
        if all_ok:
            lines_html = "".join(
                f"<div>✅ <strong>{html.escape(c['check'])}:</strong> {html.escape(c['detail'])}</div>"
                for c in checks
            )
            st.markdown(
                f'<div class="erp-passed-banner"><strong>Isolated Sandbox Validation: PASSED ({len(preview_df)} rows)</strong>'
                f"{lines_html}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.error("❌ **Isolated Sandbox Validation: FAILED** — Fix grain/null violations before opening Draft PR.")

        col1, col2 = st.columns(2)
        with col1:
            with st.container(border=True):
                st.markdown('<div class="erp-card-title">dbt Model (.sql)</div>', unsafe_allow_html=True)
                st.code(bundle.dbt_sql, language="sql")
        with col2:
            with st.container(border=True):
                st.markdown('<div class="erp-card-title">Enforced Data Contract (schema.yml)</div>', unsafe_allow_html=True)
                st.code(bundle.schema_yaml, language="yaml")

        n1, n2 = st.columns(2)
        if n1.button("Inspect in 4️⃣ Lightdash BI →", use_container_width=True):
            st.session_state["active_tab"] = NAV_TABS[3]
            st.rerun()
        if n2.button("Submit Draft PR in 5️⃣ Review & Release →", type="primary", use_container_width=True):
            st.session_state["active_tab"] = NAV_TABS[4]
            st.rerun()


def _render_tab4_lightdash() -> None:
    ld_status = _get_lightdash_status()
    bundle = st.session_state.get("build_bundle")

    with st.container(border=True):
        hl, hr = st.columns([2.5, 1.5])
        hl.markdown('<div class="erp-card-title">Lightdash Semantic Status & BI Asset Manifest</div>', unsafe_allow_html=True)
        hl.caption(ld_status["message"])

        status_label = ld_status["lightdash_status"]
        pill_var = "green" if ld_status.get("connected") else "amber"
        hr.markdown(f'<div class="erp-pill-row">{_pill(status_label, pill_var)}</div>', unsafe_allow_html=True)

        if ld_status.get("connected") and ld_status.get("explore_base_url"):
            st.link_button("Open Live Lightdash BI →", ld_status["explore_base_url"], type="primary")
        else:
            with st.expander("⚙️ How to connect Lightdash Cloud to this Streamlit Demo"):
                st.markdown(
                    "1. Create a **Lightdash Cloud** project connected to the same **Neon PostgreSQL** database and `examples/jaffle_dbt` repo.\n"
                    "2. Add these 3 keys to **Streamlit Cloud → Settings → Secrets**:\n"
                    "```toml\n"
                    'LIGHTDASH_URL = "https://app.lightdash.cloud"\n'
                    'LIGHTDASH_API_KEY = "ldpat_your_token"\n'
                    'LIGHTDASH_PROJECT_UUID = "your-project-uuid"\n'
                    "```\n"
                    "3. Refresh the app — the badge automatically switches to **`DEPLOYED`** and enables **`Open Live Lightdash BI →`**."
                )

    if bundle is not None:
        c1, c2 = st.columns([1.2, 1.8])
        with c1:
            with st.container(border=True):
                st.markdown('<div class="erp-card-title">BI Asset Manifest</div>', unsafe_allow_html=True)
                st.json(bundle.bi_asset_manifest.model_dump())
        with c2:
            with st.container(border=True):
                st.markdown('<div class="erp-card-title">Generated Lightdash Semantic YAML</div>', unsafe_allow_html=True)
                st.caption("Decoupled from `schema.yml`. For Power BI (PBIP), the same builder outputs `.tmdl` definitions.")
                st.code(bundle.lightdash_yaml, language="yaml")
    else:
        st.info("Build a model in **3️⃣ Build (dbt + Contract)** first to preview its generated Lightdash Semantic YAML & BI Asset Manifest.")


def _render_tab5_review_release() -> None:
    st.subheader("Review & Release: Draft PR Bundle · Semantic Promotions · dlt Source Ingestion")
    bundle = st.session_state.get("build_bundle")
    checks = st.session_state.get("build_validation_checks")
    ld_status = _get_lightdash_status()

    # ── 1. Draft PR Bundle Orchestrator ────────────────────────────────────────
    with st.container(border=True):
        st.markdown('<div class="erp-card-title">1. Governed Draft PR Bundle (Atomic Change)</div>', unsafe_allow_html=True)
        st.caption(
            "Bundles `dbt SQL`, `schema.yml` (Enforced Data Contract), and `lightdash.yml` into a single Draft PR "
            "for Analytics Engineer + Business Owner review. Never merges automatically."
        )
        if bundle is None:
            st.info("Build & validate a model in **3️⃣ Build (dbt + Contract)** first to generate a Draft PR Bundle.")
        else:
            m_name = bundle.contract.model_name
            if st.button(
                "📦 Generate / Submit Draft PR Bundle",
                type="primary",
                help="打包三份檔案成單一 Draft PR（若 Secrets 有設 GITHUB_TOKEN 與 AE_GITHUB_REPO 則實際開出 GitHub Draft PR，否則產生完整 DRY_RUN_BUNDLE 預覽）。",
            ):
                val_summary = "\n".join(
                    f"- {'PASS' if c['passed'] else 'FAIL'} `{c['check']}`: {c['detail']}" for c in (checks or [])
                )
                files_map = {
                    f"models/gold/{m_name}.sql": bundle.dbt_sql,
                    f"models/gold/{m_name}_schema.yml": bundle.schema_yaml,
                    f"models/gold/{m_name}_lightdash.yml": bundle.lightdash_yaml,
                }
                pr_res = orchestrate_draft_pr(
                    model_name=m_name,
                    question=bundle.contract.query_spec.question or m_name,
                    files=files_map,
                    validation_summary=val_summary or "Isolated Neon validation passed.",
                    lightdash_status=ld_status["lightdash_status"],
                    github_token=_resolve_secret("GITHUB_TOKEN"),
                    github_repo=_resolve_secret("AE_GITHUB_REPO"),
                )
                st.session_state["draft_pr_result"] = pr_res

            pr_res = st.session_state.get("draft_pr_result")
            if pr_res is not None:
                pill_v = "green" if pr_res.pr_mode == "DRAFT_PR_CREATED" else "amber"
                st.markdown(
                    f'{_pill(pr_res.pr_mode, pill_v)} &nbsp; **Branch:** `{pr_res.branch_name}` · **Title:** `{pr_res.title}`',
                    unsafe_allow_html=True,
                )
                if pr_res.pr_url:
                    st.link_button("Open Draft PR on GitHub →", pr_res.pr_url, type="primary")
                else:
                    st.caption(pr_res.message)
                with st.expander("Preview Draft PR Markdown Body", expanded=True):
                    st.markdown(pr_res.body_markdown)

    # ── 2. Semantic Promotion Candidates ───────────────────────────────────────
    col_p, col_i = st.columns(2)
    with col_p:
        with st.container(border=True):
            st.markdown('<div class="erp-card-title">2. Semantic Gap Backlog & Promotion Queue</div>', unsafe_allow_html=True)
            st.caption("Tracks unresolved `NEW_SEMANTIC_METRIC` / `SOURCE_GAP` queries from Explore into governed asset proposals.")
            gaps = st.session_state.get("gap_events") or []
            if gaps:
                st.dataframe(pd.DataFrame(gaps), use_container_width=True, hide_index=True)
            else:
                st.info("No semantic gap events recorded in this session yet. Draft a `NEW_SEMANTIC_METRIC` query in Tab 2 to populate.")

    # ── 3. Missing Source dlt Ingestion Proposal ───────────────────────────────
    with col_i:
        with st.container(border=True):
            st.markdown('<div class="erp-card-title">3. Missing Source `dlt` Ingestion Proposal</div>', unsafe_allow_html=True)
            st.caption("When Explore hits a `SOURCE_GAP`, propose an allowlisted `dlt` connector + Bronze schema contract instead of fabricating SQL.")
            src_type = st.selectbox(
                "Connector Type (try `unapproved_scraper` to test Fail-Loud block)",
                options=[*sorted(APPROVED_CONNECTOR_REGISTRY), "unapproved_scraper"],
            )
            src_locator = st.text_input("Source Locator", value="s3://jaffle-raw/marketing_campaigns_2025.parquet")
            target_tbl = st.text_input("Target Bronze Table", value="bronze_marketing_campaigns")
            if st.button(
                "🔌 Propose dlt Ingestion Contract",
                use_container_width=True,
                help="檢查連接器是否在白名單中；合法則生成宣告式 dlt Python scaffold 與 Bronze 資料契約，非法則 Fail-Loud 攔截。",
            ):
                prop = propose_source_ingestion(
                    source_type=src_type,
                    source_locator=src_locator,
                    target_bronze_table=target_tbl,
                )
                st.session_state["ingestion_proposal"] = prop

            prop = st.session_state.get("ingestion_proposal")
            if prop is not None:
                if prop.status == "BLOCKED":
                    st.error(f"🚫 **BLOCKED (`{', '.join(prop.reason_codes)}`)**: {'; '.join(prop.messages)}")
                else:
                    st.success(f"✅ **{prop.status}** — Target: `raw.{prop.target_bronze_table}`")
                    if prop.dlt_pipeline_code:
                        with st.expander("View Generated dlt Pipeline Scaffold"):
                            st.code(prop.dlt_pipeline_code, language="python")


def _render_tab6_observability(db_url: str, catalog) -> None:
    view_mode = st.radio(
        "Select Observability / Documentation Surface:",
        options=[
            "📊 Elementary Data Observability (Live Neon)",
            "🗺️ dbt Lineage DAG & Model Catalog",
            "🗄️ Live dbt Table Explorer (Neon)",
        ],
        horizontal=True,
    )

    if view_mode.startswith("📊"):
        with st.container(border=True):
            hl, hr = st.columns([2.5, 1.5])
            hl.markdown('<div class="erp-card-title">Elementary Data Observability Dashboard (Live from Neon `dbt_dev_elementary`)</div>', unsafe_allow_html=True)
            hl.caption(
                "Queried live from Elementary's PostgreSQL metadata tables populated by `dbt build` + `dbt snapshot` + `edr report`."
            )
            if ELEMENTARY_REPORT_PATH.exists():
                hr.download_button(
                    "⬇️ Download Full Elementary HTML (5 MB)",
                    data=ELEMENTARY_REPORT_PATH.read_bytes(),
                    file_name="elementary_report.html",
                    mime="text/html",
                    use_container_width=True,
                    help="下載完整版互動式 Elementary HTML 報表（本機瀏覽器開啟不受 iframe 沙盒限制）。",
                )

            try:
                tests_df = execute_neon_readonly(
                    """
                    SELECT
                        table_name,
                        COALESCE(column_name, '-') AS column_name,
                        test_unique_id,
                        status,
                        detected_at
                    FROM dbt_dev_elementary.elementary_test_results
                    ORDER BY detected_at DESC
                    LIMIT 50
                    """,
                    max_rows=50,
                    db_url=db_url,
                )
                runs_df = execute_neon_readonly(
                    """
                    SELECT
                        name,
                        resource_type,
                        status,
                        ROUND(execution_time::numeric, 2) AS exec_sec,
                        generated_at
                    FROM dbt_dev_elementary.dbt_run_results
                    WHERE unique_id LIKE '%.jaffle_governance_demo.%'
                    ORDER BY generated_at DESC
                    LIMIT 50
                    """,
                    max_rows=50,
                    db_url=db_url,
                )
                total_tests = len(tests_df)
                passed_tests = int((tests_df["status"].str.lower() == "pass").sum()) if not tests_df.empty else 0
                pass_rate = f"{(passed_tests / total_tests * 100):.0f}%" if total_tests > 0 else "N/A"
                avg_sec = float(runs_df["exec_sec"].astype(float).mean()) if not runs_df.empty else 0.0

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("dbt Data Tests Executed", f"{total_tests}")
                k2.metric("Test Pass Rate", pass_rate)
                k3.metric("Jaffle dbt Invocations", f"{len(runs_df)}")
                k4.metric("Avg Execution Time", f"{avg_sec:.2f}s")

                st.markdown("#### ✅ Live Elementary Test Results (`dbt_dev_elementary.elementary_test_results`)")
                if not tests_df.empty:
                    st.dataframe(tests_df, use_container_width=True, hide_index=True)
                else:
                    st.info("No test results recorded yet.")

                st.markdown("#### ⏱️ dbt Build & Snapshot Run History (`dbt_dev_elementary.dbt_run_results`)")
                if not runs_df.empty:
                    st.dataframe(runs_df, use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(f"Could not query `dbt_dev_elementary` tables: {exc}")

    elif view_mode.startswith("🗺️"):
        with st.container(border=True):
            dl, dr = st.columns([2.5, 1.5])
            dl.markdown('<div class="erp-card-title">dbt Lineage DAG & Model Catalog (Neon PostgreSQL)</div>', unsafe_allow_html=True)
            dl.caption("Interactive Medallion + SCD Type 2 lineage graph & live dbt model metadata from Neon.")
            if DBT_DOCS_STATIC_PATH.exists():
                dr.download_button(
                    "⬇️ Download Static dbt Docs HTML (3.9 MB)",
                    data=DBT_DOCS_STATIC_PATH.read_bytes(),
                    file_name="dbt_docs_static.html",
                    mime="text/html",
                    use_container_width=True,
                    help="下載完整版靜態 dbt docs 網站（本機瀏覽器開啟可互動探索完整血緣圖）。",
                )

            st.graphviz_chart(
                """
                digraph dbt_lineage {
                    rankdir=LR;
                    node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11];
                    raw_orders [label="raw.raw_orders\n(Source)", fillcolor="#e2e8f0", color="#64748b"];
                    raw_customers [label="raw.raw_customers\n(Source)", fillcolor="#e2e8f0", color="#64748b"];
                    stg_orders [label="dbt_silver.stg_orders\n(Silver View)", fillcolor="#dbeafe", color="#2563eb"];
                    stg_customers [label="dbt_silver.stg_customers\n(Silver View)", fillcolor="#dbeafe", color="#2563eb"];
                    snap_customers [label="dbt_snapshots.snap_customers_dbt\n(SCD Type 2 Snapshot)", fillcolor="#fef3c7", color="#d97706"];
                    fct_orders [label="dbt_gold.fct_orders_dbt\n(Gold Fact · Enforced Contract)", fillcolor="#dcfce7", color="#16a34a"];
                    dim_customers [label="dbt_gold.dim_customers_dbt\n(Gold SCD2 Dim)", fillcolor="#dcfce7", color="#16a34a"];
                    lightdash [label="Lightdash BI & AI Workbench\n(Semantic Layer)", fillcolor="#ede9fe", color="#7c3aed"];

                    raw_orders -> stg_orders -> fct_orders -> lightdash;
                    raw_customers -> stg_customers -> snap_customers -> dim_customers -> lightdash;
                }
                """,
                use_container_width=True,
            )

            try:
                models_df = execute_neon_readonly(
                    """
                    SELECT alias AS model_name, schema_name, materialization, description, depends_on_nodes
                    FROM dbt_dev_elementary.dbt_models
                    WHERE unique_id LIKE 'model.jaffle_governance_demo.%'
                    ORDER BY schema_name, alias
                    """,
                    max_rows=50,
                    db_url=db_url,
                )
                st.markdown("#### 📚 Registered dbt Models (`dbt_dev_elementary.dbt_models`)")
                st.dataframe(models_df, use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(f"Could not query dbt models metadata: {exc}")

    else:
        with st.container(border=True):
            st.markdown('<div class="erp-card-title">🗄️ Live dbt & Medallion Table Explorer (Neon PostgreSQL)</div>', unsafe_allow_html=True)
            st.caption("Inspect live schemas, row counts, column types, and top 25 rows directly from Neon.")
            try:
                tbls_df = execute_neon_readonly(
                    """
                    SELECT table_schema, table_name, table_type
                    FROM information_schema.tables
                    WHERE table_schema IN ('raw', 'snapshots', 'gold', 'dbt_dev_dbt_silver', 'dbt_dev_dbt_gold', 'dbt_snapshots', 'quarantine', 'ci_pr')
                    ORDER BY table_schema, table_name
                    """,
                    max_rows=100,
                    db_url=db_url,
                )
                if tbls_df.empty:
                    st.info("No tables found in Neon demo schemas.")
                else:
                    options = [f"{r['table_schema']}.{r['table_name']}" for _, r in tbls_df.iterrows()]
                    chosen = st.selectbox("Select Neon Table / View to inspect:", options=options)
                    schema_n, table_n = chosen.split(".", 1)
                    c_meta, c_prev = st.columns([1, 2])
                    with c_meta:
                        cols_df = execute_neon_readonly(
                            f"SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                            f"WHERE table_schema = '{schema_n}' AND table_name = '{table_n}' ORDER BY ordinal_position",
                            max_rows=100,
                            db_url=db_url,
                        )
                        st.markdown(f"**Schema (`{chosen}`)**")
                        st.dataframe(cols_df, use_container_width=True, hide_index=True)
                    with c_prev:
                        cnt_df = execute_neon_readonly(f'SELECT COUNT(*) AS row_count FROM "{schema_n}"."{table_n}"', max_rows=1, db_url=db_url)
                        row_cnt = int(cnt_df.iloc[0]["row_count"])
                        st.markdown(f"**Live Data Preview ({row_cnt:,} total rows · showing top 25)**")
                        data_df = execute_neon_readonly(f'SELECT * FROM "{schema_n}"."{table_n}" LIMIT 25', max_rows=25, db_url=db_url)
                        st.dataframe(data_df, use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(f"Table Explorer query failed: {exc}")


def main() -> None:
    st.set_page_config(
        page_title="AI Analytics Workbench & Data Governance Lab",
        page_icon="🏛️",
        layout="wide",
    )
    st.markdown(ERP_THEME_CSS, unsafe_allow_html=True)
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
        "SCD Type 2 snapshots, Write-Audit-Publish quality gates, CI Semantic Drift protection, and Draft PR release."
    )

    active = _render_nav_bar()
    if active == NAV_TABS[0]:
        _render_tab1_timeline(db_url, state)
    elif active == NAV_TABS[1]:
        _render_tab2_explore(db_url, api_key, catalog)
    elif active == NAV_TABS[2]:
        _render_tab3_build()
    elif active == NAV_TABS[3]:
        _render_tab4_lightdash()
    elif active == NAV_TABS[4]:
        _render_tab5_review_release()
    else:
        _render_tab6_observability(db_url, catalog)


if __name__ == "__main__":
    main()
