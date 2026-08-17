"""Narrow read-only sports operations status wrapper.

This module intentionally does not expose the raw Sports Platform snapshot. It
compresses the broad projection into the few surfaces that are safe and useful
for assistant answers: mission plan, roster/document risk, incidents, matchday
state and an action queue.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from samchat.sports_platform import build_sports_platform_snapshot

from .business_diff_preview import NOT_EXECUTED
from .soul_wizard import build_soul_wizard_draft, validate_soul_wizard_draft
from .sports_platform_audit import (
    COMMERCIAL_OR_DEMO_MODULES,
    INTERNAL_SOURCE_MODULES,
    build_sports_platform_audit_from_snapshot,
)

SPORTS_OPERATIONS_STATUS_ONLY = "sports_operations_status_only"


@dataclass(frozen=True)
class SportsOperationsStatusReport:
    report_id: str
    headline: str
    summary: str
    tournament: dict[str, Any]
    operational_status: str
    priorities: list[str] = field(default_factory=list)
    action_counts: dict[str, int] = field(default_factory=dict)
    top_actions: list[dict[str, Any]] = field(default_factory=list)
    roster_summary: dict[str, Any] = field(default_factory=dict)
    incident_summary: dict[str, Any] = field(default_factory=dict)
    matchday_summary: dict[str, Any] = field(default_factory=dict)
    communication_summary: dict[str, Any] = field(default_factory=dict)
    wizard_alignment: dict[str, Any] = field(default_factory=dict)
    source_modules: list[str] = field(default_factory=list)
    source_summary: dict[str, Any] = field(default_factory=dict)
    excluded_modules: list[str] = field(default_factory=list)
    safety_summary: dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    report_language: str = SPORTS_OPERATIONS_STATUS_ONLY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _status_from_counts(*, high: int, open_actions: int, blocked_teams: int, open_matches: int, incidents: int = 0) -> str:
    if high or blocked_teams or incidents:
        return "attention_required"
    if open_actions or open_matches:
        return "in_progress"
    return "stable"


def _headline(status: str) -> str:
    if status == "attention_required":
        return "Operaciones tiene bloqueos o riesgos que atender"
    if status == "in_progress":
        return "Operaciones tiene pendientes abiertos sin bloqueo cr?tico"
    return "Operaciones no muestra bloqueos en el snapshot disponible"


def _summary(status: str, *, open_actions: int, blocked_teams: int, incidents: int, open_matches: int) -> str:
    if status == "attention_required":
        return (
            f"Hay {blocked_teams} equipo(s) bloqueado(s), {incidents} incidente(s) "
            f"y {open_actions} accion(es) operativas abiertas."
        )
    if status == "in_progress":
        return (
            f"Hay {open_actions} accion(es) abiertas y {open_matches} partido(s) "
            "o cedula(s) pendientes de preparar/cerrar."
        )
    return "El wrapper no detecto acciones bloqueantes con la evidencia recibida."


def _tournament(platform: Mapping[str, Any]) -> dict[str, Any]:
    command_center = _as_dict(platform.get("command_center"))
    tournament = _as_dict(command_center.get("tournament"))
    return {
        "id": tournament.get("id"),
        "name": tournament.get("name"),
        "slug": tournament.get("slug"),
    }


def _wizard_alignment(payload: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not payload:
        return {
            "present": False,
            "source": None,
            "live_operations_created": False,
        }

    draft_payload = payload.get("draft") if isinstance(payload.get("draft"), Mapping) else payload
    draft = build_soul_wizard_draft(draft_payload)
    readiness = validate_soul_wizard_draft(draft)
    phases = [phase.to_dict() for phase in draft.phases]
    activity_count = sum(len(phase.activities) for phase in draft.phases)
    issue_rows = [issue.to_dict() for issue in readiness.issues]
    missing_paths = [item["path"] for item in issue_rows if item.get("severity") == "error"]
    warning_paths = [item["path"] for item in issue_rows if item.get("severity") == "warning"]
    return {
        "present": True,
        "source": "soul_wizard",
        "draft_id": draft.draft_id,
        "draft_hash": draft.draft_hash,
        "tournament_name": draft.tournament_name,
        "edition_year": draft.edition_year,
        "readiness_status": readiness.status,
        "readiness_score": readiness.readiness_score,
        "required_missing_count": readiness.required_missing_count,
        "warnings_count": readiness.warnings_count,
        "phase_count": len(draft.phases),
        "activity_count": activity_count,
        "phases": phases[:10],
        "missing_paths": missing_paths,
        "warning_paths": warning_paths,
        "integration_decision": (
            "wizard_ready_for_operations_review"
            if readiness.status == "ready_for_review"
            else "wizard_needs_completion_before_operations_review"
        ),
        "live_operations_created": False,
        "operational_writes_allowed": False,
    }


def _snapshot_from_tournament_source(source: Any) -> dict[str, Any]:
    project = getattr(source, "project", None)
    operations = getattr(source, "observed_operations", None)
    link = getattr(source, "operations_link", None)
    project_name = _safe_str(getattr(project, "name", ""))
    project_id = _safe_str(getattr(project, "id", ""))
    slug = _safe_str(getattr(link, "operations_tournament_slug", ""))
    categories = list(getattr(project, "categorias", []) or getattr(operations, "categories", []) or [])
    teams_count = _safe_int(getattr(operations, "teams_count", 0))
    players_count = _safe_int(getattr(operations, "players_count", 0))
    unavailable = list(getattr(source, "unavailable_components", []) or [])
    phases = list(getattr(project, "etapas", []) or [])
    scope_slug = _safe_str(getattr(operations, "scope_slug", ""))

    risks: list[dict[str, Any]] = []
    pending_actions: list[str] = []
    if not scope_slug:
        risks.append(
            {
                "severity": "medium",
                "code": "missing_operations_link",
                "message": "El torneo local no tiene liga operativa observada para equipos/jugadores.",
            }
        )
        pending_actions.append("Configurar liga operativa del torneo antes de prometer tablero vivo completo.")
    if "matches_and_schedule" in unavailable:
        risks.append(
            {
                "severity": "medium",
                "code": "schedule_unavailable",
                "message": "Fechas/calendario rico no estan disponibles en la fuente local actual.",
            }
        )
        pending_actions.append("Completar fases, fechas y actividades desde SOUL Wizard o fuente local equivalente.")

    states = list(getattr(operations, "states", []) or [])
    entity_name = _safe_str(states[0] if states else None) or "Operaciones"
    team_stub = {
        "team_id": scope_slug or project_id or project_name,
        "team_name": project_name or "Torneo",
        "entity_name": entity_name,
        "category": categories[0] if categories else None,
        "players_count": players_count,
        "documents_complete_players": players_count if players_count else 0,
        "documents_verified_players": 0,
        "primary_manager": {},
    }

    return {
        "ok": True,
        "tournaments": [
            {
                "id": project_id,
                "name": project_name,
                "slug": slug or project_id,
            }
        ],
        "summary": {
            "teams_count": teams_count,
            "players_count": players_count,
            "matches_count": 0,
            "teams_with_incomplete_documents": 0,
        },
        "operations": {"matches": [], "standings": []},
        "communications": {"email_inbox_unread": 0, "whatsapp_unread": 0},
        "marketing": {"media": {}},
        "soul": {
            "tournament": {
                "id": project_id,
                "name": project_name,
                "slug": slug or project_id,
            },
            "pending_actions": pending_actions,
            "risks": risks,
            "operations": {
                "entities": [
                    {
                        "entity_name": entity_name,
                        "teams": [team_stub] if teams_count or players_count else [],
                    }
                ],
            },
            "compliance": {
                "players_count": players_count,
                "completion_rate": 1.0 if players_count else 0.0,
                "verification_rate": 0.0,
                "incomplete_teams": [],
                "required_documents": [],
            },
            "phases": [
                {
                    "phase_id": f"phase_{index}",
                    "name": phase,
                    "start_date": None,
                    "end_date": None,
                    "activities": [],
                }
                for index, phase in enumerate(phases, start=1)
            ],
        },
    }


def build_sports_operations_status_from_tournament_source(
    source: Any,
    *,
    max_actions: int = 5,
    focus: Optional[str] = None,
    soul_wizard_payload: Optional[Mapping[str, Any]] = None,
) -> SportsOperationsStatusReport:
    report = build_sports_operations_status_from_snapshot(
        _snapshot_from_tournament_source(source),
        max_actions=max_actions,
        focus=focus,
        soul_wizard_payload=soul_wizard_payload,
    )
    payload = report.to_dict()
    payload["source_summary"] = {
        "source": "samchat_local_tournament_db",
        "source_hash": getattr(source, "source_hash", None),
        "schema_version": getattr(source, "schema_version", None),
        "unavailable_components": list(getattr(source, "unavailable_components", []) or []),
        "domain_write_performed": bool(getattr(source, "domain_write_performed", False)),
    }
    return SportsOperationsStatusReport(**payload)


def build_sports_operations_status_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    max_actions: int = 5,
    focus: Optional[str] = None,
    soul_wizard_payload: Optional[Mapping[str, Any]] = None,
) -> SportsOperationsStatusReport:
    """Build a narrowed assistant-safe operations status from a tournament snapshot."""

    platform = build_sports_platform_snapshot(dict(snapshot))
    wizard_alignment = _wizard_alignment(soul_wizard_payload)
    audit = build_sports_platform_audit_from_snapshot(snapshot)

    mission_control = _as_dict(platform.get("mission_control"))
    action_queue = _as_dict(platform.get("action_queue"))
    roster = _as_dict(platform.get("roster_intelligence"))
    incidents = _as_dict(platform.get("incident_center"))
    matchday = _as_dict(platform.get("matchday_ops"))
    communications = _as_dict(mission_control.get("urgent_communications"))

    actions = [item for item in _as_list(action_queue.get("actions")) if isinstance(item, Mapping)]
    if focus:
        wanted = focus.casefold().strip()
        actions = [
            item
            for item in actions
            if wanted in _safe_str(item.get("title")).casefold()
            or wanted in _safe_str(item.get("module")).casefold()
            or wanted in _safe_str(item.get("detail")).casefold()
            or wanted in _safe_str(item.get("source")).casefold()
        ]

    open_actions = len(actions) if focus else _safe_int(action_queue.get("open_count"))
    high_actions = sum(1 for item in actions if item.get("severity") == "high") if focus else _safe_int(action_queue.get("high_count"))
    blocked_teams = len(_as_list(mission_control.get("blocked_teams")))
    open_matches = _safe_int(matchday.get("open_cedulas_count"))
    incident_count = _safe_int(incidents.get("open_count"))
    status = _status_from_counts(
        high=high_actions,
        open_actions=open_actions,
        blocked_teams=blocked_teams,
        open_matches=open_matches,
        incidents=incident_count,
    )

    ready_modules = set(audit.assistant_ready_modules)
    source_modules = [
        module
        for module in [
            "mission_control",
            "action_queue",
            "incident_center",
            "roster_intelligence",
            "matchday_ops",
            "team_journey",
            "match_center",
        ]
        if module in ready_modules or module in platform
    ]
    excluded = sorted(
        set(audit.internal_source_modules + audit.commercial_or_demo_modules)
        | INTERNAL_SOURCE_MODULES
        | COMMERCIAL_OR_DEMO_MODULES
    )

    return SportsOperationsStatusReport(
        report_id="sports_operations_status_v1",
        headline=_headline(status),
        summary=_summary(
            status,
            open_actions=open_actions,
            blocked_teams=blocked_teams,
            incidents=incident_count,
            open_matches=open_matches,
        ),
        tournament=_tournament(platform),
        operational_status=status,
        priorities=[_safe_str(item) for item in _as_list(mission_control.get("today_plan")) if _safe_str(item)],
        action_counts={
            "open": open_actions,
            "high": high_actions,
            "medium": sum(1 for item in actions if item.get("severity") == "medium") if focus else _safe_int(action_queue.get("medium_count")),
            "low": sum(1 for item in actions if item.get("severity") == "low") if focus else _safe_int(action_queue.get("low_count")),
        },
        top_actions=[dict(item) for item in actions[: max(0, max_actions)]],
        roster_summary={
            "players_count": _safe_int(roster.get("players_count")),
            "completion_rate": roster.get("completion_rate") or 0,
            "verification_rate": roster.get("verification_rate") or 0,
            "incomplete_team_count": len(_as_list(roster.get("incomplete_teams"))),
            "rules": _as_list(roster.get("rules"))[:5],
        },
        incident_summary={
            "open_count": incident_count,
            "high_count": _safe_int(incidents.get("high_count")),
            "incidents": [dict(item) for item in _as_list(incidents.get("incidents"))[:5] if isinstance(item, Mapping)],
        },
        matchday_summary={
            "matches_count": _safe_int(matchday.get("matches_count")),
            "open_cedulas_count": open_matches,
            "field_actions": _as_list(matchday.get("field_actions"))[:8],
        },
        communication_summary={
            "whatsapp_unread": _safe_int(communications.get("whatsapp_unread")),
            "email_inbox_unread": _safe_int(communications.get("email_inbox_unread")),
        },
        wizard_alignment=wizard_alignment,
        source_modules=source_modules,
        excluded_modules=excluded,
        safety_summary={
            "read_only": True,
            "writes_enabled": False,
            "raw_snapshot_exposed": False,
            "raw_snapshot_should_not_be_exposed": True,
            "approval_required_for_durable_outputs": True,
            "audit_decision": audit.decision,
            "soul_wizard_context_accepted": wizard_alignment.get("present") is True,
            "soul_wizard_creates_live_operations": False,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        report_language=SPORTS_OPERATIONS_STATUS_ONLY,
    )


__all__ = [
    "SPORTS_OPERATIONS_STATUS_ONLY",
    "SportsOperationsStatusReport",
    "build_sports_operations_status_from_snapshot",
    "build_sports_operations_status_from_tournament_source",
]
