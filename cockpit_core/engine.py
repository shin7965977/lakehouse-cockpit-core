"""Main Cockpit Runtime Engine.

Assembles UI layout, Medallion layer navigation, KPI grid, and AI drawer
driven entirely by an injected BaseCockpitAdapter.
"""

from __future__ import annotations

import streamlit as st
from cockpit_core.adapter import BaseCockpitAdapter
from cockpit_core.components.ai_drawer import render_ai_drawer
from cockpit_core.components.kpi_grid import render_kpi_grid
from cockpit_core.styles import MODERN_COCKPIT_CSS


def launch_cockpit_app(adapter: BaseCockpitAdapter) -> None:
    """Entrypoint to launch the full-featured Lakehouse Cockpit."""
    try:
        st.set_page_config(
            page_title=adapter.brand_title,
            page_icon=adapter.brand_icon,
            layout="wide",
            initial_sidebar_state="expanded",
        )
    except Exception:
        pass

    # Inject global CSS
    st.markdown(MODERN_COCKPIT_CSS, unsafe_allow_html=True)

    # Session State for Language & Advisor Drawer
    if "cockpit_lang" not in st.session_state:
        st.session_state["cockpit_lang"] = adapter.default_language
    if "advisor_drawer_open" not in st.session_state:
        st.session_state["advisor_drawer_open"] = False

    is_en = st.session_state["cockpit_lang"] == "en"

    # Sidebar: Global Navigation & Controls
    with st.sidebar:
        st.title(f"{adapter.brand_icon} {adapter.brand_title}")
        st.caption(f"⚡ {adapter.brand_badge_text}")

        # Language Toggle
        lang_choice = st.radio(
            "Language / 語系",
            options=["English", "繁體中文"],
            index=0 if is_en else 1,
            horizontal=True,
        )
        st.session_state["cockpit_lang"] = "en" if lang_choice == "English" else "zh"
        is_en = st.session_state["cockpit_lang"] == "en"

        # Date Range Filter
        st.markdown("---")
        st.subheader("📅 " + ("Date Range Filter" if is_en else "日期區間篩選"))
        date_range = st.date_input(
            "Filter Period:" if is_en else "選擇時間區間：",
            value=[],
        )
        start_date = date_range[0] if isinstance(date_range, (list, tuple)) and len(date_range) > 0 else None
        end_date = date_range[1] if isinstance(date_range, (list, tuple)) and len(date_range) > 1 else None

        # Advisor Drawer Toggle Button
        st.markdown("---")
        drawer_btn_label = (
            ("🤖 Close Advisor" if is_en else "🤖 關閉 AI 顧問")
            if st.session_state["advisor_drawer_open"]
            else ("🤖 Open AI Advisor" if is_en else "🤖 開啟 AI 顧問")
        )
        if st.button(drawer_btn_label, use_container_width=True, type="primary"):
            st.session_state["advisor_drawer_open"] = not st.session_state["advisor_drawer_open"]
            st.rerun()

    # Load Domain Data
    data_dict = adapter.load_domain_data(start_date=start_date, end_date=end_date)

    # Top-Level Layout: Adaptive Columns depending on Advisor Drawer state
    if st.session_state["advisor_drawer_open"]:
        main_col, ai_col = st.columns([7, 3])
    else:
        main_col = st.container()
        ai_col = None

    # Main BI Dashboard
    with main_col:
        st.title(adapter.brand_title)
        st.caption("Live Lakehouse Intelligence · Enterprise Medallion Architecture")

        # 1. Responsive KPI Grid
        render_kpi_grid(cards=adapter.kpi_cards, data_dict=data_dict, is_en=is_en)

        st.markdown("---")

        # 2. Medallion Layer Selector
        st.subheader("🏛️ " + ("Medallion Data Lakehouse Layers" if is_en else "現代湖倉資料分層導覽"))
        selected_layer = st.radio(
            "Select Layer / 選擇層級：",
            options=["gold", "silver", "bronze"],
            format_func=lambda x: {
                "gold": "🥇 Gold Marts (Business Aggregations & KPIs)" if is_en else "🥇 金牌層 (業務聚合集市 & 核心 KPI)",
                "silver": "🥈 Silver Layer (Normalized Facts & Dimensions)" if is_en else "🥈 銀牌層 (標準化維度與事實表)",
                "bronze": "🥉 Bronze Layer (Raw Ingestion Logs)" if is_en else "🥉 銅牌層 (原始日誌與同步歷程)",
            }[x],
            horizontal=True,
        )

        st.markdown("---")
        # Display layer preview
        layer_specs = adapter.medallion_layers.get(selected_layer, {})
        if isinstance(layer_specs, dict) and "tabs" in layer_specs:
            tab_names = [
                (t.title_en if is_en else t.title_zh) for t in layer_specs["tabs"]
            ]
            if tab_names:
                rendered_tabs = st.tabs(tab_names)
                for idx, tab_spec in enumerate(layer_specs["tabs"]):
                    with rendered_tabs[idx]:
                        tab_spec.render_content(data_dict, is_en, adapter)

    # Right-Hand AI Copilot Drawer
    if ai_col is not None:
        with ai_col:
            render_ai_drawer(
                tools=adapter.get_fastmcp_tools(),
                default_prompt_zh=adapter.default_system_prompt_zh,
                default_prompt_en=adapter.default_system_prompt_en,
                active_scope_id=selected_layer,
                active_scope_title=selected_layer.upper(),
                is_en=is_en,
                quick_questions=(
                    adapter.quick_diagnosis_questions_en
                    if is_en
                    else adapter.quick_diagnosis_questions_zh
                ),
            )
