"""Interactive Showcase Dashboard for lakehouse-cockpit-core.

Guarantees 1:1 synchronization between Mode A (Integrated Cockpit) and Mode B (Component Storybook)
via a single unified COMPONENT_REGISTRY and shared sidebar state (language + date range).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

# Ensure repository root is importable when run via `streamlit run examples/showcase_app.py`
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapter import (  # noqa: E402
    BaseCockpitAdapter,
    KPICardSpec,
    MedallionTabSpec,
    MetricComponent,
    OnDemandInsightResult,
)
from components.data_center import render_data_center_view  # noqa: E402
from components.databot import render_databot  # noqa: E402
from components.looker_view import render_looker_enterprise_view  # noqa: E402
from components.on_demand_panel import render_on_demand_insight_panel  # noqa: E402
from components.schema_designer import render_schema_designer_view  # noqa: E402
from components.sidebar_ops import render_sidebar_pipeline_ops  # noqa: E402
from components.sql_workbench import (  # noqa: E402
    render_sql_snippet_module,
    render_sql_workbench,
)
from components.vanna_view import render_vanna_view  # noqa: E402
from styles import MODERN_COCKPIT_CSS  # noqa: E402


@dataclass(frozen=True)
class ShowcaseComponentSpec:
    """Single source of truth for a component rendered in both Mode A and Mode B."""

    id: str
    tab_title_en: str
    tab_title_zh: str
    storybook_label: str
    fn_signature: str
    source_files: tuple[str, ...]
    snippet: str
    description_en: str
    description_zh: str
    problem_solved_en: str
    problem_solved_zh: str
    placement_in_mode_a: str  # "top_kpi" | "workspace_tab" | "right_drawer" | "left_sidebar"
    render: Callable[[], None]


def _build_mock_datasets() -> dict[str, pd.DataFrame]:
    """Generate deterministic E-Commerce mock datasets for Silver and Bronze layers."""
    base_date = date(2026, 10, 1)
    categories = ["Electronics", "Apparel", "Home & Living", "Beauty"]
    regions = ["Taipei", "Taichung", "Kaohsiung", "Tainan"]

    order_rows = []
    for idx in range(60):
        order_dt = base_date + timedelta(days=idx % 30)
        cat = categories[idx % len(categories)]
        region = regions[(idx // 2) % len(regions)]
        gross = round(1200 + (idx * 173) % 4800, 2)
        returned = 1 if idx % 19 == 0 else 0
        order_rows.append(
            {
                "order_id": f"ORD-{1000 + idx}",
                "event_date": order_dt.isoformat(),
                "category": cat,
                "region": region,
                "gross_amount": gross,
                "units": 1 + (idx % 3),
                "is_returned": returned,
            }
        )
    fact_orders = pd.DataFrame(order_rows)

    mart_product = (
        fact_orders.groupby("category", as_index=False)
        .agg(
            total_gmv=("gross_amount", "sum"),
            order_count=("order_id", "count"),
            returned_orders=("is_returned", "sum"),
        )
        .assign(
            avg_order_value=lambda d: (d["total_gmv"] / d["order_count"]).round(2),
            return_rate_pct=lambda d: ((d["returned_orders"] / d["order_count"]) * 100).round(2),
        )
    )

    raw_events = fact_orders[["order_id", "event_date", "category", "gross_amount"]].head(25).copy()
    raw_events["ingested_at"] = "2026-10-31T08:00:00Z"
    raw_events["source_system"] = "shopify_webhook_v2"

    return {
        "fact_orders": fact_orders,
        "mart_product_performance": mart_product,
        "raw_order_events": raw_events,
    }


def _build_mock_insight(
    data_dict: dict[str, pd.DataFrame], is_en: bool = False
) -> OnDemandInsightResult:
    """Create a sample On-Demand Insight payload to demonstrate on_demand_panel.py."""
    mart_df = data_dict["mart_product_performance"]
    fig = px.bar(
        mart_df,
        x="category",
        y="total_gmv",
        color="return_rate_pct",
        title="GMV & Return Rate by Category (Mock Insight)",
        template="plotly_dark",
    )
    fig.update_layout(height=320, margin=dict(l=20, r=20, t=45, b=20))
    return OnDemandInsightResult(
        spec_id="SPEC-SHOWCASE-001",
        title=(
            "Category GMV & Return Rate Anomaly Diagnosis (Mock Insight)"
            if is_en
            else "品類 GMV 與退貨率異常診斷（Mock Insight）"
        ),
        executive_takeaway=(
            "Electronics drives the highest GMV but has a 6.7% return rate. Prioritize reviewing high-ticket SKU descriptions and packaging."
            if is_en
            else "Electronics 貢獻最高 GMV，但退貨率達 6.7%，建議優先檢視高單價 SKU 的商品規格描述與物流包裝。"
        ),
        figure=fig,
        executed_sql=(
            "SELECT category, SUM(gross_amount) AS total_gmv, COUNT(*) AS order_count\n"
            "FROM main_silver.fact_orders\n"
            "GROUP BY category\n"
            "ORDER BY total_gmv DESC;"
        ),
        source_table_label="main_silver.fact_orders → mart_product_performance",
        lineage_markdown="**Upstream:** `raw_order_events` → **Silver:** `fact_orders` → **Mart:** `mart_product_performance`",
        df_snippet=mart_df,
        governance_badges=["FinOps < 10MB", "Read-Only Verified", "Mock Showcase"],
    )


class ShowcaseRetailAdapter(BaseCockpitAdapter):
    """Mock Domain Adapter for showcasing all lakehouse-cockpit-core UI widgets."""

    @property
    def project_id(self) -> str:
        return "showcase-retail-lakehouse"

    @property
    def brand_title(self) -> str:
        return "Retail Lakehouse Cockpit (Showcase)"

    @property
    def brand_icon(self) -> str:
        return "🛒"

    @property
    def brand_badge_text(self) -> str:
        return "Mock Data · Component Showcase"

    @property
    def default_language(self) -> str:
        return "zh"

    @property
    def supports_looker(self) -> bool:
        return True

    @property
    def supports_schema_designer(self) -> bool:
        return True

    @property
    def supports_spec_guidance(self) -> bool:
        return True

    @property
    def dbt_manifest_path(self) -> Path:
        return Path(__file__).resolve().parent / "sample_manifest.json"

    @property
    def lookml_view_definition(self) -> str:
        return (
            "view: mart_product_performance {\n"
            "  sql_table_name: `main_silver.mart_product_performance` ;;\n\n"
            "  dimension: category {\n"
            "    type: string\n"
            "    sql: ${TABLE}.category ;;\n"
            "  }\n\n"
            "  measure: total_gmv {\n"
            "    type: sum\n"
            "    value_format_name: usd\n"
            "    sql: ${TABLE}.total_gmv ;;\n"
            "  }\n"
            "}"
        )

    @property
    def kpi_cards(self) -> list[KPICardSpec]:
        return [
            KPICardSpec(
                key="gmv",
                title_en="Gross Merchandise Value",
                title_zh="總成交金額 (GMV)",
                metric_formula="SUM(gross_amount)",
                target_rule="Target >= $150,000",
                accent_color="#10b981",
                calculate=lambda d: {
                    "value": f"${d['fact_orders']['gross_amount'].sum():,.0f}",
                    "delta": "+12.4% vs last period",
                    "status": "normal",
                },
            ),
            KPICardSpec(
                key="orders",
                title_en="Total Orders",
                title_zh="總訂單數",
                metric_formula="COUNT(order_id)",
                target_rule="Target >= 50",
                accent_color="#6366f1",
                calculate=lambda d: {
                    "value": f"{len(d['fact_orders']):,}",
                    "delta": "+8.1% MoM",
                    "status": "normal",
                },
            ),
            KPICardSpec(
                key="aov",
                title_en="Average Order Value",
                title_zh="平均客單價 (AOV)",
                metric_formula="total_gmv / order_count",
                target_rule="Target >= $2,500",
                accent_color="#3b82f6",
                formula_numerator=MetricComponent(
                    label="total_gmv",
                    role="Numerator",
                    description="Sum of gross order amounts",
                    description_zh="所有訂單的毛交易總額",
                ),
                formula_denominator=MetricComponent(
                    label="order_count",
                    role="Denominator",
                    description="Distinct count of completed orders",
                    description_zh="有效訂單總筆數",
                ),
                calculate=lambda d: {
                    "value": f"${d['fact_orders']['gross_amount'].mean():,.0f}"
                    if len(d["fact_orders"]) > 0
                    else "$0",
                    "delta": "+3.2%",
                    "status": "normal",
                },
            ),
            KPICardSpec(
                key="return_rate",
                title_en="Return Rate",
                title_zh="退貨率",
                metric_formula="returned_orders / order_count",
                target_rule="Target <= 5.0%",
                accent_color="#f43f5e",
                formula_numerator=MetricComponent(
                    label="returned_orders",
                    role="Numerator",
                    description="Orders flagged as returned",
                    description_zh="標記為退貨的訂單數",
                ),
                formula_denominator=MetricComponent(
                    label="order_count",
                    role="Denominator",
                    description="Total orders in period",
                    description_zh="期間內總訂單數",
                ),
                calculate=lambda d: self._calc_return_rate(d),
            ),
        ]

    @staticmethod
    def _calc_return_rate(d: dict[str, pd.DataFrame]) -> dict[str, Any]:
        df = d["fact_orders"]
        rate = (df["is_returned"].sum() / len(df) * 100) if len(df) > 0 else 0.0
        return {
            "value": f"{rate:.1f}%",
            "delta": "+0.8% (Alert)" if rate > 5.0 else "-0.3% vs target",
            "status": "warning" if rate > 5.0 else "normal",
        }

    def load_domain_data(
        self, start_date: date | None = None, end_date: date | None = None
    ) -> dict[str, pd.DataFrame]:
        datasets = _build_mock_datasets()
        if start_date or end_date:
            df = datasets["fact_orders"].copy()
            dt_series = pd.to_datetime(df["event_date"]).dt.date
            if start_date:
                df = df[dt_series >= start_date]
                dt_series = pd.to_datetime(df["event_date"]).dt.date
            if end_date:
                df = df[dt_series <= end_date]
            datasets["fact_orders"] = df
        return datasets

    @property
    def medallion_layers(self) -> dict[str, Any]:
        return {
            "silver": {
                "tabs": [
                    MedallionTabSpec(
                        tab_id="silver_trend",
                        title_en="📈 Revenue Trend (fact_orders)",
                        title_zh="📈 營收趨勢 (fact_orders)",
                        render_content=self._render_silver_trend,
                    ),
                    MedallionTabSpec(
                        tab_id="silver_mart",
                        title_en="🏷️ Category Matrix (mart_product_performance)",
                        title_zh="🏷️ 品類表現矩陣 (mart_product_performance)",
                        render_content=self._render_silver_mart,
                    ),
                ]
            },
            "bronze": {
                "tabs": [
                    MedallionTabSpec(
                        tab_id="bronze_raw",
                        title_en="🪵 Raw Order Events",
                        title_zh="🪵 原始訂單事件 (raw_order_events)",
                        render_content=self._render_bronze_raw,
                    )
                ]
            },
        }

    @staticmethod
    def _render_silver_trend(
        data_dict: dict[str, pd.DataFrame],
        is_en: bool,
        adapter: Any,
        start_date: date | None,
        end_date: date | None,
    ) -> None:
        df = data_dict["fact_orders"]
        daily = df.groupby("event_date", as_index=False)["gross_amount"].sum()
        fig = px.area(
            daily,
            x="event_date",
            y="gross_amount",
            title="Daily GMV Trend" if is_en else "每日 GMV 趨勢",
            template="plotly_dark",
        )
        fig.update_layout(height=300, margin=dict(l=20, r=20, t=40, b=20))
        st.plotly_chart(fig, use_container_width=True)

        render_sql_snippet_module(
            module_id="silver_daily_gmv",
            title_en="Compiled SQL: Daily GMV",
            title_zh="動態編譯 SQL：每日 GMV",
            source_table_label="main_silver.fact_orders",
            sql_template=(
                "SELECT event_date, SUM(gross_amount) AS daily_gmv\n"
                "FROM main_silver.fact_orders\n"
                "WHERE event_date BETWEEN '{start_str}' AND '{end_str}'\n"
                "GROUP BY event_date\n"
                "ORDER BY event_date ASC;"
            ),
            df_preview=daily.head(10),
            start_date=start_date,
            end_date=end_date,
            is_en=is_en,
        )

    @staticmethod
    def _render_silver_mart(
        data_dict: dict[str, pd.DataFrame],
        is_en: bool,
        adapter: Any,
        start_date: date | None,
        end_date: date | None,
    ) -> None:
        mart_df = data_dict["mart_product_performance"]
        st.dataframe(mart_df, use_container_width=True)

    @staticmethod
    def _render_bronze_raw(
        data_dict: dict[str, pd.DataFrame],
        is_en: bool,
        adapter: Any,
        start_date: date | None,
        end_date: date | None,
    ) -> None:
        raw_df = data_dict["raw_order_events"]
        render_sql_snippet_module(
            module_id="bronze_raw_events",
            title_en="Bronze Ingestion Log",
            title_zh="Bronze 原始攝取日誌",
            source_table_label="main_bronze.raw_order_events",
            sql_template=(
                "SELECT order_id, event_date, category, gross_amount, ingested_at\n"
                "FROM main_bronze.raw_order_events\n"
                "WHERE event_date BETWEEN '{start_str}' AND '{end_str}'\n"
                "LIMIT 25;"
            ),
            df_preview=raw_df,
            start_date=start_date,
            end_date=end_date,
            is_en=is_en,
        )

    @property
    def default_system_prompt_en(self) -> str:
        return "You are a Senior MBB Analytics Advisor for Retail E-Commerce. Answer with Pyramid Principle."

    @property
    def default_system_prompt_zh(self) -> str:
        return (
            "你是擁有麥肯錫 (McKinsey) / 貝恩 (Bain) / BCG 資深合夥人 (Senior Partner) 水準的頂級管理顧問，"
            "專精於現代零售電商與現代數據架構 (Lakehouse) 全盤商業診斷與營運獲利優化。\n\n"
            "【分析架構規範：全盤視野 + 當前焦點加權】：\n"
            "1. 全盤視野 (Global Lakehouse Visibility)：結合 GMV、AOV、退貨率與品類結構進行交叉診斷。\n"
            "2. 金字塔原理 (Pyramid Principle)：先給結論型標題 (Action Title)，再展開 MECE 分析與 30-60-90 天行動方案。"
        )

    @property
    def quick_diagnosis_questions_zh(self) -> list[str]:
        return [
            "哪個品類的退貨率最高？該如何改善？",
            "目前整體 GMV 與 AOV 的健康度如何？",
            "請給我 30-60-90 天的營收優化建議",
        ]

    def handle_spec_inquiry_turn(
        self,
        user_query: str,
        chat_history: list[dict[str, str]] | None = None,
        data_dict: dict[str, pd.DataFrame] | None = None,
        is_en: bool = True,
        api_key: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Offline Mock AI response when no Gemini API key is provided."""
        if is_en:
            reply = (
                "### 📌 Offline Mock Advisor Diagnosis (Showcase Mode)\n"
                f"- **Question Received**: {user_query}\n"
                "- **Action Title**: Electronics leads GMV but shows elevated return rates; prioritize SKU spec clarity and fulfillment QC.\n"
                "- **Next Step**: Enter a Gemini API Key above to enable live FastMCP tool execution."
            )
        else:
            reply = (
                "### 📌 離線 Mock 顧問診斷（Showcase Mode）\n"
                f"- **收到問題**：{user_query}\n"
                "- **核心結論（Action Title）**：Electronics 品類雖然貢獻最高 GMV，但退貨率偏高，建議優先優化商品規格說明與出貨檢核。\n"
                "- **下一步**：可在真實環境輸入 Gemini API Key 以啟用完整 FastMCP 工具呼叫。"
            )
        return {"reply": reply, "spec_contract": None}


def build_component_registry(
    adapter: ShowcaseRetailAdapter,
    data_dict: dict[str, pd.DataFrame],
    is_en: bool,
    start_date: date | None,
    end_date: date | None,
) -> list[ShowcaseComponentSpec]:
    """Single source of truth registry powering both Mode A and Mode B."""
    sample_insight = _build_mock_insight(data_dict, is_en=is_en)

    def _render_schema() -> None:
        render_schema_designer_view(adapter=adapter, data_dict=data_dict, is_en=is_en)

    return [
        ShowcaseComponentSpec(
            id="on_demand_panel",
            tab_title_en="🔍 On-Demand Insight",
            tab_title_zh="🔍 洞察面板 (On-Demand)",
            storybook_label="1. on_demand_panel.py",
            fn_signature="render_on_demand_insight_panel()",
            source_files=("components/on_demand_panel.py",),
            snippet="render_on_demand_insight_panel(insight=OnDemandInsightResult(...), is_en=is_en)",
            description_en="Stakeholder On-Demand Insight Panel with Action Title, chart, SQL, and lineage.",
            description_zh="Stakeholder 即時動態洞察面板（Action Title + 圖表 + SQL + 血緣）。",
            problem_solved_en=(
                "Prevents AI hallucinations in BI by pairing every generated chart with its exact "
                "executed SQL, upstream lineage, and FinOps verification badges in one auditable card."
            ),
            problem_solved_zh=(
                "解決傳統 AI 報表『只給圖表不給依據』的信任問題，把一句話結論、互動圖表、實際執行 SQL 與資料血緣綁在同一個可稽核面板中。"
            ),
            placement_in_mode_a="workspace_tab",
            render=lambda: render_on_demand_insight_panel(insight=sample_insight, is_en=is_en),
        ),
        ShowcaseComponentSpec(
            id="vanna_view",
            tab_title_en="📊 Vanna.ai Ad-Hoc Charts",
            tab_title_zh="📊 Vanna.ai 語意生圖",
            storybook_label="2. vanna_view.py",
            fn_signature="render_vanna_view()",
            source_files=("components/vanna_view.py",),
            snippet="render_vanna_view(adapter=adapter, data_dict=data_dict, is_en=is_en)",
            description_en="100% open-source (MIT) Vanna.ai RAG Text-to-SQL & Auto-Plotly charting trained on Lakehouse DDL and dbt manifest.json.",
            description_zh="100% 開源免費（MIT）的 Vanna.ai RAG 自然語言生圖工作台：自動讀取湖倉 DDL 與 dbt manifest.json 現場執行 DuckDB 並繪製 Plotly 圖表。",
            problem_solved_en=(
                "Replaces paid external GenBI iframes and brittle 1,400-line regex scripts with an "
                "in-process, MIT-open-source Vanna RAG Text-to-SQL & auto-Plotly charting workbench."
            ),
            problem_solved_zh=(
                "取代需付費的外部雲端 iframe 與上千行手寫生圖規則，直接以 MIT 開源免費的 Vanna RAG 架構在 Streamlit 內即時生成 SQL 與互動圖表。"
            ),
            placement_in_mode_a="workspace_tab",
            render=lambda: render_vanna_view(adapter=adapter, data_dict=data_dict, is_en=is_en),
        ),
        ShowcaseComponentSpec(
            id="sql_workbench",
            tab_title_en="🥈 SQL & Lineage Workbench",
            tab_title_zh="🥈 SQL 與血緣工作台",
            storybook_label="3. sql_workbench.py",
            fn_signature="render_sql_workbench()",
            source_files=("components/sql_workbench.py",),
            snippet="render_sql_workbench(adapter=adapter, data_dict=data_dict, start_date=start_date, end_date=end_date, is_en=is_en)",
            description_en="Unified Silver/Bronze dbt manifest explorer with live preview, compiled SQL, and read-only DuckDB sandbox.",
            description_zh="整合式 Silver / Bronze 動態模型工作台：解析 dbt manifest.json 上游血緣、並排預覽與編譯 SQL，內建唯讀 DuckDB 沙盒。",
            problem_solved_en=(
                "Eliminates code-to-viz desynchronization and manual SQL maintenance by reading dbt "
                "manifest.json directly and letting analysts verify numbers in a read-only DuckDB sandbox."
            ),
            problem_solved_zh=(
                "解決『前端寫死 SQL 假文件與後端真實管線脫鉤』的痛點，直接讀取 dbt manifest.json 並提供唯讀 DuckDB 沙盒現場驗算。"
            ),
            placement_in_mode_a="workspace_tab",
            render=lambda: render_sql_workbench(
                adapter=adapter,
                data_dict=data_dict,
                start_date=start_date,
                end_date=end_date,
                is_en=is_en,
                scope_key="unified",
            ),
        ),
        ShowcaseComponentSpec(
            id="looker_view",
            tab_title_en="📈 Looker BI",
            tab_title_zh="📈 Looker 視圖",
            storybook_label="4. looker_view.py",
            fn_signature="render_looker_enterprise_view()",
            source_files=("components/looker_view.py",),
            snippet="render_looker_enterprise_view(adapter=adapter, data_dict=data_dict, is_en=is_en)",
            description_en="Looker Enterprise BI embed and LookML semantic model viewer.",
            description_zh="Looker Enterprise BI 嵌入與 LookML 定義檢視器。",
            problem_solved_en=(
                "Bridges governed BI tools and custom data apps by embedding Looker Studio reports "
                "and exposing the underlying LookML Single Source of Truth side by side."
            ),
            problem_solved_zh=(
                "解決『企業正式 BI 報表與自建分析平台割裂』的問題，在同一個介面嵌入 Looker 報表並對照底層 LookML 單一事實來源。"
            ),
            placement_in_mode_a="workspace_tab",
            render=lambda: render_looker_enterprise_view(
                adapter=adapter, data_dict=data_dict, is_en=is_en
            ),
        ),
        ShowcaseComponentSpec(
            id="schema_designer",
            tab_title_en="🏗️ Schema Designer",
            tab_title_zh="🏗️ Schema 設計器",
            storybook_label="5. schema_designer.py",
            fn_signature="render_schema_designer_view()",
            source_files=("components/schema_designer.py",),
            snippet="render_schema_designer_view(adapter=adapter, data_dict=data_dict, is_en=is_en)",
            description_en="Autonomous Schema Designer synthesizing dbt SQL, enforced Data Contracts (schema.yml), and Semantic Layer (MetricFlow + LookML).",
            description_zh="自主 Schema 設計器，一次產出 dbt SQL、強制數據合約 (schema.yml) 與語意層 (MetricFlow + LookML)。",
            problem_solved_en=(
                "Closes the PRD-to-Engineering gap for Data PMs and Analytics Engineers by turning "
                "business goals into dry-run verified SQL, enforced Data Contracts, and Semantic Layer specs."
            ),
            problem_solved_zh=(
                "解決 PM 寫數據 PRD 時『口徑模糊、與工程實作斷層』的痛點，直接產出經過驗證的數據預覽、Data Contract 與 Semantic Layer 草案。"
            ),
            placement_in_mode_a="workspace_tab",
            render=_render_schema,
        ),
        ShowcaseComponentSpec(
            id="data_center",
            tab_title_en="🏛️ Data Center",
            tab_title_zh="🏛️ 資料中心",
            storybook_label="6. data_center.py",
            fn_signature="render_data_center_view()",
            source_files=("components/data_center.py",),
            snippet="render_data_center_view()",
            description_en="Elementary observability and dbt Docs lineage portal.",
            description_zh="Elementary 數據觀測與 dbt Docs 血緣文件入口。",
            problem_solved_en=(
                "Reuses best-in-class open-source Elementary observability and dbt Docs DAG views "
                "in a single zero-clutter portal instead of reinventing lineage tooling."
            ),
            problem_solved_zh=(
                "直接復用業界成熟的 Elementary 觀測與 dbt Docs 血緣報表，避免重複造輪子，提供單一無干擾的監控入口。"
            ),
            placement_in_mode_a="workspace_tab",
            render=render_data_center_view,
        ),
        ShowcaseComponentSpec(
            id="databot",
            tab_title_en="🤖 DataBot",
            tab_title_zh="🤖 資料庫問答 DataBot",
            storybook_label="7. databot.py",
            fn_signature="render_databot()",
            source_files=("components/databot.py",),
            snippet="render_databot(active_scope_id='showcase', ..., adapter=adapter, allow_mock_offline=True)",
            description_en="Lightweight database-aware chatbot drawer for quick text Q&A over Lakehouse tables without generating charts.",
            description_zh="輕量級資料庫問答 Chatbot 抽屜，適合快速查數字、問欄位與口徑解釋（不額外生圖），內建 BYOK 金鑰安全牆。",
            problem_solved_en=(
                "Gives stakeholders instant text answers directly from database tables when they only "
                "need a quick number or metric explanation—without opening a full BI or charting tool."
            ),
            problem_solved_zh=(
                "解決日常『只想快速問資料庫一個數字或口徑，不需要特別畫圖或開 BI』的輕量查詢痛點，並透過 BYOK 保障金鑰隔離。"
            ),
            placement_in_mode_a="right_drawer",
            render=lambda: render_databot(
                active_scope_id="showcase",
                active_scope_title="SHOWCASE",
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
                allow_mock_offline=True,
            ),
        ),
        ShowcaseComponentSpec(
            id="sidebar_ops",
            tab_title_en="⚡ Pipeline Ops & CSV",
            tab_title_zh="⚡ 管線控制 & CSV 下載",
            storybook_label="8. sidebar_ops.py",
            fn_signature="render_sidebar_pipeline_ops()",
            source_files=("components/sidebar_ops.py",),
            snippet="render_sidebar_pipeline_ops(adapter=adapter, data_dict=data_dict, start_date=start_date, end_date=end_date, is_en=is_en)",
            description_en="Sidebar Cloud Workflows DAG trigger, cache refresh, and UTF-8 CSV dataset exporter.",
            description_zh="側邊欄雲端資料管線控制 (Trigger Workflow) 與 BigQuery 金牌完整數據 CSV 下載器。",
            problem_solved_en=(
                "Empowers business operators to trigger cloud ELT refreshes and export audit-ready CSVs "
                "directly from the sidebar without asking Data Engineers to run scripts."
            ),
            problem_solved_zh=(
                "解決業務人員『想看最新數據或匯出明細卻得找工程師跑腳本』的痛點，在側邊欄一鍵觸發雲端排程同步並匯出 CSV。"
            ),
            placement_in_mode_a="left_sidebar",
            render=lambda: render_sidebar_pipeline_ops(
                adapter=adapter,
                data_dict=data_dict,
                start_date=start_date,
                end_date=end_date,
                is_en=is_en,
                scope_key="showcase",
            ),
        ),
    ]


def _render_integrated_cockpit_mode_a(
    adapter: ShowcaseRetailAdapter,
    registry: list[ShowcaseComponentSpec],
    is_en: bool,
) -> None:
    """Mode A: Consumes the unified `registry` to render left sidebar ops, workspace tabs, and right DataBot drawer."""
    sidebar_specs = [c for c in registry if c.placement_in_mode_a == "left_sidebar"]
    workspace_specs = [c for c in registry if c.placement_in_mode_a == "workspace_tab"]
    right_drawer = next(c for c in registry if c.placement_in_mode_a == "right_drawer")

    with st.sidebar:
        for sb_spec in sidebar_specs:
            st.markdown("---")
            sb_spec.render()

    main_col, ai_col = st.columns([7, 3])

    with main_col:
        st.title(adapter.brand_title)
        st.caption(
            "Live Lakehouse Intelligence · Enterprise Medallion Architecture · DataBot & Vanna.ai GenBI"
        )

        st.markdown("---")

        # Embedded Tabbed Feature Workspace dynamically built from unified registry
        feature_tabs = st.tabs(
            [spec.tab_title_en if is_en else spec.tab_title_zh for spec in workspace_specs]
        )
        for tab_ui, spec in zip(feature_tabs, workspace_specs):
            with tab_ui:
                spec.render()

    with ai_col:
        # 3. Right AI Drawer from unified registry
        right_drawer.render()


def _render_component_storybook_mode_b(
    registry: list[ShowcaseComponentSpec], is_en: bool
) -> None:
    """Mode B: Consumes the exact same `registry` list to render the Storybook catalog & live preview."""
    st.title(
        "🧩 Component Storybook — Individual Widget Explorer"
        if is_en
        else "🧩 Component Storybook — 元件逐一獨立展示"
    )
    st.caption(
        "Powered by the same `COMPONENT_REGISTRY` and sidebar state as Mode A, guaranteeing 100% parity."
        if is_en
        else "與模式 A 共用同一份 `COMPONENT_REGISTRY` 與側邊欄狀態，確保兩邊 100% 同步不脫鉤。"
    )

    left_col, right_col = st.columns([3, 7])

    with left_col:
        st.markdown(
            "#### 📦 `components/` Catalog"
            if is_en
            else "#### 📦 `components/` 目錄清單"
        )
        selected_id = st.radio(
            "Select widget to preview:" if is_en else "選擇要預覽的掛件：",
            options=[item.id for item in registry],
            format_func=lambda cid: next(i.storybook_label for i in registry if i.id == cid),
            label_visibility="collapsed",
            key="storybook_selected_component",
        )

    active = next(item for item in registry if item.id == selected_id)

    with right_col:
        h_left, h_right = st.columns([8, 2])
        with h_left:
            preview_prefix = "Component Preview" if is_en else "元件預覽"
            file_prefix = "Source" if is_en else "檔案"
            desc = active.description_en if is_en else active.description_zh
            st.markdown(f"### {preview_prefix}: `{active.fn_signature}`")
            files_str = " & ".join(active.source_files)
            st.caption(f"{file_prefix}: `{files_str}` · {desc}")
        with h_right:
            st.success("Standalone Render" if is_en else "獨立渲染狀態")

        # UX & Business Problem Solved Callout
        problem_title = (
            "💡 UX & Business Problem Solved"
            if is_en
            else "💡 解決的 UX 與商業痛點"
        )
        problem_body = active.problem_solved_en if is_en else active.problem_solved_zh
        st.info(f"**{problem_title}**: {problem_body}")

        expander_label = (
            "📋 Usage Example / Props"
            if is_en
            else "📋 呼叫範例 / Props 說明"
        )
        with st.expander(expander_label, expanded=False):
            st.code(active.snippet, language="python")

        st.markdown("#### Live Preview (Mock Data)")
        with st.container(border=True):
            # Calls the exact same render callable used in Mode A!
            active.render()


def main() -> None:
    try:
        st.set_page_config(
            page_title="Retail Lakehouse Cockpit (Showcase)",
            page_icon="🛒",
            layout="wide",
            initial_sidebar_state="expanded",
        )
    except Exception:
        pass

    st.markdown(MODERN_COCKPIT_CSS, unsafe_allow_html=True)
    adapter = ShowcaseRetailAdapter()

    # Resolve language first so even the Showcase Mode switcher respects English / Chinese strictly
    current_lang = st.session_state.get("shared_lang_selector", "Traditional Chinese")
    is_en = current_lang == "English"

    # Shared Sidebar Controls across Mode A and Mode B
    with st.sidebar:
        mode = st.radio(
            "🎛️ Showcase Mode" if is_en else "🎛️ 展示模式 (Showcase Mode)",
            options=["mode_a", "mode_b"],
            format_func=lambda m: (
                ("🏛️ Mode A: Integrated Cockpit" if m == "mode_a" else "🧩 Mode B: Component Storybook")
                if is_en
                else ("🏛️ 模式 A：完整整合儀表板" if m == "mode_a" else "🧩 模式 B：元件展示館 (Storybook)")
            ),
            key="showcase_mode_selector",
        )
        st.markdown("---")
        st.title(f"{adapter.brand_icon} {adapter.brand_title}")
        st.caption(f"⚡ {adapter.brand_badge_text}")

        lang_choice = st.radio(
            "Language" if is_en else "Language / 語言",
            options=["English", "Traditional Chinese"],
            index=0 if is_en else 1,
            horizontal=True,
            key="shared_lang_selector",
        )
        is_en = lang_choice == "English"

        st.markdown("---")
        st.subheader("📅 Date Range Filter")
        date_range = st.date_input("Filter Period:", value=[], key="shared_date_filter")
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

    data_dict = adapter.load_domain_data(start_date=start_date, end_date=end_date)
    registry = build_component_registry(
        adapter=adapter,
        data_dict=data_dict,
        is_en=is_en,
        start_date=start_date,
        end_date=end_date,
    )

    if mode == "mode_a":
        _render_integrated_cockpit_mode_a(adapter, registry, is_en=is_en)
    else:
        _render_component_storybook_mode_b(registry, is_en=is_en)


if __name__ == "__main__":
    main()
