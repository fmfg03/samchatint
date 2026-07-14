from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol

from .analyst_intent import AnalystIntent
from .analyst_workbench import (
    MAX_ANALYST_EVIDENCE,
    AnalystEvidence,
    context_sufficiency_for_evidence,
    rank_analyst_evidence,
)


@dataclass(frozen=True)
class AnalystEvidenceQuery:
    intent: AnalystIntent
    question: str
    user_id: str
    role: str
    limit: int = MAX_ANALYST_EVIDENCE
    permissions: List[str] = field(default_factory=list)
    reference_date: Optional[date] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalystEvidenceAdapterResult:
    adapter_id: str
    source: str
    evidence: List[AnalystEvidence]
    coverage_level: str
    freshness: str
    permissions_applied: List[str]
    caveats: List[str]
    read_only: bool = True
    writes_supported: bool = False
    provider_called: bool = False
    actions_executed: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [item.to_dict() for item in self.evidence]
        return data


class AnalystEvidenceAdapter(Protocol):
    adapter_id: str
    source_type: str
    supports_writes: bool

    def fetch(
        self,
        query: AnalystEvidenceQuery,
        session: Optional[Any] = None,
    ) -> AnalystEvidenceAdapterResult:
        ...


class BaseReadOnlyEvidenceAdapter:
    adapter_id = "base"
    source_type = "evidence"
    source = "analyst"
    supports_writes = False
    required_permission = "analyst:evidence:read"
    empty_caveat = "No hay evidencia disponible para esta fuente."

    def fetch(
        self,
        query: AnalystEvidenceQuery,
        session: Optional[Any] = None,
    ) -> AnalystEvidenceAdapterResult:
        rows = _rows_for_source(session, self.adapter_id)
        evidence = [
            self._row_to_evidence(row, query=query)
            for row in rows[: query.limit]
        ]
        coverage = context_sufficiency_for_evidence(evidence, query.intent)
        caveats = [] if evidence else [self.empty_caveat]
        freshness = _freshness_for_rows(rows, query.reference_date)
        return AnalystEvidenceAdapterResult(
            adapter_id=self.adapter_id,
            source=self.source,
            evidence=evidence,
            coverage_level=coverage.coverage_level,
            freshness=freshness,
            permissions_applied=_permissions_applied(
                query.permissions,
                self.required_permission,
            ),
            caveats=caveats,
        )

    def _row_to_evidence(
        self,
        row: Mapping[str, Any],
        *,
        query: AnalystEvidenceQuery,
    ) -> AnalystEvidence:
        label = _compact(row.get("label") or row.get("name") or self.source)
        summary = _compact(row.get("summary") or row.get("description"))
        return AnalystEvidence(
            source_type=self.source_type,
            label=label or self.source,
            summary=summary or "Evidencia sin resumen disponible.",
            source=self.source,
            source_id=_compact(row.get("id") or row.get("source_id")),
            date=_date_text(row),
            relevance=_compact(row.get("relevance") or "medium"),
            coverage_level=_compact(row.get("coverage_level") or "medium"),
            freshness=_freshness_for_rows([row], query.reference_date),
            permissions_applied=_permissions_applied(
                query.permissions,
                self.required_permission,
            ),
            reference=_compact(row.get("reference") or row.get("url")),
            metadata=_safe_metadata(row),
        )


class ExpenseEvidenceAdapter(BaseReadOnlyEvidenceAdapter):
    adapter_id = "expenses"
    source_type = "expense"
    source = "gastos"
    required_permission = "gastos:read"
    empty_caveat = "No hay gastos disponibles para sostener el análisis."


class ExpenseAccountEvidenceAdapter(BaseReadOnlyEvidenceAdapter):
    adapter_id = "expense_accounts"
    source_type = "expense_account"
    source = "cuentas_de_gastos"
    required_permission = "cuentas_de_gastos:read"
    empty_caveat = "No hay cuentas de gastos disponibles."


class CfdiDocumentEvidenceAdapter(BaseReadOnlyEvidenceAdapter):
    adapter_id = "cfdi_documents"
    source_type = "cfdi_document"
    source = "cfdi_documentos"
    required_permission = "cfdi:read"
    empty_caveat = "No hay CFDI o documentos disponibles."


class BudgetEvidenceAdapter(BaseReadOnlyEvidenceAdapter):
    adapter_id = "budgets"
    source_type = "budget"
    source = "presupuestos"
    required_permission = "presupuestos:read"
    empty_caveat = "No hay presupuestos disponibles."


class ProjectEvidenceAdapter(BaseReadOnlyEvidenceAdapter):
    adapter_id = "projects"
    source_type = "project"
    source = "proyectos"
    required_permission = "proyectos:read"
    empty_caveat = "No hay proyectos disponibles."


class RegisteredPaymentEvidenceAdapter(BaseReadOnlyEvidenceAdapter):
    adapter_id = "registered_payments"
    source_type = "registered_payment"
    source = "pagos_registrados"
    required_permission = "pagos:read"
    empty_caveat = "No hay pagos registrados disponibles."


class VendorEvidenceAdapter(BaseReadOnlyEvidenceAdapter):
    adapter_id = "vendors"
    source_type = "vendor"
    source = "proveedores"
    required_permission = "proveedores:read"
    empty_caveat = "No hay proveedores disponibles."


class DocumentEvidenceAdapter(BaseReadOnlyEvidenceAdapter):
    adapter_id = "documents"
    source_type = "document_evidence"
    source = "evidencia_documental"
    required_permission = "documentos:read"
    empty_caveat = "No hay evidencia documental disponible."


DEFAULT_READ_ONLY_EVIDENCE_ADAPTERS: List[AnalystEvidenceAdapter] = [
    ExpenseEvidenceAdapter(),
    ExpenseAccountEvidenceAdapter(),
    CfdiDocumentEvidenceAdapter(),
    BudgetEvidenceAdapter(),
    ProjectEvidenceAdapter(),
    RegisteredPaymentEvidenceAdapter(),
    VendorEvidenceAdapter(),
    DocumentEvidenceAdapter(),
]


@dataclass(frozen=True)
class AnalystEvidenceCollection:
    evidence: List[AnalystEvidence]
    coverage_level: str
    caveats: List[str]
    adapter_results: List[AnalystEvidenceAdapterResult]
    read_only: bool = True
    writes_supported: bool = False
    provider_called: bool = False
    actions_executed: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [item.to_dict() for item in self.evidence]
        data["adapter_results"] = [
            result.to_dict() for result in self.adapter_results
        ]
        return data


def collect_read_only_analyst_evidence(
    query: AnalystEvidenceQuery,
    adapters: Iterable[AnalystEvidenceAdapter] = (
        DEFAULT_READ_ONLY_EVIDENCE_ADAPTERS
    ),
    session: Optional[Any] = None,
) -> AnalystEvidenceCollection:
    results = [adapter.fetch(query, session=session) for adapter in adapters]
    evidence = _dedupe_evidence(
        item
        for result in results
        for item in result.evidence
    )
    ranked = rank_analyst_evidence(query.intent, evidence)[: query.limit]
    coverage = context_sufficiency_for_evidence(ranked, query.intent)
    caveats = _dedupe_text(
        caveat
        for result in results
        for caveat in result.caveats
        if caveat
    )
    return AnalystEvidenceCollection(
        evidence=ranked,
        coverage_level=coverage.coverage_level,
        caveats=caveats,
        adapter_results=results,
    )


def _rows_for_source(
    session: Optional[Any],
    adapter_id: str,
) -> List[Mapping[str, Any]]:
    if session is None:
        return []
    if isinstance(session, Mapping):
        return [dict(row) for row in session.get(adapter_id, [])]
    reader = getattr(session, "read_only_rows", None)
    if callable(reader):
        return [dict(row) for row in reader(adapter_id)]
    rows = getattr(session, adapter_id, None)
    if rows is None:
        return []
    return [dict(row) for row in rows]


def _dedupe_evidence(
    evidence: Iterable[AnalystEvidence],
) -> List[AnalystEvidence]:
    deduped: List[AnalystEvidence] = []
    seen: set[str] = set()
    for item in evidence:
        key = "|".join(
            [
                item.source,
                item.source_id,
                item.label.lower(),
                item.summary[:120].lower(),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _dedupe_text(values: Iterable[str]) -> List[str]:
    output: List[str] = []
    seen: set[str] = set()
    for value in values:
        compact = _compact(value)
        key = compact.lower()
        if not compact or key in seen:
            continue
        seen.add(key)
        output.append(compact)
    return output


def _permissions_applied(
    granted: Iterable[str],
    required: str,
) -> List[str]:
    values = sorted({str(item) for item in granted if item})
    if required not in values:
        values.append(required)
    return values


def _date_text(row: Mapping[str, Any]) -> str:
    value = row.get("date") or row.get("created_at")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _compact(value)


def _freshness_for_rows(
    rows: Iterable[Mapping[str, Any]],
    reference_date: Optional[date],
) -> str:
    dates: List[date] = []
    for row in rows:
        parsed = _parse_date(_date_text(row))
        if parsed is not None:
            dates.append(parsed)
    if not dates:
        return "unknown"
    reference = reference_date or datetime.now(timezone.utc).date()
    newest = max(dates)
    age_days = (reference - newest).days
    if age_days <= 30:
        return "current"
    if age_days <= 90:
        return "recent"
    return "stale"


def _parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10]).date()
    except ValueError:
        return None


def _safe_metadata(row: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = row.get("metadata")
    if isinstance(metadata, Mapping):
        return {
            str(key): value
            for key, value in metadata.items()
            if isinstance(key, str)
        }
    return {}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
