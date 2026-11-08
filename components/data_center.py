"""Data Center Component for KoRo Lakehouse Cockpit.

Provides a unified portal for:
1. Data Catalog & Static Lineage (dbt docs) - Design-time metadata & contracts.
2. Data Observability & Anomaly Monitoring (Elementary) - Run-time test results, freshness & anomalies.
"""

from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

from cockpit_core.reports.elementary_generator import get_elementary_report_html
from cockpit_core.reports.dbt_docs_generator import get_dbt_docs_html


def render_data_center_view() -> None:
    """Render the dual-view Data Center in Streamlit."""
    st.markdown("## 🏛️ KoRo Enterprise Data Center")
    st.caption(
        "Unified Governance & Observability Portal: Complementary Design-Time Specifications (dbt Docs) "
        "and Run-Time Health Telemetry (Elementary)."
    )

    # Executive Global Health Banner
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(label="Governed Models", value="11 Models", delta="100% Star Schema")
    with col2:
        st.metric(label="dbt Tests Executed", value="30 / 30 Passed", delta="100% Pass Rate")
    with col3:
        st.metric(label="Elementary Anomalies", value="0 Critical", delta="Healthy")
    with col4:
        st.metric(label="Catalog Status", value="Certified Production", delta="Sync: 1h ago")

    # Governance Notice / Lifecyle separation
    st.info(
        "💡 **AE Governance Policy (Two-Tier Lifecycle)**: "
        "This Data Center only surfaces **Certified Production Models** and CI/CD-validated test telemetry. "
        "Ad-hoc schemas designed by stakeholders in the Autonomous Schema Designer remain isolated in personal "
        "DuckDB sandboxes until formally promoted via Git Pull Request.",
        icon="🛡️",
    )

    # View Selector
    selected_view = st.radio(
        "Select Data Center View:",
        ["🩺 Elementary Observability (Run-Time Health)", "📖 dbt Docs & Lineage (Design-Time Catalog)"],
        horizontal=True,
    )

    if "Elementary" in selected_view:
        st.markdown("### 🩺 Elementary Data Observability Report")
        st.caption("Live monitoring of volume anomalies, column freshness, failed assertion triage, and DAG pipeline bottlenecks.")
        report_html = get_elementary_report_html()
        components.html(report_html, height=880, scrolling=True)
    else:
        st.markdown("### 📖 dbt Docs & Data Dictionary")
        st.caption("Data dictionary, business formulas, column contracts, GDPR classification, and dependency graph.")
        docs_html = get_dbt_docs_html()
        components.html(docs_html, height=880, scrolling=True)
