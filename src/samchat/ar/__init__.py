"""Read-only accounts receivable projections."""

from .admin_ui import (
    ar_admin_styles,
    render_ar_item_detail_html,
    render_ar_matching_workbench_html,
    render_ar_read_model_html,
)
from .coi_exporter import (
    build_ar_coi_ready_policy_rows,
    generate_ar_coi_ready_xlsx,
)
from .collection_matches import (
    accept_ar_collection_match,
    list_ar_collection_matches,
    reverse_ar_collection_match,
)
from .matching import build_ar_matching_workbench
from .service import (
    build_ar_accounting_preview,
    build_ar_actionable_gaps,
    build_ar_operational_rows,
    build_ar_read_model,
    find_ar_operational_item,
)

__all__ = [
    "accept_ar_collection_match",
    "ar_admin_styles",
    "build_ar_accounting_preview",
    "build_ar_actionable_gaps",
    "build_ar_coi_ready_policy_rows",
    "build_ar_matching_workbench",
    "build_ar_operational_rows",
    "build_ar_read_model",
    "find_ar_operational_item",
    "generate_ar_coi_ready_xlsx",
    "render_ar_item_detail_html",
    "list_ar_collection_matches",
    "render_ar_matching_workbench_html",
    "render_ar_read_model_html",
    "reverse_ar_collection_match",
]
