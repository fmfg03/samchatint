from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_REFERENCE_DATE = date(2026, 7, 14)
STALE_EVIDENCE_DAYS = 180

AMOUNT_KEYS = (
    "amount",
    "monto",
    "total",
    "importe",
    "subtotal",
    "paid_amount",
    "budget_amount",
)

DATE_KEYS = (
    "date",
    "fecha",
    "payment_date",
    "cfdi_date",
    "invoice_date",
)

CONCEPT_KEYS = (
    "concept",
    "concepto",
    "expense_id",
    "cfdi_uuid",
    "payment_id",
    "budget_id",
)


@dataclass(frozen=True)
class EvidenceQualityDiagnostic:
    diagnostic_id: str
    diagnostic_type: str
    severity: str
    source_type: str
    source: str
    source_id: str
    label: str
    reason: str
    fields: Dict[str, Any] = field(default_factory=dict)
    blocks_conclusion: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceQualityResult:
    evidence_quality_status: str
    freshness_diagnostics: List[Dict[str, Any]]
    conflict_diagnostics: List[Dict[str, Any]]
    blocking_conflicts: List[Dict[str, Any]]
    missing_critical_sources: List[Dict[str, Any]]
    safe_to_conclude: bool
    recommended_next_questions: List[str]
    caveats: List[str]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize(value: Any) -> str:
    return _compact(value).lower()


def _field(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _metadata(item: Any) -> Dict[str, Any]:
    metadata = _field(item, "metadata", {}) or {}
    return dict(metadata) if isinstance(metadata, dict) else {}


def _source_type(item: Any) -> str:
    return _compact(_field(item, "source_type", "unknown")) or "unknown"


def _source_id(item: Any) -> str:
    return _compact(_field(item, "source_id", ""))


def _label(item: Any) -> str:
    return _compact(_field(item, "label", "")) or _source_type(item)


def _source(item: Any) -> str:
    return _compact(_field(item, "source", ""))


def _summary(item: Any) -> str:
    return _compact(_field(item, "summary", ""))


def _diagnostic(
    *,
    diagnostic_type: str,
    severity: str,
    item: Any,
    reason: str,
    fields: Optional[Dict[str, Any]] = None,
    blocks_conclusion: bool = False,
) -> EvidenceQualityDiagnostic:
    source_type = _source_type(item)
    source_id = _source_id(item)
    label = _label(item)
    stable_source = _source(item)
    key = "|".join(
        (
            diagnostic_type,
            source_type,
            source_id,
            label,
            reason,
        )
    )
    diagnostic_id = re.sub(r"[^a-z0-9_]+", "_", key.lower()).strip("_")
    return EvidenceQualityDiagnostic(
        diagnostic_id=diagnostic_id[:160],
        diagnostic_type=diagnostic_type,
        severity=severity,
        source_type=source_type,
        source=stable_source,
        source_id=source_id,
        label=label,
        reason=reason,
        fields=dict(fields or {}),
        blocks_conclusion=blocks_conclusion,
    )


def _parse_amount(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _amount(item: Any) -> Tuple[Optional[Decimal], Optional[str]]:
    metadata = _metadata(item)
    for key in AMOUNT_KEYS:
        if key in metadata:
            parsed = _parse_amount(metadata.get(key))
            if parsed is not None:
                return parsed, key
    return None, None


def _parse_date(value: Any) -> Optional[date]:
    text = _compact(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _evidence_date(item: Any) -> Tuple[Optional[date], Optional[str]]:
    direct = _parse_date(_field(item, "date", ""))
    if direct is not None:
        return direct, "date"
    metadata = _metadata(item)
    for key in DATE_KEYS:
        if key in metadata:
            parsed = _parse_date(metadata.get(key))
            if parsed is not None:
                return parsed, key
    return None, None


def _concept_key(item: Any) -> str:
    metadata = _metadata(item)
    for key in CONCEPT_KEYS:
        value = _compact(metadata.get(key))
        if value:
            return f"{key}:{value.lower()}"
    source_id = _source_id(item)
    if source_id:
        return f"source_id:{source_id.lower()}"
    label = re.sub(r"\W+", "_", _label(item).lower()).strip("_")
    return f"label:{label or _source_type(item)}"


def _duplicate_key(item: Any) -> str:
    source_id = _source_id(item)
    if source_id:
        return f"{_source_type(item)}|{source_id.lower()}"
    amount, _amount_key = _amount(item)
    item_date, _date_key = _evidence_date(item)
    return "|".join(
        (
            _source_type(item),
            _label(item).lower(),
            item_date.isoformat() if item_date else "",
            str(amount) if amount is not None else "",
        )
    )


def _intent_text(intent: Any) -> str:
    raw = _field(intent, "raw_text", "")
    analyst_intent = _field(intent, "analyst_intent", "")
    requirements = " ".join(_field(intent, "context_requirements", []) or [])
    return _normalize(f"{raw} {analyst_intent} {requirements}")


def _critical_sources_for_intent(intent: Any) -> List[Tuple[str, str, str]]:
    text = _intent_text(intent)
    required: List[Tuple[str, str, str]] = []
    signals = (
        (
            ("cfdi", "factura", "facturas"),
            "cfdi_document",
            "CFDI o factura relacionada",
        ),
        (
            ("pago", "pagos", "reembolso", "conciliacion", "conciliación"),
            "registered_payment",
            "evidencia de pago registrada",
        ),
        (
            ("presupuesto", "desviacion", "desviación", "budget"),
            "budget",
            "presupuesto aplicable",
        ),
        (
            ("gasto", "gastos", "comprobar", "expense"),
            "expense",
            "registro de gasto",
        ),
        (
            ("proveedor", "vendor"),
            "vendor",
            "proveedor relacionado",
        ),
        (
            ("documento", "contrato", "anexo", "evidencia documental"),
            "document_evidence",
            "documento o evidencia documental",
        ),
    )
    for tokens, source_type, label in signals:
        if any(token in text for token in tokens):
            required.append(
                (source_type, label, f"intent_signal:{source_type}")
            )
    return required


def _source_types(evidence: Iterable[Any]) -> set:
    return {_source_type(item) for item in evidence}


def _has_required_source(source_types: set, required_source: str) -> bool:
    aliases = {
        "document_evidence": {
            "document_evidence",
            "uploaded_file",
            "document_intake",
            "inline_context",
            "conversation",
            "report_result",
        },
        "cfdi_document": {"cfdi_document"},
        "registered_payment": {"registered_payment"},
        "budget": {"budget"},
        "expense": {"expense"},
        "vendor": {"vendor"},
    }
    required = aliases.get(required_source, {required_source})
    return bool(source_types.intersection(required))


def _question_for_missing_source(source_type: str, label: str) -> str:
    if source_type == "cfdi_document":
        return "¿Puedes compartir el CFDI o factura relacionada?"
    if source_type == "registered_payment":
        return "¿Puedes compartir la evidencia de pago registrada?"
    if source_type == "budget":
        return "¿Qué presupuesto debo usar para comparar contra lo real?"
    if source_type == "expense":
        return "¿Cuál es el registro de gasto que debo revisar?"
    if source_type == "vendor":
        return "¿Qué proveedor debo considerar en el análisis?"
    return f"¿Puedes compartir {label}?"


def _dedupe_text(items: Iterable[str]) -> List[str]:
    deduped: List[str] = []
    seen: set = set()
    for item in items:
        compact = _compact(item)
        if not compact:
            continue
        key = compact.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(compact)
    return deduped


def _status(
    *,
    has_conflict: bool,
    missing: List[Dict[str, Any]],
    stale: List[Dict[str, Any]],
    partial: bool,
    evidence_count: int,
) -> str:
    if has_conflict:
        return "conflicting"
    if missing:
        return "missing_critical_sources"
    if not evidence_count:
        return "insufficient"
    if partial:
        return "partial"
    if stale:
        return "stale"
    return "sufficient"


def evaluate_evidence_quality(
    *,
    intent: Any,
    evidence: Iterable[Any],
    coverage_level: str,
    coverage_reasons: Optional[Iterable[str]] = None,
    reference_date: Optional[date] = None,
) -> EvidenceQualityResult:
    evidence_items = list(evidence)
    reference = reference_date or DEFAULT_REFERENCE_DATE
    reasons = set(coverage_reasons or [])
    freshness: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    questions: List[str] = []
    caveats: List[str] = []

    for item in evidence_items:
        item_freshness = _normalize(_field(item, "freshness", ""))
        item_date, date_key = _evidence_date(item)
        if item_freshness == "stale":
            freshness.append(
                _diagnostic(
                    diagnostic_type="stale_evidence",
                    severity="warning",
                    item=item,
                    reason="freshness_flag_stale",
                    fields={"freshness": item_freshness},
                ).to_dict()
            )
            continue
        if item_date is not None:
            age_days = (reference - item_date).days
            if age_days > STALE_EVIDENCE_DAYS:
                freshness.append(
                    _diagnostic(
                        diagnostic_type="stale_evidence",
                        severity="warning",
                        item=item,
                        reason="evidence_date_older_than_threshold",
                        fields={
                            "date": item_date.isoformat(),
                            "date_key": date_key,
                            "age_days": age_days,
                            "threshold_days": STALE_EVIDENCE_DAYS,
                        },
                    ).to_dict()
                )

    grouped_amounts: Dict[str, List[Tuple[Any, Decimal, str]]] = {}
    grouped_dates: Dict[str, List[Tuple[Any, date, str]]] = {}
    for item in evidence_items:
        concept = _concept_key(item)
        amount, amount_key = _amount(item)
        if amount is not None:
            grouped_amounts.setdefault(concept, []).append(
                (item, amount, amount_key or "")
            )
        item_date, date_key = _evidence_date(item)
        if item_date is not None:
            grouped_dates.setdefault(concept, []).append(
                (item, item_date, date_key or "")
            )

    for concept in sorted(grouped_amounts):
        values = grouped_amounts[concept]
        unique_amounts = sorted({amount for _item, amount, _key in values})
        if len(unique_amounts) > 1:
            first_item = values[0][0]
            conflicts.append(
                _diagnostic(
                    diagnostic_type="amount_conflict",
                    severity="blocking",
                    item=first_item,
                    reason="same_concept_has_incompatible_amounts",
                    fields={
                        "concept_key": concept,
                        "amounts": [str(amount) for amount in unique_amounts],
                        "sources": [
                            {
                                "source_type": _source_type(item),
                                "source_id": _source_id(item),
                                "label": _label(item),
                                "amount": str(amount),
                                "amount_key": amount_key,
                            }
                            for item, amount, amount_key in values
                        ],
                    },
                    blocks_conclusion=True,
                ).to_dict()
            )

    for concept in sorted(grouped_dates):
        values = grouped_dates[concept]
        unique_dates = sorted({item_date for _item, item_date, _key in values})
        if len(unique_dates) > 1:
            first_item = values[0][0]
            conflicts.append(
                _diagnostic(
                    diagnostic_type="date_conflict",
                    severity="blocking",
                    item=first_item,
                    reason="same_concept_has_incompatible_dates",
                    fields={
                        "concept_key": concept,
                        "dates": [
                            item_date.isoformat()
                            for item_date in unique_dates
                        ],
                        "sources": [
                            {
                                "source_type": _source_type(item),
                                "source_id": _source_id(item),
                                "label": _label(item),
                                "date": item_date.isoformat(),
                                "date_key": date_key,
                            }
                            for item, item_date, date_key in values
                        ],
                    },
                    blocks_conclusion=True,
                ).to_dict()
            )

    seen_duplicates: Dict[str, Any] = {}
    for item in evidence_items:
        key = _duplicate_key(item)
        if key in seen_duplicates:
            conflicts.append(
                _diagnostic(
                    diagnostic_type="duplicate_evidence",
                    severity="warning",
                    item=item,
                    reason="duplicate_evidence_key",
                    fields={
                        "duplicate_key": key,
                        "first_label": _label(seen_duplicates[key]),
                        "duplicate_label": _label(item),
                    },
                ).to_dict()
            )
            continue
        seen_duplicates[key] = item

    source_types = _source_types(evidence_items)
    missing_sources: List[Dict[str, Any]] = []
    if evidence_items:
        for source_type, label, reason in _critical_sources_for_intent(intent):
            if _has_required_source(source_types, source_type):
                continue
            missing_sources.append(
                {
                    "source_type": source_type,
                    "label": label,
                    "reason": reason,
                    "blocks_conclusion": True,
                }
            )
            questions.append(_question_for_missing_source(source_type, label))

    partial = (
        coverage_level in {"none", "low"}
        or bool(
            reasons.intersection(
                {"no_evidence", "low_relevance", "clipped_evidence"}
            )
        )
        or any(not _summary(item) for item in evidence_items)
    )
    if partial:
        caveats.append(
            "La evidencia disponible es parcial o de baja cobertura."
        )
        if evidence_items and coverage_level in {"none", "low"}:
            questions.append(
                "¿Puedes compartir la fuente completa o confirmar estos "
                "hallazgos?"
            )
    if freshness:
        caveats.append(
            "Hay evidencia vieja o sin frescura suficiente; úsala con cautela."
        )
    blocking_conflicts = [
        diagnostic
        for diagnostic in conflicts
        if bool(diagnostic.get("blocks_conclusion"))
    ]
    if blocking_conflicts:
        caveats.append(
            "Hay evidencia contradictoria; no es seguro emitir una "
            "conclusión final."
        )
        questions.append(
            "¿Qué fuente debe prevalecer para resolver la contradicción?"
        )
    if missing_sources:
        caveats.append(
            "Falta una fuente crítica para sostener una conclusión definitiva."
        )

    safe_to_conclude = (
        bool(evidence_items) and not blocking_conflicts and not missing_sources
    )
    return EvidenceQualityResult(
        evidence_quality_status=_status(
            has_conflict=bool(blocking_conflicts),
            missing=missing_sources,
            stale=freshness,
            partial=partial,
            evidence_count=len(evidence_items),
        ),
        freshness_diagnostics=freshness,
        conflict_diagnostics=conflicts,
        blocking_conflicts=blocking_conflicts,
        missing_critical_sources=missing_sources,
        safe_to_conclude=safe_to_conclude,
        recommended_next_questions=_dedupe_text(questions),
        caveats=_dedupe_text(caveats),
    )
