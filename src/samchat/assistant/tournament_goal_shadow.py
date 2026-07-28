"""Pure, inert work product for creating a tournament from a base tournament.

This module deliberately has no persistence, network, ORM, or action-router
dependencies.  It turns an observed tournament snapshot into a deterministic
proposal that can be stored by the AnalystCase versioning layer.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


CONTRACT_VERSION = "tournament_goal_shadow_v1"
EXECUTION_STATUS = "not_executed"
SOURCE_NAMESPACE = "gastos.tournaments"
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ALLOWED_VISIBILITY_AREAS = (
    "Finanzas",
    "Mercadotecnia",
    "Operaciones",
    "Dirección",
)

_DRAFT_FIELDS: Tuple[str, ...] = (
    "name",
    "description",
    "active",
    "display_order",
    "accounting_account",
    "stages",
    "categories",
    "visibility_areas",
)

_FIELD_LABELS = {
    "name": "Nombre",
    "description": "Descripción",
    "active": "Estado activo",
    "display_order": "Orden de despliegue",
    "accounting_account": "Cuenta contable relacionada",
    "stages": "Etapas",
    "categories": "Categorías",
    "visibility_areas": "Áreas con visibilidad",
}


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
    raise TypeError(f"Unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return the stable JSON representation used by all shadow hashes."""

    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_sha256(value: Any) -> str:
    """Hash a contract value after canonical serialization."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _clean_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _clean_sequence(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Iterable[Any] = value.splitlines()
    elif isinstance(value, Sequence):
        values = value
    else:
        raise TypeError("Tournament list fields must be sequences of text")
    return tuple(str(item).strip() for item in values)


def _pick(payload: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return default


@dataclass(frozen=True)
class TournamentSnapshot:
    """Bounded, immutable evidence copied from the canonical local tournament."""

    tournament_id: str
    name: str
    description: Optional[str] = None
    active: bool = True
    display_order: int = 0
    accounting_account: Optional[str] = None
    stages: Tuple[str, ...] = ()
    categories: Tuple[str, ...] = ()
    visibility_areas: Tuple[str, ...] = ()
    unavailable_components: Tuple[str, ...] = ()
    updated_at: Optional[str] = None
    source_namespace: str = SOURCE_NAMESPACE
    source_authority_hash: Optional[str] = None

    def __post_init__(self) -> None:
        authority_hash = _clean_optional_text(self.source_authority_hash)
        if authority_hash is not None:
            normalized = authority_hash.casefold()
            if not SHA256_PATTERN.fullmatch(normalized):
                raise ValueError(
                    "Source authority hash must be sha256 followed by 64 hex digits"
                )
            object.__setattr__(self, "source_authority_hash", normalized)

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        source_namespace: str = SOURCE_NAMESPACE,
    ) -> "TournamentSnapshot":
        tournament_id = str(_pick(payload, "id", "tournament_id", default="")).strip()
        name = str(_pick(payload, "name", "nombre", default="")).strip()
        if not tournament_id:
            raise ValueError("Base tournament id is required")
        if not name:
            raise ValueError("Base tournament name is required")
        return cls(
            tournament_id=tournament_id,
            name=name,
            description=_clean_optional_text(
                _pick(payload, "description", "descripcion")
            ),
            active=bool(_pick(payload, "active", "activo", default=True)),
            display_order=int(
                _pick(payload, "display_order", "orden_despliegue", default=0) or 0
            ),
            accounting_account=_clean_optional_text(
                _pick(
                    payload,
                    "accounting_account",
                    "cuenta_contable_relacionada",
                )
            ),
            stages=_clean_sequence(_pick(payload, "stages", "etapas")),
            categories=_clean_sequence(_pick(payload, "categories", "categorias")),
            visibility_areas=_clean_sequence(
                _pick(payload, "visibility_areas", "form_visibility_areas")
            ),
            unavailable_components=tuple(
                sorted(set(_clean_sequence(payload.get("unavailable_components"))))
            ),
            updated_at=_clean_optional_text(payload.get("updated_at")),
            source_namespace=str(source_namespace or SOURCE_NAMESPACE).strip()
            or SOURCE_NAMESPACE,
            source_authority_hash=_clean_optional_text(
                _pick(payload, "source_authority_hash", "source_hash")
            ),
        )

    @property
    def snapshot_hash(self) -> str:
        if self.source_authority_hash:
            return self.source_authority_hash
        payload = asdict(self)
        payload.pop("source_authority_hash", None)
        return f"sha256:{canonical_sha256(payload)}"

    def to_dict(self) -> Dict[str, Any]:
        payload = _json_value(self)
        payload["snapshot_hash"] = self.snapshot_hash
        return payload


@dataclass(frozen=True)
class TournamentDraft:
    """Inert candidate configuration; it is never an operational tournament."""

    base_tournament_id: str
    base_snapshot_hash: str
    name: str
    description: Optional[str] = None
    active: bool = True
    display_order: int = 0
    accounting_account: Optional[str] = None
    stages: Tuple[str, ...] = ()
    categories: Tuple[str, ...] = ()
    visibility_areas: Tuple[str, ...] = ()
    execution_status: str = EXECUTION_STATUS
    operational_writes_allowed: bool = False
    schema_version: str = CONTRACT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(self)

    @property
    def draft_hash(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    severity: str
    field: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(self)


@dataclass(frozen=True)
class TournamentValidation:
    findings: Tuple[ValidationFinding, ...] = ()

    @property
    def valid(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "findings": [item.to_dict() for item in self.findings],
        }


@dataclass(frozen=True)
class BusinessDiffEntry:
    field: str
    label: str
    change_type: str
    before: Any
    after: Any

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(self)


@dataclass(frozen=True)
class TournamentBusinessDiff:
    entries: Tuple[BusinessDiffEntry, ...]
    base_snapshot_hash: str
    draft_hash: str

    @property
    def change_count(self) -> int:
        return len(self.entries)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "change_count": self.change_count,
            "entries": [item.to_dict() for item in self.entries],
            "base_snapshot_hash": self.base_snapshot_hash,
            "draft_hash": self.draft_hash,
        }


@dataclass(frozen=True)
class TournamentPlanStep:
    step_id: str
    title: str
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(self)


@dataclass(frozen=True)
class TournamentGoalPlan:
    goal: str
    base_tournament_id: str
    steps: Tuple[TournamentPlanStep, ...]
    execution_status: str = EXECUTION_STATUS
    operational_writes_allowed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(self)


@dataclass(frozen=True)
class TournamentGoalShadow:
    """Complete stage-052 answer contract suitable for one case version."""

    plan: TournamentGoalPlan
    source: TournamentSnapshot
    draft: TournamentDraft
    validation: TournamentValidation
    business_diff: TournamentBusinessDiff
    missing_information: Tuple[str, ...] = ()
    contract_version: str = CONTRACT_VERSION
    execution_status: str = EXECUTION_STATUS
    operational_writes_allowed: bool = False
    blocked_capabilities: Tuple[str, ...] = (
        "operational_writes",
        "route_execution",
        "external_notifications",
    )

    @property
    def work_product_hash(self) -> str:
        return canonical_sha256(self._payload(include_hash=False))

    def _payload(self, *, include_hash: bool) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "contract_version": self.contract_version,
            "execution_status": self.execution_status,
            "operational_writes_allowed": self.operational_writes_allowed,
            "blocked_capabilities": list(self.blocked_capabilities),
            "plan": self.plan.to_dict(),
            "source": self.source.to_dict(),
            "draft": self.draft.to_dict(),
            "validation": self.validation.to_dict(),
            "business_diff": self.business_diff.to_dict(),
            "missing_information": list(self.missing_information),
        }
        if include_hash:
            payload["work_product_hash"] = self.work_product_hash
        return payload

    def to_dict(self) -> Dict[str, Any]:
        return self._payload(include_hash=True)


def clone_tournament_draft(
    source: TournamentSnapshot,
    *,
    requested_name: str,
    overrides: Optional[Mapping[str, Any]] = None,
) -> TournamentDraft:
    """Clone only approved configuration fields into an inert candidate."""

    values: Dict[str, Any] = {
        "name": str(requested_name or "").strip(),
        "description": source.description,
        "active": source.active,
        "display_order": source.display_order,
        "accounting_account": source.accounting_account,
        "stages": source.stages,
        "categories": source.categories,
        "visibility_areas": source.visibility_areas,
    }
    supplied = dict(overrides or {})
    unknown = sorted(set(supplied) - set(_DRAFT_FIELDS))
    if unknown:
        raise ValueError(f"Unsupported tournament draft fields: {', '.join(unknown)}")
    values.update(supplied)
    return TournamentDraft(
        base_tournament_id=source.tournament_id,
        base_snapshot_hash=source.snapshot_hash,
        name=str(values["name"] or "").strip(),
        description=_clean_optional_text(values["description"]),
        active=bool(values["active"]),
        display_order=int(values["display_order"] or 0),
        accounting_account=_clean_optional_text(values["accounting_account"]),
        stages=_clean_sequence(values["stages"]),
        categories=_clean_sequence(values["categories"]),
        visibility_areas=_clean_sequence(values["visibility_areas"]),
    )


def _duplicate_findings(
    field_name: str, values: Sequence[str]
) -> List[ValidationFinding]:
    findings: List[ValidationFinding] = []
    if any(not value for value in values):
        findings.append(
            ValidationFinding(
                code=f"{field_name}_blank_value",
                severity="error",
                field=field_name,
                message=f"{_FIELD_LABELS[field_name]} contiene un valor vacío.",
            )
        )
    normalized = [value.casefold() for value in values if value]
    if len(normalized) != len(set(normalized)):
        findings.append(
            ValidationFinding(
                code=f"{field_name}_duplicate_value",
                severity="error",
                field=field_name,
                message=f"{_FIELD_LABELS[field_name]} contiene valores duplicados.",
            )
        )
    return findings


def validate_tournament_draft(
    source: TournamentSnapshot,
    draft: TournamentDraft,
) -> TournamentValidation:
    findings: List[ValidationFinding] = []
    if draft.base_tournament_id != source.tournament_id:
        findings.append(
            ValidationFinding(
                "base_tournament_mismatch",
                "error",
                "base_tournament_id",
                "El borrador no corresponde al torneo base observado.",
            )
        )
    if draft.base_snapshot_hash != source.snapshot_hash:
        findings.append(
            ValidationFinding(
                "base_snapshot_stale",
                "error",
                "base_snapshot_hash",
                "El torneo base cambió desde que se creó el borrador.",
            )
        )
    if not draft.name:
        findings.append(
            ValidationFinding(
                "name_required", "error", "name", "El nuevo torneo requiere nombre."
            )
        )
    elif len(draft.name) > 200:
        findings.append(
            ValidationFinding(
                "name_too_long",
                "error",
                "name",
                "El nombre excede 200 caracteres.",
            )
        )
    elif draft.name.casefold() == source.name.casefold():
        findings.append(
            ValidationFinding(
                "name_matches_base",
                "error",
                "name",
                "El nuevo torneo debe distinguirse del torneo base.",
            )
        )
    if draft.description is not None and len(draft.description) > 500:
        findings.append(
            ValidationFinding(
                "description_too_long",
                "error",
                "description",
                "La descripción excede 500 caracteres.",
            )
        )
    if draft.display_order < 0:
        findings.append(
            ValidationFinding(
                "display_order_negative",
                "error",
                "display_order",
                "El orden de despliegue no puede ser negativo.",
            )
        )
    for field_name in ("stages", "categories", "visibility_areas"):
        findings.extend(_duplicate_findings(field_name, getattr(draft, field_name)))
    allowed_visibility = {value.casefold(): value for value in ALLOWED_VISIBILITY_AREAS}
    invalid_visibility = sorted(
        {
            value
            for value in draft.visibility_areas
            if value and value.casefold() not in allowed_visibility
        },
        key=str.casefold,
    )
    if invalid_visibility:
        findings.append(
            ValidationFinding(
                "visibility_area_invalid",
                "error",
                "visibility_areas",
                "Áreas no permitidas: " + ", ".join(invalid_visibility) + ".",
            )
        )
    if not draft.stages:
        findings.append(
            ValidationFinding(
                "stages_missing",
                "warning",
                "stages",
                "El borrador no define etapas explícitas.",
            )
        )
    if not draft.categories:
        findings.append(
            ValidationFinding(
                "categories_missing",
                "warning",
                "categories",
                "El borrador no define categorías explícitas.",
            )
        )
    if draft.execution_status != EXECUTION_STATUS or draft.operational_writes_allowed:
        findings.append(
            ValidationFinding(
                "shadow_authority_violation",
                "error",
                "execution_status",
                "El stage shadow no puede declarar ejecución ni writes operativos.",
            )
        )
    for component in source.unavailable_components:
        findings.append(
            ValidationFinding(
                "source_component_unavailable",
                "warning",
                f"source.{component}",
                f"La fuente local no expone todavía el componente {component}.",
            )
        )
    return TournamentValidation(findings=tuple(findings))


def _change_type(before: Any, after: Any) -> str:
    empty_values = (None, "", (), [])
    if before in empty_values and after not in empty_values:
        return "added"
    if before not in empty_values and after in empty_values:
        return "removed"
    return "changed"


def build_tournament_business_diff(
    source: TournamentSnapshot,
    draft: TournamentDraft,
) -> TournamentBusinessDiff:
    entries: List[BusinessDiffEntry] = []
    for field_name in _DRAFT_FIELDS:
        before = getattr(source, field_name)
        after = getattr(draft, field_name)
        if before == after:
            continue
        entries.append(
            BusinessDiffEntry(
                field=field_name,
                label=_FIELD_LABELS[field_name],
                change_type=_change_type(before, after),
                before=_json_value(before),
                after=_json_value(after),
            )
        )
    return TournamentBusinessDiff(
        entries=tuple(entries),
        base_snapshot_hash=source.snapshot_hash,
        draft_hash=draft.draft_hash,
    )


def _build_plan(
    *,
    goal: str,
    source: TournamentSnapshot,
    valid: bool,
    waiting_input: bool,
) -> TournamentGoalPlan:
    if waiting_input:
        final_status = "waiting_input"
    else:
        final_status = "pending" if valid else "blocked"
    return TournamentGoalPlan(
        goal=str(goal or "").strip()
        or f"Crear un torneo nuevo tomando {source.name} como base",
        base_tournament_id=source.tournament_id,
        steps=(
            TournamentPlanStep("inspect_base", "Inspeccionar torneo base", "completed"),
            TournamentPlanStep("clone_draft", "Construir borrador inerte", "completed"),
            TournamentPlanStep(
                "validate_draft", "Validar reglas del borrador", "completed"
            ),
            TournamentPlanStep(
                "preview_business_diff",
                "Mostrar diferencias empresariales",
                "completed",
            ),
            TournamentPlanStep("await_review", "Esperar revisión humana", final_status),
        ),
    )


def build_tournament_goal_shadow(
    source: TournamentSnapshot,
    *,
    requested_name: str,
    overrides: Optional[Mapping[str, Any]] = None,
    goal: str = "",
    additional_findings: Iterable[ValidationFinding] = (),
) -> TournamentGoalShadow:
    """Build the complete deterministic stage-052 work product.

    ``additional_findings`` lets an orchestration service contribute facts that
    require external reads (for example, local database name uniqueness) while
    this module remains pure and unaware of persistence.
    """

    draft = clone_tournament_draft(
        source,
        requested_name=requested_name,
        overrides=overrides,
    )
    base_validation = validate_tournament_draft(source, draft)
    external_findings = tuple(additional_findings)
    for finding in external_findings:
        if not isinstance(finding, ValidationFinding):
            raise TypeError("Additional findings must be ValidationFinding values")
        if finding.severity not in {"error", "warning"}:
            raise ValueError("Additional finding severity must be error or warning")
    validation = TournamentValidation(
        findings=base_validation.findings + external_findings
    )
    diff = build_tournament_business_diff(source, draft)
    missing_items = [
        finding.field for finding in validation.findings if finding.severity == "error"
    ]
    missing_items.extend(
        f"source_component:{component}" for component in source.unavailable_components
    )
    missing = tuple(dict.fromkeys(missing_items))
    waiting_input = bool(source.unavailable_components)
    return TournamentGoalShadow(
        plan=_build_plan(
            goal=goal,
            source=source,
            valid=validation.valid,
            waiting_input=waiting_input,
        ),
        source=source,
        draft=draft,
        validation=validation,
        business_diff=diff,
        missing_information=missing,
    )
