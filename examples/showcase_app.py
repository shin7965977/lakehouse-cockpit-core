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
from components.ae_workflow import render_ae_workflow_stepper  # noqa: E402
from components.ai_drawer import render_ai_drawer  # noqa: E402
from components.data_center import render_data_center_view  # noqa: E402
from components.kpi_grid import render_kpi_grid  # noqa: E402
from components.looker_view import render_looker_enterprise_view  # noqa: E402
from components.on_demand_panel import render_on_demand_insight_panel  # noqa: E402
from components.sidebar_ops import render_sidebar_pipeline_ops  # noqa: E402
from components.sql_expander import (  # noqa: E402
    render_dynamic_dbt_explorer_with_sandbox,
    render_sql_snippet_module,
)
from components.text_to_chart import render_text_to_chart_module  # noqa: E402
from styles import MODERN_COCKPIT_CSS  # noqa: E402

try:
    from components.schema_designer import render_schema_designer_view  # noqa: E402
except ImportError:
    render_schema_designer_view = None  # Requires host project's `ai` package


@dataclass(frozen=True)
class ShowcaseComponentSpec:
    """Single source of truth for a component rendered in both Mode A and Mode B."""

    id: str
    tab_title: str
    storybook_label: str
    fn_signature: str
    source_files: tuple[str, ...]
    snippet: str
    description: str
    placement_in_mode_a: str  # "top_kpi" | "workspace_tab" | "right_drawer"
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


def _build_mock_insight(data_dict: dict[str, pd.DataFrame]) -> OnDemandInsightResult:
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
        title="品類 GMV 與退貨率異常診斷（Mock Insight）",
        executive_takeaway=(
            "Electronics 貢獻最高 GMV，但退貨率達 6.7%，建議優先檢視高單價 SKU 的商品規格描述與物流包裝。"
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
        return render_schema_designer_view is not None

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
    sample_insight = _build_mock_insight(data_dict)

    def _render_schema() -> None:
        if render_schema_designer_view is not None:
            render_schema_designer_view(adapter=adapter, data_dict=data_dict, is_en=is_en)
        else:
            st.info(
                "ℹ️ `schema_designer.py` 需要搭配宿主專案的 `ai` 模組（`ai.schema_engine` / `ai.socratic_copilot`）才能完整渲染。"
            )

    def _render_dc_and_ae() -> None:
        render_data_center_view()
        render_ae_workflow_stepper(adapter=adapter, data_dict=data_dict, is_en=is_en)

    return [
        ShowcaseComponentSpec(
            id="kpi_grid",
            tab_title="📊 KPI 網格",
            storybook_label="1. kpi_grid.py",
            fn_signature="render_kpi_grid()",
            source_files=("components/kpi_grid.py",),
            snippet="render_kpi_grid(cards=adapter.kpi_cards, data_dict=data_dict, is_en=is_en)",
            description="4 卡片高階 KPI 網格，支援健康度狀態標籤與公式 Hover Tooltip。",
            placement_in_mode_a="top_kpi",
            render=lambda: render_kpi_grid(cards=adapter.kpi_cards, data_dict=data_dict, is_en=is_en),
        ),
        ShowcaseComponentSpec(
            id="on_demand_panel",
            tab_title="🔍 洞察面板 (On-Demand)",
            storybook_label="2. on_demand_panel.py",
            fn_signature="render_on_demand_insight_panel()",
            source_files=("components/on_demand_panel.py",),
            snippet="render_on_demand_insight_panel(insight=OnDemandInsightResult(...), is_en=is_en)",
            description="Stakeholder 即時動態洞察面板（Action Title + 圖表 + SQL + 血緣）。",
            placement_in_mode_a="workspace_tab",
            render=lambda: render_on_demand_insight_panel(insight=sample_insight, is_en=is_en),
        ),
        ShowcaseComponentSpec(
            id="text_to_chart",
            tab_title="✨ AI 生圖 (Text-to-Chart)",
            storybook_label="3. text_to_chart.py",
            fn_signature="render_text_to_chart_module()",
            source_files=("components/text_to_chart.py",),
            snippet="render_text_to_chart_module(adapter=adapter, data_dict=data_dict, is_en=is_en)",
            description="自然語言 AI 智能圖表生成器，支援範例指令一鍵生圖、業務範疇防呆與底層數據預覽。",
            placement_in_mode_a="workspace_tab",
            render=lambda: render_text_to_chart_module(
                adapter=adapter, data_dict=data_dict, is_en=is_en
            ),
        ),
        ShowcaseComponentSpec(
            id="sql_expander",
            tab_title="🥈 Silver / Bronze 動態模型 & SQL 沙盒",
            storybook_label="4. sql_expander.py & sql_sandbox.py",
            fn_signature="render_dynamic_dbt_explorer_with_sandbox()",
            source_files=("components/sql_expander.py", "components/sql_sandbox.py"),
            snippet="render_dynamic_dbt_explorer_with_sandbox(adapter=adapter, data_dict=data_dict, start_date=start_date, end_date=end_date, is_en=is_en)",
            description="從 dbt manifest.json 動態解析 Silver / Bronze 模型、上游血緣、左右並排預覽與編譯後 SQL，並整合互動式 SQL 沙盒。",
            placement_in_mode_a="workspace_tab",
            render=lambda: render_dynamic_dbt_explorer_with_sandbox(
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
            tab_title="📈 Looker 視圖",
            storybook_label="5. looker_view.py",
            fn_signature="render_looker_enterprise_view()",
            source_files=("components/looker_view.py",),
            snippet="render_looker_enterprise_view(adapter=adapter, data_dict=data_dict, is_en=is_en)",
            description="Looker Enterprise BI 嵌入與 LookML 定義檢視器。",
            placement_in_mode_a="workspace_tab",
            render=lambda: render_looker_enterprise_view(
                adapter=adapter, data_dict=data_dict, is_en=is_en
            ),
        ),
        ShowcaseComponentSpec(
            id="schema_designer",
            tab_title="🏗️ Schema 設計器",
            storybook_label="6. schema_designer.py",
            fn_signature="render_schema_designer_view()",
            source_files=("components/schema_designer.py",),
            snippet="render_schema_designer_view(adapter=adapter, data_dict=data_dict, is_en=is_en)",
            description="自主 Schema 設計與 dbt 模型產生器（需搭配宿主專案的 ai 套件）。",
            placement_in_mode_a="workspace_tab",
            render=_render_schema,
        ),
        ShowcaseComponentSpec(
            id="data_center_ae",
            tab_title="🏛️ 資料中心 & AE 流程",
            storybook_label="7. data_center.py / ae_workflow.py",
            fn_signature="render_data_center_view() & render_ae_workflow_stepper()",
            source_files=("components/data_center.py", "components/ae_workflow.py"),
            snippet="render_data_center_view()\nrender_ae_workflow_stepper(adapter=adapter, data_dict=data_dict, is_en=is_en)",
            description="Elementary 觀測 / dbt Docs 入口與 6 步驟 AE 交付生命週期。",
            placement_in_mode_a="workspace_tab",
            render=_render_dc_and_ae,
        ),
        ShowcaseComponentSpec(
            id="ai_drawer",
            tab_title="🤖 AI 抽屜",
            storybook_label="8. ai_drawer.py",
            fn_signature="render_ai_drawer()",
            source_files=("components/ai_drawer.py",),
            snippet="render_ai_drawer(active_scope_id='showcase', ..., adapter=adapter, allow_mock_offline=True)",
            description="7:3 雙欄對話式 AI Copilot 抽屜，內建 System Prompt Playground、離線 Mock 診斷與快速問題。",
            placement_in_mode_a="right_drawer",
            render=lambda: render_ai_drawer(
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
            tab_title="⚡ 管線控制 & CSV 下載",
            storybook_label="9. sidebar_ops.py",
            fn_signature="render_sidebar_pipeline_ops()",
            source_files=("components/sidebar_ops.py",),
            snippet="render_sidebar_pipeline_ops(adapter=adapter, data_dict=data_dict, start_date=start_date, end_date=end_date, is_en=is_en)",
            description="側邊欄雲端資料管線控制 (Trigger Workflow) 與 BigQuery 金牌完整數據 CSV 下載器。",
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
) -> None:
    """Mode A: Consumes the unified `registry` to render left sidebar ops, top KPI, workspace tabs, and right AI drawer."""
    sidebar_specs = [c for c in registry if c.placement_in_mode_a == "left_sidebar"]
    top_kpi = next(c for c in registry if c.placement_in_mode_a == "top_kpi")
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
            "Live Lakehouse Intelligence · Enterprise Medallion Architecture · Conversational Analytics Copilot"
        )

        # 1. Top KPI Grid from unified registry
        top_kpi.render()

        st.markdown("---")

        # 2. Embedded Tabbed Feature Workspace dynamically built from unified registry
        feature_tabs = st.tabs([spec.tab_title for spec in workspace_specs])
        for tab_ui, spec in zip(feature_tabs, workspace_specs):
            with tab_ui:
                spec.render()

    with ai_col:
        # 3. Right AI Drawer from unified registry
        right_drawer.render()


def _render_component_storybook_mode_b(registry: list[ShowcaseComponentSpec]) -> None:
    """Mode B: Consumes the exact same `registry` list to render the Storybook catalog & live preview."""
    st.title("🧩 Component Storybook — 元件逐一獨立展示")
    st.caption("與模式 A 共用同一份 `COMPONENT_REGISTRY` 與側邊欄狀態，確保兩邊 100% 同步不脫鉤。")

    left_col, right_col = st.columns([3, 7])

    with left_col:
        st.markdown("#### 📦 `components/` 目錄清單")
        selected_id = st.radio(
            "選擇要預覽的掛件：",
            options=[item.id for item in registry],
            format_func=lambda cid: next(i.storybook_label for i in registry if i.id == cid),
            label_visibility="collapsed",
            key="storybook_selected_component",
        )

    active = next(item for item in registry if item.id == selected_id)

    with right_col:
        h_left, h_right = st.columns([8, 2])
        with h_left:
            st.markdown(f"### 元件預覽：`{active.fn_signature}`")
            files_str = " & ".join(active.source_files)
            st.caption(f"檔案：`{files_str}` · {active.description}")
        with h_right:
            st.success("獨立渲染狀態")

        with st.expander("📋 呼叫範例 / Props 說明", expanded=False):
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

    # Shared Sidebar Controls across Mode A and Mode B
    with st.sidebar:
        mode = st.radio(
            "🎛️ 展示模式 (Showcase Mode)",
            options=["mode_a", "mode_b"],
            format_func=lambda m: (
                "🏛️ 模式 A：完整整合儀表板" if m == "mode_a" else "🧩 模式 B：元件展示館 (Storybook)"
            ),
            key="showcase_mode_selector",
        )
        st.markdown("---")
        st.title(f"{adapter.brand_icon} {adapter.brand_title}")
        st.caption(f"⚡ {adapter.brand_badge_text}")

        lang_choice = st.radio(
            "Language / 語言",
            options=["English", "Traditional Chinese"],
            index=1,
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
        _render_integrated_cockpit_mode_a(adapter, registry)
    else:
        _render_component_storybook_mode_b(registry)


if __name__ == "__main__":
    main()
