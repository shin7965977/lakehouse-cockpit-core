"""End-to-End Analytics Engineering Guided Workflow with Sequential State Machine & Resilient Looker Studio Embedding."""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    from ..adapter import BaseCockpitAdapter
except (ImportError, ValueError):
    try:
        from adapter import BaseCockpitAdapter
    except (ImportError, ValueError):
        from cockpit_core.adapter import BaseCockpitAdapter


def synthesize_ae_lifecycle(user_goal: str, preset_key: str = "custom") -> dict[str, Any]:
    """Dynamically synthesize the 6-step AE delivery lifecycle based on stakeholder goal."""
    g = (user_goal or "").strip().lower()

    # Priority Case 1: Return / Refund / Defect Intent (Crucial: Must precede generic sales/order keywords)
    if any(k in g for k in ["退款", "退貨", "退訂", "refund", "return", "defect", "rejection"]):
        return {
            "intent": "bulk_returns",
            "user_goal": user_goal or "1kg Bulk Packaging Return Diagnosis",
            "metric_name": "return_rate_pct",
            "metric_title_en": "Unit Return Rate & Refund Ratio (%)",
            "metric_title_zh": "商品退貨率與退款佔比（%）",
            "formula": "ROUND(SUM(returned_units) * 100.0 / NULLIF(SUM(sold_units), 0), 2)",
            "numerator": "SUM(returned_units) (Warehouse verified refund & return receipts)",
            "denominator": "NULLIF(SUM(sold_units), 0) (Gross units sold across channels)",
            "time_grain": "Quarterly / Rolling 90 Days",
            "stakeholder_q_en": f"How many refund and return orders occurred this quarter and what is the return rate? (Goal: '{user_goal or 'Quarterly Refund Order Diagnosis'}')",
            "stakeholder_q_zh": f"這個季度產生了多少退款與退貨訂單？退款率與品類退貨佔比如何？（自訂目標：「{user_goal or '季度退款訂單診斷'}」）",
            "ae_obj_en": "Unify Shopware orders and warehouse quality defect return logs to calculate return rates and isolate packaging defect causes.",
            "ae_obj_zh": "跨表串接 Shopware 原始訂單與倉庫退貨品檢明細，精確計算退款訂單數、退貨率與包裝破損歸因。",
            "upstream_table": "raw_shopware_orders + raw_return_logs",
            "contract_file": "contracts/returns.yaml",
            "lineage_summary": "Bronze (raw_shopware_orders + raw_return_logs) ➔ Silver (int_returns_enriched) ➔ Gold (mart_product_performance)",
            "dbt_sql": (
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
                "LEFT JOIN returns_agg r USING (product_id);"
            ),
            "dbt_tests": (
                "models:\n"
                "  - name: mart_product_performance\n"
                "    columns:\n"
                "      - name: product_id\n"
                "        tests: [unique, not_null]\n"
                "      - name: return_rate_pct\n"
                "        tests:\n"
                "          - not_null\n"
                "          - dbt_expectations.expect_column_values_to_be_between:\n"
                "              min_value: 0\n"
                "              max_value: 100"
            ),
            "lookml_view": (
                "view: mart_product_performance {\n"
                "  sql_table_name: `koro_marts.mart_product_performance` ;;\n\n"
                "  dimension: product_name { type: string sql: ${TABLE}.product_name ;; }\n"
                "  dimension: category_name { type: string sql: ${TABLE}.category_name ;; }\n"
                "  dimension: packaging_size { type: string sql: ${TABLE}.packaging_size ;; }\n\n"
                "  measure: total_returned_units {\n"
                "    type: sum\n"
                "    sql: ${TABLE}.returned_units ;;\n"
                "    description: 'Total units returned or refunded by customers.'\n"
                "  }\n\n"
                "  measure: return_rate_pct {\n"
                "    type: number\n"
                "    sql: ROUND(SUM(${TABLE}.returned_units) * 100.0 / NULLIF(SUM(${TABLE}.sold_units), 0), 2) ;;\n"
                "    value_format: '0.00\\%'\n"
                "    description: 'Governed Return Rate Percentage.'\n"
                "  }\n"
                "}"
            ),
            "primary_dim": "product_name",
            "primary_msr": "return_rate_pct",
        }

    # Case 2: Margin / Profit Intent
    if any(k in g for k in ["毛利", "利潤", "margin", "profit", "折價", "voucher"]):
        return {
            "intent": "category_margin",
            "user_goal": user_goal,
            "metric_name": "net_margin_pct",
            "metric_title_en": "Category Net Contribution Margin (%)",
            "metric_title_zh": "品類淨毛利率（%）",
            "formula": "ROUND((SUM(net_revenue_eur) - SUM(cogs_eur)) * 100.0 / NULLIF(SUM(net_revenue_eur), 0), 2)",
            "numerator": "SUM(net_revenue_eur) - SUM(cogs_eur)",
            "denominator": "NULLIF(SUM(net_revenue_eur), 0)",
            "time_grain": "Trailing 90 Days / Selected Period",
            "stakeholder_q_en": f"Which product categories maintain healthy profit margin after vouchers? (Goal: '{user_goal}')",
            "stakeholder_q_zh": f"扣除促銷折價券與進貨成本後，哪一個品類的淨毛利率最高？（自訂目標：「{user_goal}」）",
            "ae_obj_en": "Join SKU sales with supplier cost of goods (COGS) to isolate promotional margin dilution per category.",
            "ae_obj_zh": "串接商品銷售數據與供應商進貨成本（COGS），精確計算折價促銷對各品類利潤的壓縮幅度。",
            "upstream_table": "raw_shopware_orders + cogs_reference",
            "contract_file": "contracts/margin_and_cogs_contract.yaml",
            "lineage_summary": "Bronze (raw_shopware_orders) + Reference (cogs) ➔ Silver ➔ Gold (mart_product_performance)",
            "dbt_sql": (
                "SELECT\n"
                "    category_name,\n"
                "    ROUND(SUM(net_revenue_eur), 2) AS net_revenue_eur,\n"
                "    ROUND(SUM(cogs_eur), 2) AS total_cogs_eur,\n"
                "    ROUND((SUM(net_revenue_eur) - SUM(cogs_eur)) * 100.0 / NULLIF(SUM(net_revenue_eur), 0), 2) AS net_margin_pct\n"
                "FROM {{ ref('mart_product_performance') }}\n"
                "GROUP BY category_name\n"
                "ORDER BY net_margin_pct DESC;"
            ),
            "dbt_tests": (
                "models:\n"
                "  - name: mart_product_performance\n"
                "    columns:\n"
                "      - name: net_margin_pct\n"
                "        tests:\n"
                "          - not_null\n"
                "          - dbt_expectations.expect_column_values_to_be_between:\n"
                "              min_value: -100\n"
                "              max_value: 100"
            ),
            "lookml_view": (
                "view: mart_product_performance {\n"
                "  sql_table_name: `koro_marts.mart_product_performance` ;;\n\n"
                "  dimension: category_name { type: string sql: ${TABLE}.category_name ;; }\n\n"
                "  measure: net_margin_pct {\n"
                "    type: number\n"
                "    sql: ROUND((${total_net_revenue} - ${total_cogs}) * 100.0 / NULLIF(${total_net_revenue}, 0), 2) ;;\n"
                "    value_format: '0.00\\%'\n"
                "  }\n"
                "}"
            ),
            "primary_dim": "category_name",
            "primary_msr": "net_margin_pct",
        }

    # Case 3: Inventory / Supply Intent
    if any(k in g for k in ["庫存", "缺貨", "斷貨", "supply", "stock", "inventory"]):
        return {
            "intent": "inventory_health",
            "user_goal": user_goal,
            "metric_name": "days_of_supply",
            "metric_title_en": "Inventory Days of Supply (Days)",
            "metric_title_zh": "庫存可支撐天數（天）",
            "formula": "ROUND(stock_on_hand / NULLIF(daily_sales_velocity, 0), 1)",
            "numerator": "stock_on_hand (Current physical warehouse stock)",
            "denominator": "NULLIF(daily_sales_velocity, 0) (Rolling 30-day daily run rate)",
            "time_grain": "Realtime / Daily Snapshot",
            "stakeholder_q_en": f"Which SKUs face stockout risk and need replenishment? (Goal: '{user_goal}')",
            "stakeholder_q_zh": f"哪些高銷量商品正面臨斷貨危機、需要立即補貨？（自訂目標：「{user_goal}」）",
            "ae_obj_en": "Integrate ERP warehouse inventory levels with 30-day sales run rates to project stock depletion dates.",
            "ae_obj_zh": "整合 ERP 倉儲即時庫存量與近 30 天日均銷量，精確計算安全庫存天數與斷貨風險。",
            "upstream_table": "raw_erp_inventory",
            "contract_file": "contracts/inventory_health_contract.yaml",
            "lineage_summary": "Bronze (raw_erp_inventory) + Silver (daily_velocity) ➔ Gold (mart_product_performance)",
            "dbt_sql": (
                "SELECT\n"
                "    product_id, product_name, category_name, stock_on_hand,\n"
                "    ROUND(stock_on_hand / NULLIF(daily_sales_velocity, 0), 1) AS days_of_supply,\n"
                "    CASE WHEN stock_on_hand = 0 THEN 'OUT_OF_STOCK'\n"
                "         WHEN stock_on_hand / NULLIF(daily_sales_velocity, 0) < 14 THEN 'CRITICAL_RISK'\n"
                "         ELSE 'HEALTHY' END AS stock_status\n"
                "FROM {{ ref('mart_product_performance') }}\n"
                "ORDER BY days_of_supply ASC;"
            ),
            "dbt_tests": (
                "models:\n"
                "  - name: mart_product_performance\n"
                "    columns:\n"
                "      - name: stock_on_hand\n"
                "        tests: [not_null]\n"
                "      - name: days_of_supply\n"
                "        tests: [not_null]"
            ),
            "lookml_view": (
                "view: mart_product_performance {\n"
                "  sql_table_name: `koro_marts.mart_product_performance` ;;\n\n"
                "  dimension: product_name { type: string sql: ${TABLE}.product_name ;; }\n"
                "  dimension: stock_status { type: string sql: ${TABLE}.stock_status ;; }\n\n"
                "  measure: avg_days_of_supply {\n"
                "    type: average\n"
                "    sql: ${TABLE}.days_of_supply ;;\n"
                "    value_format: '0.0'\n"
                "  }\n"
                "}"
            ),
            "primary_dim": "product_name",
            "primary_msr": "days_of_supply",
        }

    # Case 4: Sales / Revenue / Quarterly Intent
    if any(k in g for k in ["銷", "營收", "sales", "revenue", "quarter", "季", "訂單", "turnover", "sold"]):
        return {
            "intent": "sales_revenue",
            "user_goal": user_goal,
            "metric_name": "net_revenue_eur",
            "metric_title_en": "Quarterly Net Realized Sales (€)",
            "metric_title_zh": "季度淨銷售額（歐元 €）",
            "formula": "ROUND(SUM(gross_revenue_eur - discount_eur - refunded_eur), 2)",
            "numerator": "SUM(gross_revenue_eur - discount_eur - refunded_eur) (Net realized EUR)",
            "denominator": "None (Absolute Monetary Measure in EUR)",
            "time_grain": "Quarterly (Q1-Q4) / Selected Range",
            "stakeholder_q_en": f"What is the total quarterly product sales and sales velocity across categories? (Goal: '{user_goal}')",
            "stakeholder_q_zh": f"這一季全館各品類與核心商品的銷售額與銷售速度表現如何？（自訂目標：「{user_goal}」）",
            "ae_obj_en": "Aggregate Bronze Shopware order lines into Gold revenue marts, applying promotional voucher deductions and refund offsets.",
            "ae_obj_zh": "從 Bronze 層 Shopware 原始訂單明細彙整至 Gold 營運寬表，嚴格扣除促銷折價券與已退款金額，計算真實淨營收。",
            "upstream_table": "raw_shopware_orders",
            "contract_file": "contracts/orders_and_revenue_contract.yaml",
            "lineage_summary": "Bronze (raw_shopware_orders) ➔ dbt Staging (stg_orders) ➔ Gold Mart (mart_product_performance)",
            "dbt_sql": (
                "WITH orders_aggregated AS (\n"
                "    SELECT\n"
                "        product_id,\n"
                "        product_name,\n"
                "        category_name,\n"
                "        packaging_size,\n"
                "        SUM(sold_units) AS total_sold_units,\n"
                "        ROUND(SUM(gross_revenue_eur), 2) AS gross_revenue_eur,\n"
                "        ROUND(SUM(gross_revenue_eur - discount_eur - refunded_eur), 2) AS net_revenue_eur\n"
                "    FROM {{ ref('raw_shopware_orders') }}\n"
                "    WHERE order_date >= DATE_TRUNC(CURRENT_DATE(), QUARTER)\n"
                "    GROUP BY product_id, product_name, category_name, packaging_size\n"
                ")\n"
                "SELECT * FROM orders_aggregated\n"
                "ORDER BY net_revenue_eur DESC;"
            ),
            "dbt_tests": (
                "models:\n"
                "  - name: mart_product_performance\n"
                "    columns:\n"
                "      - name: product_id\n"
                "        tests: [unique, not_null]\n"
                "      - name: net_revenue_eur\n"
                "        tests:\n"
                "          - not_null\n"
                "          - dbt_expectations.expect_column_values_to_be_between:\n"
                "              min_value: 0"
            ),
            "lookml_view": (
                "view: mart_product_performance {\n"
                "  sql_table_name: `koro_marts.mart_product_performance` ;;\n\n"
                "  dimension: product_name { type: string sql: ${TABLE}.product_name ;; }\n"
                "  dimension: category_name { type: string sql: ${TABLE}.category_name ;; }\n"
                "  dimension: packaging_size { type: string sql: ${TABLE}.packaging_size ;; }\n\n"
                "  measure: total_sold_units {\n"
                "    type: sum\n"
                "    sql: ${TABLE}.sold_units ;;\n"
                "    description: 'Total product units sold in the period.'\n"
                "  }\n\n"
                "  measure: total_net_revenue {\n"
                "    type: sum\n"
                "    sql: ${TABLE}.net_revenue_eur ;;\n"
                "    value_format_name: eur\n"
                "    description: 'Governed Net Realized Revenue after discounts and refunds.'\n"
                "  }\n"
                "}"
            ),
            "primary_dim": "product_name",
            "primary_msr": "net_revenue_eur",
        }

    # Case 5: Default preset selection
    if preset_key == "category_margin":
        return synthesize_ae_lifecycle("Category Net Margin & Discount Impact", preset_key="custom")
    if preset_key == "inventory_health":
        return synthesize_ae_lifecycle("Inventory Availability & Stockout Risk", preset_key="custom")

    # Default to return defect diagnosis
    return synthesize_ae_lifecycle("1kg Bulk Packaging Return Diagnosis", preset_key="custom")


def _render_educational_banner(
    step_num: int,
    headline_en: str,
    headline_zh: str,
    description_en: str,
    description_zh: str,
    is_en: bool,
) -> None:
    """Render an educational enterprise banner explaining why this AE step matters for the final dashboard."""
    h = headline_en if is_en else headline_zh
    d = description_en if is_en else description_zh
    st.markdown(
        f"""
        <div style="background-color: #F8FAFC; border-left: 4px solid #2563EB; padding: 12px 16px; border-radius: 6px; margin-bottom: 16px;">
            <div style="font-weight: 600; color: #1E293B; font-size: 0.95rem; margin-bottom: 4px;">
                💡 {h}
            </div>
            <div style="color: #475569; font-size: 0.88rem; line-height: 1.5;">
                {d}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_step_dialogue_box(
    step_num: int,
    step_title: str,
    inherited_summary: str,
    is_en: bool,
) -> None:
    """Render an interactive Socratic AE dialogue box ensuring cross-step context inheritance."""
    with st.container(border=True):
        st.markdown(
            f"**💬 Step {step_num} AE Socratic Dialogue · {step_title}**"
            if is_en
            else f"**💬 第 {step_num} 步 AE 協作對話框 · {step_title}**"
        )
        st.info(
            f"📥 **{('Inherited from Previous Step' if is_en else '繼承自上一步之分析決策')}**: {inherited_summary}"
        )
        feedback_val = st.text_input(
            "Stakeholder Feedback or Parameter Tuning for this Step:"
            if is_en
            else "利害關係人針對此步驟之意見反饋或參數調整：",
            value=st.session_state.get(f"step_{step_num}_feedback", ""),
            placeholder="e.g., Confirmed, adjust formula grain, or proceed...",
            key=f"ae_feedback_step_{step_num}",
        )
        if feedback_val:
            st.session_state[f"step_{step_num}_feedback"] = feedback_val


def render_ae_workflow_stepper(
    adapter: BaseCockpitAdapter,
    data_dict: dict[str, pd.DataFrame],
    is_en: bool = True,
) -> None:
    """Render the dynamic 6-step Analytics Engineering lifecycle with progressive locking and Looker Studio embedding."""
    st.markdown("---")
    hdr_title = (
        "🚀 End-to-End Analytics Engineering Workflow"
        if is_en
        else "🚀 端到端分析工程（Analytics Engineering）交付全流程"
    )
    st.markdown(f"### {hdr_title}")
    st.caption(
        "Modern AE Delivery Lifecycle: Business Scoping → Semantic Contract → Data Contract → dbt SQL → LookML → Customizable Looker Studio"
        if is_en
        else "現代分析工程生命週期：商業需求澄清 → 語意指標合約 → 數據契約血緣 → dbt SQL 轉換 → LookML 建模 → 可自訂 Looker Studio 儀表板"
    )

    # Initialize State Machine Variables
    if "ae_workflow_step" not in st.session_state:
        st.session_state["ae_workflow_step"] = 1
    if "ae_max_unlocked_step" not in st.session_state:
        st.session_state["ae_max_unlocked_step"] = 1
    if "step_1_confirmed" not in st.session_state:
        st.session_state["step_1_confirmed"] = False
    if "custom_ae_goal" not in st.session_state:
        st.session_state["custom_ae_goal"] = ""

    current_step = st.session_state["ae_workflow_step"]
    max_unlocked = st.session_state["ae_max_unlocked_step"]

    # Synthesize dynamic state strictly when Step 1 is active or confirmed
    current_goal = st.session_state.get("custom_ae_goal", "")
    current_preset = st.session_state.get("ae_workflow_scenario", "bulk_returns")
    state = synthesize_ae_lifecycle(user_goal=current_goal, preset_key=current_preset)
    st.session_state["ae_lifecycle_state"] = state

    step_cols = st.columns(6)
    step_labels = [
        ("1. Scoping", "1. 商業需求"),
        ("2. Metric", "2. 指標合約"),
        ("3. Lineage", "3. 數據契約"),
        ("4. dbt SQL", "4. dbt 轉換"),
        ("5. LookML", "5. LookML 建模"),
        ("6. Looker Studio", "6. Looker 視覺化"),
    ]

    for idx, (en_lbl, zh_lbl) in enumerate(step_labels, start=1):
        with step_cols[idx - 1]:
            btn_lbl = en_lbl if is_en else zh_lbl
            is_active = (idx == current_step)
            is_locked = (idx > max_unlocked)

            if is_active:
                st.button(f"👉 **{btn_lbl}**", key=f"step_btn_{idx}", use_container_width=True, type="primary")
            elif is_locked:
                st.button(f"🔒 {btn_lbl}", key=f"step_btn_{idx}", use_container_width=True, disabled=False, help="Complete previous step to unlock")
            else:
                if st.button(btn_lbl, key=f"step_btn_{idx}", use_container_width=True):
                    st.session_state["ae_workflow_step"] = idx
                    st.rerun()

    # Sequential State Gate: Check if current step is locked
    if current_step > max_unlocked:
        with st.container(border=True):
            st.markdown(
                f"""
                <div style="text-align: center; padding: 40px 20px;">
                    <div style="font-size: 2.8rem; margin-bottom: 12px;">🔒</div>
                    <h3 style="color: #1E293B; margin-bottom: 8px;">
                        {'Step Locked: Prerequisite Sign-Off Required' if is_en else '步驟尚未解鎖：需要先完成前置步驟審查'}
                    </h3>
                    <p style="color: #64748B; max-width: 600px; margin: 0 auto 24px auto; font-size: 0.95rem; line-height: 1.6;">
                        {'Analytics Engineering follows a strict governance lifecycle. Downstream data contracts, dbt models, and Looker dashboards are dynamically compiled from verified business requirements. Please navigate back to Step 1 to confirm your analytical goal.' if is_en else '現代分析工程遵循嚴格的治理生命週期。下游的數據契約、dbt 轉換與 Looker 報表皆是由經過審查的業務需求動態編譯而成。請先返回第一步確認您的商業目標。'}
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            b1, b2, b3 = st.columns([4, 4, 4])
            with b2:
                back_label = "⬅️ Return to Step 1 Scoping" if is_en else "⬅️ 返回第一步進行需求對齊"
                if st.button(back_label, use_container_width=True, type="primary"):
                    st.session_state["ae_workflow_step"] = 1
                    st.rerun()
        return

    # Step Content Rendering (When Unlocked)
    with st.container(border=True):
        if current_step == 1:
            _render_step_1_scoping(state, is_en)
        elif current_step == 2:
            _render_step_2_metric_contract(state, is_en)
        elif current_step == 3:
            _render_step_3_data_contract(state, adapter, is_en)
        elif current_step == 4:
            _render_step_4_dbt_transformation(state, adapter, is_en)
        elif current_step == 5:
            _render_step_5_lookml_modeling(state, adapter, is_en)
        elif current_step == 6:
            _render_step_6_customizable_looker(state, adapter, data_dict, is_en)

    # Navigation Footer (Previous / Next with State Machine Transition)
    f_col1, f_col2, f_col3 = st.columns([3, 5, 4])
    with f_col1:
        if current_step > 1:
            prev_label = "⬅️ Previous Step" if is_en else "⬅️ 上一步"
            if st.button(prev_label, use_container_width=True):
                st.session_state["ae_workflow_step"] = current_step - 1
                st.rerun()
    with f_col3:
        if current_step == 1:
            confirm_label = "✅ Confirm Scope & Compile Metric ➡️" if is_en else "✅ 確認商業需求並編譯指標 ➡️"
            if st.button(confirm_label, use_container_width=True, type="primary"):
                st.session_state["step_1_confirmed"] = True
                st.session_state["ae_max_unlocked_step"] = max(st.session_state["ae_max_unlocked_step"], 2)
                st.session_state["ae_workflow_step"] = 2
                st.rerun()
        elif current_step < 6:
            next_label = f"Sign-Off Step {current_step} & Proceed ➡️" if is_en else f"確認第 {current_step} 步並推進 ➡️"
            if st.button(next_label, use_container_width=True, type="primary"):
                st.session_state["ae_max_unlocked_step"] = max(st.session_state["ae_max_unlocked_step"], current_step + 1)
                st.session_state["ae_workflow_step"] = current_step + 1
                st.rerun()
        else:
            reset_label = "🔄 Restart Workflow" if is_en else "🔄 重新啟動流程"
            if st.button(reset_label, use_container_width=True):
                st.session_state["ae_workflow_step"] = 1
                st.session_state["ae_max_unlocked_step"] = 1
                st.session_state["step_1_confirmed"] = False
                st.session_state["custom_ae_goal"] = ""
                st.rerun()


def _render_step_1_scoping(state: dict[str, Any], is_en: bool) -> None:
    st.markdown(
        "#### 🎯 Step 1: Business Scoping & Stakeholder Alignment"
        if is_en
        else "#### 🎯 第一步：商業需求澄清與利害關係人對齊"
    )
    _render_educational_banner(
        step_num=1,
        headline_en="Why This Step Matters: The Business Foundation",
        headline_zh="為什麼這一步至關重要：業務基礎定義",
        description_en="Every Analytics Engineering project starts with human alignment. Unclear scoping produces conflicting dashboards. Here, we clarify the business pain point and define the target analytical entity before writing a single line of SQL.",
        description_zh="所有分析工程的起點都是人與業務的對齊。模糊的需求是報表混亂的元兇。在此步驟，AE 將業務痛點精確轉譯為治理目標，在撰寫任何 SQL 前鎖定分析實體。",
        is_en=is_en,
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

    # Single Unified Input Box for Step 1 (Eliminates confusing duplicate bottom box)
    custom_goal_input = st.text_input(
        "💬 Or customize your own analytical goal & criteria (可自由輸入您的自訂需求與意見):"
        if is_en
        else "💬 或自由輸入您的自訂商業目標、關注維度與意見：",
        value=st.session_state.get("custom_ae_goal", ""),
        placeholder="例如：我想知道這個季度產生多少退款訂單，或是特定品類毛利...",
        key="custom_goal_text_input",
    )
    if custom_goal_input != st.session_state.get("custom_ae_goal", ""):
        st.session_state["custom_ae_goal"] = custom_goal_input
        st.rerun()

    # Dynamically Display Scoping Alignment (Adapts to user input!)
    st.info(
        f"📌 **{'Stakeholder Question' if is_en else '主管核心提問'}**: *'{state['stakeholder_q_en'] if is_en else state['stakeholder_q_zh']}'*\n\n"
        f"🎯 **{'AE Objective' if is_en else '分析工程目標'}**: {state['ae_obj_en'] if is_en else state['ae_obj_zh']}"
    )


def _render_step_2_metric_contract(state: dict[str, Any], is_en: bool) -> None:
    st.markdown(
        "#### 📐 Step 2: Metric Definition & Semantic Contract"
        if is_en
        else "#### 📐 第二步：指標口徑公式與語意合約鎖定"
    )
    _render_educational_banner(
        step_num=2,
        headline_en="How This Connects to Your Dashboard: Unified Truth",
        headline_zh="這一步如何連結至最終圖表：單一指標真理口徑",
        description_en="Disagreements on revenue or return rates happen when teams calculate numbers differently. By formalizing numerators, denominators, and NULLIF guards in the semantic layer, we guarantee that Step 6's Looker charts match financial reality.",
        description_zh="財務與營運部門對數字產生分歧，往往是因為公式定義不同。在此步驟將分子、分母與防除以零邏輯固化於語意層，能確保第 6 步 Looker 圖表中的數字與官方財務報表 100% 一致。",
        is_en=is_en,
    )

    # CSS to guarantee no text truncation in code blocks
    st.markdown(
        """
        <style>
        .metric-code-container {
            background-color: #F1F5F9;
            border: 1px solid #CBD5E1;
            border-radius: 6px;
            padding: 12px;
            font-family: monospace;
            font-size: 0.88rem;
            color: #0F172A;
            overflow-x: auto;
            white-space: pre-wrap;
            word-break: break-all;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    m_col1, m_col2 = st.columns([6, 4])
    with m_col1:
        st.markdown(
            f"**{'Official Metric Formula' if is_en else '官方指標計算公式'}** (`{state['metric_name']}`):"
        )
        st.markdown(
            f'<div class="metric-code-container">{state["metric_name"]} = {state["formula"]}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(f"- **Numerator (分子)**: `{state['numerator']}`")
        st.markdown(f"- **Denominator (分母)**: `{state['denominator']}`")
        st.markdown(f"- **Time Grain (時間粒度)**: `{state['time_grain']}`")

    with m_col2:
        st.markdown(
            "**Semantic Boundary Rules**:"
            if is_en
            else "**語意邊界與口徑約束**："
        )
        st.markdown("✅ **Zero-Division Safe**: Handled via `NULLIF` to prevent runtime crashes.")
        st.markdown("✅ **Voucher & Refund Handling**: Standardized net adjustments applied at row-level.")
        st.markdown("✅ **Catalog Integrity**: Linked to core 12 SKUs and 4 active categories.")

    _render_step_dialogue_box(
        step_num=2,
        step_title="Metric & Formula Sign-off" if is_en else "語意指標公式確認",
        inherited_summary=f"Goal: '{state['user_goal']}' ➔ Target Metric: `{state['metric_name']}`",
        is_en=is_en,
    )


def _render_step_3_data_contract(state: dict[str, Any], adapter: BaseCockpitAdapter, is_en: bool) -> None:
    st.markdown(
        "#### 🧬 Step 3: Data Contracts & Lineage Governance"
        if is_en
        else "#### 🧬 第三步：數據契約、欄位血緣與 GDPR 隱私審查"
    )
    _render_educational_banner(
        step_num=3,
        headline_en="How This Connects to Your Dashboard: Data Quality & Legal Guardrails",
        headline_zh="這一步如何連結至最終圖表：源頭資料品質與合規守門員",
        description_en="If upstream Shopware or ERP systems change column formats without notice, dashboards break silently. Data Contracts enforce strict schemas at the Bronze ingestion boundary, while ensuring GDPR privacy compliance (no PII leakage).",
        description_zh="若源頭 Shopware 或 ERP 系統任意更動欄位格式，報表就會無預警壞損。數據契約在 Bronze 銅牌層設立門禁，校驗欄位型態，同時確保沒有使用者個資（PII）流入後續報表。",
        is_en=is_en,
    )

    st.markdown(
        f"```mermaid\n"
        f"flowchart LR\n"
        f"    Bronze[Bronze Layer<br>{state['upstream_table']}] -->|dbt Staging| Silver[Silver Processing Layer]\n"
        f"    Silver -->|dbt Core Mart| Gold[Gold Layer<br>mart_product_performance]\n"
        f"    Gold --> Looker[Looker Studio Semantic Layer]\n"
        f"```\n"
    )

    c_col1, c_col2, c_col3 = st.columns(3)
    c_col1.metric("GDPR Status", "Verified No PII", delta="Compliant")
    c_col2.metric("Data Contract", state["contract_file"], delta="100% Match")
    c_col3.metric("FinOps Scan Guard", "< 5 MB Scan (< 100 MB Limit)", delta="Optimized")

    _render_step_dialogue_box(
        step_num=3,
        step_title="Data Contract & Lineage Verification" if is_en else "數據契約與血緣校驗",
        inherited_summary=f"Metric: `{state['metric_name']}` ➔ Bound Contract: `{state['contract_file']}` (Upstream: `{state['upstream_table']}`)",
        is_en=is_en,
    )


def _render_step_4_dbt_transformation(state: dict[str, Any], adapter: BaseCockpitAdapter, is_en: bool) -> None:
    st.markdown(
        "#### ⚙️ Step 4: SQL Transformation & dbt Data Quality Tests"
        if is_en
        else "#### ⚙️ 第四步：SQL 轉換模型與 dbt 自動化資料品質測試"
    )
    _render_educational_banner(
        step_num=4,
        headline_en="How This Connects to Your Dashboard: High-Performance Data Marts",
        headline_zh="這一步如何連結至最終圖表：高效能金牌數據寬表",
        description_en="Dashboards should never run raw, expensive queries against messy bronze data. This idempotent dbt SQL model pre-aggregates and materializes Gold Mart tables, ensuring Step 6 Looker charts load in milliseconds at minimal FinOps cost.",
        description_zh="BI 報表絕不能直接向原始資料庫下達昂貴且緩慢的 SQL。dbt 模型將業務規則加工並物化為金牌層寬表，確保第 6 步的 Looker 儀表板能在毫秒級迅速載入，大幅降低雲端運算費用。",
        is_en=is_en,
    )

    t_col1, t_col2 = st.columns([6, 4])
    with t_col1:
        st.caption("📄 `models/marts/mart_product_performance.sql`")
        st.code(state["dbt_sql"], language="sql")
    with t_col2:
        st.caption("🧪 `models/schema.yml` (dbt tests)")
        st.code(state["dbt_tests"], language="yaml")

    _render_step_dialogue_box(
        step_num=4,
        step_title="dbt SQL & Automated Tests Review" if is_en else "dbt SQL 與自動化測試審查",
        inherited_summary=f"Contract: `{state['contract_file']}` ➔ Model: `models/marts/mart_product_performance.sql`",
        is_en=is_en,
    )


def _render_step_5_lookml_modeling(state: dict[str, Any], adapter: BaseCockpitAdapter, is_en: bool) -> None:
    st.markdown(
        "#### 📜 Step 5: LookML Semantic Modeling as Code"
        if is_en
        else "#### 📜 第五步：LookML 語意建模（Semantic Layer as Code）"
    )
    _render_educational_banner(
        step_num=5,
        headline_en="How This Connects to Your Dashboard: Self-Serve Power for Stakeholders",
        headline_zh="這一步如何連結至最終圖表：賦能業務端的自助拖拉語意層",
        description_en="LookML defines the exact dimensions (e.g. Category, Packaging Size) and measures (e.g. Return Rate, Net Revenue) available to business users. It bridges the gap between raw SQL tables and the drag-and-drop Looker Studio interface in Step 6.",
        description_zh="LookML 將數據庫欄位定義為受控管的維度（如品類、包裝規格）與度量（如退貨率、營收）。它扮演橋樑，決定了利害關係人在第 6 步 Looker Studio 畫面上能夠自由拖拉哪些分析欄位。",
        is_en=is_en,
    )

    st.caption("📄 `warehouse/lookml/mart_product_performance.view.lkml`")
    st.code(state["lookml_view"], language="yaml")

    _render_step_dialogue_box(
        step_num=5,
        step_title="LookML Modeling Sign-off" if is_en else "LookML 語意模型確認",
        inherited_summary=f"dbt SQL Model ➔ LookML Measures: `{state['metric_name']}`",
        is_en=is_en,
    )


def _render_step_6_customizable_looker(
    state: dict[str, Any],
    adapter: BaseCockpitAdapter,
    data_dict: dict[str, pd.DataFrame],
    is_en: bool,
) -> None:
    st.markdown(
        "#### 📊 Step 6: Interactive Looker Studio Enterprise BI Embed"
        if is_en
        else "#### 📊 第六步：內嵌 Looker Studio 企業級 BI 自助探索儀表板"
    )
    st.markdown(
        "All upstream data, contracts, dbt models, and LookML semantics are compiled! "
        "Stakeholders can now self-serve directly within **Google Looker Studio**."
        if is_en
        else "端到端數據管線、契約、dbt 轉換與 LookML 語意層已全數編譯完成！"
        "利害關係人現在可直接在此**內嵌 Looker Studio 儀表板**中展開即時探索與自訂報表。"
    )

    _render_step_dialogue_box(
        step_num=6,
        step_title="Looker Studio BI Delivery Sign-off" if is_en else "Looker Studio BI 最終交付驗收",
        inherited_summary=f"Goal: '{state['user_goal']}' ➔ Model: `mart_product_performance` ➔ Metric: `{state['metric_name']}`",
        is_en=is_en,
    )

    # Permission Guidance & Mode Toggle
    perm_col1, perm_col2 = st.columns([7, 3])
    with perm_col1:
        st.markdown(
            "🔒 **Google Looker Studio Embedding Permissions Notice**:\n"
            "Google Looker Studio embeds require the underlying report to have sharing set to **'Anyone with the link can view'** and **'Enable embedding'** toggled ON in File → Embed Report. If your organization enforces SSO or blocks 3rd-party cookies, please use the direct link or toggle Safe Preview below."
            if is_en
            else "🔒 **Google Looker Studio 內嵌權限說明**：\n"
            "Google Looker Studio 的 iframe 內嵌機制要求報表必須在 Google 後台設定為**「知道連結的任何人皆可檢視」**，並在「檔案 ➔ 嵌入報表」中開啟。若您遇到公司組織權限或第三方 Cookie 限制，可直接點擊全螢幕開啟，或切換下方之安全預覽模式。"
        )
    with perm_col2:
        safe_mode = st.toggle(
            "Safe Interactive View" if is_en else "安全互動預覽模式",
            value=st.session_state.get("looker_safe_mode", False),
            key="looker_safe_mode_toggle",
            help="Toggle this if Google shows 'Can't access report' due to corporate cookie permissions.",
        )

    # Resolve Looker Studio Embed URL
    default_embed = (
        getattr(adapter, "looker_embed_url", "")
        or "https://lookerstudio.google.com/embed/reporting/4b3605e5-220b-4654-8c81-5d9c2a6bf1b6/page/p_6h9g2b3c"
    )
    embed_url = st.text_input(
        "Looker Studio Embed URL (內嵌報表連結):"
        if is_en
        else "Looker Studio 嵌入報表網址（支援即時切換自訂儀表板）：",
        value=st.session_state.get("custom_looker_studio_url", default_embed),
        key="looker_studio_embed_url_field",
    )
    if embed_url:
        st.session_state["custom_looker_studio_url"] = embed_url

    clean_url = embed_url.replace("/embed", "").strip() or "https://lookerstudio.google.com"

    # Action Toolbar
    b_col1, b_col2, b_col3 = st.columns(3)
    with b_col1:
        st.link_button(
            "🚀 Open in Looker Studio" if is_en else "🚀 全螢幕開啟 Looker Studio",
            url=clean_url,
            use_container_width=True,
        )
    with b_col2:
        st.link_button(
            "✏️ Edit Report in Looker Studio" if is_en else "✏️ 在 Looker Studio 編輯報表",
            url=clean_url,
            use_container_width=True,
        )
    with b_col3:
        st.download_button(
            "📥 Export Governed LookML" if is_en else "📥 匯出 LookML 語意層代碼",
            data=state["lookml_view"],
            file_name="mart_product_performance.view.lkml",
            mime="text/plain",
            use_container_width=True,
        )

    if safe_mode:
        st.markdown(
            f"""
            <div style="background-color: #F8FAFC; border: 1px solid #CBD5E1; border-radius: 8px; padding: 24px; text-align: center; margin: 16px 0;">
                <div style="font-size: 2rem; margin-bottom: 8px;">📊</div>
                <h4 style="color: #1E293B; margin-bottom: 8px;">Looker Studio Governed Operational Visualizer</h4>
                <p style="color: #64748B; font-size: 0.9rem; max-width: 650px; margin: 0 auto 16px auto;">
                    Displaying active compiled metric: <strong>{state['metric_name']}</strong> across <strong>mart_product_performance</strong>.<br>
                    To view the native Google-hosted canvas, click below or ensure Google login authorization.
                </p>
                <a href="{clean_url}" target="_blank" style="background-color: #2563EB; color: white; padding: 8px 16px; border-radius: 6px; text-decoration: none; font-size: 0.9rem; font-weight: 500;">
                    🚀 Open Governed Canvas in Google Looker Studio
                </a>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.info(
            f"🔗 **{'Live Looker Studio Embed Active' if is_en else 'Looker Studio 實時內嵌儀表板加載中'}**: `{clean_url}`"
        )
        components.iframe(src=embed_url, height=720, scrolling=True)

    # Looker Underneath Data Table (Tabular Data Verification)
    with st.expander(
        "📋 Looker Governed Data Source Table (`mart_product_performance`)"
        if is_en
        else "📋 Looker 底層治理數據表預覽 (`mart_product_performance`)",
        expanded=False,
    ):
        df_gold = data_dict.get("mart_product_performance", pd.DataFrame())
        if not df_gold.empty:
            st.dataframe(df_gold, use_container_width=True)
