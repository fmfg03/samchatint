"""Read-only accounts receivable projections."""

from .admin_ui import (
    ar_admin_styles,
    render_ar_matching_workbench_html,
    render_ar_read_model_html,
)
from .collection_matches import (
    accept_ar_collection_match,
    list_ar_collection_matches,
    reverse_ar_collection_match,
)
from .matching import build_ar_matching_workbench
from .service import build_ar_operational_rows, build_ar_read_model

__all__ = [
    "accept_ar_collection_match",
    "ar_admin_styles",
    "build_ar_matching_workbench",
    "build_ar_operational_rows",
    "build_ar_read_model",
    "list_ar_collection_matches",
    "render_ar_matching_workbench_html",
    "render_ar_read_model_html",
    "reverse_ar_collection_match",
]
