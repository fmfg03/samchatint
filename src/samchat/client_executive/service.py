"""Client-safe executive read model.

This module is deliberately limited to tournament-scoped budget aggregates.
It must not call global cash-flow, CxC, or payment read models because those
surfaces currently have no equivalent client/tournament scope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from samchat.budgets.service import build_budget_snapshot


class ClientExecutiveAccessError(PermissionError):
    """Raised when a client asks for a tournament outside its portfolio."""


async def ensure_client_executive_schema(session: Any) -> None:
    """Create the minimal, auditable portfolio-to-user authorization schema."""
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS client_executive_portfolios (
                id UUID PRIMARY KEY,
                label VARCHAR(200) NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS client_executive_access_audit_logs (
                id UUID PRIMARY KEY,
                actor_empleado_id UUID NULL REFERENCES empleados(id),
                portfolio_id UUID NULL REFERENCES client_executive_portfolios(id),
                event_type VARCHAR(80) NOT NULL,
                detail JSONB NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS client_executive_portfolio_members (
                portfolio_id UUID NOT NULL REFERENCES client_executive_portfolios(id),
                empleado_id UUID NOT NULL REFERENCES empleados(id),
                active BOOLEAN NOT NULL DEFAULT TRUE,
                PRIMARY KEY (portfolio_id, empleado_id)
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS client_executive_portfolio_tournaments (
                portfolio_id UUID NOT NULL REFERENCES client_executive_portfolios(id),
                tournament_id UUID NOT NULL REFERENCES tournaments(id),
                active BOOLEAN NOT NULL DEFAULT TRUE,
                PRIMARY KEY (portfolio_id, tournament_id)
            )
            """
        )
    )
    await session.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_client_executive_members_empleado
            ON client_executive_portfolio_members(empleado_id)
            """
        )
    )


async def _authorized_tournaments(session: Any, empleado_id: str) -> list[dict[str, str]]:
    await ensure_client_executive_schema(session)
    result = await session.execute(
        text(
            """
            SELECT DISTINCT t.id::text AS id, t.name
            FROM client_executive_portfolio_members member
            JOIN client_executive_portfolios portfolio
              ON portfolio.id = member.portfolio_id AND portfolio.active = TRUE
            JOIN client_executive_portfolio_tournaments assignment
              ON assignment.portfolio_id = portfolio.id AND assignment.active = TRUE
            JOIN tournaments t ON t.id = assignment.tournament_id
            WHERE member.empleado_id = :empleado_id AND member.active = TRUE
            ORDER BY t.name ASC
            """
        ),
        {"empleado_id": str(empleado_id)},
    )
    return [{"id": str(row.id), "name": str(row.name)} for row in result]


def _executive_card(tournament: dict[str, str], snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    comparison = snapshot.get("comparison") if isinstance(snapshot.get("comparison"), dict) else {}
    forecast = snapshot.get("forecast") if isinstance(snapshot.get("forecast"), dict) else {}
    alerts = snapshot.get("executive_alerts") if isinstance(snapshot.get("executive_alerts"), list) else []
    return {
        "tournament_id": tournament["id"],
        "tournament_name": tournament["name"],
        "budget": float(summary.get("budget_total") or 0),
        "actual": float(comparison.get("actual_total") or comparison.get("paid_total") or 0),
        "committed": float(comparison.get("committed_total") or 0),
        "projected": float(forecast.get("projected_total") or 0),
        "alerts": [
            {"severity": str(item.get("severity") or "info"), "title": str(item.get("title") or "Alerta")}
            for item in alerts[:3]
            if isinstance(item, dict)
        ],
        "source": "samchat.budgets.service.build_budget_snapshot",
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def build_client_executive_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a deterministic, read-only Sam summary from safe dashboard data."""
    cards = payload.get("cards") if isinstance(payload.get("cards"), list) else []
    high_alerts = sum(
        1
        for card in cards
        for alert in (card.get("alerts") or [])
        if isinstance(alert, dict) and alert.get("severity") == "high"
    )
    return {
        "scope": payload.get("scope"),
        "message": (
            "No hay proyectos o torneos asignados a esta cartera."
            if not cards
            else "Cartera con {} proyecto(s)/torneo(s) y {} alerta(s) alta(s)."
            .format(len(cards), high_alerts)
        ),
        "high_alert_count": high_alerts,
        "source": "client_executive.read_model",
        "read_only": True,
    }


async def build_client_dashboard(
    session: Any,
    *,
    empleado_id: str,
    edition_year: int,
    tournament_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build a client portfolio or one authorized tournament CEO view."""
    tournaments = await _authorized_tournaments(session, empleado_id)
    if tournament_id:
        tournaments = [item for item in tournaments if item["id"] == str(tournament_id)]
        if not tournaments:
            raise ClientExecutiveAccessError("Tournament is not assigned to this client.")

    cards = []
    for tournament in tournaments:
        snapshot = await build_budget_snapshot(
            session,
            tournament_id=tournament["id"],
            edition_year=edition_year,
        )
        cards.append(_executive_card(tournament, snapshot))

    return {
        "edition_year": edition_year,
        "scope": "tournament" if tournament_id else "portfolio",
        "cards": cards,
        "unavailable_metrics": [
            "cashflow", "accounts_receivable", "payments", "operational_detail"
        ],
    }
