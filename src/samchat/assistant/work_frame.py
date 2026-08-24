"""Assistant work-frame classifier.

This module is the first slice of the Claude-Code-like runtime contract. It does
not execute tools and it does not decide business truth. It converts a raw user
message into a stable, testable description of the business job SamChat must do.

The important distinction is semantic: tools are selected later as candidates,
but the work frame records what the user actually asked for and what would count
as sufficient evidence.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


READ_ONLY_BOUNDARY = "read_only"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class WorkFrame:
    frame_id: str
    user_message: str
    interpreted_goal: str
    audience: str
    domain: str
    task_kind: str
    confidence: float
    explicit_entities: tuple[str, ...] = ()
    temporal_scope: Mapping[str, Any] = field(default_factory=dict)
    required_evidence: tuple[str, ...] = ()
    forbidden_interpretations: tuple[str, ...] = ()
    answer_contract: Mapping[str, Any] = field(default_factory=dict)
    authority_boundary: str = READ_ONLY_BOUNDARY
    needs_clarification: bool = False
    clarification_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["explicit_entities"] = list(self.explicit_entities)
        payload["required_evidence"] = list(self.required_evidence)
        payload["forbidden_interpretations"] = list(self.forbidden_interpretations)
        return payload


def normalize_work_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _frame_id(text: str) -> str:
    return f"wf_{uuid.uuid5(uuid.NAMESPACE_URL, text or '').hex[:16]}"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _question_like(text: str) -> bool:
    return _contains_any(
        text,
        (
            "que",
            "cuanto",
            "cuantos",
            "cuanta",
            "cuantas",
            "cual",
            "cuales",
            "quien",
            "quienes",
            "cuando",
            "donde",
            "porque",
            "por que",
            "tenemos",
            "hay",
            "falta",
            "faltan",
        ),
    )


def _temporal_scope(text: str) -> dict[str, Any]:
    scope: dict[str, Any] = {}
    month_aliases = {
        "enero": "01",
        "febrero": "02",
        "marzo": "03",
        "abril": "04",
        "mayo": "05",
        "junio": "06",
        "julio": "07",
        "agosto": "08",
        "septiembre": "09",
        "setiembre": "09",
        "octubre": "10",
        "noviembre": "11",
        "diciembre": "12",
    }
    for name, month in month_aliases.items():
        if name in text:
            scope["month"] = month
            scope["month_label"] = name
            break
    year = re.search(r"\b(20\d{2})\b", text)
    if year:
        scope["year"] = int(year.group(1))
    if "hoy" in text:
        scope["relative"] = "today"
    elif "esta semana" in text:
        scope["relative"] = "this_week"
    elif "este mes" in text:
        scope["relative"] = "this_month"
    return scope


def _entities(text: str) -> tuple[str, ...]:
    entities: list[str] = []
    known = {
        "copa telmex": "Copa Telmex",
        "liga telmex": "Liga Telmex",
        "beisbol": "Liga Telmex Telcel de Beisbol",
        "bÃ©isbol": "Liga Telmex Telcel de Beisbol",
        "dcc": "De la Calle a la Cancha",
        "jalisco": "Jalisco",
        "odilon": "Odilon",
        "odilÃ³n": "Odilon",
        "benjamin": "Benjamin",
        "benjamÃ­n": "Benjamin",
        "juan pablo": "Juan Pablo",
        "alicia": "Alicia",
        "bibiana": "Bibiana",
        "carlos": "Carlos",
    }
    for needle, label in known.items():
        if needle in text and label not in entities:
            entities.append(label)
    return tuple(entities)


def _answer_contract(*, audience: str, domain: str, task_kind: str) -> dict[str, Any]:
    return {
        "style": "executive" if audience in {"owner", "finance", "admin"} else "operational",
        "must_include": ["direct_answer", "evidence_or_gap", "next_step", "authority_boundary"],
        "must_not_include": ["raw_tool_payload", "unsupported_people", "unsupported_dates", "unsupported_amounts"],
        "domain": domain,
        "task_kind": task_kind,
    }


def build_work_frame(raw_message: str) -> WorkFrame:
    """Classify a SamChat user turn into a stable business-work frame."""

    message = raw_message or ""
    text = normalize_work_text(message)
    temporal = _temporal_scope(text)
    entities = _entities(text)

    owner_terms = (
        "dueno",
        "director general",
        "direccion general",
        "owner pack",
        "pack del dueno",
        "carpeta",
        "entidad",
        "torneo",
        "equipos",
        "jugadores",
        "fase nacional",
        "fase estatal",
        "uniformes",
        "visitas",
        "activacion",
        "patrocinador",
    )
    finance_terms = (
        "contabilidad",
        "contable",
        "coi",
        "poliza",
        "polizas",
        "cfdi",
        "cfdis",
        "factura",
        "facturas",
        "payment run",
        "programacion de pagos",
        "amex",
        "diot",
        "banco",
        "bancos",
        "cash flow",
        "cxc",
        "cxp",
    )
    payment_terms = ("pago", "pagos", "apoyo", "apoyos", "transferido", "transferencia")
    historical_payment_terms = (
        "evidencia de pagos",
        "evidencia de apoyos",
        "pagos hechos",
        "pagos realizados",
        "pagos efectuados",
        "pagos pagados",
        "pagos o apoyos",
    )
    pending_payment_terms = (
        "pagos pendientes",
        "pendientes de pago",
        "pendientes",
        "por pagar",
        "vencen",
        "vence",
        "payment run",
        "programacion de pagos",
    )

    if _contains_any(text, historical_payment_terms):
        return WorkFrame(
            frame_id=_frame_id(message),
            user_message=message,
            interpreted_goal="Find supported evidence of payments or aids already made.",
            audience="owner" if _contains_any(text, owner_terms) else "finance",
            domain="owner" if not _contains_any(text, finance_terms) else "mixed",
            task_kind="evidence",
            confidence=0.92,
            explicit_entities=entities,
            temporal_scope=temporal,
            required_evidence=("payment_receipts", "paid_requests", "operator_support_transfers"),
            forbidden_interpretations=("pending_payment_queue", "zero_pending_payments_as_evidence"),
            answer_contract=_answer_contract(audience="owner", domain="owner", task_kind="evidence"),
        )

    if _contains_any(text, pending_payment_terms):
        return WorkFrame(
            frame_id=_frame_id(message),
            user_message=message,
            interpreted_goal="Report payments that are pending, due, or in payment-run workflow.",
            audience="finance",
            domain="finance",
            task_kind="status",
            confidence=0.9,
            explicit_entities=entities,
            temporal_scope=temporal,
            required_evidence=("pending_payment_queue", "document_status", "payment_due_dates"),
            forbidden_interpretations=("historical_payment_evidence",),
            answer_contract=_answer_contract(audience="finance", domain="finance", task_kind="status"),
        )

    if _contains_any(text, ("que falta", "faltantes", "que tan listo", "listo", "preparado", "datos para el dueno", "datos para dueno")) and _contains_any(text, owner_terms):
        return WorkFrame(
            frame_id=_frame_id(message),
            user_message=message,
            interpreted_goal="Assess Owner Pack readiness and list supported coverage and missing information.",
            audience="owner",
            domain="owner",
            task_kind="readiness",
            confidence=0.9,
            explicit_entities=entities,
            temporal_scope=temporal,
            required_evidence=("owner_pack_inventory", "owner_pack_live_evidence", "soul_coverage"),
            forbidden_interpretations=("single_variable_answer", "raw_dashboard_payload"),
            answer_contract=_answer_contract(audience="owner", domain="owner", task_kind="readiness"),
        )

    if _contains_any(text, finance_terms) and _question_like(text):
        if _contains_any(text, ("cerrar", "cierre", "bloquea", "bloquean", "no puedo cerrar")):
            kind = "diagnostic"
            required = ("closeout_diagnostics", "unbalanced_policies", "unmatched_bank_movements")
        elif _contains_any(text, ("cargada", "cargado", "tenemos", "hay", "estado", "status")):
            kind = "status"
            required = ("finance_platform_snapshot", "accounting_loaded_status")
        else:
            kind = "evidence"
            required = ("finance_platform_snapshot", "linked_documents", "accounting_artifacts")
        return WorkFrame(
            frame_id=_frame_id(message),
            user_message=message,
            interpreted_goal="Answer a finance/accounting question from read-only operational evidence.",
            audience="finance",
            domain="finance",
            task_kind=kind,
            confidence=0.84,
            explicit_entities=entities,
            temporal_scope=temporal,
            required_evidence=required,
            forbidden_interpretations=("owner_pack_readiness", "unsupported_accounting_claim"),
            answer_contract=_answer_contract(audience="finance", domain="finance", task_kind=kind),
        )

    if _contains_any(text, owner_terms) and _question_like(text):
        return WorkFrame(
            frame_id=_frame_id(message),
            user_message=message,
            interpreted_goal="Answer a concrete Owner Pack variable from supported operational evidence.",
            audience="owner",
            domain="owner",
            task_kind="evidence",
            confidence=0.78,
            explicit_entities=entities,
            temporal_scope=temporal,
            required_evidence=("owner_variable_source", "live_evidence_or_missing_reason"),
            forbidden_interpretations=("invented_person", "invented_amount", "invented_date"),
            answer_contract=_answer_contract(audience="owner", domain="owner", task_kind="evidence"),
        )

    if _contains_any(text, ("soul", "fases", "fechas", "actividades por fase")):
        return WorkFrame(
            frame_id=_frame_id(message),
            user_message=message,
            interpreted_goal="Inspect SOUL coverage for tournament operational context.",
            audience="operator",
            domain="operations",
            task_kind="data_coverage",
            confidence=0.82,
            explicit_entities=entities,
            temporal_scope=temporal,
            required_evidence=("soul_snapshot", "phase_dates", "phase_activities"),
            forbidden_interpretations=("claim_complete_tournament_without_soul",),
            answer_contract=_answer_contract(audience="operator", domain="operations", task_kind="data_coverage"),
        )

    return WorkFrame(
        frame_id=_frame_id(message),
        user_message=message,
        interpreted_goal="Unclear business request; classify before selecting tools.",
        audience=UNKNOWN,
        domain=UNKNOWN,
        task_kind=UNKNOWN,
        confidence=0.0,
        explicit_entities=entities,
        temporal_scope=temporal,
        required_evidence=(),
        forbidden_interpretations=("guessing", "unsupported_execution"),
        answer_contract=_answer_contract(audience=UNKNOWN, domain=UNKNOWN, task_kind=UNKNOWN),
        needs_clarification=True,
        clarification_reason="no_stable_business_work_frame",
    )
