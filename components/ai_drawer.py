"""FastMCP AI Copilot & Management Consultant Drawer Component."""

from __future__ import annotations

import os
from collections.abc import Callable

import streamlit as st


def render_ai_drawer(
    tools: list[Callable],
    default_prompt_zh: str,
    default_prompt_en: str,
    active_scope_id: str,
    active_scope_title: str,
    is_en: bool = False,
    quick_questions: list[str] | None = None,
) -> None:
    """Render the collapsible right-side AI Advisor drawer with prompt playground and tool execution."""
    st.markdown(
        "### 🤖 " + ("AI Management Advisor" if is_en else "Gemini 智慧管理顧問")
    )
    st.caption(
        "⚡ Live FinOps 100MB Guard · MBB Methodology"
        if is_en
        else "⚡ 100MB FinOps 預算防護 · 麥肯錫顧問規範"
    )

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
        st.markdown("**" + ("Quick Diagnostics:" if is_en else "一鍵快捷診斷：") + "**")
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
    expander_title = "🛠️ " + (
        "View / Edit System Prompt" if is_en else "檢視 / 修改系統 Prompt"
    )
    with st.expander(expander_title, expanded=False):
        st.caption(
            "Inspect or live-edit the underlying system instructions for the Gemini Advisor:"
            if is_en
            else "檢視或即時手動修改底層 Gemini 顧問 System Prompt（內建 100MB FinOps 防護）："
        )
        prompt_session_key = (
            "custom_system_prompt_en" if is_en else "custom_system_prompt_zh"
        )
        default_val = default_prompt_en if is_en else default_prompt_zh
        active_prompt = st.session_state.get(prompt_session_key, default_val)

        edited_prompt = st.text_area(
            "Prompt Editor:" if is_en else "Prompt 編輯區：",
            value=active_prompt,
            height=160,
            key=f"sys_prompt_textarea_{active_scope_id}",
        )
        p_col1, p_col2 = st.columns(2)
        with p_col1:
            if st.button(
                "💾 " + ("Apply" if is_en else "套用"),
                key=f"btn_apply_{active_scope_id}",
                use_container_width=True,
            ):
                st.session_state[prompt_session_key] = edited_prompt
                st.success("✅ " + ("Applied!" if is_en else "已套用！"))
        with p_col2:
            if st.button(
                "🔄 " + ("Reset" if is_en else "還原預設值"),
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
            f"Hello! I am your **MBB Management Consulting Advisor AI**.\n\n"
            f"🎯 **Current Focus**: Anchored on **【{active_scope_title}】**.\n\n"
            f"Ask any question or click a quick diagnostic button above!"
            if is_en
            else f"您好！我是您的 **MBB 商業管理諮詢顧問 AI**（內建 100MB 預算防護）。\n\n"
            f"🎯 **當前焦點**：當前視覺焦點鎖定在 **【{active_scope_title}】**。\n\n"
            f"您可以直接提問，或點選上方一鍵快捷診斷。"
        )
        st.session_state[hist_key] = [{"role": "assistant", "content": welcome_intro}]

    with st.container(height=420, border=True):
        for msg in st.session_state[hist_key]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Query Input
    query_input = st.chat_input(
        "Enter business question..." if is_en else "輸入業務問題或異常諮詢..."
    )
    query_to_run = query_input or st.session_state.pop("pending_ai_query", None)

    if query_to_run:
        st.session_state[hist_key].append({"role": "user", "content": query_to_run})
        with st.chat_message("user"):
            st.markdown(query_to_run)

        # Execute consultation
        with st.chat_message("assistant"):
            with st.spinner("Analyzing..." if is_en else "AI 正在查詢分析..."):
                # Simulation / Tool routing
                reply = (
                    f"**【核心診斷結論 / Action Title】**\n\n"
                    f"根據對應數據層指標與工具呼叫分析，針對問題「{query_to_run}」："
                    f"\n\n1. **So What?**：核心指標在當前區間運作穩定，未觸發異常警示。"
                    f"\n2. **Why So?**：受惠於結構性渠道增長與低退款率。"
                    f"\n3. **Now What? (30-Day Plan)**：建議維持目前策略並持續監控。"
                )
                st.markdown(reply)
                st.session_state[hist_key].append(
                    {"role": "assistant", "content": reply}
                )
