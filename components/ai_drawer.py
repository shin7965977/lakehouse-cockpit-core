"""Conversational Analytics Engineering Copilot Drawer Component."""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import streamlit as st

try:
    from ..adapter import AnalyticalSpecContract, BaseCockpitAdapter
except ImportError:
    from adapter import AnalyticalSpecContract, BaseCockpitAdapter


def reset_inquiry_state(active_scope_id: str) -> None:
    """Clear conversation history, pending spec contract, and active on-demand insight."""
    hist_key = f"ai_advisor_chat_hist_{active_scope_id}"
    st.session_state.pop(hist_key, None)
    st.session_state.pop("locked_spec_contract", None)
    st.session_state.pop("active_on_demand_insight", None)
    st.session_state.pop("pending_ai_query", None)


def render_ai_drawer(
    active_scope_id: str,
    active_scope_title: str,
    default_prompt_en: str,
    default_prompt_zh: str = "",
    is_en: bool = True,
    quick_questions: list[str] | None = None,
    adapter: BaseCockpitAdapter | None = None,
    data_dict: dict[str, pd.DataFrame] | None = None,
    start_date: Any = None,
    end_date: Any = None,
) -> None:
    """Render the right-side AI Copilot drawer with open conversational understanding."""
    hdr_col1, hdr_col2 = st.columns([7, 3])
    with hdr_col1:
        st.markdown(
            "### 🤖 Analytics Engineering Copilot"
            if is_en
            else "### 🤖 分析工程智能協作助理"
        )
        st.caption(
            "⚡ Data Catalog Grounded · Semantic Guarded"
            if is_en
            else "⚡ 全目錄真實感知 · 語意合規守護"
        )
    with hdr_col2:
        reset_label = "🔄 New Inquiry" if is_en else "🔄 開啟新諮詢"
        if st.button(reset_label, key=f"btn_new_inquiry_{active_scope_id}", use_container_width=True):
            reset_inquiry_state(active_scope_id)
            st.rerun()

    # Optional BYOK for Live Gemini
    api_key_env = os.getenv("GEMINI_API_KEY", "")
    key_input = st.text_input(
        "Gemini API Key (Optional — Auto Semantic Fallback Active):"
        if is_en
        else "Gemini API Key (選填 — 已啟用內建語意理解):",
        value=st.session_state.get("user_gemini_key", api_key_env),
        type="password",
        help="Input a Google AI Studio API key or leave blank to use the built-in catalog assistant.",
    )
    if key_input:
        st.session_state["user_gemini_key"] = key_input

    # Quick Diagnosis / Suggested Questions
    if quick_questions:
        st.markdown(
            "**💡 Suggested Questions:**"
            if is_en
            else "**💡 建議探索問題：**"
        )
        for idx, q_text in enumerate(quick_questions):
            if st.button(
                q_text,
                key=f"quick_btn_{active_scope_id}_{idx}",
                use_container_width=True,
            ):
                st.session_state["pending_ai_query"] = q_text

    st.markdown("---")

    # Conversation History Container
    hist_key = f"ai_advisor_chat_hist_{active_scope_id}"
    if hist_key not in st.session_state:
        welcome_intro = (
            f"Hello! I am your **Analytics Engineering Copilot**.\n\n"
            f"🎯 **Active Scope**: **【{active_scope_title}】**.\n\n"
            f"Feel free to explore our product catalog, ask about available metrics, or inquire about specific performance anomalies. "
            f"I will help clarify the analysis scope and generate audit-ready charts for you!"
            if is_en
            else f"您好！我是您的 **分析工程智能協作助理**。\n\n"
            f"🎯 **當前視角**：**【{active_scope_title}】**\n\n"
            f"您可以自由詢問目前有哪些資料、探索指標定義，或提出特定的營運問題。"
            f"我會協助您釐清分析範圍，並即時產出合規可驗證的圖表與 SQL！"
        )
        st.session_state[hist_key] = [{"role": "assistant", "content": welcome_intro}]

    with st.container(height=380, border=True):
        for msg in st.session_state[hist_key]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Query Input
    placeholder_txt = (
        "Ask about available data, metrics, or business anomalies..."
        if is_en
        else "自由詢問目前資料、指標定義，或特定營運問題..."
    )
    query_input = st.chat_input(placeholder_txt)
    query_to_run = query_input or st.session_state.pop("pending_ai_query", None)

    if query_to_run:
        st.session_state[hist_key].append({"role": "user", "content": query_to_run})

        if adapter is not None and getattr(adapter, "supports_spec_guidance", False):
            turn_result = adapter.handle_spec_inquiry_turn(
                user_query=query_to_run,
                chat_history=st.session_state[hist_key],
                data_dict=data_dict or {},
                is_en=is_en,
            )
            if turn_result.get("spec_contract") is not None:
                st.session_state["locked_spec_contract"] = turn_result["spec_contract"]

            st.session_state[hist_key].append(
                {"role": "assistant", "content": turn_result["reply"]}
            )
            st.rerun()
        else:
            reply = f"Thank you for your question: '{query_to_run}'. Analytics guidance is ready."
            st.session_state[hist_key].append({"role": "assistant", "content": reply})
            st.rerun()

    # Render Locked Analytical Spec Card if present in session state
    locked_spec: AnalyticalSpecContract | None = st.session_state.get(
        "locked_spec_contract"
    )
    if locked_spec is not None and adapter is not None:
        with st.container(border=True):
            st.markdown(
                f"#### 📋 Analytical Spec Card · `{locked_spec.spec_id}`"
                if is_en
                else f"#### 📋 分析規格確認卡 · `{locked_spec.spec_id}`"
            )
            st.markdown(f"**Question**: {locked_spec.business_question}")
            st.markdown(
                f"**Target Mart**: `{locked_spec.target_layer}.{locked_spec.target_mart}`"
            )
            metrics_str = ", ".join(
                [f"`{m['name']}` ({m['formula']})" for m in locked_spec.metrics]
            )
            st.markdown(f"**Metrics**: {metrics_str}")
            st.markdown(
                f"**Dimensions**: `{', '.join(locked_spec.dimensions)}` | **Chart**: `{locked_spec.chart_recommendation}`"
            )
            btn_confirm_label = (
                "✅ Confirm Spec & Generate Compliant Chart"
                if is_en
                else "✅ 確認規格並生成合規可追蹤圖表"
            )
            if st.button(
                btn_confirm_label,
                key=f"btn_confirm_spec_{locked_spec.spec_id}",
                type="primary",
                use_container_width=True,
            ):
                compiled_insight = adapter.compile_spec_to_insight(
                    spec_contract=locked_spec,
                    data_dict=data_dict or {},
                    start_date=start_date,
                    end_date=end_date,
                    is_en=is_en,
                )
                if compiled_insight is not None:
                    st.session_state["active_on_demand_insight"] = compiled_insight
                    st.rerun()
