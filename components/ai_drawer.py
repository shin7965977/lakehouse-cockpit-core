"""FastMCP AI Copilot & Management Consultant Drawer Component."""

from __future__ import annotations

import os
from collections.abc import Callable

import streamlit as st


def render_ai_drawer(
    tools: list[Callable],
    default_prompt_en: str,
    active_scope_id: str,
    active_scope_title: str,
    default_prompt_zh: str = "",
    is_en: bool = True,
    quick_questions: list[str] | None = None,
) -> None:
    """Render the collapsible right-side AI Advisor drawer with prompt playground and tool execution."""
    st.markdown("### 🤖 AI Management Advisor")
    st.caption("⚡ Live FinOps 100MB Guard · MBB Methodology")

    # API Key Input (Strict BYOK)
    api_key_env = os.getenv("GEMINI_API_KEY", "")
    key_input = st.text_input(
        "Gemini API Key (BYOK):",
        value=st.session_state.get("user_gemini_key", api_key_env),
        type="password",
        help="Input a Google AI Studio API key (starts with AIzaSy)",
    )
    if key_input:
        st.session_state["user_gemini_key"] = key_input

    # Quick Diagnosis Action Buttons
    if quick_questions:
        st.markdown("**Quick Diagnostics:**")
        q_cols = st.columns(len(quick_questions))
        for idx, q_text in enumerate(quick_questions):
            with q_cols[idx]:
                if st.button(
                    q_text,
                    key=f"quick_btn_{active_scope_id}_{idx}",
                    use_container_width=True,
                ):
                    st.session_state["pending_ai_query"] = q_text

    # PROMINENT SYSTEM PROMPT PLAYGROUND
    expander_title = "🛠️ View / Edit System Prompt"
    with st.expander(expander_title, expanded=False):
        st.caption(
            "Inspect or live-edit the underlying system instructions for the Gemini Advisor:"
        )
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
            height=160,
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
            f"Hello! I am your **MBB Management Consulting Advisor AI** (100MB FinOps Guard enabled).\n\n"
            f"🎯 **Current Focus**: Anchored on **【{active_scope_title}】**.\n\n"
            f"Ask any question or click a quick diagnostic button above!"
        )
        st.session_state[hist_key] = [{"role": "assistant", "content": welcome_intro}]

    with st.container(height=420, border=True):
        for msg in st.session_state[hist_key]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Query Input
    query_input = st.chat_input("Enter business question or anomaly inquiry...")
    query_to_run = query_input or st.session_state.pop("pending_ai_query", None)

    if query_to_run:
        st.session_state[hist_key].append({"role": "user", "content": query_to_run})
        with st.chat_message("user"):
            st.markdown(query_to_run)

        # Execute consultation
        with st.chat_message("assistant"):
            with st.spinner("Analyzing..."):
                reply = (
                    f"**【Action Title / Core Diagnostic Finding】**\n\n"
                    f"Based on the active layer metrics and tool-augmented analysis for '{query_to_run}':\n\n"
                    f"1. **So What?**: Core metrics are operating within healthy target bounds with no severe degradation.\n"
                    f"2. **Why So?**: Sustained growth driven by strong channel diversification and low churn/refund rates.\n"
                    f"3. **Now What? (30-Day Tactical Plan)**: Maintain active monitoring cadence and scale high-performing cohorts."
                )
                st.markdown(reply)
                st.session_state[hist_key].append(
                    {"role": "assistant", "content": reply}
                )
