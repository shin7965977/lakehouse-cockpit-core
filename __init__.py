"""Lakehouse Cockpit Framework - Microkernel Architecture for BI & AI Operations."""

try:
    from .adapter import (
        AnalyticalSpecContract,
        BaseCockpitAdapter,
        ChartModuleSpec,
        KPICardSpec,
        MedallionTabSpec,
        MetricComponent,
        OnDemandInsightResult,
    )
    from .engine import launch_cockpit_app
except ImportError:
    from adapter import (
        AnalyticalSpecContract,
        BaseCockpitAdapter,
        ChartModuleSpec,
        KPICardSpec,
        MedallionTabSpec,
        MetricComponent,
        OnDemandInsightResult,
    )
    from engine import launch_cockpit_app

__all__ = [
    "AnalyticalSpecContract",
    "BaseCockpitAdapter",
    "ChartModuleSpec",
    "KPICardSpec",
    "MedallionTabSpec",
    "MetricComponent",
    "OnDemandInsightResult",
    "launch_cockpit_app",
]
