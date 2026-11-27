"""Standalone Public Streamlit Demo: AI Analytics Workbench & Data Governance Lab (Neon PostgreSQL).

Matches the ERP AI Analytics Workbench visual layout & button progression (Candidate Governed Query
-> Approve & Send to Security Gate -> Confirm spec_hash -> Execute Read-Only -> Promote to Build)
while adding the interactive Neon PostgreSQL Day 0/5/10/15/20 Governance Lab and live Elementary report.

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

from services.consultant_service import (
    ConsultantQueryOutcome,
    EvidenceQuerySummary,
    answer_consultant_turn,
    draft_candidate_query,
    propose_consultant_turn,
)
from services.dbt_asset_builder import build_dbt_and_lightdash_bundle
from services.duckdb_executor import generate_gemini_structured
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
from services.semantic_gap_detector import classify_sql_semantic_gap
from services.sql_validator import validate_and_reconcile_sql

MAX_LLM_CALLS_PER_SESSION = 20
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
ELEMENTARY_REPORT_PATH = ROOT / "examples" / "demo_artifacts" / "elementary_report.html"


ERP_THEME_CSS = """
<style>
/* Clean ERP-style light canvas & card polish */
.stApp {
    background-color: #f8fafc;
}
/* Override default red primary button to ERP Royal Blue (#2563EB) */
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
/* Tab underline in ERP Blue */
button[data-baseweb="tab"][aria-selected="true"] {
    color: #2563eb !important;
    font-weight: 600 !important;
}
div[data-baseweb="tab-highlight"] {
    background-color: #2563eb !important;
}
/* Styled status pills matching ERP React UI */
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
        "llm_calls": 0,
        "chat_messages": [],
        "evidence_history": [],
        "candidate_bundle": None,
        "sent_to_gate": False,
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
                help="Optional for deterministic Labs (Tab 1, AST Gate, Tab 3, Tab 4). Required for live AI chat in Tab 2.",
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
            st.caption(
                "Offending rows are quarantined instead of silently deduplicated (`ROW_NUMBER() = 1`) or published to Gold."
            )
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
    st.subheader("Explore → Candidate Governed Query → SQLGlot AST Security Gate")
    st.caption(
        "Follows the exact ERP workflow: Free Consultation → Draft Governed Query → Approve & Send to Security Gate → "
        "Validate & Confirm `spec_hash` → Execute Read-Only (Neon) → Promote to Build."
    )

    # ── Step 1: Consultation Card (ERP layout) ─────────────────────────────────
    with st.container(border=True):
        h_left, h_right = st.columns([3, 1])
        with h_left:
            st.markdown('<div class="erp-card-title">1. Business Consultation & Intent Framing</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="erp-card-sub">Align on business hypotheses before drafting SQL. Pick a preset or ask your own question.</div>',
                unsafe_allow_html=True,
            )
        with h_right:
            if st.button(
                "✨ New Conversation",
                use_container_width=True,
                help="清空目前對話、候選查詢與 Security Gate 狀態，開始全新分析。",
            ):
                st.session_state["chat_messages"] = []
                st.session_state["evidence_history"] = []
                st.session_state["candidate_bundle"] = None
                st.session_state["sent_to_gate"] = False
                st.session_state["editor_sql"] = ""
                st.session_state["active_spec"] = None
                st.session_state["last_validation"] = None
                st.session_state["last_preview_df"] = None
                st.session_state["last_gap"] = None
                st.rerun()

        p1, p2, p3 = st.columns(3)
        preset_question = None
        if p1.button(
            "💡 Preset 1: Customer Value (NEW_SEMANTIC_METRIC)",
            use_container_width=True,
            help="發問客單價與最大單筆金額集中度，觸發 AI 假設分析與 NEW_SEMANTIC_METRIC 語意缺口。",
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

        user_q = st.text_input(
            "Ask a business question (or follow-up refinement):",
            value=preset_question or "",
            placeholder="e.g., Is our revenue driven by high average order value or broad order volume?",
        )

        a1, a2 = st.columns(2)
        if a1.button(
            "💬 Ask AI Consultant",
            type="primary",
            use_container_width=True,
            help="執行兩段式證據諮詢（跟 ERP 相同）：先由顧問提議唯讀 SQL → 實查 Neon 拿真實數據 → 結合數據給出假設並於下方展示 Query Evidence。",
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

        if a2.button(
            "📝 Draft Governed Query",
            use_container_width=True,
            help="根據對話與證據查詢起草候選 SQL、自動判斷語意缺口（REUSE_EXISTING / NEW_SEMANTIC_METRIC）並產生 Candidate Governed Query 審核卡片。",
        ):
            if not st.session_state["chat_messages"] and not user_q.strip():
                st.warning("Ask a business question first before drafting a governed query.")
            else:
                try:
                    structured_fn = _make_structured_llm_callable(api_key)
                    if not st.session_state["chat_messages"] and user_q.strip():
                        st.session_state["chat_messages"].append({"role": "user", "content": user_q.strip()})
                    history = [
                        ClarificationTurn(role=m["role"], content=m["content"])
                        for m in st.session_state["chat_messages"]
                    ]
                    with st.spinner("Synthesizing Candidate Governed Query & running AST check..."):
                        cand = draft_candidate_query(
                            history=history,
                            catalog=catalog,
                            evidence_queries=st.session_state.get("evidence_history") or None,
                            dialect="postgres",
                            tenant_id=None,
                            structured_llm_fn=structured_fn,
                        )
                    st.session_state["candidate_bundle"] = cand
                    st.session_state["sent_to_gate"] = False
                    if cand.sql:
                        st.session_state["editor_sql"] = cand.sql
                    base_spec = cand.query_spec or QuerySpec(
                        spec_id=f"qs_demo_v{cand.version}",
                        question=st.session_state["chat_messages"][0]["content"],
                        dialect="postgres",
                        output=OutputSpec(max_rows=50, certification="VALIDATED_EXPLORATORY"),
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
                        st.session_state["active_spec"] = val.reconciled_spec or base_spec
                    else:
                        st.session_state["last_validation"] = cand.validation
                        st.session_state["active_spec"] = base_spec
                    st.session_state["last_gap"] = cand.gap
                    st.rerun()
                except Exception as exc:
                    st.error(f"Fail-Loud Halt: {exc}")

    # ── Step 2: Candidate Governed Query Card (ERP signature card!) ────────────
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
                help="先不送進 Security Gate，繼續在上方聊天框微調需求。",
            ):
                st.info("Type your follow-up refinement in the consultation input box above and click Draft Governed Query again.")

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

    # ── Step 3: Semantic Gap + Advanced SQL Editor & Security Gate ─────────────
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
                '<div class="erp-card-title">Advanced SQL Editor & Security Gate (AST / Fanout)</div>',
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

            g1, g2, g3 = st.columns(3)
            if g1.button(
                "🛡️ Validate & AST Reconcile",
                use_container_width=True,
                help="用 SQLGlot 解析語法樹：驗證唯讀 SELECT、授權白名單、1:N Join Fanout，並把修改後的 SQL 反向同步回 QuerySpec 與 spec_hash。",
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
                st.rerun()

            if g2.button(
                "✅ Confirm spec_hash",
                use_container_width=True,
                help="Human-in-the-Loop 確認：將目前驗證通過的語意指紋 spec_hash 鎖定核准，允許唯讀執行。",
            ):
                if spec is None or val is None or val.status == "BLOCKED":
                    st.error("Run Validate & AST Reconcile first (must not be BLOCKED).")
                else:
                    st.session_state["active_spec"] = spec.confirm_intent(confirmed_by="analytics_engineer")
                    st.rerun()

            if g3.button(
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
                    except Exception as exc:
                        st.error(f"Neon Read-Only Execution Failed: {exc}")

            val = st.session_state.get("last_validation")
            if val is not None:
                if val.status == "BLOCKED":
                    st.error(f"**Validation Status: BLOCKED (`{', '.join(val.reason_codes)}`)** — {'; '.join(val.messages)}")
                elif val.status == "REQUIRES_AE_REVIEW":
                    st.warning(f"**Validation Status: REQUIRES_AE_REVIEW (`{', '.join(val.reason_codes)}`)** — {'; '.join(val.messages)}")
                else:
                    st.success("**Validation Status: VALID** — Passed read-only, allowlist, and 1:N join fanout AST gates.")

        preview_df = st.session_state.get("last_preview_df")
        if isinstance(preview_df, pd.DataFrame) and not preview_df.empty:
            with st.container(border=True):
                pl, pr = st.columns([3, 1])
                pl.caption(f"Provenance: `neon_postgres_readonly` (postgres) · **{len(preview_df)} rows**")
                pr.markdown(
                    f'<div class="erp-pill-row">{_pill(f"Sandbox Verified ({len(preview_df)} rows)", "green")}</div>',
                    unsafe_allow_html=True,
                )
                st.dataframe(preview_df, use_container_width=True, hide_index=True)
                st.info("👉 Ready to promote? Click **3️⃣ Promote to Build (dbt + Contract + BI)** tab above to synthesize governed dbt & Lightdash artifacts.")


def _render_tab3_build() -> None:
    spec: QuerySpec | None = st.session_state.get("active_spec")
    preview_df: pd.DataFrame | None = st.session_state.get("last_preview_df")
    cand = st.session_state.get("candidate_bundle")
    gap = st.session_state.get("last_gap")

    if spec is None or preview_df is None or preview_df.empty:
        st.info(
            "Complete **Tab 2: Explore & AST Security Gate** (Draft → Approve → Confirm `spec_hash` → Execute Read-Only) "
            "first, then come here to promote the verified query into governed dbt & BI artifacts."
        )
        return

    # ── ERP Linked Explore Query Summary Card ──────────────────────────────────
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

    # ── ERP Synthesize Card ────────────────────────────────────────────────────
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
            help="同步產出 dbt Gold SQL（自動轉 {{ ref(...) }}）、Enforced Data Contract (schema.yml)、Lightdash Semantic YAML，並對沙盒資料執行 not_null 與主鍵唯一性契約驗證。",
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
            st.dataframe(pd.DataFrame(checks), use_container_width=True, hide_index=True)

        col1, col2 = st.columns(2)
        with col1:
            with st.container(border=True):
                st.markdown('<div class="erp-card-title">dbt Model (.sql)</div>', unsafe_allow_html=True)
                st.code(bundle.dbt_sql, language="sql")
        with col2:
            with st.container(border=True):
                st.markdown('<div class="erp-card-title">Enforced Data Contract (schema.yml)</div>', unsafe_allow_html=True)
                st.code(bundle.schema_yaml, language="yaml")

        with st.container(border=True):
            bl, br = st.columns([3, 1])
            bl.markdown('<div class="erp-card-title">Lightdash Semantic Status & BI Asset Manifest</div>', unsafe_allow_html=True)
            bl.caption("Bundled into a single Draft PR alongside dbt SQL & Data Contract. For Power BI, the same builder outputs PBIP TMDL.")
            br.markdown(f'<div class="erp-pill-row">{_pill("STAGING_LOCAL_ONLY", "amber")}</div>', unsafe_allow_html=True)
            st.code(bundle.lightdash_yaml, language="yaml")


def _render_tab4_elementary_catalog(catalog) -> None:
    with st.container(border=True):
        st.markdown('<div class="erp-card-title">Elementary Data Observability Report (Generated Live from Neon PostgreSQL)</div>', unsafe_allow_html=True)
        if ELEMENTARY_REPORT_PATH.exists():
            st.caption(
                f"Real interactive HTML report (`{ELEMENTARY_REPORT_PATH.name}`, "
                f"{ELEMENTARY_REPORT_PATH.stat().st_size / 1024:.0f} KB) built via `dbt build` + `dbt snapshot` + `edr report` against Neon."
            )
            html_content = ELEMENTARY_REPORT_PATH.read_text(encoding="utf-8", errors="ignore")
            components.html(html_content, height=820, scrolling=True)
        else:
            st.warning(f"Elementary report not found at `{ELEMENTARY_REPORT_PATH}`.")

    c1, c2 = st.columns(2)
    with c1:
        with st.container(border=True):
            st.markdown('<div class="erp-card-title">Certified Metrics (Single Source of Truth)</div>', unsafe_allow_html=True)
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
        with st.container(border=True):
            st.markdown('<div class="erp-card-title">Governed Gold Relations & Grains</div>', unsafe_allow_html=True)
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
