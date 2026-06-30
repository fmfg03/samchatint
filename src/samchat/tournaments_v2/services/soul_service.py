from __future__ import annotations

from typing import Any, Dict, Optional

from samchat.tournaments_v2.adapters import tournament_soul_snapshot_v2
from samchat.tournaments_v2.supabase_client import SupabaseRestClient


SOUL_SCHEMA_VERSION = "2026-04-23.v1"


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _first_tournament(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    tournaments = snapshot.get("tournaments") or []
    return tournaments[0] if tournaments else {}


def _entities(snapshot: Dict[str, Any]) -> list[Dict[str, Any]]:
    return list(((snapshot.get("breakdowns") or {}).get("entities") or []))


def _categories(snapshot: Dict[str, Any]) -> list[Dict[str, Any]]:
    return list(((snapshot.get("breakdowns") or {}).get("categories") or []))


def _branches(snapshot: Dict[str, Any]) -> list[Dict[str, Any]]:
    return list(((snapshot.get("breakdowns") or {}).get("branches") or []))


def _entity_matches(entity: Dict[str, Any], entity_key: str) -> bool:
    wanted = _safe_str(entity_key).lower()
    if not wanted:
        return False
    name = _safe_str(entity.get("entity_name")).lower()
    return name == wanted or wanted in name


def build_compliance_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize document compliance in stable SOUL language."""

    summary = snapshot.get("summary") or {}
    players_count = int(summary.get("players_count") or 0)
    complete_players = int(summary.get("document_players_complete") or 0)
    verified_players = int(summary.get("document_players_verified") or 0)
    incomplete_entities: list[Dict[str, Any]] = []
    incomplete_teams: list[Dict[str, Any]] = []

    for entity in _entities(snapshot):
        entity_players = int(entity.get("players_count") or 0)
        entity_complete = int(entity.get("documents_complete_players") or 0)
        entity_verified = int(entity.get("documents_verified_players") or 0)
        if entity_players and entity_complete < entity_players:
            incomplete_entities.append(
                {
                    "entity_name": entity.get("entity_name"),
                    "players_count": entity_players,
                    "documents_complete_players": entity_complete,
                    "documents_verified_players": entity_verified,
                    "completion_rate": _ratio(entity_complete, entity_players),
                }
            )
        for team in entity.get("teams") or []:
            team_players = int(team.get("players_count") or 0)
            team_complete = int(team.get("documents_complete_players") or 0)
            if team_players and team_complete < team_players:
                incomplete_teams.append(
                    {
                        "entity_name": entity.get("entity_name"),
                        "team_id": team.get("team_id"),
                        "team_name": team.get("team_name"),
                        "category": team.get("category"),
                        "branch": team.get("branch"),
                        "players_count": team_players,
                        "documents_complete_players": team_complete,
                        "missing_documents_players": team_players - team_complete,
                    }
                )

    return {
        "players_count": players_count,
        "documents_complete_players": complete_players,
        "documents_verified_players": verified_players,
        "completion_rate": _ratio(complete_players, players_count),
        "verification_rate": _ratio(verified_players, players_count),
        "teams_with_incomplete_documents": int(
            summary.get("teams_with_incomplete_documents") or 0
        ),
        "incomplete_entities": incomplete_entities,
        "incomplete_teams": incomplete_teams,
    }


def build_entity_folder_seed(
    snapshot: Dict[str, Any],
    entity_key: str,
) -> Dict[str, Any]:
    """Build the folder seed for one participant entity from canonical data."""

    entity = next(
        (row for row in _entities(snapshot) if _entity_matches(row, entity_key)),
        None,
    )
    tournament = _first_tournament(snapshot)
    if not entity:
        return {
            "found": False,
            "entity_key": entity_key,
            "tournament": tournament,
            "operations": {},
            "finance": build_finance_bridge_snapshot(snapshot),
            "marketing": {},
            "evidence": [],
            "pending_actions": [
                "Crear o seleccionar una entidad con datos canonicos antes de armar la carpeta."
            ],
        }

    teams = list(entity.get("teams") or [])
    return {
        "found": True,
        "entity_key": entity_key,
        "entity_name": entity.get("entity_name"),
        "tournament": tournament,
        "operations": {
            "teams_count": entity.get("teams_count"),
            "players_count": entity.get("players_count"),
            "categories": entity.get("categories") or [],
            "branches": entity.get("branches") or [],
            "teams": teams,
            "primary_contacts": [
                {
                    "team_id": team.get("team_id"),
                    "team_name": team.get("team_name"),
                    "primary_manager": team.get("primary_manager"),
                }
                for team in teams
                if team.get("primary_manager")
            ],
        },
        "compliance": {
            "documents_complete_players": entity.get("documents_complete_players"),
            "documents_verified_players": entity.get("documents_verified_players"),
            "completion_rate": _ratio(
                int(entity.get("documents_complete_players") or 0),
                int(entity.get("players_count") or 0),
            ),
        },
        "finance": build_finance_bridge_snapshot(snapshot, entity_name=entity.get("entity_name")),
        "marketing": {
            "teams_with_public_profile": sum(
                1
                for team in teams
                if _safe_str(team.get("instagram_url"))
                or _safe_str(team.get("facebook_url"))
                or _safe_str(team.get("shield_url"))
            ),
            "profiles": [
                {
                    "team_id": team.get("team_id"),
                    "team_name": team.get("team_name"),
                    "instagram_url": team.get("instagram_url"),
                    "facebook_url": team.get("facebook_url"),
                    "shield_url": team.get("shield_url"),
                }
                for team in teams
            ],
        },
        "evidence": [],
        "pending_actions": _entity_pending_actions(entity),
    }


def build_national_phase_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    operations = snapshot.get("operations") or {}
    matches = list(operations.get("matches") or [])
    national_matches = [
        row
        for row in matches
        if any(
            token in _safe_str(row.get("phase")).lower()
            for token in ("national", "nacional", "final", "campeon")
        )
    ]
    return {
        "matches_count": len(national_matches),
        "matches": national_matches[:50],
        "standings": list(operations.get("standings") or [])[:50],
        "cedulas_count": operations.get("cedulas_count") or 0,
        "status": "with_data" if national_matches else "pending_data",
        "notes": [
            "La fase nacional se deriva de partidos con fase nacional/final cuando existan.",
            "Hospedaje, alimentos, sede medica y seguros deben entrar como evidencia o compromisos de carpeta.",
        ],
    }


def build_marketing_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    marketing = dict(snapshot.get("marketing") or {})
    communications = dict(snapshot.get("communications") or {})
    media = dict(marketing.get("media") or {})
    return {
        "media": media,
        "team_marketing_profiles_count": int(
            marketing.get("team_marketing_profiles_count") or 0
        ),
        "communications": communications,
        "activation_evidence_ready": bool(
            int(media.get("photos_count") or 0)
            or int(media.get("videos_count") or 0)
            or int(media.get("streams_count") or 0)
        ),
    }


def build_finance_bridge_snapshot(
    snapshot: Dict[str, Any],
    *,
    entity_name: Optional[str] = None,
) -> Dict[str, Any]:
    tournament = _first_tournament(snapshot)
    return {
        "scope": {
            "tournament_id": tournament.get("id"),
            "tournament_name": tournament.get("name"),
            "entity_name": entity_name,
        },
        "source": "folders_commitments_and_gastos_bridge",
        "available_now": {
            "planned_payments_from_commitments": True,
            "draft_solicitud_from_payment_commitment": True,
            "real_payment_status": False,
            "accounting_posting": False,
        },
        "rules": [
            "Carpetas no crean pagos reales ni asientos contables.",
            "Un compromiso tipo payment puede crear una SOLICITUD en borrador.",
            "Pagos reales y contabilidad se consultan en sus modulos canonicos.",
        ],
    }


def _entity_pending_actions(entity: Dict[str, Any]) -> list[str]:
    pending: list[str] = []
    if not entity.get("teams_count"):
        pending.append("Registrar equipos reales de la entidad.")
    if not entity.get("players_count"):
        pending.append("Registrar jugadores desde OCR o carga canonica.")
    if int(entity.get("players_count") or 0) and int(
        entity.get("documents_complete_players") or 0
    ) < int(entity.get("players_count") or 0):
        pending.append("Completar documentos faltantes de jugadores.")
    teams = entity.get("teams") or []
    if teams and not any(team.get("primary_manager") for team in teams):
        pending.append("Capturar responsable/contacto primario de la entidad.")
    return pending


def _global_pending_actions(snapshot: Dict[str, Any]) -> list[str]:
    summary = snapshot.get("summary") or {}
    pending: list[str] = []
    if not int(summary.get("teams_count") or 0):
        pending.append("Registrar equipos reales para activar carpetas por entidad.")
    if not int(summary.get("players_count") or 0):
        pending.append("Registrar jugadores para calcular cumplimiento documental.")
    if not int(summary.get("matches_count") or 0):
        pending.append("Cargar o generar calendario para rondas y fase nacional.")
    if int(summary.get("teams_with_incomplete_documents") or 0):
        pending.append("Atender equipos con documentos incompletos.")

    optional_sources = snapshot.get("optional_sources") or {}
    missing_sources = [
        table
        for table, status in optional_sources.items()
        if not bool((status or {}).get("available"))
    ]
    if missing_sources:
        pending.append(
            "Revisar fuentes opcionales no disponibles: " + ", ".join(sorted(missing_sources))
        )
    return pending


def _risk_register(snapshot: Dict[str, Any]) -> list[Dict[str, Any]]:
    summary = snapshot.get("summary") or {}
    risks: list[Dict[str, Any]] = []
    if int(summary.get("teams_with_incomplete_documents") or 0):
        risks.append(
            {
                "severity": "medium",
                "code": "incomplete_documents",
                "message": "Hay equipos con documentacion incompleta.",
            }
        )
    if not int(summary.get("matches_count") or 0):
        risks.append(
            {
                "severity": "low",
                "code": "missing_schedule",
                "message": "No hay calendario/partidos en el snapshot canonico.",
            }
        )
    if not int(summary.get("teams_count") or 0):
        risks.append(
            {
                "severity": "medium",
                "code": "missing_teams",
                "message": "No hay equipos canonicos para alimentar carpetas por entidad.",
            }
        )
    return risks


def _entity_folder_seeds(snapshot: Dict[str, Any]) -> list[Dict[str, Any]]:
    return [
        {
            "entity_name": entity.get("entity_name"),
            "teams_count": entity.get("teams_count"),
            "players_count": entity.get("players_count"),
            "categories": entity.get("categories") or [],
            "branches": entity.get("branches") or [],
            "pending_actions": _entity_pending_actions(entity),
        }
        for entity in _entities(snapshot)
    ]


def _build_soul_layer(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": SOUL_SCHEMA_VERSION,
        "source_snapshot_type": snapshot.get("snapshot_type"),
        "tournament": _first_tournament(snapshot),
        "operations": {
            "entities": _entities(snapshot),
            "categories": _categories(snapshot),
            "branches": _branches(snapshot),
            "matches": (snapshot.get("operations") or {}).get("matches") or [],
            "standings": (snapshot.get("operations") or {}).get("standings") or [],
        },
        "entity_folders_seed": _entity_folder_seeds(snapshot),
        "national_phase": build_national_phase_snapshot(snapshot),
        "marketing": build_marketing_snapshot(snapshot),
        "compliance": build_compliance_snapshot(snapshot),
        "finance_bridge": build_finance_bridge_snapshot(snapshot),
        "risks": _risk_register(snapshot),
        "pending_actions": _global_pending_actions(snapshot),
    }


async def build_tournament_soul_snapshot(
    *,
    tournament_key: str = "all",
    tournament_slug: Optional[str] = None,
    tournament_name: Optional[str] = None,
    include_communications: bool = True,
    include_media: bool = True,
    limit: int = 250,
    client: Optional[SupabaseRestClient] = None,
) -> Dict[str, Any]:
    """Build the stable SOUL contract consumed by folders and the assistant.

    The underlying adapter remains the canonical read-only data fetcher. This
    service adds operational language, folder seeds, compliance, national phase,
    marketing and finance bridge sections without changing the legacy keys.
    """

    snapshot = await tournament_soul_snapshot_v2(
        tournament_key=tournament_key,
        tournament_slug=tournament_slug,
        tournament_name=tournament_name,
        include_communications=include_communications,
        include_media=include_media,
        limit=limit,
        client=client,
    )
    snapshot["soul"] = _build_soul_layer(snapshot)
    snapshot["snapshot_type"] = "tournament_soul_service"
    snapshot.setdefault("notes", []).append(
        "La capa soul.* es el contrato estable para folders, planner y asistente."
    )
    return snapshot
