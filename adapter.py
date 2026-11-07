"""Base Domain Adapter Contract for Lakehouse Cockpit Framework.

Decouples BI visual presentation, FastMCP AI Copilot, Socratic Spec Guidance,
and SQL contract testing from specific domain models (e.g. Retail E-Commerce, PLG SaaS).
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
class KPICardSpec:
    """Specification for a responsive top-level KPI card."""

    key: str
    title_en: str
    metric_formula: str
    target_rule: str
    # calculate takes the loaded data dictionary and returns a dict with:
    # {"value": str, "status": "normal" | "warning", "caption": str}
    calculate: Callable[[dict[str, pd.DataFrame]], dict[str, Any]]
    title_zh: str = ""
    accent_color: str = "#6366f1"


@dataclass
class ChartModuleSpec:
    """Specification for an interactive chart module with aligned SQL and snippet."""

    module_id: str
    title_en: str
    source_table_label: str
    # sql_template can contain placeholders: {start_str}, {end_str}, {project_id}
    sql_template: str
    render_chart: Callable[[dict[str, pd.DataFrame], bool], go.Figure | None]
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
    render_content: Callable[[dict[str, pd.DataFrame], bool, Any], None]
    title_zh: str = ""


@dataclass
class AnalyticalSpecContract:
    """Immutable specification contract locked after a 2-round Socratic dialogue."""

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
        return "en"

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

    @abstractmethod
    def get_fastmcp_tools(self) -> list[Callable]:
        """List of callable tool functions exposed to the Gemini Advisor."""
        ...

    @property
    @abstractmethod
    def default_system_prompt_en(self) -> str:
        """Default MBB consulting system prompt in English."""
        ...

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
    # Conversational Spec Guidance & On-Demand Insight Hooks (Optional)
    # =========================================================================

    @property
    def supports_spec_guidance(self) -> bool:
        """Whether this adapter supports 2-round Socratic Spec Guidance & On-Demand Insights."""
        return False

    def handle_spec_inquiry_turn(
        self,
        user_query: str,
        current_round: int,
        session_context: dict[str, Any] | None = None,
        is_en: bool = True,
    ) -> dict[str, Any]:
        """Process one turn in the 2-round Socratic Spec Guidance state machine.

        Returns a dict containing:
          - 'reply': assistant markdown message
          - 'next_round': int (1 for clarifying, 2 for locked spec)
          - 'spec_contract': AnalyticalSpecContract | None
          - 'scenario_key': str | None
        """
        return {
            "reply": "Spec guidance is not enabled for this domain adapter.",
            "next_round": 0,
            "spec_contract": None,
            "scenario_key": None,
        }

    def compile_spec_to_insight(
        self,
        spec_contract: AnalyticalSpecContract | None,
        data_dict: dict[str, pd.DataFrame],
        is_en: bool = True,
    ) -> OnDemandInsightResult | None:
        """Compile a confirmed AnalyticalSpecContract into an OnDemandInsightResult."""
        return None
