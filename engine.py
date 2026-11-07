"""Main Cockpit Runtime Engine.

Assembles UI layout, Medallion layer navigation, KPI grid, On-Demand Insight Panel,
Interactive SQL Sandbox, Looker Enterprise view, and Conversational Copilot drawer.
"""

from __future__ import annotations

from datetime import date

import streamlit as st

try:
    from .adapter import BaseCockpitAdapter
    from .components.ai_drawer import render_ai_drawer
    from .components.kpi_grid import render_kpi_grid
    from .components.looker_view import render_looker_enterprise_view
    from .components.on_demand_panel import render_on_demand_insight_panel
    from .components.sql_sandbox import render_sql_sandbox
    from .styles import MODERN_COCKPIT_CSS
except ImportError:
    from adapter import BaseCockpitAdapter
    from components.ai_drawer import render_ai_drawer
    from components.kpi_grid import render_kpi_grid
    from components.looker_view import render_looker_enterprise_view
    from components.on_demand_panel import render_on_demand_insight_panel
    from components.sql_sandbox import render_sql_sandbox
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

        # Date Range Filter (Fully linked across all charts and SQL templates)
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

    # Load Domain Data strictly filtered by active Date Range
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
            "Live Lakehouse Intelligence · Enterprise Medallion Architecture · Conversational Analytics Copilot"
        )

        # 1. Responsive KPI Grid with ReadCall-style Hover Tooltips
        render_kpi_grid(cards=adapter.kpi_cards, data_dict=data_dict, is_en=is_en)

        # 1.5 Conditional Stakeholder On-Demand Insight Panel
        active_insight = st.session_state.get("active_on_demand_insight")
        if active_insight is not None:
            st.markdown("---")
            render_on_demand_insight_panel(insight=active_insight, is_en=is_en)

        st.markdown("---")

        # 2. Medallion Layer Selector + Looker Enterprise Option
        st.subheader("🏛️ Enterprise BI & Medallion Data Lakehouse Layers")
        nav_options = ["gold", "silver", "bronze"]
        if getattr(adapter, "supports_looker", False):
            nav_options.append("looker")
        if getattr(adapter, "supports_ae_workflow", False):
            nav_options.append("workflow")

        active_view_saved = st.session_state.get("cockpit_active_view", "gold")
        default_idx = nav_options.index(active_view_saved) if active_view_saved in nav_options else 0

        selected_layer = st.radio(
            "Select View:",
            options=nav_options,
            index=default_idx,
            format_func=lambda x: {
                "gold": "🥇 Gold Marts (Business Aggregations & KPIs)",
                "silver": "🥈 Silver Layer (Normalized Facts & Dimensions)",
                "bronze": "🥉 Bronze Layer (Raw Ingestion Logs)",
                "looker": "📊 Looker Enterprise BI (LookML Explorer & Embed)",
                "workflow": "🚀 AE Guided Workflow (6-Step Lifecycle & Customizable Looker)",
            }[x],
            horizontal=True,
            key="main_cockpit_view_selector",
        )
        st.session_state["cockpit_active_view"] = selected_layer

        st.markdown("---")

        # Render AE Guided Workflow or Looker Native View
        if selected_layer == "workflow":
            try:
                from .components.ae_workflow import render_ae_workflow_stepper
            except (ImportError, ValueError):
                try:
                    from components.ae_workflow import render_ae_workflow_stepper
                except (ImportError, ValueError):
                    from cockpit_core.components.ae_workflow import render_ae_workflow_stepper
            render_ae_workflow_stepper(adapter=adapter, data_dict=data_dict, is_en=is_en)
        elif selected_layer == "looker":
            render_looker_enterprise_view(adapter=adapter, data_dict=data_dict, is_en=is_en)
        else:
            # Display Selected Medallion Layer Preview (with explicit start_date and end_date injection)
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
                            # Pass start_date and end_date into render_content for 1:1 SQL and Chart alignment
                            tab_spec.render_content(
                                data_dict, is_en, adapter, start_date, end_date
                            )

        # 3. Interactive SQL Sandbox (Permanent Footer Explorer for Data Verification)
        st.markdown("---")
        render_sql_sandbox(
            adapter=adapter,
            data_dict=data_dict,
            start_date=start_date,
            end_date=end_date,
            is_en=is_en,
        )

    # Right-Hand AI Copilot Drawer (Open Conversational Understanding)
    if ai_col is not None:
        with ai_col:
            render_ai_drawer(
                active_scope_id=selected_layer,
                active_scope_title=selected_layer.upper(),
                default_prompt_en=adapter.default_system_prompt_en,
                default_prompt_zh=adapter.default_system_prompt_zh,
                is_en=is_en,
                quick_questions=(
                    adapter.quick_diagnosis_questions_zh
                    if not is_en
                    else adapter.quick_diagnosis_questions_en
                ),
                adapter=adapter,
                data_dict=data_dict,
                start_date=start_date,
                end_date=end_date,
            )
