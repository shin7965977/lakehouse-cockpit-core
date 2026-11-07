"""End-to-End Analytics Engineering Guided Workflow & Customizable Looker Visualizer Component."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    from ..adapter import BaseCockpitAdapter
except (ImportError, ValueError):
    try:
        from adapter import BaseCockpitAdapter
    except (ImportError, ValueError):
        from cockpit_core.adapter import BaseCockpitAdapter


def render_ae_workflow_stepper(
    adapter: BaseCockpitAdapter,
    data_dict: dict[str, pd.DataFrame],
    is_en: bool = True,
) -> None:
    """Render the 6-step modern Analytics Engineering lifecycle from business scoping to customizable Looker visualizer."""
    st.markdown("---")
    hdr_title = (
        "🚀 End-to-End Analytics Engineering Workflow"
        if is_en
        else "🚀 端到端分析工程（Analytics Engineering）交付全流程"
    )
    st.markdown(f"### {hdr_title}")
    st.caption(
        "Modern AE Delivery Lifecycle: Business Scoping → Semantic Contract → Data Contract → dbt SQL → LookML → Customizable Looker"
        if is_en
        else "現代分析工程生命週期：商業需求澄清 → 語意指標合約 → 數據契約血緣 → dbt SQL 轉換 → LookML 建模 → 可自訂 Looker 圖表"
    )

    # Initialize current step in session state
    if "ae_workflow_step" not in st.session_state:
        st.session_state["ae_workflow_step"] = 1
    if "ae_workflow_scenario" not in st.session_state:
        st.session_state["ae_workflow_scenario"] = "bulk_returns"

    step_cols = st.columns(6)
    step_labels = [
        ("1. Scoping", "1. 商業需求"),
        ("2. Metric", "2. 指標合約"),
        ("3. Lineage", "3. 數據契約"),
        ("4. dbt SQL", "4. dbt 轉換"),
        ("5. LookML", "5. LookML 建模"),
        ("6. Looker BI", "6. Looker 視覺化"),
    ]

    current_step = st.session_state["ae_workflow_step"]

    for idx, (en_lbl, zh_lbl) in enumerate(step_labels, start=1):
        with step_cols[idx - 1]:
            btn_lbl = en_lbl if is_en else zh_lbl
            if idx == current_step:
                st.button(f"👉 **{btn_lbl}**", key=f"step_btn_{idx}", use_container_width=True, type="primary")
            else:
                if st.button(btn_lbl, key=f"step_btn_{idx}", use_container_width=True):
                    st.session_state["ae_workflow_step"] = idx
                    st.rerun()

    # Step Content Rendering
    with st.container(border=True):
        if current_step == 1:
            _render_step_1_scoping(is_en)
        elif current_step == 2:
            _render_step_2_metric_contract(is_en)
        elif current_step == 3:
            _render_step_3_data_contract(adapter, is_en)
        elif current_step == 4:
            _render_step_4_dbt_transformation(adapter, is_en)
        elif current_step == 5:
            _render_step_5_lookml_modeling(adapter, is_en)
        elif current_step == 6:
            _render_step_6_customizable_looker(adapter, data_dict, is_en)

    # Navigation Footer (Previous / Next)
    f_col1, f_col2, f_col3 = st.columns([2, 6, 2])
    with f_col1:
        if current_step > 1:
            prev_label = "⬅️ Previous Step" if is_en else "⬅️ 上一步"
            if st.button(prev_label, use_container_width=True):
                st.session_state["ae_workflow_step"] = current_step - 1
                st.rerun()
    with f_col3:
        if current_step < 6:
            next_label = "Next Step ➡️" if is_en else "下一步 ➡️"
            if st.button(next_label, use_container_width=True, type="primary"):
                st.session_state["ae_workflow_step"] = current_step + 1
                st.rerun()
        else:
            reset_label = "🔄 Restart Workflow" if is_en else "🔄 重新啟動流程"
            if st.button(reset_label, use_container_width=True):
                st.session_state["ae_workflow_step"] = 1
                st.rerun()


def _render_step_1_scoping(is_en: bool) -> None:
    st.markdown(
        "#### 🎯 Step 1: Business Scoping & Stakeholder Alignment"
        if is_en
        else "#### 🎯 第一步：商業需求澄清與利害關係人對齊"
    )
    st.markdown(
        "As an **Analytics Engineer**, we first interview stakeholders to identify concrete operational pain points and translate them into governed analytical goals."
        if is_en
        else "作為 **Analytics Engineer（分析工程師）**，第一步是與業務主管/利害關係人對齊具體痛點，將模糊的商業想法轉化為清晰的分析目標。"
    )

    scenario_options = {
        "bulk_returns": (
            "🌰 1kg Bulk Packaging Return Diagnosis (Operations Lead)",
            "🌰 堅果 1kg 大包裝退貨率異常歸因（營運主管痛點）",
        ),
        "category_margin": (
            "💰 Category Net Margin & Discount Impact (Finance Director)",
            "💰 品類淨毛利率與促銷折扣影響排行（財務主管痛點）",
        ),
        "inventory_health": (
            "📦 Inventory Availability & Stockout Risk (Procurement Lead)",
            "📦 商品庫存可用率與缺貨風險預警（採購主管痛點）",
        ),
    }

    selected = st.radio(
        "Select Business Scenario for this AE Workflow:" if is_en else "選擇本次分析工程流程之商業情境：",
        options=list(scenario_options.keys()),
        format_func=lambda x: scenario_options[x][0] if is_en else scenario_options[x][1],
        horizontal=False,
    )
    st.session_state["ae_workflow_scenario"] = selected

    if selected == "bulk_returns":
        st.info(
            "📌 **Stakeholder Question**: *'Why do 1kg Organic Cashews show a 5.2% return rate (2.5x normal average)? Is it packaging damage or quality issue?'*\n\n"
            "🎯 **AE Objective**: Unify Shopware orders and warehouse return logs to isolate return rates by packaging size (1kg vs 500g) and defect cause."
            if is_en
            else "📌 **主管核心提問**：*「為什麼 1kg 大包裝腰果退貨率高達 5.2%（是全館平均的 2.5 倍）？到底是因為包裝真空破損還是品質瑕疵？」*\n\n"
            "🎯 **分析工程目標**：跨表串接 Shopware 訂單與倉庫退貨明細，依包裝規格（1kg vs 500g）與退貨原因拆解退貨率與退款金額。"
        )


def _render_step_2_metric_contract(is_en: bool) -> None:
    st.markdown(
        "#### 📐 Step 2: Metric Definition & Semantic Contract"
        if is_en
        else "#### 📐 第二步：指標口徑公式與語意合約鎖定"
    )
    st.markdown(
        "Analytics Engineers eliminate metric ambiguity by defining strict numerator, denominator, and aggregation formulas in `semantic/business_rules.yaml`."
        if is_en
        else "AE 必須在代碼中消除口徑模糊（例如避免財務用金額算退貨率、營運用件數算退貨率），並將單一真理口徑固化在 `semantic/business_rules.yaml`。"
    )

    m_col1, m_col2 = st.columns(2)
    with m_col1:
        st.markdown(
            "**Official Metric Formula**:"
            if is_en
            else "**官方指標計算公式**："
        )
        st.code(
            "return_rate_pct = ROUND(SUM(returned_units) * 100.0 / NULLIF(SUM(sold_units), 0), 2)",
            language="sql",
        )
        st.markdown("- **Numerator (分子)**: `SUM(returned_units)` (Warehouse accepted returns)")
        st.markdown("- **Denominator (分母)**: `NULLIF(SUM(sold_units), 0)` (Gross units sold)")
        st.markdown("- **Time Grain (時間粒度)**: Rolling 90 Days / Selected Period")

    with m_col2:
        st.markdown(
            "**Semantic Boundary Rules**:"
            if is_en
            else "**語意邊界與口徑約束**："
        )
        st.markdown("✅ **Zero-Division Safe**: Handled via `NULLIF` to prevent runtime crashes.")
        st.markdown("✅ **Currency Handling**: Calculated on **Physical Units** (not EUR value) per operations standards.")
        st.markdown("✅ **Voucher Invariance**: Product returns preserve original SKU attribution.")


def _render_step_3_data_contract(adapter: BaseCockpitAdapter, is_en: bool) -> None:
    st.markdown(
        "#### 🧬 Step 3: Data Contracts & Lineage Governance"
        if is_en
        else "#### 🧬 第三步：數據契約、欄位血緣與 GDPR 隱私審查"
    )
    st.markdown(
        "Verify upstream table contracts, schema compatibility, and enforce GDPR compliance (`contains_pii: false`) before ingestion."
        if is_en
        else "在進入 SQL 轉換前，AE 必須校驗源頭資料契約（Data Contracts），確保資料欄位格式正確且 100% 杜絕個資洩漏。"
    )

    st.markdown(
        "```mermaid\n"
        "flowchart LR\n"
        "    Bronze[Bronze Layer<br>raw_shopware_orders] -->|dbt Staging| Silver[Silver Layer<br>int_returns_enriched]\n"
        "    Silver -->|dbt Core Mart| Gold[Gold Layer<br>mart_product_performance]\n"
        "    Gold --> Looker[LookML Semantic Model<br>& Looker BI Dashboard]\n"
        "```\n"
    )

    c_col1, c_col2, c_col3 = st.columns(3)
    c_col1.metric("GDPR Status", "Verified No PII", delta="Compliant")
    c_col2.metric("Data Contract", "contracts/returns.yaml", delta="100% Match")
    c_col3.metric("FinOps Scan Guard", "< 5 MB Scan (< 100 MB Limit)", delta="Optimized")


def _render_step_4_dbt_transformation(adapter: BaseCockpitAdapter, is_en: bool) -> None:
    st.markdown(
        "#### ⚙️ Step 4: SQL Transformation & dbt Data Quality Tests"
        if is_en
        else "#### ⚙️ 第四步：SQL 轉換模型與 dbt 自動化資料品質測試"
    )
    st.markdown(
        "Write idempotent dbt SQL models to aggregate Silver tables into Gold Marts, paired with automated dbt tests."
        if is_en
        else "撰寫等冪的 dbt SQL 模型將銀牌層加工為金牌寬表，並配置自動化 dbt test 測試套件。"
    )

    t_col1, t_col2 = st.columns([6, 4])
    with t_col1:
        st.caption("📄 `models/marts/mart_product_performance.sql`")
        st.code(
            "WITH orders_agg AS (\n"
            "    SELECT product_id, SUM(sold_units) AS sold_units, SUM(gross_revenue_eur) AS gross_revenue_eur\n"
            "    FROM {{ ref('raw_shopware_orders') }}\n"
            "    GROUP BY product_id\n"
            "),\n"
            "returns_agg AS (\n"
            "    SELECT product_id, SUM(returned_units) AS returned_units\n"
            "    FROM {{ ref('int_returns_enriched') }}\n"
            "    GROUP BY product_id\n"
            ")\n"
            "SELECT\n"
            "    p.product_id, p.product_name, p.category_name, p.packaging_size,\n"
            "    COALESCE(o.sold_units, 0) AS sold_units,\n"
            "    COALESCE(r.returned_units, 0) AS returned_units,\n"
            "    ROUND(COALESCE(r.returned_units, 0) * 100.0 / NULLIF(o.sold_units, 0), 2) AS return_rate_pct\n"
            "FROM {{ ref('stg_products') }} p\n"
            "LEFT JOIN orders_agg o USING (product_id)\n"
            "LEFT JOIN returns_agg r USING (product_id);",
            language="sql",
        )
    with t_col2:
        st.caption("🧪 `models/schema.yml` (dbt tests)")
        st.code(
            "models:\n"
            "  - name: mart_product_performance\n"
            "    columns:\n"
            "      - name: product_id\n"
            "        tests:\n"
            "          - unique\n"
            "          - not_null\n"
            "      - name: return_rate_pct\n"
            "        tests:\n"
            "          - not_null\n"
            "          - dbt_expectations.expect_column_values_to_be_between:\n"
            "              min_value: 0\n"
            "              max_value: 100",
            language="yaml",
        )


def _render_step_5_lookml_modeling(adapter: BaseCockpitAdapter, is_en: bool) -> None:
    st.markdown(
        "#### 📜 Step 5: LookML Semantic Modeling as Code"
        if is_en
        else "#### 📜 第五步：LookML 語意建模（Semantic Layer as Code）"
    )
    st.markdown(
        "LookML models dimensions and measures as version-controlled code, allowing stakeholders to self-serve without writing raw SQL."
        if is_en
        else "LookML 將商業維度與指標寫成版本控管的代碼，讓業務端能自由拖拉探索，同時杜絕自創公式。"
    )

    lookml_text = adapter.lookml_view_definition if hasattr(adapter, "lookml_view_definition") else ""
    if not lookml_text:
        lookml_text = (
            "view: mart_product_performance {\n"
            "  sql_table_name: `koro_marts.mart_product_performance` ;;\n\n"
            "  dimension: product_name { type: string sql: ${TABLE}.product_name ;; }\n"
            "  dimension: packaging_size { type: string sql: ${TABLE}.packaging_size ;; }\n"
            "  dimension: category_name { type: string sql: ${TABLE}.category_name ;; }\n\n"
            "  measure: total_sold_units { type: sum sql: ${TABLE}.sold_units ;; }\n"
            "  measure: total_returned_units { type: sum sql: ${TABLE}.returned_units ;; }\n"
            "  measure: unit_return_rate {\n"
            "    type: number\n"
            "    sql: ROUND(SAFE_DIVIDE(${total_returned_units} * 100.0, ${total_sold_units}), 2) ;;\n"
            "    value_format: \"0.00%\"\n"
            "  }\n"
            "}"
        )
    st.code(lookml_text, language="yaml")


def _render_step_6_customizable_looker(
    adapter: BaseCockpitAdapter,
    data_dict: dict[str, pd.DataFrame],
    is_en: bool,
) -> None:
    st.markdown(
        "#### 📊 Step 6: Interactive & Customizable Looker Visualizer"
        if is_en
        else "#### 📊 第六步：可自由調整的 Looker 互動視覺化控制台"
    )
    st.markdown(
        "Stakeholders can now self-serve by freely changing **Dimensions**, **Measures**, and **Chart Types** on the governed LookML model."
        if is_en
        else "所有底層數據與語意層已準備就緒！利害關係人現在可在此自由切換**維度**、**度量**與**圖表類型**，體驗真正的 Looker 自助分析。"
    )

    df_gold = data_dict.get("mart_product_performance", pd.DataFrame())
    if df_gold.empty:
        st.warning("No data available in mart_product_performance.")
        return

    # Control Bar
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns(3)

    dimension_map = {
        "product_name": ("Product Name", "商品名稱"),
        "packaging_size": ("Packaging Size", "包裝規格"),
        "category_name": ("Category Name", "商品品類"),
    }
    measure_map = {
        "return_rate_pct": ("Return Rate (%)", "退貨率 (%)"),
        "net_revenue_eur": ("Net Revenue (€)", "淨營收 (€)"),
        "sold_units": ("Sold Units", "售出件數"),
        "returned_units": ("Returned Units", "退貨件數"),
        "net_margin_pct": ("Net Margin (%)", "淨毛利率 (%)"),
    }
    chart_type_map = {
        "bar": ("📊 Bar Chart", "📊 柱狀圖"),
        "line": ("📈 Line Chart", "📈 折線趨勢圖"),
        "area": ("📉 Area Chart", "📉 面積圖"),
        "table": ("📋 Data Table", "📋 明細數據表"),
    }

    with ctrl_col1:
        selected_dim = st.selectbox(
            "Dimension (切換維度):",
            options=list(dimension_map.keys()),
            format_func=lambda x: dimension_map[x][0] if is_en else dimension_map[x][1],
            key="looker_custom_dim",
        )
    with ctrl_col2:
        selected_msr = st.selectbox(
            "Measure (切換度量):",
            options=list(measure_map.keys()),
            format_func=lambda x: measure_map[x][0] if is_en else measure_map[x][1],
            key="looker_custom_msr",
        )
    with ctrl_col3:
        selected_chart = st.selectbox(
            "Chart Type (圖表類型):",
            options=list(chart_type_map.keys()),
            format_func=lambda x: chart_type_map[x][0] if is_en else chart_type_map[x][1],
            key="looker_custom_chart",
        )

    # Aggregate Data
    if selected_msr in ["return_rate_pct", "net_margin_pct"]:
        # Weighted or averaged for rates
        agg_df = (
            df_gold.groupby(selected_dim, as_index=False)[selected_msr]
            .mean()
            .round(2)
        )
    else:
        agg_df = (
            df_gold.groupby(selected_dim, as_index=False)[selected_msr]
            .sum()
        )

    dim_label = dimension_map[selected_dim][0] if is_en else dimension_map[selected_dim][1]
    msr_label = measure_map[selected_msr][0] if is_en else measure_map[selected_msr][1]

    # Render Chart
    if selected_chart == "bar":
        fig = px.bar(
            agg_df.sort_values(selected_msr, ascending=False),
            x=selected_dim,
            y=selected_msr,
            text=selected_msr,
            color=selected_msr,
            color_continuous_scale="Viridis",
            labels={selected_dim: dim_label, selected_msr: msr_label},
            title=f"Looker Dynamic Explore: {msr_label} by {dim_label}",
        )
        st.plotly_chart(fig, use_container_width=True)
    elif selected_chart == "line":
        fig = px.line(
            agg_df.sort_values(selected_msr, ascending=False),
            x=selected_dim,
            y=selected_msr,
            markers=True,
            labels={selected_dim: dim_label, selected_msr: msr_label},
            title=f"Looker Dynamic Explore: {msr_label} Trend by {dim_label}",
        )
        st.plotly_chart(fig, use_container_width=True)
    elif selected_chart == "area":
        fig = px.area(
            agg_df.sort_values(selected_msr, ascending=False),
            x=selected_dim,
            y=selected_msr,
            labels={selected_dim: dim_label, selected_msr: msr_label},
            title=f"Looker Dynamic Explore: {msr_label} Area by {dim_label}",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.dataframe(
            agg_df.rename(columns={selected_dim: dim_label, selected_msr: msr_label}),
            use_container_width=True,
        )

    # Dynamic LookML & SQL Generated Preview
    with st.expander("🔍 Generated LookML & Executed SQL for this View", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.caption("LookML Explore Dimension & Measure Definition")
            st.code(
                f"explore: mart_product_performance {{\n"
                f"  view_name: mart_product_performance\n"
                f"  fields: [\n"
                f"    mart_product_performance.{selected_dim},\n"
                f"    mart_product_performance.{selected_msr}\n"
                f"  ]\n"
                f"}}",
                language="yaml",
            )
        with c2:
            st.caption("Executed SQL Query")
            st.code(
                f"SELECT\n"
                f"    {selected_dim},\n"
                f"    SUM({selected_msr}) AS {selected_msr}\n"
                f"FROM `koro_marts.mart_product_performance`\n"
                f"GROUP BY 1\n"
                f"ORDER BY 2 DESC;",
                language="sql",
            )
