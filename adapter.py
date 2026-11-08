"""Base Domain Adapter Contract for Lakehouse Cockpit Framework.

Decouples BI visual presentation, Interactive SQL Sandbox, Looker Enterprise Views,
Socratic Spec Guidance, and SQL contract testing from specific domain models.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
import plotly.graph_objects as go


@dataclass
class MetricComponent:
    """Structure for a formula term (numerator or denominator) with hover tooltip (excluding Unit)."""

    label: str  # Display text in formula tag (e.g. "returned_units")
    role: str  # e.g. "Numerator" or "Denominator"
    description: str  # Plain English business explanation shown on hover
    description_zh: str = ""


@dataclass
class KPICardSpec:
    """Specification for a responsive top-level KPI card."""

    key: str
    title_en: str
    metric_formula: str
    target_rule: str
    calculate: Callable[[dict[str, pd.DataFrame]], dict[str, Any]]
    title_zh: str = ""
    accent_color: str = "#6366f1"
    formula_numerator: MetricComponent | None = None
    formula_denominator: MetricComponent | None = None


@dataclass
class ChartModuleSpec:
    """Specification for an interactive chart module with aligned SQL and snippet."""

    module_id: str
    title_en: str
    source_table_label: str
    sql_template: str
    render_chart: Callable[[dict[str, pd.DataFrame], bool, date | None, date | None], go.Figure | None]
    get_table_snippet: Callable[
        [dict[str, pd.DataFrame], date | None, date | None], pd.DataFrame
    ]
    title_zh: str = ""
    description_zh: str = ""
    description_en: str = ""


@dataclass
class MedallionTabSpec:
    """Specification for a tab within Gold/Silver/Bronze layers."""

    tab_id: str
    title_en: str
    render_content: Callable[[dict[str, pd.DataFrame], bool, Any, date | None, date | None], None]
    title_zh: str = ""


@dataclass
class AnalyticalSpecContract:
    """Immutable specification contract locked after an interactive Socratic dialogue."""

    spec_id: str
    scenario_key: str
    business_question: str
    target_layer: str
    target_mart: str
    metrics: list[dict[str, str]]
    dimensions: list[str]
    filters: dict[str, Any]
    chart_recommendation: str
    governance_checks: dict[str, Any] = field(default_factory=dict)


@dataclass
class OnDemandInsightResult:
    """Compiled insight payload rendered in the On-Demand Insight Panel with 3-Layer Inspector."""

    spec_id: str
    title: str
    executive_takeaway: str
    figure: go.Figure | None
    executed_sql: str
    source_table_label: str
    lineage_markdown: str
    df_snippet: pd.DataFrame
    governance_badges: list[str] = field(default_factory=list)


class BaseCockpitAdapter(ABC):
    """Abstract Base Class for Domain Adapters.

    Implement this interface to skin the Lakehouse Cockpit for any business domain.
    """

    @property
    @abstractmethod
    def project_id(self) -> str:
        """BigQuery project ID or local warehouse name."""
        ...

    @property
    @abstractmethod
    def brand_title(self) -> str:
        """Display title in browser tab and navbar."""
        ...

    @property
    @abstractmethod
    def brand_icon(self) -> str:
        """Favicon emoji or image path."""
        ...

    @property
    @abstractmethod
    def brand_badge_text(self) -> str:
        """Badge text displayed in header (e.g. 'Enterprise Lakehouse')."""
        ...

    @property
    def default_language(self) -> str:
        """Default language: 'en' or 'zh'."""
        return "zh"

    @property
    @abstractmethod
    def kpi_cards(self) -> list[KPICardSpec]:
        """List of 4 executive KPI cards."""
        ...

    @property
    def medallion_layers(self) -> dict[str, Any]:
        """Dictionary mapping 'gold', 'silver', 'bronze' to layer specs."""
        return {"gold": {"tabs": []}, "silver": {"tabs": []}, "bronze": {"tabs": []}}

    @abstractmethod
    def load_domain_data(
        self, start_date: date | None = None, end_date: date | None = None
    ) -> dict[str, pd.DataFrame]:
        """Fetch all Gold, Silver, and Bronze datasets needed by the dashboard."""
        ...

    @property
    def default_system_prompt_en(self) -> str:
        """Default MBB consulting system prompt in English."""
        return "You are an analytics engineering advisor."

    @property
    def default_system_prompt_zh(self) -> str:
        """Optional localized system prompt."""
        return self.default_system_prompt_en

    @property
    def quick_diagnosis_questions_en(self) -> list[str]:
        """Suggested quick questions in English."""
        return [
            "Executive Profitability Review",
            "Customer Cohort Diagnostics",
            "Root-Cause Anomaly Detection",
        ]

    @property
    def quick_diagnosis_questions_zh(self) -> list[str]:
        """Suggested localized quick questions (defaults to English)."""
        return self.quick_diagnosis_questions_en

    # =========================================================================
    # Interactive SQL Sandbox Hook
    # =========================================================================

    def execute_custom_sql(
        self,
        sql_query: str,
        data_dict: dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """Execute a user-entered SQL query against domain data tables (DuckDB with SQLite fallback)."""
        import re

        # Enforce read-only FinOps safety check
        forbidden = re.search(r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE)\b", sql_query, re.IGNORECASE)
        if forbidden:
            raise ValueError(f"Security Alert: Destructive command '{forbidden.group(0)}' is prohibited in Read-Only Sandbox.")

        # 1. Normalize dbt Jinja macros: strip {{ config(...) }} and resolve {{ ref('table') }} -> table
        cleaned_sql = re.sub(r"\{\{\s*config\([^)]*?\)\s*\}\}", "", sql_query, flags=re.DOTALL)
        cleaned_sql = re.sub(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}", r"\1", cleaned_sql)
        cleaned_sql = re.sub(r"\{%.*?%\}", "", cleaned_sql, flags=re.DOTALL)

        # 2. Remove backticks and normalize schema qualifications for in-memory execution
        cleaned_sql = re.sub(r"`[^`]*\b([a-zA-Z0-9_]+)`", r"\1", cleaned_sql)
        cleaned_sql = re.sub(r"\b[a-zA-Z0-9_-]+\.[a-zA-Z0-9_]+\.([a-zA-Z0-9_]+)\b", r"\1", cleaned_sql)
        cleaned_sql = re.sub(r"\b(?:main_silver|main_bronze|main_gold)\.([a-zA-Z0-9_]+)\b", r"\1", cleaned_sql, flags=re.IGNORECASE)

        try:
            import duckdb
            con = duckdb.connect(database=":memory:")
            con.execute("CREATE SCHEMA IF NOT EXISTS main_silver;")
            con.execute("CREATE SCHEMA IF NOT EXISTS main_bronze;")
            con.execute("CREATE SCHEMA IF NOT EXISTS main_gold;")
            for tbl_name, df_val in data_dict.items():
                if isinstance(df_val, pd.DataFrame):
                    clean_name = tbl_name.split(".")[-1]
                    con.register(clean_name, df_val)
                    con.register(f"main_silver.{clean_name}", df_val)
                    con.register(f"main_bronze.{clean_name}", df_val)
                    con.register(f"main_gold.{clean_name}", df_val)
            return con.execute(cleaned_sql).df()
        except ImportError:
            import sqlite3
            con_sqlite = sqlite3.connect(":memory:")
            for tbl_name, df_val in data_dict.items():
                if isinstance(df_val, pd.DataFrame):
                    clean_name = tbl_name.split(".")[-1]
                    df_val.to_sql(clean_name, con_sqlite, if_exists="replace", index=False)
            return pd.read_sql_query(cleaned_sql, con_sqlite)

    @property
    def default_sandbox_sql(self) -> str:
        """Default query prefilled in the SQL Verification Sandbox."""
        return "SELECT * FROM mart_product_performance LIMIT 10;"

    # =========================================================================
    # Looker Enterprise BI Hooks (Optional / Native)
    # =========================================================================

    @property
    def supports_looker(self) -> bool:
        """Whether this adapter provides Looker / LookML enterprise integration."""
        return True

    @property
    def looker_embed_url(self) -> str:
        """Default Looker / Looker Studio embed URL if configured."""
        return ""

    @property
    def lookml_view_definition(self) -> str:
        """LookML code definition string for the primary Gold Mart."""
        return ""

    # =========================================================================
    # Socratic Spec Guidance & On-Demand Insight Hooks
    # =========================================================================

    @property
    def supports_spec_guidance(self) -> bool:
        return False

    @property
    def supports_schema_designer(self) -> bool:
        """Whether this adapter supports the Autonomous Schema Designer & dbt Engine."""
        return True

    def handle_spec_inquiry_turn(
        self,
        user_query: str,
        chat_history: list[dict[str, str]] | None = None,
        data_dict: dict[str, pd.DataFrame] | None = None,
        is_en: bool = True,
        api_key: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Process one conversational turn in the semantic guidance flow."""
        return {
            "reply": "Spec guidance is not configured.",
            "spec_contract": None,
        }

    def compile_spec_to_insight(
        self,
        spec_contract: AnalyticalSpecContract | None,
        data_dict: dict[str, pd.DataFrame],
        start_date: date | None = None,
        end_date: date | None = None,
        is_en: bool = True,
    ) -> OnDemandInsightResult | None:
        """Compile a confirmed AnalyticalSpecContract into an OnDemandInsightResult."""
        return None
