"""Conversational Analytics Engineering Copilot Drawer Component with Mandatory BYOK Wall."""

from __future__ import annotations

import os
import re
from typing import Any

import pandas as pd
import streamlit as st

try:
    from ..adapter import AnalyticalSpecContract, BaseCockpitAdapter
    from .ae_workflow import render_ae_workflow_stepper
except (ImportError, ValueError):
    try:
        from adapter import AnalyticalSpecContract, BaseCockpitAdapter
        from components.ae_workflow import render_ae_workflow_stepper
    except (ImportError, ValueError):
        from cockpit_core.adapter import AnalyticalSpecContract, BaseCockpitAdapter
        from cockpit_core.components.ae_workflow import render_ae_workflow_stepper


def reset_inquiry_state(active_scope_id: str) -> None:
    """Clear conversation history, pending spec contract, and active on-demand insight."""
    hist_key = f"ai_advisor_chat_hist_{active_scope_id}"
    st.session_state.pop(hist_key, None)
    st.session_state.pop("locked_spec_contract", None)
    st.session_state.pop("active_on_demand_insight", None)
    st.session_state.pop("pending_ai_query", None)


def _validate_gemini_key(key: str) -> bool:
    """Instant preflight key validation."""
    k = key.strip()
    if not k or k.startswith("your_") or len(k) < 20:
        return False
    # Standard Google AI Studio keys start with AIzaSy
    if k.startswith("AIzaSy"):
        return True
    return len(k) >= 30


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
    """Render the right-side AI Copilot drawer with mandatory BYOK security wall and AE workflow trigger."""
    # 1. Resolve Active Gemini API Key (Session State or Environment)
    api_key_env = os.getenv("GEMINI_API_KEY", "").strip()
    session_key = st.session_state.get("user_gemini_key", "").strip()
    active_key = session_key or api_key_env
    has_valid_key = _validate_gemini_key(active_key)

    # 2. Mandatory BYOK Key Input Bar (1:1 with ReadCall UI)
    st.markdown(
        "**🔑 Gemini API Key (Mandatory)** *(Instant Preflight)*"
        if is_en
        else "**🔑 Gemini API Key (強制必填)** *(即時預檢驗證)*"
    )

    k_col1, k_col2 = st.columns([7, 3])
    with k_col1:
        new_key_input = st.text_input(
            label="Gemini Key Input",
            value=active_key,
            type="password",
            placeholder="AIzaSy...",
            label_visibility="collapsed",
            key=f"gemini_key_input_{active_scope_id}",
        )
    with k_col2:
        save_label = "💾 Save" if is_en else "💾 儲存"
        if st.button(save_label, key=f"btn_save_key_{active_scope_id}", use_container_width=True, type="primary"):
            if new_key_input:
                st.session_state["user_gemini_key"] = new_key_input.strip()
                st.rerun()

    # 3. Copilot Subheading with Badge
    hdr_col1, hdr_col2 = st.columns([7, 3])
    with hdr_col1:
        copilot_title = (
            "### 🤖 Analytics Engineering Copilot"
            if is_en
            else "### 🤖 分析工程智能協作助理"
        )
        st.markdown(copilot_title)
    with hdr_col2:
        if has_valid_key:
            st.markdown('<span class="key-badge-active">✅ KEY ACTIVE</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="key-badge-required">🔒 KEY REQUIRED</span>', unsafe_allow_html=True)

    # 4. Check BYOK Wall: If No Valid Key, Display Strict Warning Card & Disable Chat
    if not has_valid_key:
        st.markdown(
            """
            <div class="byok-callout-warning">
                <div style="display: flex; gap: 10px; align-items: flex-start;">
                    <span style="font-size: 1.3rem;">🔒</span>
                    <div>
                        <strong>API Key Required:</strong> Please enter a valid Google AI Studio API Key (<code>AIzaSy...</code>) above to unlock the AI Copilot. FastMCP guess mode is strictly disabled.
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            "🔒 **BYOK Security Guarantee**: Keys are never stored. 👉 [Get a Free Gemini API Key at Google AI Studio](https://aistudio.google.com/app/apikey)"
            if is_en
            else "🔒 **BYOK 安全保證**：金鑰僅保留於當前瀏覽會話，絕不上傳保存。👉 [前往 Google AI Studio 免費領取金鑰](https://aistudio.google.com/app/apikey)"
        )
        # Suppress interactive chat when key is absent
        return

    # 5. Unlocked AI Copilot Mode
    # Reset inquiry button
    r_col1, r_col2 = st.columns([7, 3])
    with r_col2:
        reset_label = "🔄 New Inquiry" if is_en else "🔄 新諮詢"
        if st.button(reset_label, key=f"btn_new_inq_unlocked_{active_scope_id}", use_container_width=True):
            reset_inquiry_state(active_scope_id)
            st.rerun()

    # Quick Diagnosis / Suggested Questions (Strictly 2 Primary Options)
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
                if "workflow" in q_text.lower() or "全流程" in q_text:
                    st.session_state["cockpit_active_view"] = "workflow"
                st.rerun()

    # Conversation History Container (Stretched to 700px with optimized typography)
    hist_key = f"ai_advisor_chat_hist_{active_scope_id}"
    if hist_key not in st.session_state:
        welcome_intro = (
            f"Hello! I am your **Analytics Engineering Copilot** (powered by Gemini Live).\n\n"
            f"🎯 **Active Scope**: **【{active_scope_title}】**.\n\n"
            f"Feel free to explore our product catalog, ask about available metrics, or inquire about specific performance anomalies in any language!"
            if is_en
            else f"您好！我是您的 **分析工程智能協作助理**（由 Gemini 驅動）。\n\n"
            f"🎯 **當前視角**：**【{active_scope_title}】**\n\n"
            f"您可以自由詢問目前有哪些資料、探索指標定義，或提出特定的營運問題，我支援全中文與英文流暢對答！"
        )
        st.session_state[hist_key] = [{"role": "assistant", "content": welcome_intro}]

    st.markdown(
        """
        <style>
        div[data-testid="stChatMessage"] {
            font-size: 0.92rem !important;
            line-height: 1.5 !important;
            padding: 0.5rem 0.75rem !important;
        }
        div[data-testid="stChatMessage"] p, div[data-testid="stChatMessage"] li {
            font-size: 0.92rem !important;
            line-height: 1.5 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.container(height=700, border=True):
        for msg in st.session_state[hist_key]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Query Input
    placeholder_txt = (
        "Ask in English or 中文 about metrics, SQL, or catalog data..."
        if is_en
        else "以中文或英文自由詢問目前資料、指標定義，或特定營運問題..."
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
                api_key=active_key,
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
                key="btn_confirm_spec_card",
                type="primary",
                use_container_width=True,
            ):
                insight_res = adapter.compile_spec_to_insight(
                    spec_contract=locked_spec,
                    data_dict=data_dict or {},
                    start_date=start_date,
                    end_date=end_date,
                    is_en=is_en,
                )
                if insight_res is not None:
                    st.session_state["active_on_demand_insight"] = insight_res
                    st.session_state.pop("locked_spec_contract", None)
                    st.rerun()
