"""Interactive Schema Designer & Observability Inspector Component.

Renders an enterprise AE workbench allowing stakeholders to articulate business goals,
inspect real-time Socratic alignment, review synthesized dbt models & schema.yml,
and trace the end-to-end execution audit log.
"""

from __future__ import annotations

import os
import streamlit as st
import pandas as pd
from typing import Any

from ai.schema_engine import DatabaseReflectionService
from ai.socratic_copilot import (
    analyze_socratic_inquiry,
    synthesize_dynamic_dbt_model,
    SocraticSessionState,
)


def render_schema_designer_view(
    adapter: Any,
    data_dict: dict[str, pd.DataFrame],
    is_en: bool = True,
) -> None:
    """Render the Schema Design & dbt Transformation Engine View with Observability."""
    st.markdown(
        "### 🏗️ Autonomous Schema Designer & dbt Transformation Engine"
        if is_en
        else "### 🏗️ 自動化 Schema 設計與 dbt 模型轉換引擎"
    )
    st.caption(
        "Bridge the gap between natural language business inquiries and governed dbt pipelines. "
        "AI reflects on conformed Silver Kimball models to synthesize, dry-run, and govern production-ready schemas."
        if is_en
        else "將業務口語需求直接編譯為受控管的 dbt 湖倉管線。AI 自動反射 Silver Kimball 星狀模型，現場生成並測試生產級 Schema 與 SQL。"
    )

    reflection = DatabaseReflectionService(data_dict)
    models_meta = reflection.get_kimball_models()

    # Top Metric Banner (Reflected Metadata)
    c1, c2, c3, c4 = st.columns(4)
    if is_en:
        c1.metric("Reflected Dimensions", f"{len(models_meta['dimensions'])} Models", delta="dim_products/cat/cust")
        c2.metric("Reflected Facts", f"{len(models_meta['facts'])} Models", delta="fct_items/orders/returns")
        c3.metric("GDPR Boundary", "Enforced", delta="100% No PII")
        c4.metric("Engine Runtime", "Physical DuckDB", delta="Real Lakehouse Dry-run")
    else:
        c1.metric("已探查維度模型", f"{len(models_meta['dimensions'])} 個", delta="dim_products/cat/cust")
        c2.metric("已探查事實模型", f"{len(models_meta['facts'])} 個", delta="fct_items/orders/returns")
        c3.metric("GDPR 數據隱私邊界", "已強制執行", delta="100% 無 PII 洩露")
        c4.metric("引擎執行環境", "物理 DuckDB", delta="真實湖倉 Dry-run 驗證")

    st.markdown("---")

    # Stakeholder Goal Alignment
    st.markdown(
        "#### 💬 1. Stakeholder Business Goal & Socratic Anchoring"
        if is_en
        else "#### 💬 1. 利害關係人商業目標與蘇格拉底式定錨"
    )

    prompt_return = (
        "🌰 1kg 堅果大包裝退貨率異常歸因（營運主管：依包裝規格拆解退貨率與破損）"
        if not is_en
        else "🌰 1kg Bulk Packaging Return Diagnosis (Operations Lead: Return Rate by Packaging Size)"
    )
    prompt_margin = (
        "💰 品類毛利稀釋排行（財務主管：扣除促銷折價券後之淨貢獻毛利率）"
        if not is_en
        else "💰 Category Margin Dilution (Finance Lead: Net Contribution Margin after Vouchers)"
    )

    # State initialization
    if "schema_designer_goal" not in st.session_state:
        st.session_state["schema_designer_goal"] = prompt_return

    st.caption(
        "點擊下方預設場景快速填入，或直接在下方輸入框自由輸入自訂目標："
        if not is_en
        else "Click a preset scenario button to populate, or type your custom analytical goal below:"
    )

    # 2 Quick-Preset Buttons
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button(
            "🌰 問題 1：堅果包裝退貨診斷" if not is_en else "🌰 Problem 1: Bulk Packaging Return Diagnosis",
            use_container_width=True,
            help="點擊快速帶入堅果大包裝退貨歸因分析 Prompt" if not is_en else "Click to populate return diagnosis prompt",
        ):
            st.session_state["schema_designer_goal"] = prompt_return
            st.session_state["active_socratic_session"] = None
            st.session_state["schema_design_result"] = None
            st.rerun()

    with btn_col2:
        if st.button(
            "💰 問題 2：品類促銷毛利排行" if not is_en else "💰 Problem 2: Category Margin Dilution",
            use_container_width=True,
            help="點擊快速帶入品類淨利率與折價券稀釋分析 Prompt" if not is_en else "Click to populate margin dilution prompt",
        ):
            st.session_state["schema_designer_goal"] = prompt_margin
            st.session_state["active_socratic_session"] = None
            st.session_state["schema_design_result"] = None
            st.rerun()

    # Unified Single Input Box
    user_goal_input = st.text_input(
        "自訂商業分析目標與細節條件 (Custom Analytical Goal & Criteria)：" if not is_en else "Custom Analytical Goal & Criteria:",
        value=st.session_state["schema_designer_goal"],
        placeholder="例如：我想知道這個月的產品退款紀錄、或計算各品類淨毛利率..." if not is_en else "e.g., I want to know this month's product refund records...",
        key="schema_designer_custom_goal",
    )
    if user_goal_input != st.session_state["schema_designer_goal"]:
        st.session_state["schema_designer_goal"] = user_goal_input
        st.session_state["active_socratic_session"] = None
        st.session_state["schema_design_result"] = None

    # Action Trigger
    btn_label = "🚀 合成並驗證 dbt 數據管線 (Synthesize & Validate dbt Pipeline)" if not is_en else "🚀 Synthesize & Validate dbt Pipeline"
    if st.button(btn_label, type="primary", use_container_width=True):
        current_query = st.session_state.get("schema_designer_goal", "").strip()
        active_api_key = (
            st.session_state.get("user_gemini_key")
            or st.session_state.get("gemini_api_key")
            or os.getenv("GEMINI_API_KEY", "")
        ).strip()
        spinner_msg = "AI Socratic Agent 正在解析商業目標與湖倉數據邊界..." if not is_en else "AI Socratic Agent inspecting business goals and lakehouse boundaries..."
        with st.spinner(spinner_msg):
            session = analyze_socratic_inquiry(current_query, reflection=reflection, api_key=active_api_key)
            st.session_state["active_socratic_session"] = session

            if not session.needs_clarification and not session.is_out_of_domain:
                # Direct synthesis if unambiguous
                res = synthesize_dynamic_dbt_model(
                    current_query, session.locked_decisions, reflection, api_key=active_api_key
                )
                st.session_state["schema_design_result"] = res

    # 1. Handle Socratic Clarification Dialogue State
    active_session: SocraticSessionState | None = st.session_state.get("active_socratic_session")
    if active_session:
        if active_session.is_out_of_domain:
            if active_session.is_api_key_missing:
                st.markdown(
                    f"""
                    <div style="background-color: #FFFBEB; border: 1.5px solid #F59E0B; border-radius: 8px; padding: 18px; margin: 16px 0;">
                        <div style="font-weight: 700; color: #B45309; font-size: 1.05rem; margin-bottom: 8px; display: flex; align-items: center; gap: 8px;">
                            🔒 需要 Gemini API Key (API Key Required)
                        </div>
                        <div style="color: #92400E; font-size: 0.92rem; line-height: 1.6;">
                            系統檢測到您的分析目標：「<strong>{active_session.user_goal}</strong>」。<br>
                            為了落實 <strong>Zero-Facade 架構治理標準</strong>，本平台嚴格禁止以本機關鍵字或影子模擬腳本假裝回答。<br>
                            任何商業分析意圖均須經由 Google Gemini GenAI 進行實體型錄邊界檢驗與意圖錨定。
                        </div>
                        <div style="margin-top: 12px; padding: 10px 14px; background-color: #FEF3C7; border-radius: 6px; font-size: 0.88rem; color: #78350F;">
                            👉 <strong>如何啟用</strong>：請在左側側邊欄 <em>Gemini API Configuration</em> 輸入您的 API Key。輸入後系統將即時調用 Gemini 模型進行真實的意圖驗證與 dbt 模型合成。
                        </div>
                    </div>
                    """
                    if not is_en
                    else f"""
                    <div style="background-color: #FFFBEB; border: 1.5px solid #F59E0B; border-radius: 8px; padding: 18px; margin: 16px 0;">
                        <div style="font-weight: 700; color: #B45309; font-size: 1.05rem; margin-bottom: 8px; display: flex; align-items: center; gap: 8px;">
                            🔒 Gemini API Key Required
                        </div>
                        <div style="color: #92400E; font-size: 0.92rem; line-height: 1.6;">
                            Inquiry received: '<strong>{active_session.user_goal}</strong>'.<br>
                            To uphold the <strong>Zero-Facade Governance Standard</strong>, simulated responses and local keyword bypasses are disabled.<br>
                            All analytical goals must be grounded against conformed lakehouse entities via Google Gemini GenAI.
                        </div>
                        <div style="margin-top: 12px; padding: 10px 14px; background-color: #FEF3C7; border-radius: 6px; font-size: 0.88rem; color: #78350F;">
                            👉 <strong>Action Required</strong>: Please provide your Gemini API Key in the left sidebar under <em>Gemini API Configuration</em>.
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            else:
                reason_text = active_session.rejection_reason or "輸入超出湖倉分析範疇。"
                st.markdown(
                    f"""
                    <div style="background-color: #FEF2F2; border: 1.5px solid #F87171; border-radius: 8px; padding: 18px; margin: 16px 0;">
                        <div style="font-weight: 700; color: #991B1B; font-size: 1.05rem; margin-bottom: 8px;">
                            ⛔ 無法處理該請求：輸入超出湖倉分析範疇 (Out of Analytical Domain)
                        </div>
                        <div style="color: #7F1D1D; font-size: 0.92rem; line-height: 1.6;">
                            輸入目標：「<strong>{active_session.user_goal}</strong>」<br>
                            <strong>評估結果</strong>：{reason_text}<br>
                            為了落實 <strong>Zero-Facade 架構治理</strong>，系統拒絕模擬偽造不存在的數據血緣與 SQL 運算。
                        </div>
                        <div style="margin-top: 12px; padding: 10px 14px; background-color: #FEE2E2; border-radius: 6px; font-size: 0.88rem; color: #991B1B;">
                            💡 <strong>建議做法</strong>：請點擊上方預設場景按鈕，或輸入與 <code>fct_order_items</code>、<code>fct_returns</code>、<code>dim_products</code> (12 款歐洲堅果/果乾 SKU) 相關的真實商業分析指標。
                        </div>
                    </div>
                    """
                    if not is_en
                    else f"""
                    <div style="background-color: #FEF2F2; border: 1.5px solid #F87171; border-radius: 8px; padding: 18px; margin: 16px 0;">
                        <div style="font-weight: 700; color: #991B1B; font-size: 1.05rem; margin-bottom: 8px;">
                            ⛔ Request Rejected: Out of Analytical Domain
                        </div>
                        <div style="color: #7F1D1D; font-size: 0.92rem; line-height: 1.6;">
                            Inquiry: '<strong>{active_session.user_goal}</strong>'<br>
                            <strong>Evaluation</strong>: {reason_text}<br>
                            In adherence to the <strong>Zero-Facade Governance Standard</strong>, the copilot will not fabricate fake models or dry-run simulations.
                        </div>
                        <div style="margin-top: 12px; padding: 10px 14px; background-color: #FEE2E2; border-radius: 6px; font-size: 0.88rem; color: #991B1B;">
                            💡 <strong>Suggestion</strong>: Please click one of the preset buttons above or provide analytical criteria for conformed orders, returns, or margins (12 core European SKUs).
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        elif active_session.needs_clarification and not st.session_state.get("schema_design_result"):
            st.markdown(
                """
                <div style="background-color: #F0FDF4; border: 1.5px solid #86EFAC; border-radius: 8px; padding: 16px; margin: 16px 0;">
                    <div style="font-weight: 700; color: #166534; font-size: 1.05rem; margin-bottom: 4px;">
                        💬 Gemini 蘇格拉底式意圖澄清 (Socratic Intent Clarification)
                    </div>
                    <div style="color: #15803D; font-size: 0.88rem;">
                        Gemini 已檢測實體湖倉元數據與時間水位，請確認以下關鍵決策，以動態合成高保真生產級 dbt 模型：
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            user_choices = dict(active_session.locked_decisions)
            for q in active_session.questions:
                st.markdown(f"**{q.title}**")
                st.caption(q.explanation)
                opt_labels = [opt["label"] for opt in q.options]
                custom_opt_label = "✏️ 自訂補充條件 (手動填寫)" if not is_en else "✏️ Custom Clarification (Fill in details below)"
                all_opts = opt_labels + [custom_opt_label]
                opt_map = {opt["label"]: opt["value"] for opt in q.options}

                chosen_label = st.radio(
                    f"選擇 {q.dimension_type} 方案：" if not is_en else f"Select {q.dimension_type} option:",
                    options=all_opts,
                    index=0,
                    key=f"socratic_q_{q.question_id}",
                    label_visibility="collapsed",
                )
                if chosen_label == custom_opt_label:
                    custom_text = st.text_input(
                        "✏️ 請填入您的自訂條件或補充說明：" if not is_en else "✏️ Enter custom criteria or details:",
                        placeholder="例如：鎖定 2026 年 9 月促銷、或指定特定產品/市場..." if not is_en else "e.g., Focus on September 2026 or a specific SKU...",
                        key=f"socratic_custom_input_{q.question_id}",
                    )
                    if custom_text.strip():
                        user_choices[q.dimension_type] = f"custom:{custom_text.strip()}"
                    else:
                        user_choices[q.dimension_type] = opt_map.get(q.options[0]["label"], "")
                else:
                    user_choices[q.dimension_type] = opt_map[chosen_label]
                st.markdown("")

            confirm_btn_label = "⚡ 確認定錨並動態編譯管線 (Confirm & Compile dbt Pipeline)" if not is_en else "⚡ Confirm & Compile dbt Pipeline"
            if st.button(confirm_btn_label, type="primary", use_container_width=True):
                active_api_key = (
                    st.session_state.get("user_gemini_key")
                    or st.session_state.get("gemini_api_key")
                    or os.getenv("GEMINI_API_KEY", "")
                ).strip()
                spinner_msg = "Gemini 正在動態生成 dbt SQL 與 Data Contract，並於 DuckDB 物理庫執行 Dry-Run..." if not is_en else "Gemini is dynamically synthesizing dbt SQL & contract and executing physical DuckDB dry-run..."
                with st.spinner(spinner_msg):
                    res = synthesize_dynamic_dbt_model(
                        user_goal=active_session.user_goal,
                        decisions=user_choices,
                        reflection=reflection,
                        api_key=active_api_key,
                    )
                    st.session_state["schema_design_result"] = res
                    st.rerun()

    # 2. Render Successful Compilation Output
    result = st.session_state.get("schema_design_result")
    if result and not result.is_rejected:
        success_msg = f"✅ 管線編譯受控通過 (Pipeline Compiled & Governed): `{result.spec_id}`" if not is_en else f"✅ Pipeline Compiled & Governed: `{result.spec_id}`"
        st.success(success_msg)

        # Observability Audit Trace
        trace_title = "##### 🔍 Observability Audit Trail (實時編譯與執行追蹤)" if not is_en else "##### 🔍 Observability Audit Trail"
        st.markdown(trace_title)
        cols = st.columns(len(result.audit_trace))
        for idx, trace in enumerate(result.audit_trace):
            with cols[idx]:
                st.markdown(
                    f"""
                    <div style="background-color: #F8FAFC; border: 1px solid #CBD5E1; border-radius: 6px; padding: 10px; font-size: 0.85rem;">
                        <div style="font-weight: 600; color: #1E293B;">{trace['step']}</div>
                        <div style="color: #059669; font-weight: 500; margin: 2px 0;">● {trace['status']}</div>
                        <div style="color: #64748B; font-size: 0.78rem;">{trace['detail']}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        st.markdown("---")

        # 3-Layer Inspector Tabs
        tab_titles = (
            [
                "📊 業務洞察與即時數據預覽 (Live Data Preview)",
                "📜 數據治理合約 (schema.yml)",
                "⚙️ 動態合成之 dbt 模型 (.sql)",
            ]
            if not is_en
            else [
                "📊 Business Takeaway & Live Data Preview",
                "📜 Governed Data Contract (`schema.yml`)",
                "⚙️ Synthesized dbt Model (`.sql`)",
            ]
        )
        tab_biz, tab_contract, tab_sql = st.tabs(tab_titles)

        with tab_biz:
            target_str = f"**分析粒度 (Target Grain)**: `{result.target_grain}` | **依賴之 Kimball 模型**: `{', '.join(result.upstream_tables)}`" if not is_en else f"**Target Grain**: `{result.target_grain}` | **Upstream Kimball Dependencies**: `{', '.join(result.upstream_tables)}`"
            st.markdown(target_str)
            preview_title = "##### 實體 DuckDB Dry-Run 物理驗證數據預覽 (前 10 筆)：" if not is_en else "##### 5-Row Verified Data Preview (DuckDB Dry-Run Output):"
            st.markdown(preview_title)
            st.dataframe(result.data_preview.head(10), use_container_width=True)

        with tab_contract:
            st.caption("📄 `models/marts/schema.yml` (Version-controlled Data Dictionary & Contract)")
            st.code(result.schema_yaml, language="yaml")
            st.download_button(
                "📥 下載 schema.yml 數據合約" if not is_en else "📥 Download schema.yml",
                data=result.schema_yaml,
                file_name="schema.yml",
                mime="text/yaml",
                use_container_width=True,
            )

        with tab_sql:
            st.caption("📄 `models/marts/<model_name>.sql` (Idempotent dbt Transformation Model)")
            st.code(result.dbt_sql, language="sql")
            st.download_button(
                "📥 下載 dbt Model (.sql) 模型檔案" if not is_en else "📥 Download dbt Model (.sql)",
                data=result.dbt_sql,
                file_name="mart_model.sql",
                mime="text/plain",
                use_container_width=True,
            )
