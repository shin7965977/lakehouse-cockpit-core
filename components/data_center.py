"""Data Center Component for KoRo Lakehouse Cockpit.

Provides a full-page, clean portal for:
1. Data Observability & Anomaly Monitoring (Elementary) - Run-time test results, freshness & anomalies.
2. Data Catalog & Static Lineage (dbt docs) - Design-time metadata & contracts.
"""

from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

from cockpit_core.reports.elementary_generator import get_elementary_report_html
from cockpit_core.reports.dbt_docs_generator import get_dbt_docs_html


def render_data_center_view() -> None:
    """Render the full-width Data Center in Streamlit with zero clutter."""
    # Top Control Bar (Clean Toggle)
    view_col1, view_col2 = st.columns([8, 2])
    with view_col1:
        selected_view = st.radio(
            "Select Portal:",
            ["🩺 Elementary Observability", "📖 dbt Docs & Lineage"],
            horizontal=True,
            label_visibility="collapsed",
            key="datacenter_subview_toggle",
        )
    with view_col2:
        if st.button("⬅️ Back to BI Dashboard", use_container_width=True):
            st.session_state["cockpit_active_view"] = "gold"
            st.rerun()

    # Direct Fullscreen Embed (No useless metric cards or redundant boxes)
    if "Elementary" in selected_view:
        report_html = get_elementary_report_html()
        components.html(report_html, height=950, scrolling=True)
    else:
        docs_html = get_dbt_docs_html()
        components.html(docs_html, height=950, scrolling=True)
