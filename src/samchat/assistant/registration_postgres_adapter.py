"""Project canonical registration reads into existing assistant contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from devnous.copa_telmex.registration_read_model import (
    dispatch_registration_snapshot,
)
from samchat.assistant.tournament_registration_reports import parse_date


def _filter_date(value: str | None) -> str | None:
    if not value:
        return None
    # Preserve an explicit timestamp; only normalize calendar-date inputs.
    if "T" in value or " " in value:
        return datetime.fromisoformat(value).isoformat()
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError("Invalid registration date filter")
    return parsed.isoformat()


async def registration_postgres_response(
    session: Any,
    *,
    projection: str,
    tournament_key: str,
    tournament_id: str | None = None,
    tournament_slug: str | None = None,
    edition_year: int | None = None,
    as_of_date: str | None = None,
    question: str | None = None,
    state: str | None = None,
    municipality: str | None = None,
    category: str | None = None,
    gender: str | None = None,
    team_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
) -> dict[str, Any] | None:
    """Return None only when this selector is outside the configured migration."""
    snapshot = await dispatch_registration_snapshot(
        session,
        tournament_id=tournament_id,
        tournament_selector=tournament_slug or tournament_key,
        tournament_slug=tournament_slug if not tournament_id else None,
        edition_year=edition_year,
        as_of_date=as_of_date,
        filters={
            "state": state,
            "municipality": municipality,
            "category": category,
            "gender": gender,
            "team_name": team_name,
            "date_from": _filter_date(date_from),
            "date_to": _filter_date(date_to),
            "limit": limit,
        },
    )
    if snapshot is None:
        return None

    result = dict(snapshot)
    summary = dict(snapshot.get("summary") or {})
    groups = dict(snapshot.get("groups") or {})

    def rows(field: str, label: str) -> list[dict[str, Any]]:
        return [
            {
                label: row.get(field) or f"(sin {label})",
                "equipos": row.get("teams"),
                "jugadores": row.get("active_players"),
                "provisional_players": row.get("provisional_players"),
            }
            for row in groups.get(f"by_{field}") or []
        ]

    teams = summary.get("total_teams")
    players = summary.get("active_players")
    result.update(
        tournament_key=tournament_key,
        read_only=True,
        registration_summary=summary,
        total_equipos=teams,
        total_jugadores=players,
        nota=(
            "Inscripción desde PostgreSQL: jugadores activos y provisionales "
            "se presentan separados. La captura no acredita elegibilidad externa."
        ),
    )
    if projection == "executive_reports":
        executive = dict(snapshot.get("executive_reports") or {})
        result.update(
            title="Reportes de cédulas por torneo",
            tournament={
                "id": snapshot.get("tournament_id"),
                "name": snapshot.get("tournament_name"),
                "slug": snapshot.get("roster_slug"),
            },
            summary=executive.get("summary")
            or {"equipos": teams, "jugadores": players},
            reports=executive.get("reports") or {},
            as_of_date=executive.get("as_of_date"),
            caveats=list(executive.get("caveats") or [])
            + list(snapshot.get("non_claims") or []),
        )
        if snapshot.get("next_action"):
            result["caveats"].append(snapshot["next_action"])
    elif projection == "breakdown":
        result.update(
            state_query=state,
            desglose_por_municipio=rows("municipality", "municipio"),
        )
    elif projection == "operations":
        result.update(
            question=question,
            totals={"equipos": teams, "jugadores": players},
            breakdowns={
                "por_estado": rows("state", "estado"),
                "por_municipio": rows("municipality", "municipio"),
                "por_categoria": rows("category", "categoria"),
                "por_rama": rows("gender", "rama"),
            },
            teams=[
                {
                    "team_id": row.get("id"),
                    "equipo": row.get("name"),
                    "estado": row.get("state"),
                    "municipio": row.get("municipality"),
                    "categoria": row.get("category"),
                    "rama": row.get("gender"),
                    "tournament_slug": snapshot.get("roster_slug"),
                    "jugadores": row.get("active_players"),
                    "provisional_players": row.get("provisional_players"),
                    "created_at": row.get("created_at"),
                }
                for row in snapshot.get("teams") or []
            ],
            limit=limit,
            players=[],
            players_detail_status="aggregate_only",
        )
    else:
        raise ValueError("Unknown registration projection")
    return result
