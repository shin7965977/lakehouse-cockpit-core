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

        # Sidebar Navigation: Direct Data Center Shortcut
        st.markdown("---")
        st.subheader("🏛️ Data Governance")
        st.caption("Centralized Catalog & Telemetry")
        is_data_center_active = st.session_state.get("cockpit_active_view") == "data_center"
        dc_btn_type = "primary" if is_data_center_active else "secondary"
        if st.button("🏛️ Open Data Center (dbt & Elementary)", use_container_width=True, type=dc_btn_type):
            st.session_state["cockpit_active_view"] = "data_center"
            st.rerun()

        if is_data_center_active:
            if st.button("⬅️ Return to Medallion BI", use_container_width=True):
                st.session_state["cockpit_active_view"] = "gold"
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
        if not st.session_state["advisor_drawer_open"]:
            h_col, expand_col = st.columns([9.3, 0.7])
            with h_col:
                st.title(adapter.brand_title)
                st.caption(
                    "Live Lakehouse Intelligence · Enterprise Medallion Architecture · Conversational Analytics Copilot"
                )
            with expand_col:
                if st.button("«", help="Open Analytics Engineering Copilot", key="btn_expand_drawer"):
                    st.session_state["advisor_drawer_open"] = True
                    st.rerun()
        else:
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

        # 2. Medallion Layer Selector + Looker Enterprise + Schema Designer + Data Center Option
        st.subheader("🏛️ Enterprise BI & Medallion Data Lakehouse Layers")
        nav_options = ["gold", "silver", "bronze"]
        if getattr(adapter, "supports_looker", False):
            nav_options.append("looker")
        if getattr(adapter, "supports_schema_designer", False):
            nav_options.append("schema_designer")
        if getattr(adapter, "supports_data_center", True):
            nav_options.append("data_center")

        current_view = st.session_state.get("cockpit_active_view", "gold")
        if current_view not in nav_options:
            current_view = "gold"
        default_idx = nav_options.index(current_view)

        def _on_view_change():
            st.session_state["cockpit_active_view"] = st.session_state["main_cockpit_view_selector"]

        selected_layer = st.radio(
            "Select View:",
            options=nav_options,
            index=default_idx,
            format_func=lambda x: {
                "gold": "🥇 Gold Marts (Business Aggregations & KPIs)",
                "silver": "🥈 Silver Layer (Kimball Conformed Facts & Dims)",
                "bronze": "🥉 Bronze Layer (Raw Ingestion Logs)",
                "looker": "📊 Looker Enterprise BI (LookML Explorer & Embed)",
                "schema_designer": "🏗️ Autonomous Schema Designer & dbt Engine",
                "data_center": "🏛️ Data Center (dbt Docs & Elementary)",
            }[x],
            horizontal=True,
            key="main_cockpit_view_selector",
            on_change=_on_view_change,
        )
        st.session_state["cockpit_active_view"] = selected_layer

        st.markdown("---")

        # Render Autonomous Schema Designer, Looker Native View, or Data Center
        if selected_layer == "data_center":
            try:
                from .components.data_center import render_data_center_view
            except (ImportError, ValueError):
                try:
                    from components.data_center import render_data_center_view
                except (ImportError, ValueError):
                    from cockpit_core.components.data_center import render_data_center_view
            render_data_center_view()
        elif selected_layer == "schema_designer":
            try:
                from .components.schema_designer import render_schema_designer_view
            except (ImportError, ValueError):
                try:
                    from components.schema_designer import render_schema_designer_view
                except (ImportError, ValueError):
                    from cockpit_core.components.schema_designer import render_schema_designer_view
            render_schema_designer_view(adapter=adapter, data_dict=data_dict, is_en=is_en)
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
