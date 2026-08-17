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
    source_modules: list[str] = field(default_factory=list)
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


def _status_from_counts(*, high: int, open_actions: int, blocked_teams: int, open_matches: int) -> str:
    if high or blocked_teams:
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


def build_sports_operations_status_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    max_actions: int = 5,
    focus: Optional[str] = None,
) -> SportsOperationsStatusReport:
    """Build a narrowed assistant-safe operations status from a tournament snapshot."""

    platform = build_sports_platform_snapshot(dict(snapshot))
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
        source_modules=source_modules,
        excluded_modules=excluded,
        safety_summary={
            "read_only": True,
            "writes_enabled": False,
            "raw_snapshot_exposed": False,
            "raw_snapshot_should_not_be_exposed": True,
            "approval_required_for_durable_outputs": True,
            "audit_decision": audit.decision,
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
]
