"""Read-only live SamChat evidence adapters for Owner Pack readiness.

This module bridges existing local SamChat tournament authority into the Owner
Pack readiness contract. It is deliberately fail-closed: a field is marked as
supported only when the local source exposes an explicit value for that exact
field. Missing, unavailable or ambiguous source reads produce no claims and no
writes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from .business_diff_preview import NOT_EXECUTED
from .owner_pack_inventory import build_owner_pack_surface_inventory
from .owner_pack_live_snapshot import (
    OWNER_PACK_LIVE_MISSING,
    OWNER_PACK_LIVE_SNAPSHOT_ONLY,
    OWNER_PACK_LIVE_SUPPORTED,
    OWNER_PACK_WORKSPACE_SOURCE,
    OwnerPackLiveFieldSnapshot,
    OwnerPackLiveSnapshotReport,
    OwnerPackLiveSurfaceSnapshot,
)
from .tournament_goal_source import (
    TournamentSourceAmbiguousError,
    TournamentSourceNotFoundError,
    TournamentSourceSnapshot,
    inspect_tournament_source,
)

OWNER_PACK_LOCAL_DB_SOURCE = "samchat_local_tournament_db"
OWNER_PACK_LIVE_EVIDENCE_ONLY = "owner_pack_live_evidence_only"


@dataclass(frozen=True)
class OwnerPackLiveEvidenceResolution:
    """Result of attempting to resolve local SamChat live evidence."""

    status: str
    source: str = OWNER_PACK_LOCAL_DB_SOURCE
    reports: list[OwnerPackLiveSnapshotReport] = field(default_factory=list)
    unresolved_reason: str = ""
    attempted_selectors: list[str] = field(default_factory=list)
    writes_attempted: int = 0
    side_effects_detected: int = 0
    execution_status: str = NOT_EXECUTED
    audit_language: str = OWNER_PACK_LIVE_EVIDENCE_ONLY

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reports"] = [report.to_dict() for report in self.reports]
        return payload


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _dedupe(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _safe_str(raw)
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _category_gender_rows(snapshot: TournamentSourceSnapshot) -> list[dict[str, Any]]:
    operations = snapshot.observed_operations
    categories = list(operations.categories or []) or [None]
    branches = list(operations.branches or []) or [None]
    rows: list[dict[str, Any]] = []
    for category in categories:
        for branch in branches:
            rows.append(
                {
                    "category": category,
                    "gender_or_branch": branch,
                    "source": "db.copa_telmex.teams.distinct_dimensions",
                }
            )
    return rows


def _entity_evidence(snapshot: TournamentSourceSnapshot, entity_name: str | None) -> dict[str, Any]:
    project = snapshot.project
    operations = snapshot.observed_operations
    evidence: dict[str, Any] = {
        "tournament": {
            "name": project.name,
            "active": project.active,
            "source_hash": snapshot.source_hash,
        }
    }
    if entity_name:
        evidence["entity_name"] = entity_name
    if operations.teams_count:
        rows = _category_gender_rows(snapshot)
        evidence["real_teams"] = [
            {
                **row,
                "teams_count_total": operations.teams_count,
                "states": list(operations.states or []),
                "municipalities": list(operations.municipalities or []),
            }
            for row in rows
        ]
    if operations.players_count:
        evidence["players_by_category_age_gender"] = [
            {
                **row,
                "age": None,
                "players_count_total": operations.players_count,
                "age_status": "pending_player_birthdate_rollup",
            }
            for row in _category_gender_rows(snapshot)
        ]
    if project.etapas:
        evidence["state_phase_operations"] = {
            "configured_phases": list(project.etapas),
            "status": "phase_names_available_dates_pending",
        }
    return evidence


def _national_evidence(snapshot: TournamentSourceSnapshot) -> dict[str, Any]:
    project = snapshot.project
    operations = snapshot.observed_operations
    evidence: dict[str, Any] = {}
    if project.name or project.categorias:
        evidence["tournament_category"] = {
            "tournament_name": project.name,
            "categories": list(project.categorias or []),
            "operations_slug": (
                snapshot.operations_link.operations_tournament_slug
                if snapshot.operations_link
                else None
            ),
        }
    if operations.available:
        evidence["sports_venue_and_fields"] = {
            "status": "pending_specific_venue_and_field_count",
            "observed_scope_slug": operations.scope_slug,
            "teams_count": operations.teams_count,
            "players_count": operations.players_count,
        }
    return evidence


def _marketing_evidence(snapshot: TournamentSourceSnapshot) -> dict[str, Any]:
    return {}


def _field_source_paths(field_name: str, snapshot: TournamentSourceSnapshot) -> list[str]:
    base = {
        "entity_name": ["request.entity_name"],
        "tournament": ["db.tournaments.name"],
        "real_teams": ["db.copa_telmex.teams"],
        "players_by_category_age_gender": ["db.copa_telmex.players", "db.copa_telmex.teams"],
        "state_phase_operations": ["db.tournaments.etapas"],
        "tournament_category": ["db.tournaments.name", "db.tournaments.categorias"],
        "sports_venue_and_fields": ["db.tournament_operations_links", "db.copa_telmex.teams"],
    }
    paths = list(base.get(field_name, []))
    if snapshot.source_hash:
        paths.append(snapshot.source_hash)
    return paths


def _surface_evidence(
    snapshot: TournamentSourceSnapshot,
    surface_id: str,
    entity_name: str | None,
) -> dict[str, Any]:
    if surface_id == "entity_folder":
        return _entity_evidence(snapshot, entity_name)
    if surface_id == "national_phase_folder":
        return _national_evidence(snapshot)
    if surface_id == "marketing_activation_report":
        return _marketing_evidence(snapshot)
    return {}


def build_owner_pack_live_report_from_tournament_source(
    snapshot: TournamentSourceSnapshot,
    *,
    surface_id: str,
    entity_name: str | None = None,
) -> OwnerPackLiveSnapshotReport:
    """Convert a local tournament source snapshot into Owner Pack evidence."""

    inventory = build_owner_pack_surface_inventory(surface_id)
    target = {
        "tournament_name": snapshot.project.name,
        "operations_tournament_slug": (
            snapshot.operations_link.operations_tournament_slug
            if snapshot.operations_link
            else None
        ),
        "entity_name": entity_name,
        "source_hash": snapshot.source_hash,
    }
    evidence_by_field = _surface_evidence(snapshot, surface_id, entity_name)
    fields: list[OwnerPackLiveFieldSnapshot] = []
    supported = 0
    missing = 0
    for section in inventory.sections:
        for spec in section.fields:
            value = evidence_by_field.get(spec.field)
            if _has_value(value):
                supported += 1
                status = OWNER_PACK_LIVE_SUPPORTED
                reason = "supported_by_local_samchat_source"
                source_paths = _field_source_paths(spec.field, snapshot)
            else:
                missing += 1
                status = OWNER_PACK_LIVE_MISSING
                reason = "local_samchat_source_has_no_bound_value_for_field"
                source_paths = []
            fields.append(
                OwnerPackLiveFieldSnapshot(
                    field=spec.field,
                    label=spec.label,
                    section_id=spec.section_id,
                    evidence_type=spec.evidence_type,
                    status=status,
                    value=value if status == OWNER_PACK_LIVE_SUPPORTED else None,
                    source_paths=source_paths,
                    source_files=[OWNER_PACK_LOCAL_DB_SOURCE]
                    if status == OWNER_PACK_LIVE_SUPPORTED
                    else [],
                    reason=reason,
                )
            )

    surface = OwnerPackLiveSurfaceSnapshot(
        surface_id=surface_id,
        label=inventory.label,
        target=target,
        workspace_root=OWNER_PACK_LOCAL_DB_SOURCE,
        workspace_files_checked=[OWNER_PACK_LOCAL_DB_SOURCE],
        workspace_files_found=[OWNER_PACK_LOCAL_DB_SOURCE] if supported else [],
        fields=fields,
        supported_field_count=supported,
        missing_field_count=missing,
        live_lookup_performed=True,
        source=OWNER_PACK_LOCAL_DB_SOURCE,
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_PACK_LIVE_SNAPSHOT_ONLY,
    )
    if supported:
        summary = "Se encontro evidencia viva en la base local de SamChat."
    else:
        summary = "La base local de SamChat no tiene evidencia suficiente para esta superficie."
    return OwnerPackLiveSnapshotReport(
        snapshot_id=f"owner_pack_live_evidence_v2_{surface_id}",
        headline="Evidencia viva local para Owner Pack",
        summary=summary,
        surfaces=[surface],
        supported_field_count=supported,
        missing_field_count=missing,
        safety_summary={
            "writes_enabled": False,
            "write_handlers_invoked": 0,
            "source": OWNER_PACK_LOCAL_DB_SOURCE,
            "fallback_to_legacy_supabase": False,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=OWNER_PACK_LIVE_EVIDENCE_ONLY,
    )


def _candidate_tournament_names(tournament_hint: str) -> list[str]:
    hint = _safe_str(tournament_hint)
    if not hint:
        return []
    candidates = [hint]
    slug_title = hint.replace("-", " ").title()
    candidates.append(slug_title)
    known = {
        "copa-telmex": [
            "Copa Telmex Telcel de Futbol",
            "Copa Telmex",
        ],
        "liga-telmex-telcel": [
            "Liga Telmex Telcel",
            "Liga Telmex Telcel de Beisbol",
        ],
    }
    candidates.extend(known.get(hint.casefold(), []))
    return _dedupe(candidates)


async def resolve_owner_pack_live_evidence(
    session: AsyncSession,
    *,
    scope: str = "all",
    tournament_hint: str = "",
    entity_name: str | None = None,
) -> OwnerPackLiveEvidenceResolution:
    """Resolve local DB evidence for Owner Pack readiness without writes."""

    candidates = _candidate_tournament_names(tournament_hint)
    if not candidates:
        return OwnerPackLiveEvidenceResolution(
            status="missing_tournament_context",
            unresolved_reason="tournament_hint_required",
        )
    if not hasattr(session, "execute"):
        return OwnerPackLiveEvidenceResolution(
            status="data_source_unavailable",
            unresolved_reason="session_has_no_read_execute",
            attempted_selectors=candidates,
        )

    snapshot: TournamentSourceSnapshot | None = None
    last_reason = ""
    for candidate in candidates:
        try:
            snapshot = await inspect_tournament_source(session, tournament_name=candidate)
            break
        except TournamentSourceNotFoundError:
            last_reason = "tournament_not_found"
            continue
        except AttributeError as exc:
            return OwnerPackLiveEvidenceResolution(
                status="data_source_unavailable",
                unresolved_reason=type(exc).__name__,
                attempted_selectors=candidates,
            )
        except TournamentSourceAmbiguousError:
            return OwnerPackLiveEvidenceResolution(
                status="ambiguous_tournament_context",
                unresolved_reason="tournament_name_ambiguous",
                attempted_selectors=candidates,
            )
    if snapshot is None:
        return OwnerPackLiveEvidenceResolution(
            status="not_found",
            unresolved_reason=last_reason or "tournament_not_found",
            attempted_selectors=candidates,
        )

    requested_scope = (scope or "all").strip() or "all"
    surfaces = (
        [requested_scope]
        if requested_scope != "all"
        else ["entity_folder", "national_phase_folder", "marketing_activation_report"]
    )
    reports = [
        build_owner_pack_live_report_from_tournament_source(
            snapshot,
            surface_id=surface_id,
            entity_name=entity_name,
        )
        for surface_id in surfaces
        if surface_id in {"entity_folder", "national_phase_folder", "marketing_activation_report"}
    ]
    if not reports:
        return OwnerPackLiveEvidenceResolution(
            status="unsupported_scope",
            unresolved_reason=f"unsupported_scope:{requested_scope}",
            attempted_selectors=candidates,
        )
    return OwnerPackLiveEvidenceResolution(
        status="resolved",
        reports=reports,
        attempted_selectors=candidates,
    )


__all__ = [
    "OWNER_PACK_LIVE_EVIDENCE_ONLY",
    "OWNER_PACK_LOCAL_DB_SOURCE",
    "OwnerPackLiveEvidenceResolution",
    "build_owner_pack_live_report_from_tournament_source",
    "resolve_owner_pack_live_evidence",
]
