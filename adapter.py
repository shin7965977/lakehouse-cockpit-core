"""Base Domain Adapter Contract for Lakehouse Cockpit Framework.

Decouples BI visual presentation, FastMCP AI Copilot, and SQL contract testing
from specific domain models (e.g. Platzi E-Commerce, ReadCall PLG SaaS).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
import plotly.graph_objects as go


@dataclass
class KPICardSpec:
    """Specification for a responsive top-level KPI card."""

    key: str
    title_zh: str
    title_en: str
    metric_formula: str
    target_rule: str
    # calculate takes the loaded data dictionary and returns a dict with:
    # {"value": str, "status": "normal" | "warning", "caption": str}
    calculate: Callable[[dict[str, pd.DataFrame]], dict[str, Any]]
    accent_color: str = "#6366f1"


@dataclass
class ChartModuleSpec:
    """Specification for an interactive chart module with aligned SQL and snippet."""

    module_id: str
    title_zh: str
    title_en: str
    source_table_label: str
    # sql_template can contain placeholders: {start_str}, {end_str}, {project_id}
    sql_template: str
    render_chart: Callable[[dict[str, pd.DataFrame], bool], go.Figure | None]
    get_table_snippet: Callable[
        [dict[str, pd.DataFrame], date | None, date | None], pd.DataFrame
    ]
    description_zh: str = ""
    description_en: str = ""


@dataclass
class MedallionTabSpec:
    """Specification for a tab within Gold/Silver/Bronze layers."""

    tab_id: str
    title_zh: str
    title_en: str
    render_content: Callable[[dict[str, pd.DataFrame], bool, Any], None]


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
    @abstractmethod
    def default_language(self) -> str:
        """Default language: 'zh' or 'en'."""
        ...

    @property
    @abstractmethod
    def kpi_cards(self) -> list[KPICardSpec]:
        """List of 4 executive KPI cards."""
        ...

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
    def default_system_prompt_zh(self) -> str:
        """Default MBB consulting system prompt in Traditional Chinese."""
        ...

    @property
    @abstractmethod
    def default_system_prompt_en(self) -> str:
        """Default MBB consulting system prompt in English."""
        ...

    @property
    def quick_diagnosis_questions_zh(self) -> list[str]:
        """Suggested quick questions in Chinese."""
        return ["營收與利潤全盤診斷", "熱銷商品與客群洞察", "異常指標與流失歸因"]

    @property
    def quick_diagnosis_questions_en(self) -> list[str]:
        """Suggested quick questions in English."""
        return [
            "Executive Profitability Review",
            "Customer Cohort Diagnostics",
            "Root-Cause Anomaly Detection",
        ]
