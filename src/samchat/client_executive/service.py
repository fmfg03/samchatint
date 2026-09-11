"""Internal direction executive read model.

This module is deliberately limited to tournament-scoped budget aggregates.
It must not call global cash-flow, CxC, or payment read models because those
surfaces currently have no equivalent direction/tournament scope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from samchat.budgets.service import build_budget_snapshot


class ClientExecutiveAccessError(PermissionError):
    """Raised when Direction asks for a tournament outside its assigned scope."""


DIRECTION_POSITION_KEYS = frozenset(
    {
        "direccion_general",
        "direccion_administracion_finanzas",
        "direccion_goat",
        "director_operaciones",
    }
)


async def ensure_client_executive_schema(session: Any) -> None:
    """Provision portfolio configuration outside client read requests."""
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
            CREATE TABLE IF NOT EXISTS client_executive_portfolio_positions (
                portfolio_id UUID NOT NULL REFERENCES client_executive_portfolios(id),
                position_key VARCHAR(100) NOT NULL REFERENCES authorization_positions(position_key),
                active BOOLEAN NOT NULL DEFAULT TRUE,
                PRIMARY KEY (portfolio_id, position_key)
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
            CREATE INDEX IF NOT EXISTS ix_client_executive_portfolio_positions_key
            ON client_executive_portfolio_positions(position_key)
            """
        )
    )


async def _authorized_tournaments(
    session: Any, empleado_id: str, *, is_superadmin: bool = False
) -> list[dict[str, str]]:
    """Return tournaments assigned to the caller's configured position.

    This function is deliberately query-only: schema installation belongs to the
    schema guard or the explicit provisioning command.
    """
    if is_superadmin:
        result = await session.execute(
            text(
                "SELECT id::text AS id, name, slug FROM tournaments "
                "WHERE active = TRUE ORDER BY name ASC"
            )
        )
        return [
            {"id": str(row.id), "name": str(row.name), "slug": str(row.slug or "")}
            for row in result
        ]
    result = await session.execute(
        text(
            """
            SELECT DISTINCT t.id::text AS id, t.name, t.slug
            FROM authorization_position_assignments holder
            JOIN client_executive_portfolio_positions position
              ON position.position_key = holder.position_key AND position.active = TRUE
            JOIN client_executive_portfolios portfolio
              ON portfolio.id = position.portfolio_id AND portfolio.active = TRUE
            JOIN client_executive_portfolio_tournaments assignment
              ON assignment.portfolio_id = portfolio.id AND assignment.active = TRUE
            JOIN tournaments t ON t.id = assignment.tournament_id AND t.active = TRUE
            WHERE holder.empleado_id = :empleado_id
              AND holder.active = TRUE
              AND holder.position_key = ANY(:position_keys)
            ORDER BY t.name ASC
            """
        ),
        {
            "empleado_id": str(empleado_id),
            "position_keys": sorted(DIRECTION_POSITION_KEYS),
        },
    )
    return [
        {"id": str(row.id), "name": str(row.name), "slug": str(row.slug or "")}
        for row in result
    ]


async def authorized_direction_portfolio_ids(
    session: Any, empleado_id: str, *, is_superadmin: bool = False
) -> list[str]:
    """Return active Direction portfolios visible to an internal identity."""
    if is_superadmin:
        result = await session.execute(
            text(
                "SELECT id::text AS id FROM client_executive_portfolios WHERE active = TRUE"
            )
        )
    else:
        result = await session.execute(
            text(
                """
                SELECT DISTINCT portfolio.id::text AS id
                FROM authorization_position_assignments holder
                JOIN client_executive_portfolio_positions position
                  ON position.position_key = holder.position_key AND position.active = TRUE
                JOIN client_executive_portfolios portfolio
                  ON portfolio.id = position.portfolio_id AND portfolio.active = TRUE
                WHERE holder.empleado_id = :empleado_id
                  AND holder.active = TRUE
                  AND holder.position_key = ANY(:position_keys)
                ORDER BY portfolio.id
                """
            ),
            {
                "empleado_id": str(empleado_id),
                "position_keys": sorted(DIRECTION_POSITION_KEYS),
            },
        )
    return [str(row.id) for row in result]


def _executive_card(
    tournament: dict[str, str], snapshot: dict[str, Any]
) -> dict[str, Any]:
    summary = (
        snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    )
    comparison = (
        snapshot.get("comparison")
        if isinstance(snapshot.get("comparison"), dict)
        else {}
    )
    forecast = (
        snapshot.get("forecast") if isinstance(snapshot.get("forecast"), dict) else {}
    )
    alerts = (
        snapshot.get("executive_alerts")
        if isinstance(snapshot.get("executive_alerts"), list)
        else []
    )
    return {
        "tournament_id": tournament["id"],
        "tournament_name": tournament["name"],
        "budget": float(summary.get("budget_total") or 0),
        "actual": float(
            comparison.get("actual_total") or comparison.get("paid_total") or 0
        ),
        "committed": float(comparison.get("committed_total") or 0),
        "projected": float(forecast.get("projected_total") or 0),
        "alerts": [
            {
                "severity": str(item.get("severity") or "info"),
                "title": str(item.get("title") or "Alerta"),
            }
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
            "No hay proyectos o torneos en tu alcance asignado."
            if not cards
            else "Alcance con {} proyecto(s)/torneo(s) y {} alerta(s) alta(s).".format(
                len(cards), high_alerts
            )
        ),
        "high_alert_count": high_alerts,
        "source": "direction_executive.read_model",
        "read_only": True,
    }


async def build_client_dashboard(
    session: Any,
    *,
    empleado_id: str,
    edition_year: int,
    tournament_id: Optional[str] = None,
    is_superadmin: bool = False,
) -> dict[str, Any]:
    """Build a Direction portfolio or one authorized tournament executive view."""
    tournaments = await _authorized_tournaments(
        session, empleado_id, is_superadmin=is_superadmin
    )
    if not tournaments:
        raise ClientExecutiveAccessError("No active portfolio position is assigned.")
    if tournament_id:
        tournaments = [item for item in tournaments if item["id"] == str(tournament_id)]
        if not tournaments:
            raise ClientExecutiveAccessError(
                "Tournament is not in the assigned Direction scope."
            )

    cards = []
    for tournament in tournaments:
        if not (tournament.get("name") or tournament.get("slug")):
            # A blank selector would make the legacy artifact fallback global.
            # Do not request a snapshot unless its tournament scope is verifiable.
            continue
        snapshot = await build_budget_snapshot(
            session,
            tournament_id=tournament["id"],
            tournament_name=tournament.get("name"),
            tournament_slug=tournament.get("slug"),
            edition_year=edition_year,
            ensure_schema=False,
            strict_tournament_scope=True,
        )
        cards.append(_executive_card(tournament, snapshot))

    return {
        "edition_year": edition_year,
        "scope": "tournament" if tournament_id else "portfolio",
        "cards": cards,
        "unavailable_metrics": [
            "cashflow",
            "accounts_receivable",
            "payments",
            "operational_detail",
        ],
    }


async def build_portfolio_dashboard(
    session: Any, *, portfolio_id: str, edition_year: int
) -> dict[str, Any]:
    """Build a CEO view for one configured portfolio, never a global fallback."""
    result = await session.execute(
        text(
            """
            SELECT t.id::text AS id, t.name, t.slug
            FROM client_executive_portfolio_tournaments assignment
            JOIN client_executive_portfolios portfolio
              ON portfolio.id = assignment.portfolio_id AND portfolio.active = TRUE
            JOIN tournaments t ON t.id = assignment.tournament_id AND t.active = TRUE
            WHERE assignment.portfolio_id = :portfolio_id AND assignment.active = TRUE
            ORDER BY t.name ASC
            """
        ),
        {"portfolio_id": str(portfolio_id)},
    )
    tournaments = [
        {"id": str(row.id), "name": str(row.name), "slug": str(row.slug or "")}
        for row in result
    ]
    if not tournaments:
        raise ClientExecutiveAccessError(
            "No active tournaments are assigned to this portfolio."
        )
    cards = []
    for tournament in tournaments:
        snapshot = await build_budget_snapshot(
            session,
            tournament_id=tournament["id"],
            tournament_name=tournament["name"],
            tournament_slug=tournament["slug"],
            edition_year=edition_year,
            ensure_schema=False,
            strict_tournament_scope=True,
        )
        cards.append(_executive_card(tournament, snapshot))
    return {
        "edition_year": edition_year,
        "scope": "portfolio",
        "portfolio_id": str(portfolio_id),
        "cards": cards,
        "unavailable_metrics": [
            "cashflow",
            "accounts_receivable",
            "payments",
            "operational_detail",
        ],
    }
