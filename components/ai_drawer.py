"""FastMCP AI Copilot & Socratic Spec Guidance Drawer Component."""

from __future__ import annotations

import os
from collections.abc import Callable
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
    st.session_state.pop("spec_inquiry_round", None)
    st.session_state.pop("spec_inquiry_context", None)
    st.session_state.pop("locked_spec_contract", None)
    st.session_state.pop("active_on_demand_insight", None)
    st.session_state.pop("pending_ai_query", None)


def render_ai_drawer(
    tools: list[Callable],
    default_prompt_en: str,
    active_scope_id: str,
    active_scope_title: str,
    default_prompt_zh: str = "",
    is_en: bool = True,
    quick_questions: list[str] | None = None,
    adapter: BaseCockpitAdapter | None = None,
    data_dict: dict[str, pd.DataFrame] | None = None,
) -> None:
    """Render the right-side AI Advisor drawer with 2-round Socratic Spec Guidance."""
    hdr_col1, hdr_col2 = st.columns([7, 3])
    with hdr_col1:
        st.markdown(
            "### 🤖 AI Spec & Insight Copilot"
            if is_en
            else "### 🤖 AI 規格引導與洞察助理"
        )
        st.caption(
            "⚡ 2-Round Spec Guard · 100MB FinOps Limit"
            if is_en
            else "⚡ 2 輪規格強收斂 · 100MB 成本安全閥"
        )
    with hdr_col2:
        reset_label = "🔄 New Inquiry" if is_en else "🔄 開啟新諮詢"
        if st.button(reset_label, key=f"btn_new_inquiry_{active_scope_id}", use_container_width=True):
            reset_inquiry_state(active_scope_id)
            st.rerun()

    # API Key Input (Optional BYOK with Automatic Offline Fallback)
    api_key_env = os.getenv("GEMINI_API_KEY", "")
    key_input = st.text_input(
        "Gemini API Key (Optional - Offline Fallback Active):"
        if is_en
        else "Gemini API Key (選填 — 已啟用離線確定性保險):",
        value=st.session_state.get("user_gemini_key", api_key_env),
        type="password",
        help="Input a Google AI Studio API key or leave blank to use deterministic Golden Presets",
    )
    if key_input:
        st.session_state["user_gemini_key"] = key_input

    # Quick Diagnosis / Golden Preset Action Buttons
    if quick_questions:
        st.markdown(
            "**⚡ Golden Scenarios (1-Click):**"
            if is_en
            else "**⚡ 一鍵黃金示範場景 (0 延遲):**"
        )
        for idx, q_text in enumerate(quick_questions):
            if st.button(
                q_text,
                key=f"quick_btn_{active_scope_id}_{idx}",
                use_container_width=True,
            ):
                st.session_state["pending_ai_query"] = q_text

    # PROMINENT SYSTEM PROMPT PLAYGROUND
    expander_title = (
        "🛠️ View / Edit System Prompt" if is_en else "🛠️ 檢視 / 編輯系統提示詞"
    )
    with st.expander(expander_title, expanded=False):
        prompt_session_key = (
            "custom_system_prompt_zh"
            if not is_en and default_prompt_zh
            else "custom_system_prompt_en"
        )
        default_val = (
            default_prompt_zh if not is_en and default_prompt_zh else default_prompt_en
        )
        active_prompt = st.session_state.get(prompt_session_key, default_val)

        edited_prompt = st.text_area(
            "Prompt Editor:",
            value=active_prompt,
            height=130,
            key=f"sys_prompt_textarea_{active_scope_id}",
        )
        p_col1, p_col2 = st.columns(2)
        with p_col1:
            if st.button(
                "💾 Apply",
                key=f"btn_apply_{active_scope_id}",
                use_container_width=True,
            ):
                st.session_state[prompt_session_key] = edited_prompt
                st.success("✅ Applied!")
        with p_col2:
            if st.button(
                "🔄 Reset",
                key=f"btn_reset_{active_scope_id}",
                use_container_width=True,
            ):
                st.session_state[prompt_session_key] = default_val
                st.rerun()

    st.markdown("---")

    # Conversation History Container
    hist_key = f"ai_advisor_chat_hist_{active_scope_id}"
    if hist_key not in st.session_state:
        welcome_intro = (
            f"Hello! I am your **Socratic Analytics Engineering Copilot**.\n\n"
            f"🎯 **Layer Scope**: **【{active_scope_title}】**.\n\n"
            f"Click a **Golden Scenario** above or ask any business question. I will clarify key metric definitions in **Round 1** and lock down an **Analytical Spec Card** in **Round 2**!"
            if is_en
            else f"您好！我是您的 **蘇格拉底式分析工程引導助理**。\n\n"
            f"🎯 **當前視角**：**【{active_scope_title}】**\n\n"
            f"請點擊上方 **一鍵黃金示範場景** 或輸入業務問題。我會在 **第 1 輪** 協助釐清關鍵指標口徑，並在 **第 2 輪** 為您鎖定可驗證的 **分析規格確認卡 (Analytical Spec Card)**！"
        )
        st.session_state[hist_key] = [{"role": "assistant", "content": welcome_intro}]

    with st.container(height=360, border=True):
        for msg in st.session_state[hist_key]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Query Input
    placeholder_txt = (
        "Ask a business question or reply to clarify scope..."
        if is_en
        else "輸入業務問題或回覆口徑確認..."
    )
    query_input = st.chat_input(placeholder_txt)
    query_to_run = query_input or st.session_state.pop("pending_ai_query", None)

    if query_to_run:
        st.session_state[hist_key].append({"role": "user", "content": query_to_run})

        if adapter is not None and adapter.supports_spec_guidance:
            current_round = st.session_state.get("spec_inquiry_round", 0)
            session_ctx = st.session_state.get("spec_inquiry_context", {})
            turn_result = adapter.handle_spec_inquiry_turn(
                user_query=query_to_run,
                current_round=current_round,
                session_context=session_ctx,
                is_en=is_en,
            )
            st.session_state["spec_inquiry_round"] = turn_result.get("next_round", 0)
            st.session_state["spec_inquiry_context"] = {
                "scenario_key": turn_result.get("scenario_key"),
                "last_user_query": query_to_run,
            }
            if turn_result.get("spec_contract") is not None:
                st.session_state["locked_spec_contract"] = turn_result["spec_contract"]

            st.session_state[hist_key].append(
                {"role": "assistant", "content": turn_result["reply"]}
            )
            st.rerun()
        else:
            reply = (
                f"**【Action Title / Core Diagnostic Finding】**\n\n"
                f"Based on the active layer metrics for '{query_to_run}':\n\n"
                f"1. **So What?**: Core metrics are operating within healthy target bounds.\n"
                f"2. **Why So?**: Sustained performance with controlled return rates.\n"
                f"3. **Now What?**: Continue monitoring key product cohorts."
            )
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
                    is_en=is_en,
                )
                if compiled_insight is not None:
                    st.session_state["active_on_demand_insight"] = compiled_insight
                    st.rerun()
