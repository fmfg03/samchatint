"""Read-only executive dashboards for externally scoped client portfolios."""

from .service import ClientExecutiveAccessError, build_client_dashboard

__all__ = ["ClientExecutiveAccessError", "build_client_dashboard"]
