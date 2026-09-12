"""Portfolio-scoped budget and operational read models for internal Direction."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
import unicodedata

from sqlalchemy import text

from samchat.budgets.service import budget_alias_candidates, build_budget_snapshot
from samchat.sports_platform import build_director_general_entity_dossier
from samchat.tournaments_v2.supabase_client import TournamentsV2Error
from samchat.tournaments_v2.services import build_tournament_soul_snapshot


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
                """SELECT DISTINCT t.id::text AS id, t.name, NULL::text AS slug
                FROM client_executive_portfolio_tournaments assignment
                JOIN client_executive_portfolios portfolio
                  ON portfolio.id = assignment.portfolio_id AND portfolio.active = TRUE
                JOIN tournaments t ON t.id = assignment.tournament_id AND t.active = TRUE
                WHERE assignment.active = TRUE
                ORDER BY t.name ASC"""
            )
        )
        return [
            {"id": str(row.id), "name": str(row.name), "slug": str(row.slug or "")}
            for row in result
        ]
    result = await session.execute(
        text(
            """
            SELECT DISTINCT t.id::text AS id, t.name, NULL::text AS slug
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


def _optional_money(mapping: dict[str, Any], *keys: str) -> Optional[float]:
    """Return the first present numeric value without turning absence into zero."""
    for key in keys:
        if key in mapping and mapping.get(key) is not None:
            try:
                return float(mapping[key])
            except (TypeError, ValueError):
                return None
    return None


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
    source_state = str(snapshot.get("source") or "").strip()
    scope_available = source_state != "budget_scope_unavailable"

    card: dict[str, Any] = {
        "tournament_id": tournament["id"],
        "tournament_name": tournament["name"],
        "budget": _optional_money(summary, "budget_total") if scope_available else None,
        "actual": (
            _optional_money(comparison, "actual_total", "paid_total")
            if scope_available
            else None
        ),
        "committed": (
            _optional_money(comparison, "committed_total") if scope_available else None
        ),
        "projected": (
            _optional_money(forecast, "projected_close_total", "projected_total")
            if scope_available
            else None
        ),
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

    # Production snapshots carry source metadata. Test fixtures historically did
    # not, so these fields stay conditional for backwards-compatible contracts.
    if source_state:
        card["budget_source_status"] = (
            "unavailable" if source_state == "budget_scope_unavailable" else "available"
        )
        card["budget_snapshot_source"] = source_state
        card["budget_version"] = snapshot.get("version")
        card["paid"] = (
            _optional_money(comparison, "paid_total") if scope_available else None
        )
        card["requested"] = (
            _optional_money(comparison, "requested_total") if scope_available else None
        )
        card["pending_to_pay"] = (
            _optional_money(comparison, "pending_to_pay_total")
            if scope_available
            else None
        )
        card["budget_breakdowns"] = (
            snapshot.get("breakdowns")
            if isinstance(snapshot.get("breakdowns"), dict)
            else {}
        )
        bridge = snapshot.get("direction_scope_bridge")
        if isinstance(bridge, dict):
            card["budget_scope_bridge"] = dict(bridge)
    return card


def _unavailable_dossier(tournament: dict[str, str]) -> dict[str, Any]:
    """Return a safe contract when the scoped operations source is unavailable."""
    return {
        "ok": False,
        "read_only": True,
        "schema_version": "samchat.dg_entity_dossier.v1",
        "source": "tournament_soul_snapshot",
        "source_status": "unavailable",
        "tournament": {
            "id": tournament["id"],
            "name": tournament["name"],
            "slug": tournament["slug"],
        },
        "summary": {},
        "entities": [],
        "national_phase": {
            "status": "unavailable",
            "matches": [],
            "standings": [],
        },
        "marketing": {"status": "unavailable", "media": {}},
        "non_claims": [
            "La fuente operativa no estuvo disponible; no se sustituyeron datos con ceros.",
        ],
    }


def _identity_text(value: object) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    ascii_text = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return " ".join(ascii_text.split())


def _source_year(item: dict[str, Any]) -> Optional[int]:
    raw = str(item.get("start_date") or "")[:4]
    return int(raw) if raw.isdigit() else None


def _uuid_snapshot_matches(
    snapshot: dict[str, Any], tournament: dict[str, str], edition_year: int
) -> bool:
    source_tournaments = [
        item for item in list(snapshot.get("tournaments") or []) if isinstance(item, dict)
    ]
    if len(source_tournaments) != 1:
        return False
    item = source_tournaments[0]
    return str(item.get("id") or "") == str(tournament["id"]) and _source_year(
        item
    ) == int(edition_year)


def _exact_name_snapshot_matches(
    snapshot: dict[str, Any], tournament: dict[str, str], edition_year: int
) -> bool:
    source_tournaments = [
        item for item in list(snapshot.get("tournaments") or []) if isinstance(item, dict)
    ]
    if len(source_tournaments) != 1:
        return False
    item = source_tournaments[0]
    if _identity_text(item.get("name")) != _identity_text(tournament.get("name")):
        return False
    if _source_year(item) != int(edition_year):
        return False
    soul = snapshot.get("soul") if isinstance(snapshot.get("soul"), dict) else {}
    soul_tournament = (
        soul.get("tournament") if isinstance(soul.get("tournament"), dict) else {}
    )
    if soul_tournament.get("id") and str(soul_tournament.get("id")) != str(
        item.get("id")
    ):
        return False
    return True


async def _load_soul_snapshot(
    tournament: dict[str, str], *, edition_year: int
) -> tuple[Optional[dict[str, Any]], str]:
    """Resolve SOUL without allowing display names to broaden Direction scope."""
    observed_wrong_edition = False
    try:
        uuid_snapshot = await build_tournament_soul_snapshot(
            tournament_key="all",
            tournament_slug=tournament["id"],
            include_communications=False,
            include_media=True,
            limit=1000,
        )
    except TournamentsV2Error:
        uuid_snapshot = None

    if isinstance(uuid_snapshot, dict):
        if _uuid_snapshot_matches(uuid_snapshot, tournament, edition_year):
            return uuid_snapshot, "authorized_uuid"
        years = {
            _source_year(item)
            for item in list(uuid_snapshot.get("tournaments") or [])
            if isinstance(item, dict)
        }
        observed_wrong_edition = bool(years - {None, int(edition_year)})

    # The operational store can have a different UUID namespace. The only safe
    # bridge is one unique exact normalized name in the requested edition. Any
    # ambiguous/sub-string result fails closed.
    if tournament.get("name"):
        try:
            name_snapshot = await build_tournament_soul_snapshot(
                tournament_key="all",
                tournament_slug=tournament["name"],
                include_communications=False,
                include_media=True,
                limit=1000,
            )
        except TournamentsV2Error:
            name_snapshot = None
        if isinstance(name_snapshot, dict):
            if _exact_name_snapshot_matches(name_snapshot, tournament, edition_year):
                return name_snapshot, "exact_name_edition_bridge"
            years = {
                _source_year(item)
                for item in list(name_snapshot.get("tournaments") or [])
                if isinstance(item, dict)
            }
            observed_wrong_edition = observed_wrong_edition or bool(
                years - {None, int(edition_year)}
            )

    return None, "edition_unavailable" if observed_wrong_edition else "unavailable"


async def _build_operational_dossier(
    tournament: dict[str, str],
    *,
    edition_year: int,
) -> dict[str, Any]:
    """Build one strictly tournament-scoped dossier without operational writes."""
    snapshot, bridge = await _load_soul_snapshot(tournament, edition_year=edition_year)
    if snapshot is None:
        unavailable = _unavailable_dossier(tournament)
        if bridge == "edition_unavailable":
            unavailable["source_status"] = "edition_unavailable"
            unavailable["non_claims"] = [
                "La fuente operativa no acredita datos para la edición solicitada; "
                "no se mostraron datos de otra edición.",
            ]
        return unavailable

    dossier = build_director_general_entity_dossier(snapshot)
    soul = snapshot.get("soul") if isinstance(snapshot.get("soul"), dict) else {}
    dossier["source_status"] = "available"
    dossier["source_bridge"] = bridge
    dossier["national_phase"] = dict((soul or {}).get("national_phase") or {})
    dossier["marketing"] = dict((soul or {}).get("marketing") or {})
    return dossier


async def _budget_alias_bridge_is_safe(
    session: Any,
    *,
    tournament: dict[str, str],
    edition_year: int,
    version_id: str,
    aliases: list[str],
) -> bool:
    """Verify that a legacy alias resolves only rows for the authorized tournament."""
    if not version_id or not aliases or not tournament.get("name"):
        return False
    result = await session.execute(
        text(
            """
            SELECT
                COUNT(*) AS line_count,
                COUNT(*) FILTER (
                    WHERE UPPER(TRIM(COALESCE(l.tournament_name, '')))
                          <> UPPER(TRIM(:tournament_name))
                ) AS foreign_name_count,
                COUNT(*) FILTER (
                    WHERE l.tournament_id IS NOT NULL
                      AND CAST(l.tournament_id AS text) <> :tournament_id
                ) AS foreign_id_count
            FROM budget_lines l
            WHERE CAST(l.budget_version_id AS text) = :version_id
              AND COALESCE(l.line_direction, 'expense') = 'expense'
              AND (
                    UPPER(COALESCE(l.tournament_code, '')) = ANY(:aliases)
                    OR UPPER(TRIM(COALESCE(l.tournament_name, '')))
                       = UPPER(TRIM(:tournament_name))
              )
            """
        ),
        {
            "version_id": str(version_id),
            "tournament_id": str(tournament["id"]),
            "tournament_name": str(tournament["name"]),
            "aliases": aliases,
        },
    )
    row = result.mappings().first()
    if not row:
        return False
    return (
        int(row.get("line_count") or 0) > 0
        and int(row.get("foreign_name_count") or 0) == 0
        and int(row.get("foreign_id_count") or 0) == 0
    )


async def _build_direction_budget_snapshot(
    session: Any,
    *,
    tournament: dict[str, str],
    edition_year: int,
) -> dict[str, Any]:
    """Read budget truth with a guarded bridge for legacy name/code rows."""
    strict = await build_budget_snapshot(
        session,
        tournament_id=tournament["id"],
        tournament_name=tournament.get("name"),
        tournament_slug=tournament.get("slug"),
        edition_year=edition_year,
        ensure_schema=False,
        strict_tournament_scope=True,
    )
    if strict.get("source") != "budget_scope_unavailable":
        return strict

    version = strict.get("version") if isinstance(strict.get("version"), dict) else {}
    version_id = str(version.get("id") or "")
    aliases = sorted(budget_alias_candidates(tournament.get("name") or ""))
    if not await _budget_alias_bridge_is_safe(
        session,
        tournament=tournament,
        edition_year=edition_year,
        version_id=version_id,
        aliases=aliases,
    ):
        return strict

    bridged = await build_budget_snapshot(
        session,
        tournament_id=tournament["id"],
        tournament_name=tournament.get("name"),
        tournament_slug=tournament.get("slug"),
        edition_year=edition_year,
        version_id=version_id,
        ensure_schema=False,
        strict_tournament_scope=False,
    )
    if bridged.get("source") != "budget_db":
        return strict
    bridged["direction_scope_bridge"] = {
        "status": "exact_name_alias_bridge",
        "authorized_tournament_id": tournament["id"],
        "budget_version_id": version_id,
        "aliases": aliases,
        "read_only": True,
    }
    return bridged


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
    include_operational_detail: bool = False,
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
        snapshot = await _build_direction_budget_snapshot(
            session,
            tournament=tournament,
            edition_year=edition_year,
        )
        card = _executive_card(tournament, snapshot)
        if include_operational_detail:
            card["dossier"] = await _build_operational_dossier(
                tournament,
                edition_year=edition_year,
            )
        cards.append(card)

    return {
        "edition_year": edition_year,
        "scope": "tournament" if tournament_id else "portfolio",
        "cards": cards,
        "data_boundary": {
            "operations": "tournament_soul_snapshot",
            "budget": "samchat.budgets.service.build_budget_snapshot",
            "entity_finance": "budget_breakdowns + canonical accounting actuals",
            "writes": False,
        },
        "unavailable_metrics": ["cashflow", "accounts_receivable", "payments"],
    }


async def build_portfolio_dashboard(
    session: Any, *, portfolio_id: str, edition_year: int
) -> dict[str, Any]:
    """Build a CEO view for one configured portfolio, never a global fallback."""
    result = await session.execute(
        text(
            """
            SELECT t.id::text AS id, t.name, NULL::text AS slug
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
        snapshot = await _build_direction_budget_snapshot(
            session,
            tournament=tournament,
            edition_year=edition_year,
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
