"""Main Cockpit Runtime Engine.

Assembles UI layout, Medallion layer navigation, KPI grid, On-Demand Insight Panel,
and AI drawer driven entirely by an injected BaseCockpitAdapter.
"""

from __future__ import annotations

import streamlit as st

try:
    from .adapter import BaseCockpitAdapter
    from .components.ai_drawer import render_ai_drawer
    from .components.kpi_grid import render_kpi_grid
    from .components.on_demand_panel import render_on_demand_insight_panel
    from .styles import MODERN_COCKPIT_CSS
except ImportError:
    from adapter import BaseCockpitAdapter
    from components.ai_drawer import render_ai_drawer
    from components.kpi_grid import render_kpi_grid
    from components.on_demand_panel import render_on_demand_insight_panel
    from styles import MODERN_COCKPIT_CSS


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
        st.session_state["advisor_drawer_open"] = True

    is_en = st.session_state["cockpit_lang"] == "en"

    # Sidebar: Global Navigation & Controls
    with st.sidebar:
        st.title(f"{adapter.brand_icon} {adapter.brand_title}")
        st.caption(f"⚡ {adapter.brand_badge_text}")

        # Language Selector
        lang_choice = st.radio(
            "Language",
            options=["English", "Traditional Chinese"],
            index=0 if is_en else 1,
            horizontal=True,
        )
        st.session_state["cockpit_lang"] = "en" if lang_choice == "English" else "zh"
        is_en = st.session_state["cockpit_lang"] == "en"

        # Date Range Filter
        st.markdown("---")
        st.subheader("📅 Date Range Filter")
        date_range = st.date_input(
            "Filter Period:",
            value=[],
        )
        start_date = (
            date_range[0]
            if isinstance(date_range, (list, tuple)) and len(date_range) > 0
            else None
        )
        end_date = (
            date_range[1]
            if isinstance(date_range, (list, tuple)) and len(date_range) > 1
            else None
        )

        # Advisor Drawer Toggle Button
        st.markdown("---")
        drawer_btn_label = (
            "🤖 Close Advisor"
            if st.session_state["advisor_drawer_open"]
            else "🤖 Open AI Advisor"
        )
        if st.button(drawer_btn_label, use_container_width=True, type="primary"):
            st.session_state["advisor_drawer_open"] = not st.session_state[
                "advisor_drawer_open"
            ]
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
        st.caption(
            "Live Lakehouse Intelligence · Enterprise Medallion Architecture · Socratic Spec Engine"
        )

        # 1. Responsive KPI Grid
        render_kpi_grid(cards=adapter.kpi_cards, data_dict=data_dict, is_en=is_en)

        # 1.5 Conditional Stakeholder On-Demand Insight Panel (Directly beneath KPI Cards)
        active_insight = st.session_state.get("active_on_demand_insight")
        if active_insight is not None:
            st.markdown("---")
            render_on_demand_insight_panel(insight=active_insight, is_en=is_en)

        st.markdown("---")

        # 2. Medallion Layer Selector
        st.subheader("🏛️ Medallion Data Lakehouse Layers")
        selected_layer = st.radio(
            "Select Layer:",
            options=["gold", "silver", "bronze"],
            format_func=lambda x: {
                "gold": "🥇 Gold Marts (Business Aggregations & KPIs)",
                "silver": "🥈 Silver Layer (Normalized Facts & Dimensions)",
                "bronze": "🥉 Bronze Layer (Raw Ingestion Logs)",
            }[x],
            horizontal=True,
        )

        st.markdown("---")
        # Display layer preview
        layer_specs = adapter.medallion_layers.get(selected_layer, {})
        if isinstance(layer_specs, dict) and "tabs" in layer_specs:
            tab_names = [
                (
                    t.title_en
                    if is_en and getattr(t, "title_en", None)
                    else getattr(t, "title_zh", getattr(t, "title_en", str(t)))
                )
                for t in layer_specs["tabs"]
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
                    adapter.quick_diagnosis_questions_zh
                    if not is_en
                    else adapter.quick_diagnosis_questions_en
                ),
                adapter=adapter,
                data_dict=data_dict,
            )
