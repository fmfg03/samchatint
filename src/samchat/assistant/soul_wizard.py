"""Read-only SOUL wizard contract for tournament setup drafts.

This module defines the inert work product for a future operations wizard. It
does not create tournaments, write database rows, send notifications, or call
external services. It only normalizes a draft, validates whether the draft is
ready for human review, and exposes the expected wizard steps.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Optional, Sequence


CONTRACT_VERSION = "soul_wizard_contract_v1"
EXECUTION_STATUS = "not_executed"


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_optional_text(value: Any) -> Optional[str]:
    cleaned = _clean_text(value)
    return cleaned or None


def _clean_sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Iterable[Any] = value.splitlines()
    elif isinstance(value, Sequence):
        values = value
    else:
        return ()
    cleaned = [_clean_text(item) for item in values]
    return tuple(dict.fromkeys(item for item in cleaned if item))


def _clean_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class SoulWizardActivity:
    activity_id: str
    name: str
    owner: Optional[str] = None
    due_date: Optional[str] = None
    status: str = "draft"
    evidence_required: tuple[str, ...] = ()
    evidence_status: str = "pending"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, index: int) -> "SoulWizardActivity":
        return cls(
            activity_id=_clean_text(payload.get("activity_id")) or f"activity_{index}",
            name=_clean_text(payload.get("name") or payload.get("nombre")),
            owner=_clean_optional_text(payload.get("owner") or payload.get("responsable")),
            due_date=_clean_optional_text(payload.get("due_date") or payload.get("fecha_limite")),
            status=_clean_text(payload.get("status") or payload.get("estado")) or "draft",
            evidence_required=_clean_sequence(
                payload.get("evidence_required") or payload.get("evidencia_requerida")
            ),
            evidence_status=_clean_text(
                payload.get("evidence_status") or payload.get("estado_evidencia")
            )
            or "pending",
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass(frozen=True)
class SoulWizardPhase:
    phase_id: str
    name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: str = "draft"
    activities: tuple[SoulWizardActivity, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, index: int) -> "SoulWizardPhase":
        activities = tuple(
            SoulWizardActivity.from_mapping(item, index=activity_index + 1)
            for activity_index, item in enumerate(
                _as_list_of_mappings(payload.get("activities") or payload.get("actividades"))
            )
        )
        return cls(
            phase_id=_clean_text(payload.get("phase_id")) or f"phase_{index}",
            name=_clean_text(payload.get("name") or payload.get("nombre")),
            start_date=_clean_optional_text(payload.get("start_date") or payload.get("fecha_inicio")),
            end_date=_clean_optional_text(payload.get("end_date") or payload.get("fecha_fin")),
            status=_clean_text(payload.get("status") or payload.get("estado")) or "draft",
            activities=activities,
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass(frozen=True)
class SoulWizardDraft:
    draft_id: str
    tournament_name: str
    edition_year: Optional[int] = None
    categories: tuple[str, ...] = ()
    branches: tuple[str, ...] = ()
    phases: tuple[SoulWizardPhase, ...] = ()
    expected_entities: tuple[str, ...] = ()
    expected_teams: Optional[int] = None
    required_documents: tuple[str, ...] = ()
    eligibility_rules: tuple[str, ...] = ()
    finance_baseline: tuple[str, ...] = ()
    source_tournament_id: Optional[str] = None
    source_snapshot_id: Optional[str] = None
    execution_status: str = EXECUTION_STATUS
    operational_writes_allowed: bool = False
    contract_version: str = CONTRACT_VERSION

    @property
    def draft_hash(self) -> str:
        return _canonical_sha256(self)

    def to_dict(self) -> dict[str, Any]:
        payload = _json_value(self)
        payload["draft_hash"] = self.draft_hash
        return payload


@dataclass(frozen=True)
class SoulWizardValidationIssue:
    code: str
    severity: str
    message: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass(frozen=True)
class SoulWizardReadinessReport:
    status: str
    readiness_score: int
    issues: tuple[SoulWizardValidationIssue, ...] = ()
    execution_status: str = EXECUTION_STATUS
    operational_writes_allowed: bool = False
    writes_attempted: int = 0
    side_effects_detected: int = 0

    @property
    def required_missing_count(self) -> int:
        return sum(1 for item in self.issues if item.severity == "error")

    @property
    def warnings_count(self) -> int:
        return sum(1 for item in self.issues if item.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        payload = _json_value(self)
        payload["required_missing_count"] = self.required_missing_count
        payload["warnings_count"] = self.warnings_count
        return payload


def build_soul_wizard_draft(payload: Mapping[str, Any]) -> SoulWizardDraft:
    phases = tuple(
        SoulWizardPhase.from_mapping(item, index=index + 1)
        for index, item in enumerate(
            _as_list_of_mappings(payload.get("phases") or payload.get("fases"))
        )
    )
    return SoulWizardDraft(
        draft_id=_clean_text(payload.get("draft_id")) or "soul_wizard_draft",
        tournament_name=_clean_text(
            payload.get("tournament_name") or payload.get("nombre_torneo") or payload.get("name")
        ),
        edition_year=_clean_int(payload.get("edition_year") or payload.get("edicion")),
        categories=_clean_sequence(payload.get("categories") or payload.get("categorias")),
        branches=_clean_sequence(payload.get("branches") or payload.get("ramas")),
        phases=phases,
        expected_entities=_clean_sequence(
            payload.get("expected_entities") or payload.get("entidades_esperadas")
        ),
        expected_teams=_clean_int(payload.get("expected_teams") or payload.get("equipos_esperados")),
        required_documents=_clean_sequence(
            payload.get("required_documents") or payload.get("documentos_requeridos")
        ),
        eligibility_rules=_clean_sequence(
            payload.get("eligibility_rules") or payload.get("reglas_elegibilidad")
        ),
        finance_baseline=_clean_sequence(
            payload.get("finance_baseline") or payload.get("base_financiera")
        ),
        source_tournament_id=_clean_optional_text(payload.get("source_tournament_id")),
        source_snapshot_id=_clean_optional_text(payload.get("source_snapshot_id")),
    )


def validate_soul_wizard_draft(draft: SoulWizardDraft) -> SoulWizardReadinessReport:
    issues: list[SoulWizardValidationIssue] = []

    def add(code: str, severity: str, message: str, path: str) -> None:
        issues.append(SoulWizardValidationIssue(code, severity, message, path))

    if not draft.tournament_name:
        add("missing_tournament_name", "error", "Tournament name is required.", "tournament_name")
    if draft.edition_year is None:
        add("missing_edition_year", "error", "Edition year is required.", "edition_year")
    if not draft.categories:
        add("missing_categories", "warning", "At least one category should be declared.", "categories")
    if not draft.branches:
        add("missing_branches", "warning", "At least one branch/gender should be declared.", "branches")
    if not draft.phases:
        add("missing_phases", "error", "At least one phase with dates is required.", "phases")

    for index, phase in enumerate(draft.phases):
        phase_path = f"phases[{index}]"
        if not phase.name:
            add("missing_phase_name", "error", "Phase name is required.", f"{phase_path}.name")
        start = _parse_date(phase.start_date)
        end = _parse_date(phase.end_date)
        if start is None:
            add("missing_phase_start_date", "error", "Phase start date is required.", f"{phase_path}.start_date")
        if end is None:
            add("missing_phase_end_date", "error", "Phase end date is required.", f"{phase_path}.end_date")
        if start and end and end < start:
            add("phase_end_before_start", "error", "Phase end date cannot be before start date.", f"{phase_path}.end_date")
        if not phase.activities:
            add("missing_phase_activities", "error", "Each phase needs at least one activity.", f"{phase_path}.activities")
        for activity_index, activity in enumerate(phase.activities):
            activity_path = f"{phase_path}.activities[{activity_index}]"
            if not activity.name:
                add("missing_activity_name", "error", "Activity name is required.", f"{activity_path}.name")
            if not activity.owner:
                add("missing_activity_owner", "warning", "Activity owner should be assigned before review.", f"{activity_path}.owner")
            if activity.due_date and _parse_date(activity.due_date) is None:
                add("invalid_activity_due_date", "error", "Activity due date must use YYYY-MM-DD.", f"{activity_path}.due_date")

    if not draft.expected_entities:
        add("missing_expected_entities", "warning", "Expected participating entities should be declared.", "expected_entities")
    if draft.expected_teams is None:
        add("missing_expected_teams", "warning", "Expected team count should be declared.", "expected_teams")
    if not draft.required_documents:
        add("missing_required_documents", "warning", "Required player/team documents should be declared.", "required_documents")
    if not draft.eligibility_rules:
        add("missing_eligibility_rules", "warning", "Eligibility rules should be declared.", "eligibility_rules")

    error_count = sum(1 for item in issues if item.severity == "error")
    warning_count = sum(1 for item in issues if item.severity == "warning")
    score = max(0, 100 - error_count * 20 - warning_count * 5)
    status = "ready_for_review" if error_count == 0 else "incomplete"
    return SoulWizardReadinessReport(status=status, readiness_score=score, issues=tuple(issues))


def build_soul_wizard_contract() -> dict[str, Any]:
    steps = (
        {"step_id": "tournament_identity", "title": "Tournament identity", "required_fields": ("tournament_name", "edition_year")},
        {"step_id": "categories_branches", "title": "Categories and branches", "required_fields": ("categories", "branches")},
        {"step_id": "phases_dates", "title": "Phases and dates", "required_fields": ("phases[].name", "phases[].start_date", "phases[].end_date")},
        {"step_id": "phase_activities", "title": "Activities by phase", "required_fields": ("phases[].activities[].name", "phases[].activities[].due_date")},
        {"step_id": "responsibilities", "title": "Owners and responsibilities", "required_fields": ("phases[].activities[].owner",)},
        {"step_id": "entities_teams", "title": "Entities and expected teams", "required_fields": ("expected_entities", "expected_teams")},
        {"step_id": "documents_eligibility", "title": "Documents and eligibility rules", "required_fields": ("required_documents", "eligibility_rules")},
        {"step_id": "finance_baseline", "title": "Finance baseline", "required_fields": ("finance_baseline",)},
        {"step_id": "review_activation", "title": "Review and activation preview", "required_fields": ("human_review", "explicit_authority_before_write")},
    )
    return {
        "contract_id": CONTRACT_VERSION,
        "read_only": True,
        "execution_status": EXECUTION_STATUS,
        "operational_writes_allowed": False,
        "steps": [_json_value(item) for item in steps],
        "non_claims": (
            "does_not_create_tournament",
            "does_not_create_teams",
            "does_not_create_calendar",
            "does_not_notify_users",
            "does_not_grant_authority",
        ),
    }


def build_soul_wizard_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    draft = build_soul_wizard_draft(payload)
    readiness = validate_soul_wizard_draft(draft)
    return {
        "contract": build_soul_wizard_contract(),
        "draft": draft.to_dict(),
        "readiness": readiness.to_dict(),
    }


__all__ = [
    "CONTRACT_VERSION",
    "EXECUTION_STATUS",
    "SoulWizardActivity",
    "SoulWizardDraft",
    "SoulWizardPhase",
    "SoulWizardReadinessReport",
    "SoulWizardValidationIssue",
    "build_soul_wizard_contract",
    "build_soul_wizard_draft",
    "build_soul_wizard_payload",
    "validate_soul_wizard_draft",
]
