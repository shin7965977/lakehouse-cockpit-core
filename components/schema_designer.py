"""Interactive Schema Designer & Observability Inspector Component.

Renders an enterprise AE workbench allowing stakeholders to articulate business goals,
inspect real-time Socratic alignment, review synthesized dbt models & schema.yml,
and trace the end-to-end execution audit log.
"""

from __future__ import annotations

import os
import textwrap
import streamlit as st
import pandas as pd
from typing import Any

from ai.schema_engine import DatabaseReflectionService
from ai.socratic_copilot import (
    analyze_socratic_inquiry,
    synthesize_dynamic_dbt_model,
    chat_socratic_turn,
    summarize_socratic_requirements,
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
        st.session_state["socratic_chat_history"] = []
        st.session_state["socratic_summary_memo"] = None
        st.session_state["socratic_signed_off"] = False

    # Action Trigger
    btn_label = "🚀 合成並驗證 dbt 數據管線 (Synthesize & Validate dbt Pipeline)" if not is_en else "🚀 Synthesize & Validate dbt Pipeline"
    if st.button(btn_label, type="primary", use_container_width=True):
        current_query = (user_goal_input or st.session_state.get("schema_designer_goal", "")).strip()
        st.session_state["schema_designer_goal"] = current_query
        st.session_state["active_socratic_session"] = None
        st.session_state["schema_design_result"] = None
        st.session_state["socratic_chat_history"] = []
        st.session_state["socratic_summary_memo"] = None
        st.session_state["socratic_signed_off"] = False

        active_api_key = (
            st.session_state.get("user_gemini_key")
            or st.session_state.get("gemini_api_key")
            or os.getenv("GEMINI_API_KEY", "")
        ).strip()
        spinner_msg = "AI Socratic Agent 正在解析商業目標與湖倉數據邊界..." if not is_en else "AI Socratic Agent inspecting business goals and lakehouse boundaries..."
        with st.spinner(spinner_msg):
            session = analyze_socratic_inquiry(current_query, reflection=reflection, api_key=active_api_key, is_en=is_en)
            st.session_state["active_socratic_session"] = session

            if not session.needs_clarification and not session.is_out_of_domain:
                # Direct synthesis if unambiguous
                res = synthesize_dynamic_dbt_model(
                    current_query, session.locked_decisions, reflection, api_key=active_api_key
                )
                st.session_state["schema_design_result"] = res
                st.rerun()

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

        elif active_session.needs_clarification:
            st.markdown(
                """
                <div style="background-color: #F0FDF4; border: 1.5px solid #86EFAC; border-radius: 8px; padding: 16px; margin: 16px 0;">
                    <div style="font-weight: 700; color: #166534; font-size: 1.05rem; margin-bottom: 4px;">
                        💬 Gemini Socratic Intent Clarification
                    </div>
                    <div style="color: #15803D; font-size: 0.88rem;">
                        Gemini has inspected lakehouse metadata and physical watermarks. Please confirm key decisions to dynamically synthesize production-ready dbt models:
                    </div>
                </div>
                """
                if is_en
                else """
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
                opt_map = {opt["label"]: opt["value"] for opt in q.options}

                chosen_label = st.radio(
                    f"選擇 {q.dimension_type} 方案：" if not is_en else f"Select {q.dimension_type} option:",
                    options=opt_labels,
                    index=0,
                    key=f"socratic_q_{q.question_id}",
                    label_visibility="collapsed",
                )
                user_choices[q.dimension_type] = opt_map.get(chosen_label, q.options[0]["value"])
                st.markdown("")

            st.markdown("---")

            # -------------------------------------------------------------
            # Persistent Multi-turn Conversational Chat Window
            # -------------------------------------------------------------
            if "socratic_chat_history" not in st.session_state:
                st.session_state["socratic_chat_history"] = []
            if "socratic_chat_draft" not in st.session_state:
                st.session_state["socratic_chat_draft"] = ""

            chat_history: list[dict[str, str]] = st.session_state["socratic_chat_history"]

            # Render dialogue history if non-empty
            if chat_history:
                st.markdown(
                    "##### 💬 分析顧問對話歷程 (Socratic Advisory History)"
                    if not is_en
                    else "##### 💬 Socratic Advisory History"
                )
                for item in chat_history:
                    role_icon = "👤" if item.get("role") == "user" else "🤖"
                    role_title = (
                        ("利害關係人 (Stakeholder)" if item.get("role") == "user" else "AI 湖倉顧問 (Analytics Copilot)")
                        if not is_en
                        else ("Stakeholder" if item.get("role") == "user" else "AI Lakehouse Copilot")
                    )
                    bg_color = "#F8FAFC" if item.get("role") == "user" else "#F0FDF4"
                    border_color = "#E2E8F0" if item.get("role") == "user" else "#BBF7D0"
                    st.markdown(
                        f"""
                        <div style="background-color: {bg_color}; border: 1px solid {border_color}; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; font-size: 0.90rem;">
                            <div style="font-weight: 600; color: #1E293B; margin-bottom: 4px;">{role_icon} {role_title}</div>
                            <div style="color: #334155; line-height: 1.5;">{item.get('content', '')}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

            # Conversational Input & Dual Action Row
            st.markdown(
                "###### 深入需求探討或補充條件 (Deeper Analytical Inquiry & Clarification)"
                if not is_en
                else "###### Deeper Analytical Inquiry & Clarification"
            )

            # Custom CSS for Red (Send) and Dark Slate / Black (Summarize) buttons
            st.markdown(
                """
                <style>
                div[data-testid="stButton"] button[kind="primary"] {
                    background-color: #EF4444 !important;
                    border-color: #DC2626 !important;
                    color: white !important;
                    font-weight: 600 !important;
                }
                div[data-testid="stButton"] button[kind="primary"]:hover {
                    background-color: #DC2626 !important;
                    border-color: #B91C1C !important;
                }
                div[data-testid="stButton"] button[kind="secondary"] {
                    background-color: #1E293B !important;
                    border-color: #0F172A !important;
                    color: white !important;
                    font-weight: 600 !important;
                }
                div[data-testid="stButton"] button[kind="secondary"]:hover {
                    background-color: #0F172A !important;
                    border-color: #020617 !important;
                    color: #F8FAFC !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )

            # Dynamic safe key pattern to reset input without StreamlitWidgetAlreadyInstantiatedError
            if "socratic_chat_counter" not in st.session_state:
                st.session_state["socratic_chat_counter"] = 0

            chat_in_col, send_btn_col, sum_btn_col = st.columns([6, 1.5, 1.8])
            with chat_in_col:
                chat_user_msg = st.text_input(
                    "其他對話細節內容：" if not is_en else "Additional context & inquiry:",
                    value="",
                    placeholder="例如：我想要細看各產品包裝規格（packaging_size）或折扣對銷售的影響..."
                    if not is_en
                    else "e.g., I want to drill down into packaging_size or discount impact on revenue...",
                    key=f"socratic_chat_input_w_{st.session_state['socratic_chat_counter']}",
                    label_visibility="collapsed",
                )

            with send_btn_col:
                send_label = "💬 輸入" if not is_en else "💬 Send"
                send_help = "送出對話，向 AI 顧問提問或補充細節（不立即生成 SQL）" if not is_en else "Send message to clarify requirements with AI Copilot"
                if st.button(send_label, type="primary", use_container_width=True, help=send_help):
                    if chat_user_msg.strip():
                        active_api_key = (
                            st.session_state.get("user_gemini_key")
                            or st.session_state.get("gemini_api_key")
                            or os.getenv("GEMINI_API_KEY", "")
                        ).strip()
                        chat_history.append({"role": "user", "content": chat_user_msg.strip()})
                        with st.spinner("AI 顧問正在分析湖倉實體維度並給予建議..." if not is_en else "AI Copilot is inspecting lakehouse dimensions..."):
                            advice = chat_socratic_turn(
                                user_message=chat_user_msg.strip(),
                                chat_history=chat_history,
                                user_goal=active_session.user_goal,
                                reflection=reflection,
                                api_key=active_api_key,
                                is_en=is_en,
                            )
                            chat_history.append({"role": "assistant", "content": advice})
                            st.session_state["socratic_chat_history"] = chat_history
                            # Reset memo since user added new requirements
                            st.session_state["socratic_summary_memo"] = None
                            # Safely reset next input by incrementing counter
                            st.session_state["socratic_chat_counter"] += 1
                            st.rerun()

            with sum_btn_col:
                sum_label = "📋 總結需求" if not is_en else "📋 Summarize"
                sum_help = "根據當前勾選項與所有對話，生成結構化需求總結備忘錄" if not is_en else "Consolidate selected options and dialogue into a structured memo"
                if st.button(sum_label, type="secondary", use_container_width=True, help=sum_help):
                    active_api_key = (
                        st.session_state.get("user_gemini_key")
                        or st.session_state.get("gemini_api_key")
                        or os.getenv("GEMINI_API_KEY", "")
                    ).strip()
                    with st.spinner("正在總結對話細節與湖倉建模規格..." if not is_en else "Summarizing requirement memo..."):
                        memo = summarize_socratic_requirements(
                            user_goal=active_session.user_goal,
                            decisions=user_choices,
                            chat_history=chat_history,
                            api_key=active_api_key,
                            is_en=is_en,
                        )
                        st.session_state["socratic_summary_memo"] = memo
                        st.session_state["socratic_memo_edit_mode"] = False
                        st.rerun()

            # -------------------------------------------------------------
            # Requirement Summary Card & In-Place Textual Fine-Tuning Gate
            # -------------------------------------------------------------
            memo = st.session_state.get("socratic_summary_memo")
            is_edit_mode = st.session_state.get("socratic_memo_edit_mode", False)
            if memo:
                if is_edit_mode:
                    # In-place Textual Edit Mode
                    edit_banner_title = "✏️ In-Place Textual Specification Fine-Tuning" if is_en else "✏️ 就地微調需求備忘錄 (In-Place Textual Specification Fine-Tuning)"
                    edit_banner_sub = "Directly adjust analysis dimensions below, then click 'Save Changes' to update:" if is_en else "您可以直接在下方文字框中微調各項分析維度與目標，修改完成後點擊「💾 保存修改」即可即時更新規格："
                    st.markdown(
                        f"""
                        <div style="background-color: #EFF6FF; border: 2px dashed #2563EB; border-radius: 8px; padding: 16px; margin: 16px 0;">
                            <div style="font-weight: 700; color: #1D4ED8; font-size: 1.05rem; margin-bottom: 8px;">
                                {edit_banner_title}
                            </div>
                            <div style="color: #1E40AF; font-size: 0.85rem; margin-bottom: 12px;">
                                {edit_banner_sub}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    edit_obj = st.text_area(
                        "Analytical Objective:" if is_en else "商業分析目標 (Analytical Objective):",
                        value=memo.get("analytical_objective", ""),
                        key="edit_memo_obj_field",
                    )
                    e_col1, e_col2 = st.columns(2)
                    with e_col1:
                        edit_grain = st.text_input(
                            "Target Data Grain:" if is_en else "分析資料粒度 (Target Grain):",
                            value=memo.get("target_grain", ""),
                            key="edit_memo_grain_field",
                        )
                        edit_market = st.text_input(
                            "Target Market Country (e.g. DE / AT / FR / ALL_EU):" if is_en else "目標地理市場 (Market Country e.g. DE / AT / FR / ALL_EU):",
                            value=memo.get("market_country", "ALL_EU"),
                            key="edit_memo_market_field",
                        )
                    with e_col2:
                        edit_focus = st.text_input(
                            "Focus Entity (e.g. return_reason / pricing_revenue):" if is_en else "核心分析焦點 (Focus Entity e.g. return_reason / pricing_revenue):",
                            value=memo.get("focus_entity", "revenue"),
                            key="edit_memo_focus_field",
                        )
                        edit_cat = st.text_input(
                            "Category Filter (e.g. Nuts & Butters / null):" if is_en else "產品品類過濾 (Category Filter e.g. Nuts & Butters / null):",
                            value=memo.get("category_filter") or "",
                            key="edit_memo_cat_field",
                        )
                    edit_metrics_str = st.text_input(
                        "Target Metrics (comma-separated):" if is_en else "核心計算指標 (Target Metrics, 逗號分隔):",
                        value=", ".join(memo.get("target_metrics", [])),
                        key="edit_memo_metrics_field",
                    )

                    save_col, cancel_col = st.columns([1, 1])
                    with save_col:
                        if st.button("💾 Save Changes" if is_en else "💾 保存修改 (Save Edits)", type="primary", use_container_width=True):
                            clean_market = edit_market.strip().upper()
                            # Physical Catalog Dynamic Grounding Check
                            valid_physical_markets = reflection.get_distinct_column_values("fct_orders", "market_country")
                            if clean_market and clean_market != "ALL_EU" and valid_physical_markets and clean_market not in valid_physical_markets:
                                if is_en:
                                    st.error(
                                        f"❌ Physical Catalog Grounding Mismatch:\n\n"
                                        f"Market '{clean_market}' does not exist in lakehouse `fct_orders.market_country`!\n\n"
                                        f"Valid physical markets: {', '.join(sorted(valid_physical_markets))}.\n\n"
                                        f"Phantom market entities are prohibited under Zero-Facade governance."
                                    )
                                else:
                                    st.error(
                                        f"❌ 實體資料庫校驗未通過 (Physical Catalog Mismatch)：\n\n"
                                        f"市場 '{clean_market}' 不存在於湖倉實體資料庫 `fct_orders.market_country`！\n\n"
                                        f"實體庫中實際存在之活躍市場僅有：{', '.join(sorted(valid_physical_markets))}。\n\n"
                                        f"禁止設定不存在的幽靈市場實體，請修正後再保存！"
                                    )
                            else:
                                memo["analytical_objective"] = edit_obj.strip()
                                memo["target_grain"] = edit_grain.strip()
                                memo["market_country"] = clean_market
                                memo["focus_entity"] = edit_focus.strip()
                                memo["category_filter"] = edit_cat.strip() if edit_cat.strip() else None
                                memo["target_metrics"] = [m.strip() for m in edit_metrics_str.split(",") if m.strip()]
                                st.session_state["socratic_summary_memo"] = memo
                                st.session_state["socratic_memo_edit_mode"] = False
                                st.rerun()
                    with cancel_col:
                        if st.button("❌ Cancel" if is_en else "❌ 取消微調 (Cancel)", use_container_width=True):
                            st.session_state["socratic_memo_edit_mode"] = False
                            st.rerun()

                else:
                    # View Mode: High-contrast Memo Presentation
                    if is_en:
                        cat_display = f"<br>• <strong>Category Filter</strong>: <code>{memo.get('category_filter')}</code>" if memo.get('category_filter') else ""
                        focus_display = f"<br>• <strong>Focus Entity</strong>: <code>{memo.get('focus_entity')}</code>" if memo.get('focus_entity') else ""
                        memo_html = (
                            f'<div style="background-color: #F8FAFC; border: 2px solid #3B82F6; border-radius: 8px; padding: 18px; margin: 16px 0;">'
                            f'<div style="font-weight: 700; color: #1E40AF; font-size: 1.05rem; margin-bottom: 8px; display: flex; align-items: center; gap: 8px;">'
                            f'📋 Analytical Requirement Specification Memo'
                            f'</div>'
                            f'<div style="color: #1E293B; font-size: 0.92rem; line-height: 1.6;">'
                            f'• <strong>Analytical Objective</strong>: {memo.get("analytical_objective")}<br>'
                            f'• <strong>Target Data Grain</strong>: <code>{memo.get("target_grain")}</code><br>'
                            f'• <strong>Temporal Analysis Window</strong>: <code>{memo.get("date_range")}</code><br>'
                            f'• <strong>Target Geographic Market</strong>: <code>{memo.get("market_country")}</code>'
                            f'{cat_display}'
                            f'{focus_display}<br>'
                            f'• <strong>Target Metrics</strong>: <code>{", ".join(memo.get("target_metrics", []))}</code><br>'
                            f'• <strong>Upstream Dependencies</strong>: <code>{", ".join(memo.get("upstream_tables", []))}</code>'
                            f'{"<br>• <strong>Advisory Consensus Notes</strong>: <em>" + str(memo.get("chat_context_notes")) + "</em>" if memo.get("chat_context_notes") else ""}'
                            f'</div>'
                            f'<div style="margin-top: 10px; font-size: 0.84rem; color: #64748B;">'
                            f'👉 Please review the requirement specification above. Click \'Fine-tune Details\' to modify, or click \'Sign-off & Compile\' to launch pipeline compilation.'
                            f'</div>'
                            f'</div>'
                        )
                    else:
                        cat_display = f"<br>• <strong>產品品類篩選</strong>：<code>{memo.get('category_filter')}</code>" if memo.get('category_filter') else ""
                        focus_display = f"<br>• <strong>分析焦點實體</strong>：<code>{memo.get('focus_entity')}</code>" if memo.get('focus_entity') else ""
                        memo_html = (
                            f'<div style="background-color: #F8FAFC; border: 2px solid #3B82F6; border-radius: 8px; padding: 18px; margin: 16px 0;">'
                            f'<div style="font-weight: 700; color: #1E40AF; font-size: 1.05rem; margin-bottom: 8px; display: flex; align-items: center; gap: 8px;">'
                            f'📋 分析需求總結備忘錄 (Analytical Requirement Specification Memo)'
                            f'</div>'
                            f'<div style="color: #1E293B; font-size: 0.92rem; line-height: 1.6;">'
                            f'• <strong>商業分析目標</strong>：{memo.get("analytical_objective")}<br>'
                            f'• <strong>目標資料粒度 (Grain)</strong>：<code>{memo.get("target_grain")}</code><br>'
                            f'• <strong>時間分析區間</strong>：<code>{memo.get("date_range")}</code><br>'
                            f'• <strong>目標地理市場</strong>：<code>{memo.get("market_country")}</code>'
                            f'{cat_display}'
                            f'{focus_display}<br>'
                            f'• <strong>核心計算指標</strong>：<code>{", ".join(memo.get("target_metrics", []))}</code><br>'
                            f'• <strong>上游依賴實體</strong>：<code>{", ".join(memo.get("upstream_tables", []))}</code>'
                            f'{"<br>• <strong>對話補充要點</strong>：<em>" + str(memo.get("chat_context_notes")) + "</em>" if memo.get("chat_context_notes") else ""}'
                            f'</div>'
                            f'<div style="margin-top: 10px; font-size: 0.84rem; color: #64748B;">'
                            f'👉 請確認以上需求總結是否符合預期。若需就地修改文字請點擊「✏️ 還需微調細節」；確認無誤後點擊右方按鈕啟動管線編譯。'
                            f'</div>'
                            f'</div>'
                        )
                    st.markdown(memo_html, unsafe_allow_html=True)

                    gate_c1, gate_c2 = st.columns([1, 2])
                    with gate_c1:
                        if st.button("✏️ 還需微調細節" if not is_en else "✏️ Fine-tune Details", use_container_width=True):
                            st.session_state["socratic_memo_edit_mode"] = True
                            st.rerun()

                    with gate_c2:
                        compile_signoff_label = (
                            "⚡ 確認無誤，動態編譯數據管線 (Sign-off & Compile)"
                            if not is_en
                            else "⚡ Sign-off & Compile dbt Pipeline"
                        )
                        if st.button(compile_signoff_label, type="primary", use_container_width=True):
                            active_api_key = (
                                st.session_state.get("user_gemini_key")
                                or st.session_state.get("gemini_api_key")
                                or os.getenv("GEMINI_API_KEY", "")
                            ).strip()
                            spinner_msg = (
                                "Gemini 正在動態生成 dbt SQL 與 Data Contract，並於 DuckDB 物理庫執行 Dry-Run..."
                                if not is_en
                                else "Gemini is dynamically synthesizing dbt SQL & contract and executing physical DuckDB dry-run..."
                            )
                            with st.spinner(spinner_msg):
                                # Append user chat notes and extracted memo predicates into decisions so synthesizer honors the depth
                                signed_decisions = dict(user_choices)
                                if memo.get("chat_context_notes"):
                                    signed_decisions["custom_chat_notes"] = f"custom:{memo.get('chat_context_notes')}"
                                if memo.get("start_date") and memo.get("end_date"):
                                    signed_decisions["start_date"] = memo.get("start_date")
                                    signed_decisions["end_date"] = memo.get("end_date")
                                    signed_decisions["temporal"] = f"{memo.get('start_date')}:{memo.get('end_date')}"
                                if memo.get("market_country"):
                                    signed_decisions["geographic"] = memo.get("market_country")
                                if memo.get("category_filter"):
                                    signed_decisions["category_filter"] = memo.get("category_filter")
                                if memo.get("focus_entity"):
                                    signed_decisions["focus_entity"] = memo.get("focus_entity")
                                if memo.get("target_grain"):
                                    signed_decisions["grain"] = memo.get("target_grain")

                                res = synthesize_dynamic_dbt_model(
                                    user_goal=active_session.user_goal,
                                    decisions=signed_decisions,
                                    reflection=reflection,
                                    api_key=active_api_key,
                                )
                                st.session_state["schema_design_result"] = res
                                st.session_state["socratic_signed_off"] = True
                                st.session_state["socratic_memo_edit_mode"] = False
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
            preview_rows = min(len(result.data_preview), 10)
            preview_title = f"##### 實體 DuckDB Dry-Run 物理驗證數據預覽 (前 {preview_rows} 筆)：" if not is_en else f"##### {preview_rows}-Row Verified Data Preview (DuckDB Dry-Run Output):"
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

    elif result and result.is_rejected:
        rej_title = "🛑 實體型錄邊界檢驗中斷拒絕 (Compilation Halted & Rejected)" if not is_en else "🛑 Pipeline Compilation Halted & Rejected"
        st.error(f"{rej_title}: `{result.spec_id}`")
        st.warning(result.rejection_reason)

        if result.audit_trace:
            trace_title = "##### 🔍 Observability Audit Trail (拒絕與中斷追蹤)" if not is_en else "##### 🔍 Observability Audit Trail"
            st.markdown(trace_title)
            cols = st.columns(len(result.audit_trace))
            for idx, trace in enumerate(result.audit_trace):
                with cols[idx]:
                    status_color = "#DC2626" if trace["status"] in ["REJECTED", "BLOCKED"] else "#059669"
                    st.markdown(
                        f"""
                        <div style="background-color: #FEF2F2; border: 1px solid #FCA5A5; border-radius: 6px; padding: 10px; font-size: 0.85rem;">
                            <div style="font-weight: 600; color: #991B1B;">{trace['step']}</div>
                            <div style="color: {status_color}; font-weight: 500; margin: 2px 0;">● {trace['status']}</div>
                            <div style="color: #7F1D1D; font-size: 0.78rem;">{trace['detail']}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
