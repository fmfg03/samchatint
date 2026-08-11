"""Director General executive dossiers over tournament operational snapshots."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _soul(snapshot: dict[str, Any]) -> dict[str, Any]:
    return dict(snapshot.get("soul") or {})


def _tournament(snapshot: dict[str, Any]) -> dict[str, Any]:
    soul_tournament = _soul(snapshot).get("tournament") or {}
    if soul_tournament:
        return dict(soul_tournament)
    return dict((snapshot.get("tournaments") or [{}])[0] or {})


def _entities(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    soul_entities = (_soul(snapshot).get("operations") or {}).get("entities") or []
    if soul_entities:
        return [dict(row) for row in soul_entities]
    return [
        dict(row) for row in ((snapshot.get("breakdowns") or {}).get("entities") or [])
    ]


def _unique_contacts(entity: dict[str, Any]) -> list[dict[str, Any]]:
    contacts: dict[tuple[str, str, str], dict[str, Any]] = {}
    for team in entity.get("teams") or []:
        manager = team.get("primary_manager") or {}
        name = _safe_str(manager.get("name"))
        email = _safe_str(manager.get("email"))
        phone = _safe_str(manager.get("phone"))
        if not any((name, email, phone)):
            continue
        contacts[(name.casefold(), email.casefold(), phone)] = {
            "name": name or None,
            "email": email or None,
            "phone": phone or None,
            "source": "team.primary_manager",
        }
    return sorted(
        contacts.values(),
        key=lambda row: (
            _safe_str(row.get("name")).casefold(),
            _safe_str(row.get("email")).casefold(),
        ),
    )


def _category_gender_key(team: dict[str, Any]) -> tuple[str, str]:
    return (
        _safe_str(team.get("category")) or "Sin categoría",
        _safe_str(team.get("branch")) or "Sin género/rama",
    )


def _real_teams_by_category(entity: dict[str, Any]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for team in entity.get("teams") or []:
        key = _category_gender_key(team)
        item = buckets.setdefault(
            key,
            {
                "category": key[0],
                "gender_or_branch": key[1],
                "teams_count": 0,
                "players_count": 0,
                "team_names": [],
            },
        )
        item["teams_count"] += 1
        item["players_count"] += _safe_int(team.get("players_count"))
        if _safe_str(team.get("team_name")):
            item["team_names"].append(_safe_str(team.get("team_name")))
    return sorted(
        buckets.values(),
        key=lambda row: (
            row["category"].casefold(),
            row["gender_or_branch"].casefold(),
        ),
    )


def _players_by_category_age_gender(entity: dict[str, Any]) -> list[dict[str, Any]]:
    # Age is not present in the current aggregate snapshot. Keep an explicit
    # unknown bucket instead of inventing demographic splits.
    rows = []
    for item in _real_teams_by_category(entity):
        rows.append(
            {
                "category": item["category"],
                "age": None,
                "gender_or_branch": item["gender_or_branch"],
                "players_count": item["players_count"],
                "source": "entity.teams.players_count",
                "age_status": "pending_player_birthdate_rollup",
            }
        )
    return rows


def _missing_ops_fields(entity: dict[str, Any]) -> list[str]:
    missing = []
    contacts = _unique_contacts(entity)
    if not contacts:
        missing.append("Contacto de la entidad: teléfono, correo, nacimiento y pareja.")
    missing.extend(
        [
            "Equipos esperados por categoría/género.",
            "Equipos que superan cada ronda.",
            "Descripción de fase estatal, cuotas de arbitraje/transporte.",
            "Equipos que pasan a fase nacional.",
            "Fecha/lugar de entrega de uniformes fase estatal.",
            "Fechas de viaje ida/vuelta al nacional.",
            "Lugar final de clasificación por equipo.",
        ]
    )
    return missing


def _entity_operations_folder(entity: dict[str, Any]) -> dict[str, Any]:
    teams = list(entity.get("teams") or [])
    teams_count = _safe_int(entity.get("teams_count")) or len(teams)
    players_from_teams = sum(_safe_int(team.get("players_count")) for team in teams)
    complete_docs_from_teams = sum(
        _safe_int(team.get("documents_complete_players")) for team in teams
    )
    verified_docs_from_teams = sum(
        _safe_int(team.get("documents_verified_players")) for team in teams
    )
    players_count = players_from_teams or _safe_int(entity.get("players_count"))
    complete_docs = complete_docs_from_teams or _safe_int(
        entity.get("documents_complete_players")
    )
    verified_docs = verified_docs_from_teams or _safe_int(
        entity.get("documents_verified_players")
    )
    return {
        "entity_name": _safe_str(entity.get("entity_name")) or "Sin entidad",
        "ps_owner": None,
        "entity_contacts": _unique_contacts(entity),
        "expected_teams_by_category_gender": [],
        "expected_teams_status": "pending_data",
        "real_teams_by_category_gender": _real_teams_by_category(entity),
        "players_by_category_age_gender": _players_by_category_age_gender(entity),
        "round_advancement": [],
        "state_phase_description": None,
        "national_phase_qualifiers": [],
        "state_uniform_delivery": None,
        "national_travel_dates": None,
        "final_classification": [],
        "document_metrics": {
            "players_count": players_count,
            "documents_complete_players": complete_docs,
            "documents_verified_players": verified_docs,
            "completion_rate": _ratio(complete_docs, players_count),
            "verification_rate": _ratio(verified_docs, players_count),
        },
        "summary": {
            "teams_count": teams_count,
            "players_count": players_count,
            "categories": list(entity.get("categories") or []),
            "branches": list(entity.get("branches") or []),
        },
        "pending_fields": _missing_ops_fields(entity),
    }


def _entity_finance_folder(entity: dict[str, Any]) -> dict[str, Any]:
    return {
        "first_and_successive_aid_transfers": [],
        "equipment_costs": [],
        "visit_reports": [],
        "visit_expenses": [],
        "source_status": "pending_finance_entity_bridge",
        "pending_fields": [
            "Fecha y monto de primera ayuda y pagos sucesivos al operador.",
            "Costo de uniformes, balones, equipamiento y utilería entregados.",
            "Informes de visitas AZ/CL u otros responsables.",
            "Monto de gastos por visita.",
        ],
    }


def _entity_readiness(
    operations: dict[str, Any], finance: dict[str, Any]
) -> dict[str, Any]:
    ops_pending = len(operations.get("pending_fields") or [])
    finance_pending = len(finance.get("pending_fields") or [])
    has_real_teams = bool((operations.get("summary") or {}).get("teams_count"))
    has_contacts = bool(operations.get("entity_contacts"))
    score = 0
    score += 35 if has_real_teams else 0
    score += 20 if has_contacts else 0
    score += round(
        float((operations.get("document_metrics") or {}).get("completion_rate") or 0)
        * 25
    )
    score += 20 if not finance_pending else 0
    if score >= 80:
        status = "usable"
    elif score >= 45:
        status = "partial"
    else:
        status = "needs_data"
    return {
        "score": min(score, 100),
        "status": status,
        "operations_pending_count": ops_pending,
        "finance_pending_count": finance_pending,
    }


def build_director_general_entity_dossier(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build the DG-facing tournament/entity folder contract.

    The object is intentionally read-only and fail-closed: if the snapshot does
    not contain a requested fact, the field is null/empty and listed as pending.
    """
    tournament = _tournament(snapshot)
    entities = []
    for entity in _entities(snapshot):
        operations = _entity_operations_folder(entity)
        finance = _entity_finance_folder(entity)
        entities.append(
            {
                "entity_name": operations["entity_name"],
                "operations": operations,
                "finance": finance,
                "readiness": _entity_readiness(operations, finance),
            }
        )
    entities.sort(
        key=lambda row: (
            -_safe_int(
                ((row.get("operations") or {}).get("summary") or {}).get(
                    "players_count"
                )
            ),
            _safe_str(row.get("entity_name")).casefold(),
        )
    )
    return {
        "ok": True,
        "read_only": True,
        "schema_version": "samchat.dg_entity_dossier.v1",
        "source": "tournament_soul_snapshot",
        "tournament": {
            "id": tournament.get("id"),
            "name": tournament.get("name"),
            "slug": tournament.get("slug"),
            "start_date": tournament.get("start_date"),
            "end_date": tournament.get("end_date"),
        },
        "summary": {
            "entities_count": len(entities),
            "teams_count": sum(
                _safe_int(
                    ((row.get("operations") or {}).get("summary") or {}).get(
                        "teams_count"
                    )
                )
                for row in entities
            ),
            "players_count": sum(
                _safe_int(
                    ((row.get("operations") or {}).get("summary") or {}).get(
                        "players_count"
                    )
                )
                for row in entities
            ),
            "usable_entities": sum(
                1
                for row in entities
                if (row.get("readiness") or {}).get("status") == "usable"
            ),
            "partial_entities": sum(
                1
                for row in entities
                if (row.get("readiness") or {}).get("status") == "partial"
            ),
            "needs_data_entities": sum(
                1
                for row in entities
                if (row.get("readiness") or {}).get("status") == "needs_data"
            ),
        },
        "entities": entities,
        "non_claims": [
            "No infiere contactos, fechas, cuotas, viajes, clasificación ni finanzas si no existen en la fuente.",
            "No crea solicitudes, pagos, pólizas ni expedientes persistentes; es una vista read-only.",
            "La capa financiera por entidad queda marcada pending_finance_entity_bridge hasta cruzar fuentes contables reales.",
        ],
    }
