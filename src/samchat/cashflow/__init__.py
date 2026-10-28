"""Canonical cashflow read model and consistent HTML/XLSX views."""

from .admin_ui import (
    cashflow_admin_styles,
    export_cashflow_xlsx,
    render_cashflow_filters,
    render_cashflow_planning_html,
)
from .service import (
    build_cashflow_planning_read_model,
    build_cashflow_report,
    build_cashflow_request_payload,
)

__all__ = [
    "build_cashflow_planning_read_model",
    "build_cashflow_report",
    "build_cashflow_request_payload",
    "cashflow_admin_styles",
    "render_cashflow_planning_html",
    "render_cashflow_filters",
    "export_cashflow_xlsx",
]
