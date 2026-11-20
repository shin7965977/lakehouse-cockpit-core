"""Shared backend services for Lakehouse Cockpit Core."""

try:
    from .duckdb_executor import (
        DatabaseReflectionService,
        detect_pii_columns,
        execute_readonly_duckdb,
        validate_readonly_sql,
    )
except (ImportError, ValueError):
    try:
        from services.duckdb_executor import (
            DatabaseReflectionService,
            detect_pii_columns,
            execute_readonly_duckdb,
            validate_readonly_sql,
        )
    except (ImportError, ValueError):
        from cockpit_core.services.duckdb_executor import (
            DatabaseReflectionService,
            detect_pii_columns,
            execute_readonly_duckdb,
            validate_readonly_sql,
        )

__all__ = [
    "DatabaseReflectionService",
    "detect_pii_columns",
    "execute_readonly_duckdb",
    "validate_readonly_sql",
]
