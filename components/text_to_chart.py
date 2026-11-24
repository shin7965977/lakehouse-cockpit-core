"""Natural Language AI Smart Chart Generator (Text-to-Chart) Component.

Ported and generalized from `data-pipeline-consulting-demo/chart_generator.py`
for the reusable `lakehouse-cockpit-core` microkernel.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    from ..adapter import BaseCockpitAdapter
    from ..services.duckdb_executor import execute_readonly_duckdb, generate_gemini_text
except (ImportError, ValueError):
    try:
        from adapter import BaseCockpitAdapter
        from services.duckdb_executor import execute_readonly_duckdb, generate_gemini_text
    except (ImportError, ValueError):
        from cockpit_core.adapter import BaseCockpitAdapter
        from cockpit_core.services.duckdb_executor import (
            execute_readonly_duckdb,
            generate_gemini_text,
        )


BUSINESS_CHART_KEYWORDS = (
    "銷售",
    "營收",
    "gmv",
    "訂單",
    "商品",
    "客戶",
    "業績",
    "退貨",
    "退款",
    "排行",
    "金額",
    "單價",
    "aov",
    "品類",
    "區域",
    "地區",
    "數據",
    "指標",
    "圖",
    "走勢",
    "趨勢",
    "圓餅",
    "長條",
    "柱狀",
    "佔比",
    "比較",
    "分析",
    "chart",
    "plot",
    "revenue",
    "sales",
    "order",
    "product",
    "category",
    "region",
    "return",
    "trend",
    "bar",
    "pie",
    "line",
)


def generate_chart_from_nl(
    prompt: str,
    data_dict: dict[str, pd.DataFrame],
    is_en: bool = False,
) -> tuple[go.Figure | None, str, str, pd.DataFrame]:
    """Parse a natural-language prompt and synthesize an interactive Plotly chart, title, insight, and DataFrame."""
    clean_prompt = prompt.strip().lower()

    if not any(kw in clean_prompt for kw in BUSINESS_CHART_KEYWORDS):
        if is_en:
            return (
                None,
                "⚠️ Domain Scope Guardrail",
                (
                    f'Your query *"{prompt}"* is unrelated to Lakehouse business metrics, orders, categories, or revenue.\n\n'
                    "💡 **Suggested prompts:**\n"
                    '- 📈 *"Show daily GMV revenue trend"*\n'
                    '- 🏆 *"Plot category GMV comparison bar chart"*\n'
                    '- ⚠️ *"Compare return rate by category"*\n'
                    '- 🥧 *"Show revenue share by region in a pie chart"*'
                ),
                pd.DataFrame(),
            )
        return (
            None,
            "⚠️ 業務範疇約束提醒",
            (
                f"您輸入的提問 *「{prompt}」* 與營運銷售、品類、訂單或退貨指標無關，因此無法生成圖表。\n\n"
                "💡 **建議您可以嘗試以下指令：**\n"
                "- 📈 *「請畫出每日 GMV 營收趨勢走勢圖」*\n"
                "- 🏆 *「用長條圖呈現各品類 GMV 銷售額排行」*\n"
                "- ⚠️ *「比較各品類的退貨率表現」*\n"
                "- 🥧 *「用圓餅圖呈現各區域營收佔比」*"
            ),
            pd.DataFrame(),
        )

    fact_df = data_dict.get("fact_orders", pd.DataFrame())
    mart_df = data_dict.get("mart_product_performance", pd.DataFrame())

    # 1. Pie / Share by Region or Category
    if any(kw in clean_prompt for kw in ("圓餅", "佔比", "地區", "區域", "pie", "region", "share")):
        if not fact_df.empty and {"region", "gross_amount"}.issubset(fact_df.columns):
            reg_df = (
                fact_df.groupby("region", as_index=False)["gross_amount"]
                .sum()
                .sort_values("gross_amount", ascending=False)
            )
            title = "Revenue Share by Region" if is_en else "各區域 GMV 營收貢獻佔比（圓餅圖）"
            fig = px.pie(
                reg_df,
                names="region",
                values="gross_amount",
                hole=0.4,
                title=title,
                template="plotly_dark",
            )
            fig.update_layout(height=360, margin={"l": 20, "r": 20, "t": 45, "b": 20})
            top_row = reg_df.iloc[0]
            total = reg_df["gross_amount"].sum()
            share = (top_row["gross_amount"] / total * 100) if total > 0 else 0.0
            insight = (
                f"Top region is **{top_row['region']}** (${top_row['gross_amount']:,.0f}, {share:.1f}% of total GMV)."
                if is_en
                else f"營收貢獻最高區域為 **{top_row['region']}**（累計 ${top_row['gross_amount']:,.0f}，佔全區總額 {share:.1f}%）。"
            )
            return fig, title, insight, reg_df

    # 2. Return Rate / Refund monitoring
    if any(kw in clean_prompt for kw in ("退貨", "退款", "異常", "return", "refund")):
        if not mart_df.empty and {"category", "return_rate_pct"}.issubset(mart_df.columns):
            sub_df = mart_df.sort_values("return_rate_pct", ascending=False)
            title = "Return Rate (%) by Category" if is_en else "各品類退貨率監控比較（%）"
            fig = px.bar(
                sub_df,
                x="category",
                y="return_rate_pct",
                color="return_rate_pct",
                color_continuous_scale="Reds",
                title=title,
                template="plotly_dark",
            )
            fig.update_layout(height=360, margin={"l": 20, "r": 20, "t": 45, "b": 20})
            worst = sub_df.iloc[0]
            insight = (
                f"Highest return rate is **{worst['category']}** at {worst['return_rate_pct']:.1f}%. Prioritize SKU quality review."
                if is_en
                else f"**{worst['category']}** 品類退貨率最高（達 {worst['return_rate_pct']:.1f}%），建議優先檢視該品類高單價商品規格與包裝。"
            )
            return fig, title, insight, sub_df

    # 3. Category / Product Bar Chart
    if any(kw in clean_prompt for kw in ("品類", "商品", "長條", "柱狀", "排行", "category", "product", "bar")):
        if not mart_df.empty and {"category", "total_gmv"}.issubset(mart_df.columns):
            sub_df = mart_df.sort_values("total_gmv", ascending=False)
            title = "GMV Ranking by Category ($)" if is_en else "各品類 GMV 銷售額排行（長條圖）"
            fig = px.bar(
                sub_df,
                x="category",
                y="total_gmv",
                color="category",
                title=title,
                template="plotly_dark",
            )
            fig.update_layout(height=360, margin={"l": 20, "r": 20, "t": 45, "b": 20})
            top_cat = sub_df.iloc[0]
            insight = (
                f"**{top_cat['category']}** leads with ${top_cat['total_gmv']:,.0f} across {int(top_cat['order_count'])} orders."
                if is_en
                else f"**{top_cat['category']}** 穩居銷售冠軍，累計 GMV 達 ${top_cat['total_gmv']:,.0f}（共 {int(top_cat['order_count'])} 筆訂單）。"
            )
            return fig, title, insight, sub_df

    # 4. Default: Daily GMV & Order Trend
    if not fact_df.empty and {"event_date", "gross_amount"}.issubset(fact_df.columns):
        daily_df = (
            fact_df.groupby("event_date", as_index=False)
            .agg(daily_gmv=("gross_amount", "sum"), orders=("order_id", "count"))
            .sort_values("event_date")
        )
        title = "Daily GMV Revenue Trend ($)" if is_en else "每日 GMV 營收趨勢走勢圖 ($)"
        fig = px.area(
            daily_df,
            x="event_date",
            y="daily_gmv",
            markers=True,
            title=title,
            template="plotly_dark",
        )
        fig.update_layout(height=360, margin={"l": 20, "r": 20, "t": 45, "b": 20})
        total_gmv = daily_df["daily_gmv"].sum()
        peak_row = daily_df.loc[daily_df["daily_gmv"].idxmax()]
        insight = (
            f"Cumulative GMV reached **${total_gmv:,.0f}**, peaking on `{peak_row['event_date']}` at **${peak_row['daily_gmv']:,.0f}**."
            if is_en
            else f"期間累計 GMV 達 **${total_gmv:,.0f}**，單日營收高峰出現在 `{peak_row['event_date']}`（達 **${peak_row['daily_gmv']:,.0f}**）。"
        )
        return fig, title, insight, daily_df

    return None, "No Data Available", "Dataset is empty.", pd.DataFrame()


def render_text_to_chart_module(
    adapter: BaseCockpitAdapter | None = None,
    data_dict: dict[str, pd.DataFrame] | None = None,
    is_en: bool = True,
    scope_key: str = "default",
) -> None:
    """Render the 2-step HITL Natural Language AI Chart Studio (with Spec Preview Form & Q&A Chat)."""
    import os

    st.subheader(
        "✨ Natural Language AI Chart Studio (2-Step HITL & Q&A Refinement)"
        if is_en
        else "✨ 自然語言 AI 智慧圖表工作台（兩階段規格確認 & 對話微調）"
    )
    st.caption(
        "🤖 Step 1: Enter chart goal -> Step 2: Review & refine X/Y axes, 8 chart types, and chat -> Step 3: Render verified Plotly chart."
        if is_en
        else "🤖 步驟 1：輸入圖表需求 ➔ 步驟 2：確認資料表、X/Y 軸型別防呆與對話微調 ➔ 步驟 3：繪製互動圖表與檢視底層明細。"
    )

    safe_dict = {
        k: v
        for k, v in (data_dict or {}).items()
        if isinstance(v, pd.DataFrame) and not v.empty
    }
    if not safe_dict:
        st.error(
            "❌ No non-empty datasets available for Text-to-Chart."
            if is_en
            else "❌ 目前無非空資料表可供生圖。"
        )
        return

    col_desc_en = {
        "order_id": "Order ID",
        "event_date": "Order Date",
        "category": "Product Category",
        "region": "Sales Region",
        "gross_amount": "Gross Order Amount (USD)",
        "units": "Units Sold",
        "is_returned": "Returned Flag (0/1)",
        "total_gmv": "Total GMV (USD)",
        "order_count": "Total Orders",
        "returned_orders": "Returned Orders",
        "avg_order_value": "Average Order Value (USD)",
        "return_rate_pct": "Return Rate (%)",
        "ingested_at": "Ingestion Timestamp",
        "source_system": "Source System",
    }
    col_desc_zh = {
        "order_id": "訂單編號",
        "event_date": "訂單日期",
        "category": "商品品類",
        "region": "銷售地區",
        "gross_amount": "單筆訂單金額 (USD)",
        "units": "銷售件數",
        "is_returned": "是否退貨 (0/1)",
        "total_gmv": "總銷售額 GMV (USD)",
        "order_count": "訂單總數",
        "returned_orders": "退貨訂單數",
        "avg_order_value": "平均客單價 AOV (USD)",
        "return_rate_pct": "退貨率 (%)",
        "ingested_at": "資料攝取時間",
        "source_system": "來源系統",
    }
    active_desc_map = col_desc_en if is_en else col_desc_zh

    # Synthesize regional performance mart if fact_orders has region + gross_amount
    if (
        "mart_region_performance" not in safe_dict
        and "fact_orders" in safe_dict
        and {"region", "gross_amount", "order_id"}.issubset(safe_dict["fact_orders"].columns)
    ):
        f_df = safe_dict["fact_orders"]
        reg_mart = (
            f_df.groupby("region", as_index=False)
            .agg(
                total_gmv=("gross_amount", "sum"),
                order_count=("order_id", "count"),
                avg_order_value=("gross_amount", "mean"),
            )
            .assign(avg_order_value=lambda d: d["avg_order_value"].round(2))
            .sort_values("avg_order_value", ascending=False)
        )
        safe_dict["mart_region_performance"] = reg_mart

    dataset_schemas: dict[str, dict[str, str]] = {}
    dataset_getters: dict[str, Any] = {}
    for tbl_name, df in safe_dict.items():
        dataset_schemas[tbl_name] = {
            str(col): active_desc_map.get(str(col), str(df[col].dtype))
            for col in df.columns
        }
        dataset_getters[tbl_name] = lambda _df=df: _df

    presets = (
        [
            {"label": "📈 Daily GMV Trend", "query": "Show daily GMV revenue trend"},
            {"label": "🏆 Category GMV Bar Chart", "query": "Plot category GMV comparison bar chart"},
            {"label": "⚠️ Category Return Rate", "query": "Compare return rate by category"},
            {"label": "🍩 Regional Share Donut", "query": "Show revenue share by region in a donut chart"},
        ]
        if is_en
        else [
            {"label": "📈 每日 GMV 趨勢走勢", "query": "請畫出每日 GMV 營收趨勢走勢圖"},
            {"label": "🏆 各品類銷售額長條圖", "query": "用長條圖呈現各品類 GMV 銷售額排行"},
            {"label": "⚠️ 各品類退貨率監控", "query": "比較各品類的退貨率表現"},
            {"label": "🍩 各區域營收佔比圖", "query": "用甜甜圈圖呈現各區域營收佔比"},
        ]
    )

    first_tbl = next(iter(safe_dict.keys()))
    fact_tbl = "fact_orders" if "fact_orders" in safe_dict else first_tbl
    mart_tbl = "mart_product_performance" if "mart_product_performance" in safe_dict else fact_tbl
    reg_tbl = "mart_region_performance" if "mart_region_performance" in safe_dict else fact_tbl

    def _fallback_cfg(query: str) -> dict[str, Any]:
        q_l = (query or "").strip().lower()
        is_aov = any(k in q_l for k in ("客單價", "平均", "單價", "aov", "avg", "average"))
        is_region = any(k in q_l for k in ("地區", "區域", "城市", "region", "city", "area"))
        is_daily = any(k in q_l for k in ("每日", "趨勢", "走勢", "時間", "日期", "daily", "trend", "date", "time"))
        is_cat = any(k in q_l for k in ("品類", "品項", "項目", "類別", "商品", "產品", "category", "product", "item", "sku"))

        # 0. Dual-Dimension: Daily by Region or Category (SQL GROUP BY event_date, region/category)
        if is_daily and is_region and {"event_date", "region", "gross_amount"}.issubset(safe_dict[fact_tbl].columns):
            agg_mode = "AVG" if is_aov else "SUM"
            return {
                "dataset": fact_tbl,
                "chart_type": "line",
                "x": "event_date",
                "y": ["gross_amount"],
                "color": "region",
                "agg": agg_mode,
                "title": (
                    f"Daily {'Average Order Value' if is_aov else 'Revenue'} by Region (USD)"
                    if is_en
                    else f"各地區每日{'平均客單價' if is_aov else '總銷售額'}趨勢 (USD)"
                ),
                "takeaway": (
                    "Compares daily sales trajectories across all regions via DuckDB GROUP BY event_date, region."
                    if is_en
                    else "透過 DuckDB SQL 依日期與地區雙維度聚合，呈現各地區每日銷售走勢。"
                ),
                "query": query,
                "generated_by": "fallback_rules",
            }
        if is_daily and is_cat and {"event_date", "category", "gross_amount"}.issubset(safe_dict[fact_tbl].columns):
            agg_mode = "AVG" if is_aov else "SUM"
            return {
                "dataset": fact_tbl,
                "chart_type": "line",
                "x": "event_date",
                "y": ["gross_amount"],
                "color": "category",
                "agg": agg_mode,
                "title": (
                    f"Daily {'Average Order Value' if is_aov else 'Revenue'} by Category (USD)"
                    if is_en
                    else f"各品類每日{'平均客單價' if is_aov else '總銷售額'}趨勢 (USD)"
                ),
                "takeaway": (
                    "Compares daily sales trajectories across product categories via DuckDB GROUP BY event_date, category."
                    if is_en
                    else "透過 DuckDB SQL 依日期與品類雙維度聚合，呈現各品類每日走勢。"
                ),
                "query": query,
                "generated_by": "fallback_rules",
            }

        # 1. Regional AOV (Average Order Value by Region) -> Bar Chart on fact_orders with AVG
        if is_region and is_aov and {"region", "gross_amount"}.issubset(safe_dict[fact_tbl].columns):
            return {
                "dataset": fact_tbl,
                "chart_type": "bar",
                "x": "region",
                "y": ["gross_amount"],
                "agg": "AVG",
                "title": "Average Order Value (AOV) by Region (USD)" if is_en else "各地區平均客單價 (AOV) 比較 (USD)",
                "takeaway": (
                    "Compares average order value (AVG gross_amount) across sales regions."
                    if is_en
                    else "透過 DuckDB AVG 聚合比較各銷售地區的平均客單價 (AOV)。"
                ),
                "query": query,
                "generated_by": "fallback_rules",
            }

        # 2. Category AOV (Average Order Value by Category)
        if is_aov and "avg_order_value" in safe_dict[mart_tbl].columns:
            return {
                "dataset": mart_tbl,
                "chart_type": "bar",
                "x": "category" if "category" in safe_dict[mart_tbl].columns else safe_dict[mart_tbl].columns[0],
                "y": ["avg_order_value"],
                "agg": "AVG",
                "title": "Average Order Value (AOV) by Category (USD)" if is_en else "各品類平均客單價 (AOV) 比較 (USD)",
                "takeaway": (
                    "Compares average order value (USD) across product categories."
                    if is_en
                    else "比較各商品品類的平均客單價 (AOV)。"
                ),
                "query": query,
                "generated_by": "fallback_rules",
            }

        # 3. Regional GMV Share / Donut or Bar
        if is_region or any(k in q_l for k in ("圓餅", "甜甜圈", "佔比", "pie", "donut", "share")):
            ctype = "donut" if any(k in q_l for k in ("圓餅", "甜甜圈", "佔比", "pie", "donut", "share")) else "bar"
            target_reg_tbl = reg_tbl if "total_gmv" in safe_dict[reg_tbl].columns else fact_tbl
            y_reg_col = "total_gmv" if "total_gmv" in safe_dict[target_reg_tbl].columns else "gross_amount"
            return {
                "dataset": target_reg_tbl,
                "chart_type": ctype,
                "x": "region" if "region" in safe_dict[target_reg_tbl].columns else safe_dict[target_reg_tbl].columns[0],
                "y": [y_reg_col],
                "agg": "SUM",
                "title": "Revenue by Region (USD)" if is_en else "各區域 GMV 營收比較 (USD)",
                "takeaway": (
                    "Displays regional GMV contribution across cities."
                    if is_en
                    else "呈現各銷售區域的 GMV 營收表現。"
                ),
                "query": query,
                "generated_by": "fallback_rules",
            }
        if any(k in q_l for k in ("退貨", "退款", "異常", "return", "refund")):
            return {
                "dataset": mart_tbl,
                "chart_type": "bar",
                "x": "category" if "category" in safe_dict[mart_tbl].columns else safe_dict[mart_tbl].columns[0],
                "y": ["return_rate_pct" if "return_rate_pct" in safe_dict[mart_tbl].columns else safe_dict[mart_tbl].columns[-1]],
                "title": "Return Rate (%) by Category" if is_en else "各品類退貨率監控比較 (%)",
                "takeaway": (
                    "Compares return rate percentage across product categories."
                    if is_en
                    else "比較各商品品類的退貨率表現，協助定位高退貨品類。"
                ),
                "query": query,
                "generated_by": "fallback_rules",
            }
        if any(k in q_l for k in ("每日", "趨勢", "走勢", "時間", "日期", "daily", "trend", "date", "time", "area", "line")):
            return {
                "dataset": fact_tbl,
                "chart_type": "area",
                "x": "event_date" if "event_date" in safe_dict[fact_tbl].columns else safe_dict[fact_tbl].columns[0],
                "y": ["gross_amount" if "gross_amount" in safe_dict[fact_tbl].columns else safe_dict[fact_tbl].columns[-1]],
                "title": "Daily GMV Revenue Trend ($)" if is_en else "每日 GMV 營收趨勢走勢圖 ($)",
                "takeaway": (
                    "Tracks daily gross merchandise value trajectory over time."
                    if is_en
                    else "追蹤期間內每日 GMV 營收變化走勢與單日高峰。"
                ),
                "query": query,
                "generated_by": "fallback_rules",
            }
        if any(k in q_l for k in ("品類", "品項", "項目", "類別", "商品", "產品", "長條", "柱狀", "排行", "銷售", "營收", "category", "product", "item", "sku", "bar", "gmv", "revenue", "sales")):
            return {
                "dataset": mart_tbl,
                "chart_type": "bar",
                "x": "category" if "category" in safe_dict[mart_tbl].columns else safe_dict[mart_tbl].columns[0],
                "y": ["total_gmv" if "total_gmv" in safe_dict[mart_tbl].columns else safe_dict[mart_tbl].columns[-1]],
                "title": "GMV Ranking by Category ($)" if is_en else "各品類／品項 GMV 總銷售額排行 ($)",
                "takeaway": (
                    "Compares total GMV across product categories."
                    if is_en
                    else "比較各商品品類／品項的累計總銷售額 (GMV) 與訂單貢獻。"
                ),
                "query": query,
                "generated_by": "fallback_rules",
            }

        # Strict Fail-Loud policy: never silently substitute a default chart for unrecognized queries
        return {
            "error": (
                f"Cannot parse offline chart request '{query}'. Please specify a valid dimension/metric (category, region, daily trend, or return rate) or provide a Gemini API Key."
                if is_en
                else f"離線規則無法解析「{query}」的圖表維度或指標。請明確指定維度（如：各品類/品項、各地區、每日趨勢、退貨率），或於右側填入 Gemini API Key 啟用完整 AI 解析。"
            ),
            "query": query,
        }

    active_api_key = (
        st.session_state.get("user_gemini_key")
        or st.session_state.get("sidebar_gemini_api_key")
        or st.session_state.get("gemini_api_key")
        or os.getenv("GEMINI_API_KEY", "")
    ).strip()

    lang_tag = "en" if is_en else "zh"
    render_interactive_nl2chart_studio(
        dataset_schemas=dataset_schemas,
        dataset_getters=dataset_getters,
        api_key=active_api_key,
        domain_context="Retail E-Commerce Lakehouse (fact_orders, mart_product_performance, raw_order_events).",
        presets=presets,
        fallback_fn=_fallback_cfg,
        is_en=is_en,
        state_prefix=f"nl2chart_{scope_key}_{lang_tag}",
    )


# ==============================================================================
# Shared Schema-Agnostic Gemini AI NL2Chart Core Engine
# ==============================================================================
def _discover_gemini_flash_models(client) -> list[str]:
    """Return ordered cascade list of available Gemini Flash models."""
    import re

    discovered: list[str] = []
    try:
        for m in client.models.list():
            m_name = m.name.replace("models/", "")
            if "flash" in m_name.lower() and not any(
                bad in m_name.lower()
                for bad in ("omni", "embed", "imagen", "tts", "stt", "realtime")
            ):
                discovered.append(m_name)

        def _score(name: str) -> float:
            nums = re.findall(r"(\d+(?:\.\d+)?)", name)
            return float(nums[0]) if nums else 0.0

        discovered.sort(key=_score, reverse=True)
    except Exception:  # noqa: BLE001
        pass

    fallbacks = ["gemini-3.5-flash-lite"]
    candidates = discovered if discovered else list(fallbacks)
    for fb in fallbacks:
        if fb not in candidates:
            candidates.append(fb)
    return candidates


SUPPORTED_CHART_TYPES: tuple[str, ...] = (
    "line",
    "area",
    "bar",
    "stacked_bar",
    "horizontal_bar",
    "donut",
    "scatter",
    "dual_axis",
)

SUPPORTED_AGGREGATIONS: tuple[str, ...] = (
    "SUM",
    "AVG",
    "COUNT",
    "MAX",
    "MIN",
    "NONE",
)


def compile_spec_to_duckdb_sql(
    config: dict[str, Any],
    df: pd.DataFrame,
) -> tuple[str, pd.DataFrame, str]:
    """Deterministically compile a validated Chart Spec into read-only DuckDB SQL and execute it.

    Eliminates duplicate X-axis sawtooth artifacts by grouping & aggregating before Plotly rendering.
    Returns ``(compiled_sql, aggregated_df, error_message)``.
    """
    if df is None or df.empty:
        return "", pd.DataFrame(), "Target dataset is empty."

    valid_cols = set(df.columns)
    ds_name = str(config.get("dataset") or "active_table")
    safe_ds = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in ds_name) or "active_table"
    ctype = str(config.get("chart_type", "line")).strip().lower()
    agg = str(config.get("agg", "NONE" if ctype == "scatter" else "SUM")).strip().upper()
    if agg not in SUPPORTED_AGGREGATIONS:
        agg = "NONE" if ctype == "scatter" else "SUM"

    x_col = str(config.get("x", "")).strip()
    raw_y = config.get("y", [])
    y_cols = [raw_y] if isinstance(raw_y, str) else [str(c).strip() for c in (raw_y or [])]
    color_col = config.get("color")
    valid_color = str(color_col).strip() if color_col and str(color_col).strip() in valid_cols else None

    if x_col not in valid_cols:
        return "", pd.DataFrame(), f"X-axis column `{x_col}` not found in `{safe_ds}`."
    y_cols = [c for c in y_cols if c in valid_cols]
    if not y_cols:
        return "", pd.DataFrame(), f"No valid Y-axis columns found in `{safe_ds}`."

    def _q(col: str) -> str:
        return '"' + col.replace('"', '""') + '"'

    # Raw unaggregated mode (scatter or explicit NONE)
    if ctype == "scatter" or agg == "NONE":
        proj = [x_col] + [c for c in y_cols if c != x_col]
        if valid_color and valid_color not in proj:
            proj.append(valid_color)
        sql = f"SELECT {', '.join(_q(c) for c in proj)}\nFROM {_q(safe_ds)}"
        res_df, err = execute_readonly_duckdb(sql, {safe_ds: df})
        return sql, res_df, err

    # Horizontal bar: X is numeric, Y[0] is categorical
    if ctype == "horizontal_bar":
        cat_col = y_cols[0]
        num_col = x_col
        group_cols = [cat_col] + ([valid_color] if valid_color and valid_color != cat_col else [])
        metric_expr = (
            f"COUNT({_q(num_col)}) AS {_q(num_col)}"
            if agg == "COUNT"
            else f"ROUND({agg}(CAST({_q(num_col)} AS DOUBLE)), 2) AS {_q(num_col)}"
        )
        limit_n = max(1, min(int(config.get("limit", 12)), 50))
        grp_select = ", ".join(_q(c) for c in group_cols)
        grp_by = ", ".join(str(i + 1) for i in range(len(group_cols)))
        sql = (
            f"SELECT {grp_select}, {metric_expr}\n"
            f"FROM {_q(safe_ds)}\n"
            f"GROUP BY {grp_by}\n"
            f"ORDER BY {_q(num_col)} DESC\n"
            f"LIMIT {limit_n}"
        )
        res_df, err = execute_readonly_duckdb(sql, {safe_ds: df})
        return sql, res_df, err

    # Standard aggregated charts: line, area, bar, stacked_bar, donut, dual_axis
    group_cols = [x_col] + ([valid_color] if valid_color and valid_color != x_col else [])
    metric_exprs: list[str] = []
    for y_c in y_cols:
        if y_c in group_cols:
            continue
        if agg == "COUNT":
            metric_exprs.append(f"COUNT({_q(y_c)}) AS {_q(y_c)}")
        else:
            metric_exprs.append(f"ROUND({agg}(CAST({_q(y_c)} AS DOUBLE)), 2) AS {_q(y_c)}")

    if not metric_exprs:
        return "", pd.DataFrame(), "No aggregatable metric column selected."

    grp_select = ", ".join(_q(c) for c in group_cols)
    grp_by = ", ".join(str(i + 1) for i in range(len(group_cols)))
    is_temporal_x = pd.api.types.is_datetime64_any_dtype(df[x_col]) or "date" in x_col.lower() or "time" in x_col.lower()
    order_clause = f"ORDER BY {_q(x_col)} ASC" if is_temporal_x else f"ORDER BY {_q(y_cols[0])} DESC"

    sql = (
        f"SELECT {grp_select},\n"
        f"       {', '.join(metric_exprs)}\n"
        f"FROM {_q(safe_ds)}\n"
        f"GROUP BY {grp_by}\n"
        f"{order_clause}"
    )
    res_df, err = execute_readonly_duckdb(sql, {safe_ds: df})
    return sql, res_df, err


def classify_dataframe_columns(
    df: pd.DataFrame | None, allowed_cols: list[str]
) -> dict[str, list[str]]:
    """Classify columns into temporal, numeric, and categorical buckets for axis guardrails."""
    temporal: list[str] = []
    numeric: list[str] = []
    categorical: list[str] = []

    if df is None or df.empty:
        for c in allowed_cols:
            cl = c.lower()
            if "date" in cl or "time" in cl:
                temporal.append(c)
            elif any(
                tok in cl
                for tok in (
                    "name",
                    "category",
                    "group",
                    "stage",
                    "component",
                    "country",
                    "region",
                    "device",
                    "channel",
                )
            ):
                categorical.append(c)
            else:
                numeric.append(c)
        return {
            "temporal": temporal,
            "numeric": numeric,
            "categorical": categorical,
        }

    for c in allowed_cols:
        if c not in df.columns:
            continue
        series = df[c]
        cl = c.lower()
        if pd.api.types.is_datetime64_any_dtype(series) or "date" in cl:
            temporal.append(c)
        elif pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(
            series
        ):
            numeric.append(c)
        else:
            categorical.append(c)
    # Place high-cardinality primary key ID columns (e.g. order_id) after true business dimensions (region, category)
    categorical.sort(key=lambda col: (1 if col.lower() == "id" or col.lower().endswith("_id") else 0))
    return {"temporal": temporal, "numeric": numeric, "categorical": categorical}


def get_allowed_axis_columns(
    chart_type: str, col_types: dict[str, list[str]]
) -> tuple[list[str], list[str], list[str]]:
    """Return `(allowed_x, allowed_y, allowed_color)` strictly enforcing Tableau/Looker-style data types."""
    temp = col_types.get("temporal", [])
    num = col_types.get("numeric", [])
    cat = col_types.get("categorical", [])
    color_cat = [
        c for c in cat if c.lower() != "id" and not c.lower().endswith("_id")
    ]

    if chart_type == "horizontal_bar":
        return num, cat, color_cat
    if chart_type == "donut":
        return cat, num, []
    if chart_type == "scatter":
        return num, num, color_cat
    if chart_type == "dual_axis":
        return temp + cat, num, []
    # line, area, bar, stacked_bar
    return temp + cat, num, color_cat


def get_supported_chart_types_for_df(col_types: dict[str, list[str]]) -> list[str]:
    """Filter chart types to only those mathematically valid for this dataset's column types."""
    valid: list[str] = []
    for ct in SUPPORTED_CHART_TYPES:
        ax_x, ax_y, _ = get_allowed_axis_columns(ct, col_types)
        if ct in ("dual_axis", "scatter"):
            if ax_x and len(ax_y) >= 2:
                valid.append(ct)
        elif ax_x and ax_y:
            valid.append(ct)
    return valid or ["line", "bar"]


def _col_type_icon(col: str, col_types: dict[str, list[str]]) -> str:
    if col in col_types.get("temporal", []):
        return "📅"
    if col in col_types.get("numeric", []):
        return "🔢"
    return "🏷️"


def generate_ai_chart_config(
    query: str,
    dataset_schemas: dict[str, dict[str, str]],
    api_key: str | None = None,
    domain_context: str = "",
    fallback_fn=None,
) -> dict:
    """Schema-agnostic Gemini AI NL2Chart engine with strict column whitelist validation.

    Shared across all Lakehouse Cockpit projects (ReadCall, Consulting Demo, etc.).
    """
    import json
    import os

    clean_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if clean_key and dataset_schemas:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=clean_key)
            candidates = _discover_gemini_flash_models(client)
            schema_doc = json.dumps(dataset_schemas, ensure_ascii=False, indent=2)
            ds_list = " | ".join(f'"{k}"' for k in dataset_schemas)

            system_prompt = (
                f"You are a Product & Business Analytics NL2Chart engine. {domain_context}\n"
                "Given a user's chart request in Chinese or English, return ONLY a valid JSON object.\n"
                "CRITICAL GUARDRAIL: If the user's request is unrelated to business analytics or cannot be mapped to any columns in DATASET_SCHEMAS "
                "(e.g., general trivia, politics, people like '柯文哲是誰', weather, or non-existent dimensions), DO NOT guess a chart! Return ONLY:\n"
                '{"error": "<explicit reason in the user\'s language explaining why the query cannot be plotted from the available datasets>"}\n\n'
                "Otherwise, when the request clearly maps to DATASET_SCHEMAS, return:\n"
                "{\n"
                f'  "dataset": {ds_list},\n'
                '  "chart_type": "line" | "area" | "bar" | "stacked_bar" | "horizontal_bar" | "donut" | "scatter" | "dual_axis",\n'
                '  "x": "<exact column name from chosen dataset>",\n'
                '  "y": ["<exact column name(s) from chosen dataset>"],\n'
                '  "color": "<optional categorical grouping column or null>",\n'
                '  "agg": "SUM" | "AVG" | "COUNT" | "MAX" | "MIN" | "NONE",\n'
                '  "title": "<concise chart title matching the user language>",\n'
                '  "takeaway": "<1-sentence explanation of what this chart measures, matching user language>",\n'
                '  "limit": 12\n'
                "}\n\n"
                "Rules:\n"
                "1. Use ONLY exact column names from the chosen dataset in DATASET_SCHEMAS. Never invent columns.\n"
                "2. Set agg='AVG' for average/AOV/rate queries, agg='SUM' for total revenue/GMV/units, agg='COUNT' for order counts, and agg='NONE' for scatter plots.\n"
                "3. For multi-dimensional queries like 'daily revenue by region', use the table with event_date, set x='event_date', color='region', y=['gross_amount'], agg='SUM'.\n"
                "4. For ranking/top-N categorical datasets, set chart_type='horizontal_bar' and 'limit' to requested top-N.\n\n"
                f"DATASET_SCHEMAS:\n{schema_doc}"
            )

            for model_name in candidates:
                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=f"User chart request: {query}",
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            temperature=0.0,
                            response_mime_type="application/json",
                        ),
                    )
                    if not resp or not resp.text:
                        continue
                    parsed = json.loads(resp.text.strip())
                    if parsed.get("error"):
                        return {
                            "error": str(parsed["error"]),
                            "query": query,
                            "generated_by": f"gemini:{model_name}",
                        }
                    ds = str(parsed.get("dataset", "")).strip().lower()
                    if ds not in dataset_schemas:
                        continue

                    valid_cols = set(dataset_schemas[ds].keys())
                    ctype = str(parsed.get("chart_type", "line")).strip().lower()
                    if ctype not in SUPPORTED_CHART_TYPES:
                        ctype = "line"

                    if ctype == "horizontal_bar" and "event_name" in valid_cols:
                        limit_val = max(1, min(int(parsed.get("limit", 12)), 32))
                        return {
                            "dataset": ds,
                            "chart_type": "horizontal_bar",
                            "x": "total_event_count"
                            if "total_event_count" in valid_cols
                            else next(iter(valid_cols)),
                            "y": "event_name",
                            "color": "module_category"
                            if "module_category" in valid_cols
                            else None,
                            "title": str(parsed.get("title") or query),
                            "takeaway": str(parsed.get("takeaway") or ""),
                            "limit": limit_val,
                            "query": query,
                            "generated_by": f"gemini:{model_name}",
                        }

                    x_col = str(parsed.get("x", "event_date")).strip()
                    if x_col not in valid_cols:
                        x_col = (
                            "event_date"
                            if "event_date" in valid_cols
                            else next(iter(valid_cols))
                        )

                    raw_y = parsed.get("y", [])
                    if isinstance(raw_y, str):
                        raw_y = [raw_y]
                    y_cols = [
                        c
                        for c in raw_y
                        if isinstance(c, str) and c in valid_cols and c != x_col
                    ]
                    if not y_cols:
                        continue

                    raw_agg = str(parsed.get("agg", "NONE" if ctype == "scatter" else "SUM")).strip().upper()
                    agg_val = raw_agg if raw_agg in SUPPORTED_AGGREGATIONS else ("NONE" if ctype == "scatter" else "SUM")
                    col_c = parsed.get("color")
                    valid_color_c = str(col_c).strip() if isinstance(col_c, str) and str(col_c).strip() in valid_cols else None

                    return {
                        "dataset": ds,
                        "chart_type": ctype,
                        "x": x_col,
                        "y": y_cols[:3],
                        "color": valid_color_c,
                        "agg": agg_val,
                        "title": str(parsed.get("title") or query),
                        "takeaway": str(parsed.get("takeaway") or ""),
                        "query": query,
                        "generated_by": f"gemini:{model_name}",
                    }
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass

    if callable(fallback_fn):
        return fallback_fn(query)
    first_ds = next(iter(dataset_schemas)) if dataset_schemas else "kpi"
    return {
        "dataset": first_ds,
        "chart_type": "line",
        "x": "event_date",
        "y": [],
        "title": query,
        "query": query,
        "generated_by": "fallback_rules",
    }


def render_ai_dynamic_chart(config: dict, df: pd.DataFrame) -> go.Figure | None:
    """Shared Plotly renderer supporting 8 chart types with strict numeric guardrails & % formatting."""
    if df is None or df.empty or not config:
        return None

    ctype = str(config.get("chart_type", "line")).strip().lower()
    title = config.get("title", "AI Generated Chart")
    palette = ["#2563eb", "#10b981", "#f59e0b", "#8b5cf6"]

    def _is_rate_col(name: str) -> bool:
        return any(tok in name.lower() for tok in ("rate", "ratio", "pct", "share"))

    def _pick_color(name: str, idx: int) -> str:
        is_neg = any(
            neg in name.lower() for neg in ("fail", "error", "return", "churn", "drop")
        )
        return "#ef4444" if is_neg else palette[idx % len(palette)]

    x_col = config.get("x", "event_date")
    raw_y = config.get("y", [])
    y_cols = [raw_y] if isinstance(raw_y, str) else list(raw_y)
    color_col = config.get("color")
    valid_color = color_col if color_col and color_col in df.columns else None

    # 1. Horizontal Top-N Ranking Bar Chart (X=numeric, Y=categorical)
    if ctype == "horizontal_bar":
        y_cat = y_cols[0] if y_cols and y_cols[0] in df.columns else ("event_name" if "event_name" in df.columns else None)
        x_num = x_col if x_col in df.columns and pd.api.types.is_numeric_dtype(df[x_col]) else None
        if not y_cat or not x_num:
            return None
        limit_n = max(1, min(int(config.get("limit", 12)), 32))
        sub = df.sort_values(x_num, ascending=False).head(limit_n).copy()
        fig = px.bar(
            sub,
            x=x_num,
            y=y_cat,
            color=valid_color,
            orientation="h",
            title=title,
            template="plotly_white",
        )
        fig.update_layout(
            yaxis={"categoryorder": "total ascending"},
            height=420,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        return fig

    # 2. Donut / Pie Share Chart (X=categorical slice labels, Y[0]=numeric sum)
    if ctype == "donut":
        num_y = [c for c in y_cols if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]
        if x_col not in df.columns or not num_y:
            return None
        val_col = num_y[0]
        agg = df.groupby(x_col, as_index=False)[val_col].sum().sort_values(val_col, ascending=False)
        agg = agg[agg[val_col] > 0]
        if agg.empty:
            return None
        fig = px.pie(
            agg,
            names=x_col,
            values=val_col,
            hole=0.45,
            title=title,
            template="plotly_white",
            color_discrete_sequence=palette + ["#ec4899", "#06b6d4"],
        )
        fig.update_traces(textposition="inside", textinfo="percent+label")
        fig.update_layout(
            height=400,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        return fig

    # 3. Scatter Correlation Chart (X=numeric, Y[0]=numeric)
    if ctype == "scatter":
        num_y = [c for c in y_cols if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]
        if x_col not in df.columns or not pd.api.types.is_numeric_dtype(df[x_col]) or not num_y:
            return None
        fig = px.scatter(
            df,
            x=x_col,
            y=num_y[0],
            color=valid_color,
            title=title,
            template="plotly_white",
        )
        fig.update_layout(
            height=390,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        return fig

    # 4. Dual-Axis Combo Chart (Left Y=Bar, Right Y=Line — solves mixed units!)
    avail_y = [c for c in y_cols if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]
    if not avail_y or x_col not in df.columns:
        return None

    sub = df.sort_values(x_col).copy()

    if ctype == "dual_axis" and len(avail_y) >= 2:
        from plotly.subplots import make_subplots

        left_col, right_col = avail_y[0], avail_y[1]
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(
            go.Bar(
                x=sub[x_col],
                y=sub[left_col],
                name=left_col,
                marker_color=_pick_color(left_col, 0),
                opacity=0.82,
            ),
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=sub[x_col],
                y=sub[right_col],
                mode="lines+markers",
                name=right_col,
                line={
                    "color": _pick_color(right_col, 1),
                    "width": 2.5,
                    "shape": "linear" if _is_rate_col(right_col) else "spline",
                },
            ),
            secondary_y=True,
        )
        fig.update_yaxes(
            title_text=left_col,
            rangemode="tozero",
            tickformat=".0%" if _is_rate_col(left_col) else None,
            secondary_y=False,
        )
        fig.update_yaxes(
            title_text=right_col,
            rangemode="tozero",
            tickformat=".0%" if _is_rate_col(right_col) else None,
            secondary_y=True,
        )
        fig.update_layout(
            title=title,
            template="plotly_white",
            height=400,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            hovermode="x unified",
            legend={"orientation": "h", "y": 1.12},
        )
        return fig

    # 5. Line / Area / Bar / Stacked Bar (strictly numeric Y, supporting Color grouping)
    is_rate_chart = all(_is_rate_col(c) for c in avail_y)
    if valid_color and len(avail_y) == 1:
        y_single = avail_y[0]
        if ctype in ("bar", "stacked_bar"):
            fig = px.bar(
                sub,
                x=x_col,
                y=y_single,
                color=valid_color,
                barmode="stack" if ctype == "stacked_bar" else "group",
                title=title,
                template="plotly_white",
                color_discrete_sequence=palette + ["#ec4899", "#06b6d4"],
            )
        elif ctype == "area":
            fig = px.area(
                sub,
                x=x_col,
                y=y_single,
                color=valid_color,
                markers=True,
                title=title,
                template="plotly_white",
                color_discrete_sequence=palette + ["#ec4899", "#06b6d4"],
            )
        else:
            fig = px.line(
                sub,
                x=x_col,
                y=y_single,
                color=valid_color,
                markers=True,
                title=title,
                template="plotly_white",
                color_discrete_sequence=palette + ["#ec4899", "#06b6d4"],
            )
        fig.update_layout(
            height=400,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            hovermode="x unified",
            legend={"orientation": "h", "y": 1.12},
        )
        return fig

    fig = go.Figure()
    for idx, col in enumerate(avail_y):
        color = _pick_color(col, idx)
        if ctype in ("bar", "stacked_bar"):
            fig.add_trace(
                go.Bar(x=sub[x_col], y=sub[col], name=col, marker_color=color)
            )
        elif ctype == "area":
            fig.add_trace(
                go.Scatter(
                    x=sub[x_col],
                    y=sub[col],
                    mode="lines+markers",
                    name=col,
                    fill="tozeroy" if idx == 0 else "tonexty",
                    line={"color": color, "width": 2.2, "shape": "linear" if is_rate_chart else "spline"},
                )
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=sub[x_col],
                    y=sub[col],
                    mode="lines+markers",
                    name=col,
                    line={
                        "color": color,
                        "width": 2.5,
                        "shape": "linear" if is_rate_chart else "spline",
                    },
                )
            )

    layout_kwargs = {
        "barmode": "stack" if ctype == "stacked_bar" else "group",
        "title": title,
        "template": "plotly_white",
        "height": 390,
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "hovermode": "x unified",
        "legend": {"orientation": "h", "y": 1.1},
    }
    if is_rate_chart:
        layout_kwargs["yaxis"] = {"tickformat": ".0%"}
    fig.update_layout(**layout_kwargs)
    return fig


def refine_ai_chart_config(
    current_config: dict,
    user_message: str,
    chat_history: list[dict[str, str]],
    dataset_schemas: dict[str, dict[str, str]],
    api_key: str | None = None,
    domain_context: str = "",
    is_en: bool = False,
) -> tuple[dict, str]:
    """Refine an existing chart config or answer user questions in a multi-turn HITL loop."""
    import json
    import os
    import re

    updated = dict(current_config)
    clean_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()

    if clean_key and dataset_schemas:
        schema_doc = json.dumps(dataset_schemas, ensure_ascii=False, indent=2)
        hist_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in chat_history[-8:]
        )
        full_prompt = (
            f"You are a Human-in-the-Loop BI Chart Spec Assistant. {domain_context}\n"
            "The user is inspecting a pending Plotly chart specification BEFORE rendering it.\n"
            "They may ask a question (e.g. why a column was chosen, why daily data is missing, what a metric means) "
            "OR ask to modify parameters (e.g. switch dataset to fact_orders, change X to event_date, set color to region, change chart_type).\n"
            "Return ONLY a valid JSON object without markdown fences:\n"
            "{\n"
            '  "reply": "<concise helpful answer/confirmation in the user language>",\n'
            '  "config": {\n'
            '    "dataset": "<valid dataset key>",\n'
            '    "chart_type": "line" | "area" | "bar" | "stacked_bar" | "horizontal_bar" | "donut" | "scatter" | "dual_axis",\n'
            '    "x": "<valid column>",\n'
            '    "y": ["<valid column(s)>"],\n'
            '    "color": "<optional valid column or null>",\n'
            '    "agg": "SUM" | "AVG" | "COUNT" | "MAX" | "MIN" | "NONE",\n'
            '    "limit": 12,\n'
            '    "title": "<chart title>",\n'
            '    "takeaway": "<1-sentence takeaway>"\n'
            "  }\n"
            "}\n"
            "Rules: Only use exact columns from DATASET_SCHEMAS. Preserve unchanged fields from current_config.\n\n"
            f"DATASET_SCHEMAS:\n{schema_doc}\n\n"
            f"Current Chart Spec:\n{json.dumps(current_config, ensure_ascii=False, indent=2)}\n\n"
            f"Conversation History:\n{hist_text}\n\n"
            f"New User Message: {user_message}"
        )
        try:
            raw_txt = generate_gemini_text(
                api_key=clean_key,
                prompt=full_prompt,
                model="gemini-3.5-flash-lite",
            )
            m_json = re.search(r"\{.*\}", raw_txt, re.DOTALL)
            if not m_json:
                return updated, (
                    f"❌ Gemini returned non-JSON response: {raw_txt[:200]}"
                    if is_en
                    else f"❌ Gemini 未回傳有效 JSON 格式：{raw_txt[:200]}"
                )
            parsed = json.loads(m_json.group(0))
            reply = str(parsed.get("reply") or "").strip()
            new_cfg = parsed.get("config") or {}
            ds = str(new_cfg.get("dataset", updated.get("dataset", ""))).strip().lower()
            if ds in dataset_schemas:
                valid_cols = set(dataset_schemas[ds].keys())
                updated["dataset"] = ds
                ctype = str(
                    new_cfg.get("chart_type", updated.get("chart_type", "line"))
                ).strip().lower()
                if ctype in SUPPORTED_CHART_TYPES:
                    updated["chart_type"] = ctype
                x_c = str(new_cfg.get("x", updated.get("x", ""))).strip()
                if x_c in valid_cols:
                    updated["x"] = x_c
                raw_y = new_cfg.get("y", updated.get("y", []))
                if isinstance(raw_y, str):
                    raw_y = [raw_y]
                valid_y = [c for c in raw_y if isinstance(c, str) and c in valid_cols]
                if valid_y:
                    updated["y"] = (
                        valid_y[0]
                        if updated.get("chart_type") == "horizontal_bar"
                        and "event_name" in valid_y
                        else valid_y[:3]
                    )
                col_c = new_cfg.get("color")
                if col_c in valid_cols:
                    updated["color"] = col_c
                elif col_c is None:
                    updated.pop("color", None)
                if new_cfg.get("agg") and str(new_cfg["agg"]).upper() in SUPPORTED_AGGREGATIONS:
                    updated["agg"] = str(new_cfg["agg"]).upper()
                if "limit" in new_cfg and new_cfg["limit"]:
                    updated["limit"] = max(1, min(int(new_cfg["limit"]), 32))
                if new_cfg.get("title"):
                    updated["title"] = str(new_cfg["title"])
                if new_cfg.get("takeaway"):
                    updated["takeaway"] = str(new_cfg["takeaway"])
                updated["generated_by"] = "gemini:gemini-3.5-flash-lite"
            return updated, reply or (
                "Updated chart specification above."
                if is_en
                else "已依據您的補充更新上方圖表參數。"
            )
        except Exception as exc:
            return updated, (
                f"❌ **Gemini API Error during refinement**: {exc}"
                if is_en
                else f"❌ **Gemini 對話微調呼叫失敗**：{exc}"
            )

    # Deterministic offline refinement fallback (no API key needed for simple tweaks)
    msg_l = user_message.strip().lower()
    changes: list[str] = []
    if any(k in msg_l for k in ("長條", "柱狀", "bar")):
        if updated.get("chart_type") != "horizontal_bar":
            updated["chart_type"] = "bar"
            changes.append("圖表類型 ➔ 長條圖 (bar)" if not is_en else "chart_type -> bar")
    elif any(k in msg_l for k in ("折線", "趨勢線", "line")):
        updated["chart_type"] = "line"
        changes.append("圖表類型 ➔ 折線圖 (line)" if not is_en else "chart_type -> line")

    m_top = re.search(r"(?:top|前)\s*(\d+)", msg_l)
    if m_top:
        n_lim = max(1, min(int(m_top.group(1)), 32))
        updated["limit"] = n_lim
        changes.append(f"筆數上限 ➔ Top {n_lim}" if not is_en else f"limit -> Top {n_lim}")

    if changes:
        reply_txt = (
            ("Updated: " + ", ".join(changes) + ". Click 'Render Chart' when ready.")
            if is_en
            else ("已套用調整：" + "、".join(changes) + "。確認無誤後可點擊下方「產生圖表」。")
        )
    else:
        reply_txt = (
            "Tip: You can directly adjust X/Y columns or chart type in the form above, or bind your Gemini API Key on the right for full AI conversational Q&A."
            if is_en
            else "提示：您可以直接在上方表單手動調整 X/Y 軸與圖表類型；若於右側綁定 Gemini API Key，即可進行完整 AI 疑問解答與自然語言微調。"
        )
    return updated, reply_txt


def _format_col_label(col: str, schema_map: dict[str, str]) -> str:
    desc = schema_map.get(col, "")
    return f"{col}（{desc}）" if desc else col


def _audit_chart_logic_with_gemini(
    current_config: dict[str, Any],
    df: pd.DataFrame | None,
    schema_map: dict[str, str],
    user_query: str,
    api_key: str,
    is_en: bool,
) -> str:
    """Inspect current chart specification against live DataFrame statistics & semantic ontology via Gemini AI."""
    profile_lines: list[str] = []
    if df is not None and not df.empty:
        profile_lines.append(f"Row count: {len(df)}")
        for col in df.columns:
            s = df[col]
            desc = schema_map.get(str(col), "")
            cl = str(col).lower()
            if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
                sem_tag = (
                    "Non-Additive Rate/Percentage"
                    if any(k in cl for k in ("rate", "pct", "ratio", "share"))
                    else (
                        "Non-Additive Average/Unit Value"
                        if any(k in cl for k in ("avg", "aov", "price"))
                        else "Additive Measure"
                    )
                )
                profile_lines.append(
                    f"- {col} ({desc}) [Numeric | {sem_tag}]: min={s.min()}, max={s.max()}, mean={round(float(s.mean()), 2)}, nunique={s.nunique()}"
                )
            elif pd.api.types.is_datetime64_any_dtype(s) or "date" in cl or "time" in cl:
                profile_lines.append(
                    f"- {col} ({desc}) [Temporal Time Axis]: nunique={s.nunique()}, range=({s.min()} .. {s.max()})"
                )
            else:
                sem_tag = "High-Cardinality ID" if (cl == "id" or cl.endswith("_id")) else "Nominal Categorical Dimension"
                profile_lines.append(
                    f"- {col} ({desc}) [Categorical | {sem_tag}]: nunique={s.nunique()}, sample={list(s.dropna().unique()[:5])}"
                )
    profile_text = "\n".join(profile_lines) or "No DataFrame profile available."

    prompt = (
        "You are a Senior BI & Statistical Visualization Auditor.\n"
        "Inspect whether the user's current Step 2 Chart Specification is logically, statistically, and semantically valid.\n\n"
        f"1. Original User Request (Step 1): {user_query}\n"
        f"2. Current Chart Spec (Step 2): dataset={current_config.get('dataset')}, chart_type={current_config.get('chart_type')}, "
        f"agg={current_config.get('agg', 'SUM')}, x={current_config.get('x')}, y={current_config.get('y')}, color={current_config.get('color')}\n"
        f"3. Live DataFrame & Semantic/Ontology Profile:\n{profile_text}\n\n"
        "Audit Checklist:\n"
        "- Aggregation & Sawtooth Check: If chart_type is line/area/bar and agg='NONE' on a dataset where X-axis has duplicate dates/categories (row_count > x.nunique), warn that unaggregated rows will cause zig-zag sawtooth artifacts and recommend SUM or AVG.\n"
        "- Magnitude / Unit Mismatch: Are multiple Y columns plotted on a single axis (bar/stacked_bar/line/area) when their means differ by >20x or have incompatible units (e.g. USD vs Order Count)? If so, recommend 'dual_axis'.\n"
        "- Additivity Violation: Is a Non-Additive metric (average/AOV or percentage/rate) used in a 'donut' share chart or 'stacked_bar' where values are summed?\n"
        "- Categorical Trend Misuse: Is a 'line' or 'area' chart used on a Nominal Categorical X-axis (e.g. category, region) instead of a Temporal date axis?\n"
        "- Degenerate Scatter / Low Points: Does a 'scatter' chart have X == Y, or too few rows (e.g. only 4 summary rows)?\n"
        "- Intent Drift: Does the current X/Y/Color selection omit a key dimension requested in the Original User Request (e.g. user asked for 'daily by region' but either date or region is missing)?\n\n"
        "Response Format:\n"
        "- If ALL checks pass with no logical issues: start with '✅' and confirm briefly that the chart specification is statistically and semantically sound.\n"
        "- If ANY issue is found: start with '⚠️', clearly list which logic check(s) failed, ask a brief clarifying question if user intent is ambiguous, and give concrete parameter fix recommendations.\n"
        f"Respond concisely in {'English' if is_en else 'Traditional Chinese (繁體中文)'}."
    )
    return generate_gemini_text(api_key=api_key, prompt=prompt, model="gemini-3.5-flash-lite")


def _render_nl2chart_studio_body(
    dataset_schemas: dict[str, dict[str, str]],
    dataset_getters: dict[str, Any],
    api_key: str = "",
    domain_context: str = "",
    presets: list[dict[str, str]] | None = None,
    fallback_fn=None,
    is_en: bool = False,
    state_prefix: str = "nl2chart_hitl",
) -> None:
    """Core body of the 2-step Human-in-the-Loop NL2Chart Studio."""
    k_query = f"{state_prefix}_query"
    k_draft = f"{state_prefix}_draft_cfg"
    k_chat = f"{state_prefix}_chat_hist"
    k_rendered = f"{state_prefix}_rendered_cfg"
    k_init = f"{state_prefix}_initialized"
    k_rev = f"{state_prefix}_spec_rev"

    # Always resolve the freshest Gemini API Key inside the fragment (never rely on stale parameter capture)
    live_api_key = (
        st.session_state.get("user_gemini_key")
        or st.session_state.get("sidebar_gemini_api_key")
        or st.session_state.get("gemini_api_key")
        or api_key
        or os.getenv("GEMINI_API_KEY", "")
    ).strip()

    def _clear_spec_widget_state() -> None:
        """Bump widget revision counter and clear cached selectbox keys so new specs take effect 100% of the time."""
        st.session_state[k_rev] = int(st.session_state.get(k_rev, 0)) + 1
        prefixes = (
            f"{state_prefix}_sel_ds",
            f"{state_prefix}_sel_ctype_",
            f"{state_prefix}_sel_agg_",
            f"{state_prefix}_sel_limit",
            f"{state_prefix}_sel_color_",
            f"{state_prefix}_sel_x_",
            f"{state_prefix}_sel_y1_",
            f"{state_prefix}_sel_y_",
        )
        for k in list(st.session_state.keys()):
            if any(str(k).startswith(p) for p in prefixes):
                st.session_state.pop(k, None)

    if k_query not in st.session_state:
        st.session_state[k_query] = (
            presets[0]["query"] if presets else "Show daily active users trend"
        )
    if k_chat not in st.session_state:
        st.session_state[k_chat] = []
    if k_init not in st.session_state and callable(fallback_fn):
        st.session_state[k_init] = True
        init_cfg = fallback_fn(st.session_state[k_query])
        if init_cfg and not init_cfg.get("error"):
            st.session_state[k_draft] = dict(init_cfg)
            st.session_state[k_rendered] = dict(init_cfg)
            st.session_state[k_chat] = [
                {
                    "role": "assistant",
                    "content": (
                        f"Drafted chart spec for **{st.session_state[k_query]}**. Review or refine parameters below, then click **Confirm & Generate Chart**."
                        if is_en
                        else f"已為「**{st.session_state[k_query]}**」解析出圖表參數規格！請確認下方 X/Y 軸與其他參數，若有疑問可直接在對話視窗補充，確認無誤後點擊「**確認參數，產生圖表**」。"
                    ),
                }
            ]

    # Optional preset quick buttons
    if presets:
        cols_p = st.columns(len(presets))
        for idx, p in enumerate(presets):
            if cols_p[idx].button(
                p["label"],
                key=f"{state_prefix}_preset_{idx}",
                use_container_width=True,
            ):
                st.session_state[k_query] = p["query"]
                cfg = generate_ai_chart_config(
                    query=p["query"],
                    dataset_schemas=dataset_schemas,
                    api_key=live_api_key,
                    domain_context=domain_context,
                    fallback_fn=fallback_fn,
                )
                st.session_state[k_draft] = cfg
                _clear_spec_widget_state()
                st.session_state[k_chat] = [
                    {
                        "role": "assistant",
                        "content": (
                            f"Drafted chart spec for **{p['query']}**. Review or refine parameters below, then click **Render Chart**."
                            if is_en
                            else f"已為「**{p['query']}**」解析出圖表參數規格！請確認下方 X/Y 軸與其他參數，若有疑問可直接補充，確認無誤後再點擊「**產生圖表**」。"
                        ),
                    }
                ]

    # Step 1: Natural Language Request Input
    st.markdown(
        f"**{'Step 1 · Enter chart request:' if is_en else '步驟 1 · 輸入圖表需求：'}**"
    )
    c_in, c_btn = st.columns([4.6, 1.4])
    with c_in:
        nl_q = st.text_input(
            "Enter chart request:" if is_en else "輸入圖表需求：",
            key=k_query,
            label_visibility="collapsed",
        )
    with c_btn:
        btn_analyze = st.button(
            "🔍 " + ("Analyze Spec" if is_en else "解析圖表參數"),
            key=f"{state_prefix}_btn_analyze",
            type="primary",
            use_container_width=True,
        )

    if btn_analyze and nl_q.strip():
        with st.spinner(
            "🤖 Analyzing chart parameters..."
            if is_en
            else "🤖 正在解析 X/Y 軸與圖表參數規格..."
        ):
            cfg = generate_ai_chart_config(
                query=nl_q.strip(),
                dataset_schemas=dataset_schemas,
                api_key=live_api_key,
                domain_context=domain_context,
                fallback_fn=fallback_fn,
            )
        st.session_state[k_draft] = cfg
        _clear_spec_widget_state()
        ds_k = cfg.get("dataset", "")
        x_v = cfg.get("x", "")
        y_v = cfg.get("y", [])
        y_str = ", ".join(y_v) if isinstance(y_v, list) else str(y_v)
        st.session_state[k_chat] = [
            {
                "role": "assistant",
                "content": (
                    f"Proposed spec from `{ds_k}`: X=`{x_v}`, Y=`{y_str}`, Type=`{cfg.get('chart_type')}`. "
                    "Ask any question or request edits below before rendering!"
                    if is_en
                    else f"已從 `{ds_k}` 規劃初步規格：X軸=`{x_v}`、Y軸=`{y_str}`、類型=`{cfg.get('chart_type')}`。若對欄位或計算方式有疑問，可直接在下方對話補充，確認後再按「產生圖表」！"
                ),
            }
        ]

    # Step 2: Interactive Chart Spec Confirmation Form + Accumulating Q&A Box
    draft_cfg = st.session_state.get(k_draft)
    if draft_cfg and draft_cfg.get("error"):
        st.error(f"❌ {draft_cfg['error']}")
    elif draft_cfg:
        ds_keys = list(dataset_schemas.keys())
        cur_ds = draft_cfg.get("dataset", ds_keys[0])
        if cur_ds not in dataset_schemas:
            cur_ds = ds_keys[0]
        cur_schema = dataset_schemas[cur_ds]
        all_cols = list(cur_schema.keys())

        with st.container(border=True):
            st.markdown(
                "#### 📋 "
                + (
                    "Step 2 · Review Chart Parameters & Refine (Before Plotting)"
                    if is_en
                    else "步驟 2 · 圖表參數確認與補充對話（確認後才繪圖）"
                )
            )

            # Readable summary banner matching user's exact requested format
            raw_y_disp = draft_cfg.get("y", [])
            y_list_disp = [raw_y_disp] if isinstance(raw_y_disp, str) else list(raw_y_disp)
            x_label = _format_col_label(str(draft_cfg.get("x", "")), cur_schema)
            y_label = "、".join(_format_col_label(c, cur_schema) for c in y_list_disp)
            other_parts = [
                f"資料表={cur_ds}" if not is_en else f"Dataset={cur_ds}",
                f"類型={draft_cfg.get('chart_type', 'line')}"
                if not is_en
                else f"Type={draft_cfg.get('chart_type', 'line')}",
                f"聚合={draft_cfg.get('agg', 'NONE' if draft_cfg.get('chart_type') == 'scatter' else 'SUM')}"
                if not is_en
                else f"Agg={draft_cfg.get('agg', 'NONE' if draft_cfg.get('chart_type') == 'scatter' else 'SUM')}",
            ]
            if draft_cfg.get("color"):
                other_parts.append(
                    f"顏色分組={draft_cfg['color']}"
                    if not is_en
                    else f"Color={draft_cfg['color']}"
                )
            if draft_cfg.get("chart_type") == "horizontal_bar":
                other_parts.append(
                    f"顯示筆數=Top {draft_cfg.get('limit', 12)}"
                    if not is_en
                    else f"Limit=Top {draft_cfg.get('limit', 12)}"
                )
            is_rate_preview = bool(y_list_disp) and all(
                any(t in c.lower() for t in ("rate", "ratio", "pct", "share"))
                for c in y_list_disp
            )
            if is_rate_preview:
                other_parts.append("Y軸單位=百分比 (%)" if not is_en else "Y-Axis=%")

            if is_en:
                st.info(
                    f"**X-Axis:** ({x_label}) , **Y-Axis:** ({y_label})\n\n"
                    f"**Other Parameters:** ({' ｜ '.join(other_parts)})"
                )
            else:
                st.info(
                    f"**X軸：** ({x_label}) ， **Y軸：** ({y_label})\n\n"
                    f"**其他欄位或資訊：** ({' ｜ '.join(other_parts)})"
                )

            # Editable Parameter Controls with Typed Axis Guardrails (📅 Temporal / 🏷️ Text / 🔢 Numeric)
            rev = int(st.session_state.get(k_rev, 0))
            r1_c1, r1_c2, r1_c3 = st.columns(3)
            with r1_c1:
                sel_ds = st.selectbox(
                    "資料表 (Dataset)" if not is_en else "Dataset",
                    options=ds_keys,
                    index=ds_keys.index(cur_ds),
                    key=f"{state_prefix}_sel_ds_{rev}",
                )
            if sel_ds != cur_ds:
                cur_ds = sel_ds
                cur_schema = dataset_schemas[cur_ds]
                all_cols = list(cur_schema.keys())
                draft_cfg["dataset"] = cur_ds

            # Inspect real DataFrame dtypes to enforce Tableau/Looker-style typed column guardrails
            ds_getter = dataset_getters.get(cur_ds)
            preview_df = ds_getter() if callable(ds_getter) else ds_getter
            col_types = classify_dataframe_columns(preview_df, all_cols)
            chart_types = get_supported_chart_types_for_df(col_types)

            cur_ctype = draft_cfg.get("chart_type", chart_types[0])
            if cur_ctype not in chart_types:
                cur_ctype = chart_types[0]

            ctype_labels = {
                "line": "📈 line (折線圖)" if not is_en else "📈 line (Line Chart)",
                "area": "🌊 area (面積圖)" if not is_en else "🌊 area (Area Chart)",
                "bar": "📊 bar (並列長條圖)" if not is_en else "📊 bar (Grouped Bar)",
                "stacked_bar": "🧱 stacked_bar (堆疊長條圖)"
                if not is_en
                else "🧱 stacked_bar (Stacked Bar)",
                "horizontal_bar": "🏆 horizontal_bar (橫向排行圖)"
                if not is_en
                else "🏆 horizontal_bar (Horizontal Ranking)",
                "donut": "🍩 donut (甜甜圈佔比圖)"
                if not is_en
                else "🍩 donut (Donut Share)",
                "scatter": "✨ scatter (雙指標散佈圖)"
                if not is_en
                else "✨ scatter (Scatter Correlation)",
                "dual_axis": "⚖️ dual_axis (雙 Y 軸：柱＋線)"
                if not is_en
                else "⚖️ dual_axis (Dual Y-Axis: Bar + Line)",
            }
            with r1_c2:
                sel_ctype = st.selectbox(
                    "圖表類型 (Chart Type)" if not is_en else "Chart Type",
                    options=chart_types,
                    index=chart_types.index(cur_ctype),
                    format_func=lambda ct: ctype_labels.get(ct, ct),
                    key=f"{state_prefix}_sel_ctype_{cur_ds}_{rev}",
                )
            draft_cfg["chart_type"] = sel_ctype

            allowed_x, allowed_y, allowed_color = get_allowed_axis_columns(
                sel_ctype, col_types
            )
            allowed_x = allowed_x or all_cols
            allowed_y = allowed_y or all_cols

            with r1_c3:
                if sel_ctype == "horizontal_bar":
                    sel_limit = st.number_input(
                        "顯示筆數上限 (Top N)" if not is_en else "Top N Limit",
                        min_value=1,
                        max_value=32,
                        value=int(draft_cfg.get("limit", 12)),
                        key=f"{state_prefix}_sel_limit_{rev}",
                    )
                    draft_cfg["limit"] = int(sel_limit)
                elif allowed_color:
                    color_opts = ["(None)"] + allowed_color
                    cur_color = draft_cfg.get("color")
                    color_idx = (
                        color_opts.index(cur_color) if cur_color in color_opts else 0
                    )
                    sel_color = st.selectbox(
                        "顏色分組 (🏷️ 分類)" if not is_en else "Color Group (🏷️ Cat)",
                        options=color_opts,
                        index=color_idx,
                        key=f"{state_prefix}_sel_color_{cur_ds}_{sel_ctype}_{rev}",
                    )
                    if sel_color == "(None)":
                        draft_cfg.pop("color", None)
                    else:
                        draft_cfg["color"] = sel_color
                else:
                    draft_cfg.pop("color", None)
                    st.caption(
                        "🛡️ 此資料表／圖表無分類顏色欄位"
                        if not is_en
                        else "🛡️ No categorical color column for this chart type"
                    )

            def _fmt_typed(c: str) -> str:
                return f"{_col_type_icon(c, col_types)} {_format_col_label(c, cur_schema)}"

            r2_c1, r2_c2, r2_c3 = st.columns([1.1, 1.8, 0.9])
            cur_x = draft_cfg.get("x", allowed_x[0])
            if cur_x not in allowed_x:
                cur_x = allowed_x[0]
            x_title_hint = (
                "X 軸（🏷️ 分類切片）"
                if sel_ctype == "donut" and not is_en
                else (
                    "Slice Labels (🏷️ Cat)"
                    if sel_ctype == "donut"
                    else (
                        "X 軸（🔢 數值）"
                        if sel_ctype in ("horizontal_bar", "scatter") and not is_en
                        else ("X 軸欄位 (X-Axis)" if not is_en else "X-Axis Column")
                    )
                )
            )
            with r2_c1:
                sel_x = st.selectbox(
                    x_title_hint,
                    options=allowed_x,
                    index=allowed_x.index(cur_x),
                    format_func=_fmt_typed,
                    key=f"{state_prefix}_sel_x_{cur_ds}_{sel_ctype}_{rev}",
                )
            prev_x = draft_cfg.get("x")
            prev_y = draft_cfg.get("y")
            draft_cfg["x"] = sel_x

            if sel_ctype == "scatter":
                allowed_y = [c for c in allowed_y if c != sel_x] or allowed_y

            raw_y_cur = draft_cfg.get("y", [])
            y_cur_list = [raw_y_cur] if isinstance(raw_y_cur, str) else list(raw_y_cur)
            valid_y_defaults = [c for c in y_cur_list if c in allowed_y]
            if not valid_y_defaults:
                if sel_ctype == "dual_axis" and len(allowed_y) >= 2:
                    valid_y_defaults = allowed_y[:2]
                else:
                    valid_y_defaults = [allowed_y[0]]
            elif sel_ctype == "dual_axis" and len(valid_y_defaults) < 2 and len(allowed_y) >= 2:
                second = next((c for c in allowed_y if c not in valid_y_defaults), allowed_y[1])
                valid_y_defaults = [valid_y_defaults[0], second]

            y_title_hint = (
                "Y 軸（左柱 ＋ 右線，選 2 個 🔢 數值）"
                if sel_ctype == "dual_axis" and not is_en
                else (
                    "Y-Axis (Left Bar + Right Line, pick 2 🔢 Numeric)"
                    if sel_ctype == "dual_axis"
                    else (
                        "Y 軸（🏷️ 分類項目）"
                        if sel_ctype == "horizontal_bar" and not is_en
                        else (
                            "數值指標（🔢 僅限數值）"
                            if not is_en
                            else "Y-Axis Columns (🔢 Numeric Guard)"
                        )
                    )
                )
            )
            with r2_c2:
                if sel_ctype in ("horizontal_bar", "donut", "scatter"):
                    sel_y_single = st.selectbox(
                        y_title_hint,
                        options=allowed_y,
                        index=allowed_y.index(valid_y_defaults[0]),
                        format_func=_fmt_typed,
                        key=f"{state_prefix}_sel_y1_{cur_ds}_{sel_ctype}_{rev}",
                    )
                    draft_cfg["y"] = [sel_y_single] if sel_ctype != "horizontal_bar" else sel_y_single
                else:
                    sel_y = st.multiselect(
                        y_title_hint,
                        options=allowed_y,
                        default=valid_y_defaults,
                        format_func=_fmt_typed,
                        key=f"{state_prefix}_sel_y_{cur_ds}_{sel_ctype}_{rev}",
                    )
                    if sel_y:
                        draft_cfg["y"] = sel_y

            agg_labels = {
                "SUM": "➕ SUM (總和)" if not is_en else "➕ SUM",
                "AVG": "➗ AVG (平均)" if not is_en else "➗ AVG",
                "COUNT": "#️⃣ COUNT (筆數)" if not is_en else "#️⃣ COUNT",
                "MAX": "⬆️ MAX (最大)" if not is_en else "⬆️ MAX",
                "MIN": "⬇️ MIN (最小)" if not is_en else "⬇️ MIN",
                "NONE": "📄 NONE (明細)" if not is_en else "📄 NONE (Raw)",
            }
            cur_agg = str(
                draft_cfg.get("agg", "NONE" if sel_ctype == "scatter" else "SUM")
            ).upper()
            if cur_agg not in SUPPORTED_AGGREGATIONS:
                cur_agg = "NONE" if sel_ctype == "scatter" else "SUM"
            with r2_c3:
                sel_agg = st.selectbox(
                    "SQL 聚合 (Agg)" if not is_en else "SQL Aggregation",
                    options=list(SUPPORTED_AGGREGATIONS),
                    index=SUPPORTED_AGGREGATIONS.index(cur_agg),
                    format_func=lambda a: agg_labels.get(a, a),
                    key=f"{state_prefix}_sel_agg_{cur_ds}_{sel_ctype}_{rev}",
                )
            draft_cfg["agg"] = sel_agg

            if draft_cfg.get("x") != prev_x or draft_cfg.get("y") != prev_y:
                y_now = draft_cfg.get("y", [])
                y_now_str = ", ".join(y_now) if isinstance(y_now, list) else str(y_now)
                draft_cfg["title"] = (
                    f"{y_now_str} by {draft_cfg.get('x')} ({sel_ctype})"
                    if is_en
                    else f"{draft_cfg.get('x')} × {y_now_str} ({sel_ctype})"
                )

            st.caption(
                "🛡️ 型別防呆已啟用：📅 時間維度 ｜ 🏷️ 文字分類 ｜ 🔢 數值度量（系統已自動過濾不合法欄位，防止文本誤入數值軸）"
                if not is_en
                else "🛡️ Typed Axis Guardrails active: 📅 Temporal | 🏷️ Categorical | 🔢 Numeric (invalid text columns are automatically blocked from numeric axes)"
            )
            st.session_state[k_draft] = draft_cfg

            # Accumulating Q&A / Refinement Chat Box (no full-page reload!)
            st.markdown(
                "**💬 "
                + (
                    "Follow-up Q&A & Parameter Refinement (Accumulates without reloading page):"
                    if is_en
                    else "疑問補充對話視窗（對話持續累加，不刷新頁面）："
                )
                + "**"
            )
            if st.session_state[k_chat]:
                with st.container(height=180, border=True):
                    for msg in st.session_state[k_chat]:
                        avatar = "🤖" if msg["role"] == "assistant" else "👤"
                        with st.chat_message(msg["role"], avatar=avatar):
                            st.markdown(msg["content"])

            with st.form(key=f"{state_prefix}_refine_form", clear_on_submit=True):
                q_col, b_send_col, b_audit_col = st.columns([3.2, 1.3, 1.5])
                with q_col:
                    followup_txt = st.text_input(
                        "疑問或補充說明" if not is_en else "Ask or refine parameters...",
                        placeholder=(
                            "例：改成柱狀圖、拿掉某個欄位、為什麼這個指標會破 100%？"
                            if not is_en
                            else "e.g., switch to bar chart, remove a column, why is this rate > 100%?"
                        ),
                        label_visibility="collapsed",
                        key=f"{state_prefix}_followup_input",
                    )
                with b_send_col:
                    btn_send_followup = st.form_submit_button(
                        "💬 " + ("Send" if is_en else "送出補充"),
                        use_container_width=True,
                    )
                with b_audit_col:
                    btn_check_logic = st.form_submit_button(
                        "🩺 " + ("Check Chart Logic" if is_en else "檢查圖表邏輯"),
                        use_container_width=True,
                    )

            if btn_check_logic:
                clean_audit_key = (
                    api_key
                    or st.session_state.get("user_gemini_key")
                    or st.session_state.get("sidebar_gemini_api_key")
                    or st.session_state.get("gemini_api_key")
                    or os.getenv("GEMINI_API_KEY", "")
                ).strip()
                if not clean_audit_key:
                    st.warning(
                        "⚠️ Chart logic inspection requires Gemini AI. Please enter your Gemini API Key in the right drawer first."
                        if is_en
                        else "⚠️ 圖表邏輯檢查需調用 Gemini AI，請先於右側填入 Gemini API Key"
                    )
                else:
                    with st.spinner(
                        "🩺 Gemini AI is auditing chart logic against live DataFrame statistics..."
                        if is_en
                        else "🩺 Gemini AI 正在根據底層資料統計與語意規則檢查圖表邏輯..."
                    ):
                        try:
                            audit_reply = _audit_chart_logic_with_gemini(
                                current_config=dict(st.session_state[k_draft]),
                                df=preview_df,
                                schema_map=cur_schema,
                                user_query=str(st.session_state.get(k_query, "")),
                                api_key=clean_audit_key,
                                is_en=is_en,
                            )
                        except Exception as exc:
                            audit_reply = (
                                f"❌ **Gemini API Error during chart logic inspection**: {exc}"
                                if is_en
                                else f"❌ **圖表邏輯檢查呼叫 Gemini API 失敗**：{exc}"
                            )
                    st.session_state[k_chat].append(
                        {
                            "role": "user",
                            "content": "🩺 Check Chart Logic" if is_en else "🩺 檢查圖表邏輯",
                        }
                    )
                    st.session_state[k_chat].append(
                        {"role": "assistant", "content": audit_reply}
                    )
                    st.rerun(scope="fragment") if hasattr(st, "fragment") else st.rerun()

            if btn_send_followup and followup_txt.strip():
                st.session_state[k_chat].append(
                    {"role": "user", "content": followup_txt.strip()}
                )
                with st.spinner(
                    "🤖 Refining spec..." if is_en else "🤖 正在依據您的補充調整規格..."
                ):
                    new_cfg, ai_reply = refine_ai_chart_config(
                        current_config=dict(st.session_state[k_draft]),
                        user_message=followup_txt.strip(),
                        chat_history=st.session_state[k_chat],
                        dataset_schemas=dataset_schemas,
                        api_key=live_api_key,
                        domain_context=domain_context,
                        is_en=is_en,
                    )
                st.session_state[k_draft] = new_cfg
                _clear_spec_widget_state()
                st.session_state[k_chat].append(
                    {"role": "assistant", "content": ai_reply}
                )
                st.rerun(scope="fragment") if hasattr(st, "fragment") else st.rerun()

            # Action Row: Render Chart vs Reset
            act_c1, act_c2 = st.columns([3.5, 1.5])
            with act_c1:
                if st.button(
                    "🚀 "
                    + (
                        "Confirm & Generate Chart"
                        if is_en
                        else "確認參數，產生圖表"
                    ),
                    key=f"{state_prefix}_btn_render_final",
                    type="primary",
                    use_container_width=True,
                ):
                    st.session_state[k_rendered] = dict(st.session_state[k_draft])
            with act_c2:
                if st.button(
                    "🔄 " + ("Reset" if is_en else "清除重來"),
                    key=f"{state_prefix}_btn_reset",
                    use_container_width=True,
                ):
                    st.session_state.pop(k_draft, None)
                    st.session_state.pop(k_rendered, None)
                    st.session_state[k_chat] = []
                    _clear_spec_widget_state()
                    st.rerun(scope="fragment") if hasattr(st, "fragment") else st.rerun()

    # Step 3: Compile Deterministic DuckDB SQL -> Execute Aggregation -> Render Confirmed Plotly Chart
    rendered_cfg = st.session_state.get(k_rendered)
    if rendered_cfg:
        st.markdown("---")
        r_ds = rendered_cfg.get("dataset", "kpi")
        getter = (
            dataset_getters[r_ds]
            if r_ds in dataset_getters
            else next(iter(dataset_getters.values()))
        )
        target_df = getter() if callable(getter) else getter
        compiled_sql, agg_df, sql_err = compile_spec_to_duckdb_sql(rendered_cfg, target_df)

        if sql_err:
            st.error(
                f"❌ **DuckDB Aggregation Error**: {sql_err}"
                if is_en
                else f"❌ **DuckDB SQL 聚合執行錯誤**：{sql_err}"
            )
            if compiled_sql:
                st.code(compiled_sql, language="sql")
            return

        fig_out = render_ai_dynamic_chart(rendered_cfg, agg_df)

        gen_by = rendered_cfg.get("generated_by", "fallback_rules")
        r_agg = rendered_cfg.get("agg", "NONE" if rendered_cfg.get("chart_type") == "scatter" else "SUM")
        if gen_by.startswith("gemini:"):
            m_tag = gen_by.split(":", 1)[1]
            st.caption(
                f"✨ **AI-Generated Chart** (`{m_tag}` + DuckDB `{r_agg}`) · Dataset: `{r_ds}` · X: `{rendered_cfg.get('x')}` · Y: `{rendered_cfg.get('y')}`"
                if is_en
                else f"✨ **Gemini AI 圖表**（`{m_tag}` ＋ DuckDB `{r_agg}` 聚合）· 資料表：`{r_ds}` · X軸：`{rendered_cfg.get('x')}` · Y軸：`{rendered_cfg.get('y')}`"
            )
        else:
            st.caption(
                f"ℹ️ **Rule-based preview** (DuckDB `{r_agg}` aggregation · bind your Gemini API Key on the right for full AI chart Q&A)."
                if is_en
                else f"ℹ️ **離線規則預覽**（DuckDB `{r_agg}` 聚合 · 於右側綁定 Gemini API Key 可解鎖完整 AI 對話微調）。"
            )

        if rendered_cfg.get("takeaway"):
            st.info(f"💡 **AI Takeaway**: {rendered_cfg['takeaway']}")

        if fig_out:
            st.plotly_chart(fig_out, use_container_width=True)
            exp_c1, exp_c2 = st.columns(2)
            with exp_c1:
                with st.expander(
                    "📜 View Executed DuckDB SQL"
                    if is_en
                    else "📜 檢視實際執行的 DuckDB SQL",
                    expanded=False,
                ):
                    st.code(compiled_sql, language="sql")
            with exp_c2:
                if isinstance(agg_df, pd.DataFrame) and not agg_df.empty:
                    with st.expander(
                        f"📋 Underlying Aggregated Data (`{r_ds}` · {len(agg_df)} rows)"
                        if is_en
                        else f"📋 檢視聚合後底層數據明細（`{r_ds}` · 共 {len(agg_df)} 筆）",
                        expanded=False,
                    ):
                        st.dataframe(agg_df, use_container_width=True, hide_index=True)
        else:
            st.warning(
                "⚠️ No matching data rows for selected columns."
                if is_en
                else "⚠️ 所選欄位在目前日期範圍內無對應數據。"
            )


if hasattr(st, "fragment"):

    @st.fragment
    def render_interactive_nl2chart_studio(
        dataset_schemas: dict[str, dict[str, str]],
        dataset_getters: dict[str, Any],
        api_key: str = "",
        domain_context: str = "",
        presets: list[dict[str, str]] | None = None,
        fallback_fn=None,
        is_en: bool = False,
        state_prefix: str = "nl2chart_hitl",
    ) -> None:
        """Fragment-isolated 2-step Human-in-the-Loop NL2Chart Studio (zero full-page reloads)."""
        _render_nl2chart_studio_body(
            dataset_schemas=dataset_schemas,
            dataset_getters=dataset_getters,
            api_key=api_key,
            domain_context=domain_context,
            presets=presets,
            fallback_fn=fallback_fn,
            is_en=is_en,
            state_prefix=state_prefix,
        )
else:
    render_interactive_nl2chart_studio = _render_nl2chart_studio_body
