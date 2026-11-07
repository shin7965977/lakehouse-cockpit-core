"""On-Demand Insight Panel with Executive Takeaway & 3-Layer Traceability Inspector."""

from __future__ import annotations

import streamlit as st

try:
    from ..adapter import OnDemandInsightResult
except ImportError:
    from adapter import OnDemandInsightResult


def render_on_demand_insight_panel(
    insight: OnDemandInsightResult,
    is_en: bool = True,
) -> None:
    """Render the prominent On-Demand Insight Panel beneath the 4 top-level KPI cards."""
    header_label = (
        "🎯 Stakeholder On-Demand Insight"
        if is_en
        else "🎯 Stakeholder 即時動態洞察面板"
    )
    st.markdown(f"### {header_label} · `{insight.spec_id}`")

    # Governance & Compliance Badges
    if insight.governance_badges:
        badge_str = " &nbsp;|&nbsp; ".join(
            [f"✅ **{b}**" for b in insight.governance_badges]
        )
        st.caption(badge_str)

    # 1. Executive Takeaway Card (MBB "So What?")
    takeaway_label = (
        "💡 Executive Takeaway (So What?)"
        if is_en
        else "💡 高階主管一句話結論 (Executive Takeaway)"
    )
    st.info(f"**{takeaway_label}**\n\n{insight.executive_takeaway}")

    # 2. Interactive Plotly Chart
    if insight.figure is not None:
        st.plotly_chart(insight.figure, use_container_width=True)

    # 3. 3-Layer Traceability Inspector
    inspector_title = (
        f"🔍 3-Layer Traceability & Code Inspector — {insight.title}"
        if is_en
        else f"🔍 3 層程式碼可觀測與溯源抽屜 — {insight.title}"
    )
    with st.expander(inspector_title, expanded=True):
        tab_labels = (
            [
                "1️⃣ Executed SQL",
                "2️⃣ Metric Lineage & Dictionary",
                "3️⃣ Aligned Data Snippet (Top 5)",
            ]
            if is_en
            else [
                "1️⃣ 實際執行 SQL (Executed SQL)",
                "2️⃣ 指標血緣與資料字典 (Metric Lineage)",
                "3️⃣ 1:1 對齊數據切片 (Data Snippet)",
            ]
        )
        t_sql, t_lin, t_data = st.tabs(tab_labels)

        with t_sql:
            st.markdown(f"**Governed Source Mart**: `{insight.source_table_label}`")
            st.code(insight.executed_sql, language="sql")

        with t_lin:
            st.markdown(insight.lineage_markdown)

        with t_data:
            if insight.df_snippet is not None and not insight.df_snippet.empty:
                st.dataframe(insight.df_snippet.head(5), use_container_width=True)
            else:
                st.info("No rows returned.")
