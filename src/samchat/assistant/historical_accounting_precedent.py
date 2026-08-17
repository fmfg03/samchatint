"""Read-only historical accounting precedent query for SamChat.

The assistant may use this artifact to answer "what did we do before in similar
accounting cases?". It deliberately returns precedent candidates, not an account
assignment. Human/finance authority remains responsible for choosing and posting
accounts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .business_diff_preview import NOT_EXECUTED

HISTORICAL_ACCOUNTING_PRECEDENT_ONLY = "historical_accounting_precedent_only"


@dataclass(frozen=True)
class HistoricalAccountingPrecedentCandidate:
    account_code: str
    account_name: str
    match_count: int
    policy_count: int
    debit_total: float
    credit_total: float
    last_policy_date: Optional[str] = None
    sample_concepts: list[str] = field(default_factory=list)
    sample_policies: list[str] = field(default_factory=list)
    evidence_paths: list[str] = field(default_factory=list)
    confidence: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HistoricalAccountingPrecedentReport:
    report_id: str
    status: str
    headline: str
    summary: str
    query: str
    company_code: str
    fiscal_year: Optional[int]
    source_summary: dict[str, Any]
    candidates: list[HistoricalAccountingPrecedentCandidate] = field(default_factory=list)
    non_claims: list[str] = field(default_factory=list)
    recommended_next_steps: list[str] = field(default_factory=list)
    safety_summary: dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = HISTORICAL_ACCOUNTING_PRECEDENT_ONLY

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidates"] = [item.to_dict() for item in self.candidates]
        return payload


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _company_code(value: Any) -> str:
    raw = _safe_str(value) or "01"
    return raw.zfill(2) if raw.isdigit() else raw


def _tokens(value: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in value.replace("/", " ").replace("-", " ").split():
        token = raw.strip().casefold()
        if len(token) < 3 or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result[:8]


def _row_value(row: Any, name: str, index: int, default: Any = None) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    try:
        return row[index]
    except (TypeError, IndexError, KeyError):
        return default


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [_safe_str(value)] if _safe_str(value) else []
    if isinstance(value, (list, tuple, set)):
        return [_safe_str(item) for item in value if _safe_str(item)]
    return [_safe_str(value)] if _safe_str(value) else []


def _confidence(match_count: int, policy_count: int) -> str:
    if policy_count >= 5 and match_count >= 8:
        return "high"
    if policy_count >= 2 or match_count >= 3:
        return "medium"
    return "low"


def _empty_report(
    *,
    status: str,
    query: str,
    company_code: str,
    fiscal_year: Optional[int],
    reason: str,
) -> HistoricalAccountingPrecedentReport:
    return HistoricalAccountingPrecedentReport(
        report_id="historical_accounting_precedent_v1",
        status=status,
        headline="Sin precedente contable hist?rico suficiente",
        summary=reason,
        query=query,
        company_code=company_code,
        fiscal_year=fiscal_year,
        source_summary={"source": "historical_accounting_tables", "reason": reason},
        candidates=[],
        non_claims=[
            "No asigna cuenta contable por inferencia.",
            "No crea ni modifica gastos, polizas, catalogos o partidas.",
            "Sin evidencia historica suficiente, el asistente debe decir que no sabe.",
        ],
        recommended_next_steps=[
            "Verificar que existan importaciones historicas aplicadas para la empresa/anio.",
            "Ampliar o ajustar la busqueda con proveedor, concepto, proyecto o cuenta.",
        ],
        safety_summary={
            "read_only": True,
            "writes_enabled": False,
            "account_assignment_performed": False,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=HISTORICAL_ACCOUNTING_PRECEDENT_ONLY,
    )


async def _latest_historical_run(
    session: AsyncSession,
    *,
    company_code: str,
    fiscal_year: Optional[int],
) -> tuple[Optional[str], Optional[int], Optional[str]]:
    year_clause = "AND hs.fiscal_year = :fiscal_year" if fiscal_year else ""
    params: dict[str, Any] = {"company_code": company_code}
    if fiscal_year:
        params["fiscal_year"] = int(fiscal_year)
    result = await session.execute(
        text(
            f"""
            SELECT air.id, MAX(hs.fiscal_year) AS fiscal_year, MAX(hs.company_label) AS company_label
            FROM accounting_import_runs air
            JOIN historical_accounting_source_files hs ON hs.import_run_id = air.id
            WHERE air.source_type = 'historical_accounting'
              AND hs.company_code = :company_code
              AND air.mode = 'apply'
              AND air.status = 'completed'
              {year_clause}
            GROUP BY air.id, air.finished_at, air.started_at
            ORDER BY MAX(hs.fiscal_year) DESC, air.finished_at DESC NULLS LAST, air.started_at DESC NULLS LAST
            LIMIT 1
            """
        ),
        params,
    )
    row = result.first()
    if not row:
        return None, fiscal_year, None
    return _safe_str(_row_value(row, "id", 0)) or None, _safe_int(_row_value(row, "fiscal_year", 1)), _safe_str(_row_value(row, "company_label", 2)) or None


async def query_historical_accounting_precedents(
    session: AsyncSession,
    *,
    query: str,
    company_code: str = "01",
    fiscal_year: Optional[int] = None,
    account_code: Optional[str] = None,
    limit: int = 5,
) -> HistoricalAccountingPrecedentReport:
    """Return source-backed historical account candidates without assigning them."""

    normalized_company = _company_code(company_code)
    search = _safe_str(query)
    selected_account = _safe_str(account_code)
    if not search and not selected_account:
        return _empty_report(
            status="invalid_query",
            query=search,
            company_code=normalized_company,
            fiscal_year=fiscal_year,
            reason="Se requiere query o account_code para buscar precedentes.",
        )
    if not hasattr(session, "execute"):
        return _empty_report(
            status="data_source_unavailable",
            query=search,
            company_code=normalized_company,
            fiscal_year=fiscal_year,
            reason="La sesion no permite lecturas SQL.",
        )

    import_run_id, resolved_year, company_label = await _latest_historical_run(
        session,
        company_code=normalized_company,
        fiscal_year=fiscal_year,
    )
    if not import_run_id:
        return _empty_report(
            status="no_historical_source",
            query=search,
            company_code=normalized_company,
            fiscal_year=fiscal_year,
            reason="No hay importacion historica aplicada para esa empresa/anio.",
        )

    token_values = _tokens(search)
    search_clauses: list[str] = []
    params: dict[str, Any] = {
        "import_run_id": import_run_id,
        "company_code": normalized_company,
        "limit": max(1, min(int(limit or 5), 20)),
    }
    if selected_account:
        search_clauses.append("l.account_code_raw = :account_code")
        params["account_code"] = selected_account
    for index, token in enumerate(token_values):
        key = f"token_{index}"
        params[key] = f"%{token}%"
        search_clauses.append(
            f"""
            (
              lower(coalesce(l.account_code_raw, '')) LIKE :{key}
              OR lower(coalesce(l.account_name_raw, '')) LIKE :{key}
              OR lower(coalesce(l.line_concept, '')) LIKE :{key}
              OR lower(coalesce(l.counterparty_raw, '')) LIKE :{key}
              OR lower(coalesce(l.project_raw, '')) LIKE :{key}
              OR lower(coalesce(h.concept_raw, '')) LIKE :{key}
            )
            """
        )
    where_search = " AND (" + " OR ".join(search_clauses) + ")" if search_clauses else ""
    result = await session.execute(
        text(
            f"""
            SELECT
              l.account_code_raw AS account_code,
              MAX(l.account_name_raw) AS account_name,
              COUNT(*) AS match_count,
              COUNT(DISTINCT h.policy_id_natural) AS policy_count,
              ROUND(COALESCE(SUM(l.debit_amount), 0)::numeric, 2) AS debit_total,
              ROUND(COALESCE(SUM(l.credit_amount), 0)::numeric, 2) AS credit_total,
              MAX(h.policy_date) AS last_policy_date,
              ARRAY_REMOVE(ARRAY_AGG(DISTINCT COALESCE(NULLIF(l.line_concept, ''), h.concept_raw))[1:5], NULL) AS sample_concepts,
              ARRAY_AGG(DISTINCT h.policy_id_natural)[1:5] AS sample_policies
            FROM historical_policy_lines l
            JOIN historical_policy_headers h ON h.id = l.policy_header_id
            WHERE l.import_run_id = :import_run_id
              AND l.company_code = :company_code
              {where_search}
            GROUP BY l.account_code_raw
            ORDER BY COUNT(DISTINCT h.policy_id_natural) DESC, COUNT(*) DESC, l.account_code_raw
            LIMIT :limit
            """
        ),
        params,
    )
    rows = list(result)
    candidates = []
    for row in rows:
        match_count = _safe_int(_row_value(row, "match_count", 2))
        policy_count = _safe_int(_row_value(row, "policy_count", 3))
        account = _safe_str(_row_value(row, "account_code", 0))
        last_date = _row_value(row, "last_policy_date", 6)
        last_policy_date = last_date.isoformat() if hasattr(last_date, "isoformat") else (_safe_str(last_date) or None)
        candidates.append(
            HistoricalAccountingPrecedentCandidate(
                account_code=account,
                account_name=_safe_str(_row_value(row, "account_name", 1)),
                match_count=match_count,
                policy_count=policy_count,
                debit_total=_safe_float(_row_value(row, "debit_total", 4)),
                credit_total=_safe_float(_row_value(row, "credit_total", 5)),
                last_policy_date=last_policy_date,
                sample_concepts=_list_value(_row_value(row, "sample_concepts", 7)),
                sample_policies=_list_value(_row_value(row, "sample_policies", 8)),
                evidence_paths=[
                    "historical_policy_lines",
                    "historical_policy_headers",
                    f"import_run:{import_run_id}",
                    f"company:{normalized_company}",
                ],
                confidence=_confidence(match_count, policy_count),
            )
        )

    if not candidates:
        return _empty_report(
            status="no_precedent_found",
            query=search,
            company_code=normalized_company,
            fiscal_year=resolved_year or fiscal_year,
            reason="No se encontraron movimientos historicos que coincidan con la busqueda.",
        )

    top = candidates[0]
    return HistoricalAccountingPrecedentReport(
        report_id="historical_accounting_precedent_v1",
        status="precedents_found",
        headline="Precedentes contables historicos encontrados",
        summary=(
            f"Se encontraron {len(candidates)} cuenta(s) historicas candidatas; "
            f"la mas frecuente fue {top.account_code} - {top.account_name}."
        ),
        query=search,
        company_code=normalized_company,
        fiscal_year=resolved_year or fiscal_year,
        source_summary={
            "source": "historical_accounting_tables",
            "import_run_id": import_run_id,
            "company_code": normalized_company,
            "company_label": company_label,
            "fiscal_year": resolved_year or fiscal_year,
            "tokens": token_values,
            "account_code_filter": selected_account or None,
        },
        candidates=candidates,
        non_claims=[
            "No asigna automaticamente la cuenta contable.",
            "No reemplaza la revision de Finanzas/Contabilidad.",
            "No crea ni modifica gastos, polizas, catalogos o partidas.",
        ],
        recommended_next_steps=[
            "Comparar el concepto actual contra los sample_concepts y polizas historicas.",
            "Si el precedente coincide, usarlo como evidencia para la prepoliza; si no, pedir criterio contable.",
        ],
        safety_summary={
            "read_only": True,
            "writes_enabled": False,
            "account_assignment_performed": False,
            "approval_required_for_accounting_change": True,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=HISTORICAL_ACCOUNTING_PRECEDENT_ONLY,
    )


__all__ = [
    "HISTORICAL_ACCOUNTING_PRECEDENT_ONLY",
    "HistoricalAccountingPrecedentCandidate",
    "HistoricalAccountingPrecedentReport",
    "query_historical_accounting_precedents",
]
