"""Governed registry for SamChat specialist preview tasks."""

from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

ENABLED = "enabled"
DISABLED = "disabled"
DEPRECATED = "deprecated"
ROUTABLE_STATUSES = {ENABLED}


@dataclass(frozen=True)
class SpecialistTaskRegistration:
    task_id: str
    title: str
    agent_type: str
    case_type: str
    status: str
    version: str
    tags: Tuple[str, ...]
    required_any: Tuple[str, ...]
    signals: Tuple[str, ...]
    min_signal_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        payload["required_any"] = list(self.required_any)
        payload["signals"] = list(self.signals)
        return payload

    @property
    def routable(self) -> bool:
        return self.status in ROUTABLE_STATUSES


def _reg(
    *,
    task_id: str,
    title: str,
    agent_type: str,
    case_type: str,
    tags: Sequence[str],
    required_any: Sequence[str],
    signals: Sequence[str],
    status: str = ENABLED,
    version: str = "v1",
    min_signal_count: int = 1,
) -> SpecialistTaskRegistration:
    return SpecialistTaskRegistration(
        task_id=task_id,
        title=title,
        agent_type=agent_type,
        case_type=case_type,
        status=status,
        version=version,
        tags=tuple(tags),
        required_any=tuple(required_any),
        signals=tuple(signals),
        min_signal_count=min_signal_count,
    )


def build_specialist_task_registry() -> Tuple[SpecialistTaskRegistration, ...]:
    return (
        _reg(
            task_id="SAMCHAT-CXC-COLLECTION-001",
            title="CxC collection preview",
            agent_type="finance",
            case_type="money_request",
            tags=("finance", "cxc", "cfdi", "cash_flow"),
            required_any=("cxc", "cuentas por cobrar", "cobro", "cobranza", "factura emitida", "669dbf39"),
            signals=("bimbo", "dcc", "dcc nacional", "factura", "cfdi", "1150-001-001", "4100-001-004", "cobrar"),
        ),
        _reg(
            task_id="SAMCHAT-FIN-AMEX-001",
            title="AMEX expense reconciliation preview",
            agent_type="finance",
            case_type="expense_report",
            tags=("finance", "amex", "expense_report"),
            required_any=("amex", "american express", "tarjeta corporativa", "referencia 28", "ref 28"),
            signals=("odilon", "fgv", "45007", "comprobacion", "comprobar", "estado de cuenta", "tarifa aerea"),
        ),
        _reg(
            task_id="SAMCHAT-SUPPLIER-HOTEL-001",
            title="Supplier lodging tax precedent preview",
            agent_type="finance",
            case_type="supplier",
            tags=("supplier", "lodging_tax", "precedent"),
            required_any=("hospedaje", "ish", "impuesto sobre hospedaje", "hotel"),
            signals=("leon", "128", "impuesto local", "cfdi", "cuenta contable"),
        ),
        _reg(
            task_id="SAMCHAT-TEAM-REG-001",
            title="Team registration preview",
            agent_type="operations",
            case_type="team",
            tags=("operations", "team", "registration"),
            required_any=("equipo", "cedula", "registro", "roster", "aguiluchos"),
            signals=("jugadores", "juvenil", "tercera pagina", "pagina tres", "copa telmex"),
        ),
        _reg(
            task_id="SAMCHAT-PLAYER-ELIG-001",
            title="Player eligibility preview",
            agent_type="operations",
            case_type="player_validation",
            tags=("player", "eligibility", "no_guessing"),
            required_any=("jugador", "elegibilidad", "axel"),
            signals=("curp", "documentos", "fecha de nacimiento", "soto ramirez", "validacion"),
        ),
        _reg(
            task_id="SAMCHAT-DOC-INCIDENT-001",
            title="Document incident preview",
            agent_type="operations",
            case_type="document_incident",
            tags=("document_incident", "curp", "human_review"),
            required_any=("incidente", "curp duplicada", "duplicidad", "documental"),
            signals=("curp", "documento", "revision humana", "excepcion"),
        ),
        _reg(
            task_id="SAMCHAT-MONEY-REQ-001",
            title="Money request/reimbursement preview",
            agent_type="finance",
            case_type="money_request",
            tags=("finance", "money_request", "reimbursement"),
            required_any=("solicitud", "s-2600071", "reembolso", "referencia 9", "ref 9"),
            signals=("bibiana", "628", "deudores", "transferencia", "referencia operaciones"),
        ),
        _reg(
            task_id="SAMCHAT-BUDGET-2027-001",
            title="Budget 2027 reforecast preview",
            agent_type="budget",
            case_type="budget",
            tags=("budget", "reforecast", "2027"),
            required_any=("presupuesto", "budget", "reforecast"),
            signals=("2027", "historico", "anual", "dcc", "4100-001-004"),
        ),
        _reg(
            task_id="SAMCHAT-TOURNAMENT-2027-001",
            title="Tournament 2027 setup preview",
            agent_type="tournament_operations",
            case_type="tournament",
            tags=("tournament", "operations", "soul_wizard"),
            required_any=("crear torneo", "torneo 2027", "copa telmex 2027", "sub-17", "sub 17"),
            signals=("torneo", "categoria", "copa telmex", "2026", "nuevo"),
        ),
        _reg(
            task_id="SAMCHAT-OWNER-DCC-001",
            title="Owner pack DCC entity-folder preview",
            agent_type="institutional_knowledge",
            case_type="tournament",
            tags=("owner_pack", "tournament", "cxc"),
            required_any=("dueno pack", "owner pack", "carpeta", "entidad"),
            signals=("dcc", "bimbo", "torneo", "direccion", "finanzas"),
        ),
    )


def specialist_task_ids(*, include_disabled: bool = False) -> Tuple[str, ...]:
    return tuple(
        item.task_id
        for item in build_specialist_task_registry()
        if include_disabled or item.routable
    )


def get_specialist_task_registration(
    task_id: str,
) -> Optional[SpecialistTaskRegistration]:
    target = str(task_id or "").upper()
    for item in build_specialist_task_registry():
        if item.task_id == target:
            return item
    return None


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    normalized_terms = tuple(_normalize_text(term) for term in terms)
    return any(term in text for term in normalized_terms)


def route_specialist_task_from_text(
    normalized_text: str,
    *,
    registry: Iterable[SpecialistTaskRegistration] | None = None,
) -> Optional[str]:
    normalized_text = _normalize_text(normalized_text)
    selected = tuple(registry or build_specialist_task_registry())
    required_matches = []
    for item in selected:
        if item.routable and _contains_any(normalized_text, item.required_any):
            required_matches.append(item)
    if len(required_matches) != 1:
        return None
    item = required_matches[0]
    signal_count = sum(
        1 for signal in item.signals if _normalize_text(signal) in normalized_text
    )
    if signal_count < item.min_signal_count:
        return None
    return item.task_id


def validate_specialist_task_registry(
    *,
    seed_task_ids: Iterable[str] | None = None,
) -> Dict[str, Any]:
    registry = build_specialist_task_registry()
    ids = [item.task_id for item in registry]
    duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
    invalid_status = sorted(
        item.task_id
        for item in registry
        if item.status not in {ENABLED, DISABLED, DEPRECATED}
    )
    missing_route_hints = sorted(
        item.task_id
        for item in registry
        if item.routable and (not item.required_any or not item.signals)
    )
    seed_ids = set(seed_task_ids or ())
    registry_ids = set(ids)
    missing_from_registry = sorted(seed_ids - registry_ids)
    extra_in_registry = sorted(registry_ids - seed_ids) if seed_ids else []
    errors = duplicates + invalid_status + missing_route_hints + missing_from_registry + extra_in_registry
    return {
        "registry_id": "samchat_specialist_task_registry_v1",
        "authority": "read_only_registry",
        "status": "valid" if not errors else "invalid",
        "total": len(registry),
        "enabled": sum(1 for item in registry if item.status == ENABLED),
        "disabled": sum(1 for item in registry if item.status == DISABLED),
        "deprecated": sum(1 for item in registry if item.status == DEPRECATED),
        "duplicates": duplicates,
        "invalid_status": invalid_status,
        "missing_route_hints": missing_route_hints,
        "missing_from_registry": missing_from_registry,
        "extra_in_registry": extra_in_registry,
    }


def build_specialist_task_registry_report(
    *,
    seed_task_ids: Iterable[str] | None = None,
) -> Dict[str, Any]:
    registry = build_specialist_task_registry()
    validation = validate_specialist_task_registry(seed_task_ids=seed_task_ids)
    return {
        **validation,
        "tasks": [item.to_dict() for item in registry],
        "routable_task_ids": list(specialist_task_ids()),
    }
