"""Interactive Schema Designer & Observability Inspector Component.

Renders an enterprise AE workbench allowing stakeholders to articulate business goals,
inspect real-time Socratic alignment, review synthesized dbt models & schema.yml,
and trace the end-to-end execution audit log.
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
from typing import Any

from ai.schema_engine import DatabaseReflectionService, compile_dbt_and_schema_contract


def render_schema_designer_view(
    adapter: Any,
    data_dict: dict[str, pd.DataFrame],
    is_en: bool = True,
) -> None:
    """Render the Schema Design & dbt Transformation Engine View with Observability."""
    st.markdown(
        "### 🏗️ Autonomous Schema Designer & dbt Transformation Engine"
        if is_en
        else "### 🏗️ 自動化 Schema 設計與 dbt 模型轉換引擎"
    )
    st.caption(
        "Bridge the gap between natural language business inquiries and governed dbt pipelines. "
        "AI reflects on conformed Silver Kimball models to synthesize, dry-run, and govern production-ready schemas."
        if is_en
        else "將業務口語需求直接編譯為受控管的 dbt 湖倉管線。AI 自動反射 Silver Kimball 星狀模型，現場生成並測試生產級 Schema 與 SQL。"
    )

    reflection = DatabaseReflectionService(data_dict)
    models_meta = reflection.get_kimball_models()

    # Top Metric Banner (Reflected Metadata)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Reflected Dimensions", f"{len(models_meta['dimensions'])} Models", delta="dim_products/cat/cust")
    c2.metric("Reflected Facts", f"{len(models_meta['facts'])} Models", delta="fct_items/orders/returns")
    c3.metric("GDPR Boundary", "Enforced", delta="100% No PII")
    c4.metric("Engine Runtime", "DuckDB In-Memory", delta="Sub-second Dry-run")

    st.markdown("---")

    # Stakeholder Goal Alignment
    st.markdown(
        "#### 💬 1. Stakeholder Business Goal & Socratic Anchoring"
        if is_en
        else "#### 💬 1. 利害關係人商業目標與蘇格拉底式定錨"
    )

    preset_options = {
        "bulk_returns": (
            "🌰 1kg Bulk Packaging Return Diagnosis (Operations Lead: Return Rate by Packaging Size)",
            "🌰 1kg 堅果大包裝退貨率異常歸因（營運主管：依包裝規格拆解退貨率與破損）",
        ),
        "category_margin": (
            "💰 Category Margin Dilution (Finance Lead: Net Contribution Margin after Vouchers)",
            "💰 品類毛利稀釋排行（財務主管：扣除促銷折價券後之淨貢獻毛利率）",
        ),
    }

    selected_preset = st.selectbox(
        "Select Business Scenario or Input Custom Goal:" if is_en else "選擇預設商業場景或自由輸入目標：",
        options=list(preset_options.keys()),
        format_func=lambda x: preset_options[x][0] if is_en else preset_options[x][1],
    )

    custom_input = st.text_input(
        "Custom Analytical Goal & Criteria:" if is_en else "自訂分析目標與細節條件：",
        value=st.session_state.get("schema_designer_goal", preset_options[selected_preset][0]),
        placeholder="e.g., I want to analyze return rates across packaging sizes and isolate defects...",
        key="schema_designer_custom_goal",
    )

    # Action Trigger
    btn_label = "🚀 Synthesize & Validate dbt Pipeline" if is_en else "🚀 合成並驗證 dbt 數據管線"
    if st.button(btn_label, type="primary", use_container_width=True):
        st.session_state["active_schema_design_trigger"] = True

    if st.session_state.get("active_schema_design_trigger", False):
        with st.spinner("Reflecting on Silver schema, compiling dbt model, and executing DuckDB dry-run..."):
            result = compile_dbt_and_schema_contract(
                scenario_key=selected_preset,
                user_goal=custom_input,
                reflection=reflection,
            )

        st.success(f"✅ Pipeline Compiled & Governed: `{result.spec_id}`")

        # Observability Audit Trace
        st.markdown("##### 🔍 Observability Audit Trail (實時編譯與執行追蹤)")
        cols = st.columns(len(result.audit_trace))
        for idx, trace in enumerate(result.audit_trace):
            with cols[idx]:
                st.markdown(
                    f"""
                    <div style="background-color: #F8FAFC; border: 1px solid #CBD5E1; border-radius: 6px; padding: 10px; font-size: 0.85rem;">
                        <div style="font-weight: 600; color: #1E293B;">{trace['step']}</div>
                        <div style="color: #059669; font-weight: 500; margin: 2px 0;">● {trace['status']}</div>
                        <div style="color: #64748B; font-size: 0.78rem;">{trace['detail']}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        st.markdown("---")

        # 3-Layer Inspector Tabs
        tab_biz, tab_contract, tab_sql = st.tabs(
            [
                "📊 Business Takeaway & Live Data Preview",
                "📜 Governed Data Contract (`schema.yml`)",
                "⚙️ Synthesized dbt Model (`.sql`)",
            ]
        )

        with tab_biz:
            st.markdown(f"**Target Grain**: `{result.target_grain}` | **Upstream Kimball Dependencies**: `{', '.join(result.upstream_tables)}`")
            st.markdown("##### 5-Row Verified Data Preview (DuckDB Dry-Run Output):")
            st.dataframe(result.data_preview.head(10), use_container_width=True)

        with tab_contract:
            st.caption("📄 `models/marts/schema.yml` (Version-controlled Data Dictionary & Contract)")
            st.code(result.schema_yaml, language="yaml")
            st.download_button(
                "📥 Download schema.yml",
                data=result.schema_yaml,
                file_name="schema.yml",
                mime="text/yaml",
                use_container_width=True,
            )

        with tab_sql:
            st.caption("📄 `models/marts/<model_name>.sql` (Idempotent dbt Transformation Model)")
            st.code(result.dbt_sql, language="sql")
            st.download_button(
                "📥 Download dbt Model (.sql)",
                data=result.dbt_sql,
                file_name="mart_model.sql",
                mime="text/plain",
                use_container_width=True,
            )
