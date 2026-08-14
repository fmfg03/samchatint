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


def _activity_from_line(line: str, *, index: int) -> dict[str, Any]:
    parts = [part.strip() for part in str(line or "").split("|")]
    while len(parts) < 3:
        parts.append("")
    return {
        "activity_id": f"activity_{index}",
        "name": parts[0],
        "owner": parts[1],
        "due_date": parts[2],
    }


def _activities_from_text(value: Any) -> list[dict[str, Any]]:
    lines = [line.strip() for line in str(value or "").splitlines()]
    return [
        _activity_from_line(line, index=index + 1)
        for index, line in enumerate(lines)
        if line
    ]


def _list_names(rows: Any, *keys: str) -> tuple[str, ...]:
    names: list[str] = []
    if isinstance(rows, Mapping):
        rows = rows.values()
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return ()
    for row in rows:
        if isinstance(row, Mapping):
            for key in keys:
                value = _clean_text(row.get(key))
                if value:
                    names.append(value)
                    break
        else:
            value = _clean_text(row)
            if value:
                names.append(value)
    return tuple(dict.fromkeys(names))


def _source_tournament(source: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(source.get("tournament"), Mapping):
        return source["tournament"]
    tournaments = source.get("tournaments")
    if isinstance(tournaments, Sequence) and not isinstance(tournaments, (str, bytes)):
        first = next((item for item in tournaments if isinstance(item, Mapping)), None)
        if first is not None:
            return first
    draft = source.get("draft")
    if isinstance(draft, Mapping):
        return draft
    return source


def _source_breakdowns(source: Mapping[str, Any]) -> Mapping[str, Any]:
    breakdowns = source.get("breakdowns")
    return breakdowns if isinstance(breakdowns, Mapping) else {}


def _source_categories(source: Mapping[str, Any]) -> tuple[str, ...]:
    if source.get("categories"):
        return _clean_sequence(source.get("categories"))
    breakdowns = _source_breakdowns(source)
    return _list_names(breakdowns.get("categories"), "category", "name", "category_name")


def _source_branches(source: Mapping[str, Any]) -> tuple[str, ...]:
    if source.get("branches"):
        return _clean_sequence(source.get("branches"))
    breakdowns = _source_breakdowns(source)
    return _list_names(breakdowns.get("branches"), "branch", "name", "branch_name")


def _source_entities(source: Mapping[str, Any]) -> tuple[str, ...]:
    if source.get("expected_entities"):
        return _clean_sequence(source.get("expected_entities"))
    breakdowns = _source_breakdowns(source)
    return _list_names(breakdowns.get("entities"), "entity_name", "name")


def _source_expected_teams(source: Mapping[str, Any]) -> Optional[int]:
    explicit = _clean_int(source.get("expected_teams"))
    if explicit is not None:
        return explicit
    summary = source.get("summary")
    if isinstance(summary, Mapping):
        teams = _clean_int(summary.get("teams_count") or summary.get("total_teams"))
        if teams is not None:
            return teams
    breakdowns = _source_breakdowns(source)
    entities = breakdowns.get("entities")
    if isinstance(entities, Sequence) and not isinstance(entities, (str, bytes)):
        total = 0
        found = False
        for entity in entities:
            if isinstance(entity, Mapping):
                count = _clean_int(entity.get("teams_count"))
                if count is not None:
                    total += count
                    found = True
        if found:
            return total
    return None


def _source_required_documents(source: Mapping[str, Any]) -> tuple[str, ...]:
    if source.get("required_documents"):
        return _clean_sequence(source.get("required_documents"))
    compliance = source.get("compliance")
    if isinstance(compliance, Mapping):
        docs = compliance.get("required_documents") or compliance.get("documents")
        if docs:
            return _clean_sequence(docs)
    return ()


def _source_eligibility_rules(source: Mapping[str, Any]) -> tuple[str, ...]:
    if source.get("eligibility_rules"):
        return _clean_sequence(source.get("eligibility_rules"))
    rules = source.get("rules")
    if rules:
        return _clean_sequence(rules)
    compliance = source.get("compliance")
    if isinstance(compliance, Mapping) and compliance.get("eligibility_rules"):
        return _clean_sequence(compliance.get("eligibility_rules"))
    return ()


def _source_finance_baseline(source: Mapping[str, Any]) -> tuple[str, ...]:
    if source.get("finance_baseline"):
        return _clean_sequence(source.get("finance_baseline"))
    finance = source.get("finance") or source.get("finance_bridge")
    if isinstance(finance, Mapping):
        baseline = finance.get("baseline") or finance.get("rules")
        if baseline:
            return _clean_sequence(baseline)
    return ()


def _source_phases(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_phases = source.get("phases") or source.get("fases")
    phases: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list_of_mappings(raw_phases), start=1):
        activities = []
        for activity_index, activity in enumerate(
            _as_list_of_mappings(item.get("activities") or item.get("actividades")),
            start=1,
        ):
            activities.append(
                {
                    "activity_id": _clean_text(activity.get("activity_id")) or f"activity_{activity_index}",
                    "name": _clean_text(activity.get("name") or activity.get("nombre")),
                    "owner": _clean_optional_text(activity.get("owner") or activity.get("responsable")),
                    "due_date": _clean_optional_text(activity.get("due_date") or activity.get("fecha_limite")),
                    "evidence_required": _clean_sequence(
                        activity.get("evidence_required") or activity.get("evidencia_requerida")
                    ),
                }
            )
        phases.append(
            {
                "phase_id": _clean_text(item.get("phase_id")) or f"phase_{index}",
                "name": _clean_text(item.get("name") or item.get("nombre")),
                "start_date": _clean_optional_text(item.get("start_date") or item.get("fecha_inicio")),
                "end_date": _clean_optional_text(item.get("end_date") or item.get("fecha_fin")),
                "activities": activities,
            }
        )
    if phases:
        return phases
    operations = source.get("operations")
    if isinstance(operations, Mapping):
        matches = operations.get("matches")
        phase_names = _list_names(matches, "phase", "fase")
        return [
            {
                "phase_id": f"phase_{index}",
                "name": name,
                "start_date": None,
                "end_date": None,
                "activities": [
                    {
                        "activity_id": "review_phase_plan",
                        "name": f"Revisar plan operativo de {name}",
                        "owner": None,
                        "due_date": None,
                    }
                ],
            }
            for index, name in enumerate(phase_names, start=1)
        ]
    return []


def build_soul_wizard_clone_payload(
    source: Mapping[str, Any],
    *,
    overrides: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Clone a source tournament/SOUL shape into an inert wizard draft.

    The clone copies only planning context. It never writes to the tournament
    catalog and intentionally leaves dates/owners as validation findings when
    the source does not prove them.
    """

    overrides = overrides or {}
    tournament = _source_tournament(source)
    source_id = _clean_optional_text(
        tournament.get("id")
        or tournament.get("tournament_id")
        or source.get("source_tournament_id")
    )
    source_hash = _clean_optional_text(
        source.get("snapshot_hash")
        or source.get("source_snapshot_id")
        or source.get("work_product_hash")
    )
    source_name = _clean_text(
        tournament.get("name") or tournament.get("tournament_name") or source.get("tournament_name")
    )
    payload = {
        "draft_id": _clean_text(overrides.get("draft_id")) or "soul_wizard_clone_draft",
        "tournament_name": overrides.get("tournament_name") or source_name,
        "edition_year": overrides.get("edition_year") or overrides.get("edicion") or source.get("edition_year") or source.get("edicion"),
        "categories": overrides.get("categories") or _source_categories(source),
        "branches": overrides.get("branches") or _source_branches(source),
        "expected_entities": overrides.get("expected_entities") or _source_entities(source),
        "expected_teams": overrides.get("expected_teams") or _source_expected_teams(source),
        "required_documents": overrides.get("required_documents") or _source_required_documents(source),
        "eligibility_rules": overrides.get("eligibility_rules") or _source_eligibility_rules(source),
        "finance_baseline": overrides.get("finance_baseline") or _source_finance_baseline(source),
        "source_tournament_id": overrides.get("source_tournament_id") or source_id,
        "source_snapshot_id": overrides.get("source_snapshot_id") or source_hash,
        "phases": overrides.get("phases") or _source_phases(source),
    }
    result = build_soul_wizard_payload(payload)
    result["clone"] = {
        "source_bound": bool(source_id or source_hash or source_name),
        "source_tournament_id": source_id,
        "source_snapshot_id": source_hash,
        "source_tournament_name": source_name or None,
        "overrides_applied": sorted(str(key) for key in overrides.keys()),
        "execution_status": EXECUTION_STATUS,
        "operational_writes_allowed": False,
    }
    return result


def _form_phases(form: Mapping[str, Any]) -> list[dict[str, Any]]:
    phases: list[dict[str, Any]] = []
    for index in range(1, 7):
        name = _clean_text(form.get(f"phase_{index}_name"))
        start_date = _clean_optional_text(form.get(f"phase_{index}_start_date"))
        end_date = _clean_optional_text(form.get(f"phase_{index}_end_date"))
        activities = _activities_from_text(form.get(f"phase_{index}_activities"))
        if not (name or start_date or end_date or activities):
            continue
        phases.append(
            {
                "phase_id": f"phase_{index}",
                "name": name,
                "start_date": start_date,
                "end_date": end_date,
                "activities": activities,
            }
        )
    return phases


def _form_base_payload(form: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "draft_id": _clean_text(form.get("draft_id")) or "soul_wizard_ui_draft",
        "tournament_name": form.get("tournament_name"),
        "edition_year": form.get("edition_year"),
        "categories": form.get("categories_text"),
        "branches": form.get("branches_text"),
        "expected_entities": form.get("expected_entities_text"),
        "expected_teams": form.get("expected_teams"),
        "required_documents": form.get("required_documents_text"),
        "eligibility_rules": form.get("eligibility_rules_text"),
        "finance_baseline": form.get("finance_baseline_text"),
        "source_tournament_id": form.get("source_tournament_id"),
        "source_snapshot_id": form.get("source_snapshot_id"),
        "phases": _form_phases(form),
    }


def _non_empty_overrides(payload: Mapping[str, Any]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "draft_id" and isinstance(value, str) and value.strip() and value != "soul_wizard_ui_draft":
            overrides[key] = value
        elif isinstance(value, str) and value.strip():
            overrides[key] = value
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and value:
            overrides[key] = value
        elif value is not None and not isinstance(value, (str, Sequence)):
            overrides[key] = value
    return overrides


def build_soul_wizard_payload_from_form(form: Mapping[str, Any]) -> dict[str, Any]:
    """Build a wizard payload from simple HTML form fields.

    Phase fields follow the pattern phase_{n}_name, phase_{n}_start_date,
    phase_{n}_end_date and phase_{n}_activities. Activities are entered one
    per line as: activity name | owner | due date. If source_snapshot_json is
    supplied, the form produces a clone payload and applies non-empty fields as
    overrides.
    """

    payload = _form_base_payload(form)
    source_json = _clean_text(form.get("source_snapshot_json"))
    if not source_json:
        return build_soul_wizard_payload(payload)
    try:
        source = json.loads(source_json)
    except json.JSONDecodeError as exc:
        result = build_soul_wizard_payload(payload)
        result["clone"] = {
            "source_bound": False,
            "error": f"Invalid source snapshot JSON: {exc.msg}",
            "execution_status": EXECUTION_STATUS,
            "operational_writes_allowed": False,
        }
        return result
    if not isinstance(source, Mapping):
        result = build_soul_wizard_payload(payload)
        result["clone"] = {
            "source_bound": False,
            "error": "Source snapshot JSON must be an object.",
            "execution_status": EXECUTION_STATUS,
            "operational_writes_allowed": False,
        }
        return result
    return build_soul_wizard_clone_payload(
        source,
        overrides=_non_empty_overrides(payload),
    )


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
    "build_soul_wizard_clone_payload",
    "build_soul_wizard_contract",
    "build_soul_wizard_draft",
    "build_soul_wizard_payload",
    "build_soul_wizard_payload_from_form",
    "validate_soul_wizard_draft",
]
