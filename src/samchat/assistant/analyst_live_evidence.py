from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence, Set
from uuid import UUID

from sqlalchemy import String, and_, case, cast, func, or_, select, text

from devnous.gastos.models import (
    CFDIReport,
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
    ProveedorCliente,
    Tournament,
)
from devnous.gastos.services.tournament_project_visibility import (
    SCOPED_LIST_VIEW_DEPARTAMENTOS,
    canonical_departamento,
    departamento_column_matches,
)

from .analyst_evidence_adapters import (
    DEFAULT_READ_ONLY_EVIDENCE_ADAPTERS,
    AnalystEvidenceCollection,
    AnalystEvidenceQuery,
    collect_read_only_analyst_evidence,
)
from .analyst_intent import AnalystIntent, normalize_analyst_text


DEFAULT_LIVE_EVIDENCE_SOURCES = (
    "expenses",
    "expense_accounts",
    "cfdi_documents",
    "budgets",
    "projects",
    "registered_payments",
    "vendors",
    "documents",
)
LIVE_EVIDENCE_PERMISSION_BY_SOURCE = {
    "expenses": "gastos:read",
    "expense_accounts": "cuentas_de_gastos:read",
    "cfdi_documents": "cfdi:read",
    "budgets": "presupuestos:read",
    "projects": "proyectos:read",
    "registered_payments": "pagos:read",
    "vendors": "proveedores:read",
    "documents": "documentos:read",
}
LIVE_EVIDENCE_QUERY_TOKENS = {
    "expenses": ("gasto", "gastos", "viatico", "viaticos"),
    "expense_accounts": ("cuenta de gasto", "cuentas de gastos"),
    "cfdi_documents": ("cfdi", "cfdis", "factura", "facturas"),
    "budgets": ("presupuesto", "presupuestos", "budget"),
    "projects": ("proyecto", "proyectos", "torneo", "torneos"),
    "registered_payments": (
        "pago",
        "pagos",
        "pagado",
        "reembolso",
        "reembolsos",
    ),
    "vendors": ("proveedor", "proveedores"),
    "documents": (
        "documento financiero",
        "documentos financieros",
        "solicitud de pago",
        "solicitudes de pago",
    ),
}
LIVE_EVIDENCE_ENTITY_PATTERNS = {
    "expense_accounts": r"\bcuentas? de gastos?\b",
    "cfdi_documents": r"\b(?:cfdi|cfdis|factura|facturas)\b",
    "projects": r"\b(?:proyecto|proyectos|torneo|torneos)\b",
    "vendors": r"\b(?:proveedor|proveedores)\b",
    "registered_payments": (
        r"\b(?:pago|pagos|pagado|reembolso|reembolsos)\b"
    ),
    "documents": (
        r"\b(?:documento|documentos|solicitud de pago|"
        r"solicitudes de pago)\b"
    ),
}
LIVE_EVIDENCE_ENTITY_STOPWORDS = {
    "activo",
    "activos",
    "activa",
    "activas",
    "abierta",
    "abiertas",
    "aprobado",
    "aprobados",
    "caso",
    "cerrado",
    "cerrados",
    "cerrada",
    "cerradas",
    "de",
    "del",
    "documento",
    "documentos",
    "el",
    "esta",
    "este",
    "financiero",
    "financieros",
    "la",
    "pago",
    "pagos",
    "pendiente",
    "pendientes",
    "por",
    "reciente",
    "recientes",
    "solicitud",
    "solicitudes",
    "ultimo",
    "ultimos",
    "ultima",
    "ultimas",
}
LIVE_EVIDENCE_ENTITY_TERMINATORS = {
    "con",
    "contra",
    "en",
    "para",
    "segun",
    "sobre",
    "y",
    "o",
}
LIVE_EVIDENCE_BUDGET_MODIFIERS = {
    "actual",
    "actuales",
    "anual",
    "anuales",
    "disponible",
    "disponibles",
    "general",
    "generales",
    "global",
    "globales",
    "total",
    "totales",
    "vigente",
    "vigentes",
}
LIVE_EVIDENCE_PERMISSION_ALIASES = {
    "expenses": (
        "gastos:read",
        "finance.solicitudes.read",
        "finance.reimbursements.read",
        "executive.reports.read",
    ),
    "expense_accounts": (
        "cuentas_de_gastos:read",
        "finance.reimbursements.read",
    ),
    "cfdi_documents": (
        "cfdi:read",
        "finance.solicitudes.read",
        "accounting.entries.read",
        "accounting.reconciliation.read",
    ),
    "budgets": (
        "presupuestos:read",
        "budgets.read",
        "budgets.line.read",
        "executive.reports.read",
    ),
    "projects": (
        "proyectos:read",
        "operations.folders.read",
        "operations.teams.read",
        "executive.reports.read",
    ),
    "registered_payments": (
        "pagos:read",
        "finance.payments.read",
    ),
    "vendors": (
        "proveedores:read",
        "finance.solicitudes.read",
    ),
    "documents": (
        "documentos:read",
        "finance.solicitudes.read",
    ),
}
EMPLOYEE_SELF_SCOPED_SOURCES = {
    "expenses",
    "expense_accounts",
    "cfdi_documents",
    "registered_payments",
    "documents",
}
GLOBAL_READ_ROLES = {
    "coordinador",
    "finanzas",
    "admin",
    "superadmin",
    "super_admin",
}


@dataclass(frozen=True)
class LiveEvidenceContext:
    employee_id: Any
    role: str
    permissions: Set[str]
    question: str
    department: Optional[str] = None
    reference_date: Optional[date] = None
    limit_per_source: int = 8


LiveEvidenceRowsProvider = Callable[
    [LiveEvidenceContext, Set[str]],
    Awaitable[Mapping[str, Sequence[Mapping[str, Any]]]],
]


@dataclass(frozen=True)
class LiveEvidenceAcquisition:
    collection: AnalystEvidenceCollection
    enabled: bool
    attempted_sources: list[str] = field(default_factory=list)
    allowed_sources: list[str] = field(default_factory=list)
    denied_sources: list[str] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    timed_out_sources: list[str] = field(default_factory=list)
    source_counts: Dict[str, int] = field(default_factory=dict)

    def trace(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "attempted_sources": list(self.attempted_sources),
            "allowed_sources": list(self.allowed_sources),
            "denied_sources": list(self.denied_sources),
            "failed_sources": list(self.failed_sources),
            "timed_out_sources": list(self.timed_out_sources),
            "source_counts": dict(self.source_counts),
            "evidence_count": len(self.collection.evidence),
            "provider_called": self.collection.provider_called,
            "read_only": True,
            "writes_attempted": False,
        }


def live_evidence_enabled() -> bool:
    return _env_bool("ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED", False)


def configured_live_evidence_sources() -> list[str]:
    raw = os.getenv(
        "ASSISTANT_ANALYST_LIVE_EVIDENCE_SOURCES",
        ",".join(DEFAULT_LIVE_EVIDENCE_SOURCES),
    )
    output: list[str] = []
    seen: set[str] = set()
    for value in raw.split(","):
        source = value.strip().lower()
        if (
            not source
            or source not in LIVE_EVIDENCE_PERMISSION_BY_SOURCE
            or source in seen
        ):
            continue
        seen.add(source)
        output.append(source)
    return output


def live_evidence_timeout_seconds() -> float:
    return max(
        0.05,
        _env_int("ASSISTANT_ANALYST_LIVE_EVIDENCE_TIMEOUT_MS", 1200) / 1000,
    )


def live_evidence_limit_per_source() -> int:
    return max(
        1,
        min(
            25,
            _env_int("ASSISTANT_ANALYST_LIVE_EVIDENCE_LIMIT_PER_SOURCE", 8),
        ),
    )


async def acquire_live_analyst_evidence(
    *,
    context: LiveEvidenceContext,
    intent: AnalystIntent,
    rows_provider: Optional[LiveEvidenceRowsProvider],
) -> LiveEvidenceAcquisition:
    empty = _empty_collection()
    if not live_evidence_enabled():
        return LiveEvidenceAcquisition(collection=empty, enabled=False)

    requested_sources = _requested_live_evidence_sources(context.question)
    attempted = [
        source
        for source in configured_live_evidence_sources()
        if source in requested_sources
    ]
    allowed = [
        source for source in attempted if _source_access_allowed(context, source)
    ]
    denied = [source for source in attempted if source not in allowed]
    if not allowed:
        denied_caveats = (
            [
                "No se revisaron las fuentes solicitadas porque los permisos "
                "disponibles no autorizan esas lecturas."
            ]
            if denied
            else []
        )
        return LiveEvidenceAcquisition(
            collection=AnalystEvidenceCollection(
                evidence=[],
                coverage_level="none",
                caveats=denied_caveats,
                adapter_results=[],
            ),
            enabled=True,
            attempted_sources=attempted,
            allowed_sources=allowed,
            denied_sources=denied,
        )
    if rows_provider is None:
        return LiveEvidenceAcquisition(
            collection=AnalystEvidenceCollection(
                evidence=[],
                coverage_level="none",
                caveats=[
                    "La evidencia en vivo no está disponible en esta " "ejecución."
                ],
                adapter_results=[],
            ),
            enabled=True,
            attempted_sources=attempted,
            allowed_sources=allowed,
            denied_sources=denied,
            failed_sources=list(allowed),
            source_counts={source: 0 for source in allowed},
        )

    timeout = live_evidence_timeout_seconds()

    async def read_source(
        source: str,
    ) -> tuple[str, str, Sequence[Mapping[str, Any]]]:
        try:
            result = await asyncio.wait_for(
                rows_provider(context, {source}),
                timeout=timeout,
            )
            rows = [
                _sanitize_row(row)
                for row in result.get(source, [])
                if isinstance(row, Mapping)
            ][: context.limit_per_source]
            return source, "ok", rows
        except asyncio.TimeoutError:
            return source, "timeout", []
        except Exception:
            return source, "failed", []

    rows_by_source: Dict[str, Sequence[Mapping[str, Any]]] = {}
    failed: list[str] = []
    timed_out: list[str] = []
    counts: Dict[str, int] = {}
    source_results = await asyncio.gather(*(read_source(source) for source in allowed))
    for source, status, rows in source_results:
        counts[source] = len(rows)
        if status == "timeout":
            timed_out.append(source)
        elif status == "failed":
            failed.append(source)
        else:
            rows_by_source[source] = rows

    adapters = [
        adapter
        for adapter in DEFAULT_READ_ONLY_EVIDENCE_ADAPTERS
        if adapter.adapter_id in rows_by_source
    ]
    exact_permissions = [
        LIVE_EVIDENCE_PERMISSION_BY_SOURCE[source] for source in rows_by_source
    ]
    collection = collect_read_only_analyst_evidence(
        AnalystEvidenceQuery(
            intent=intent,
            question=context.question,
            user_id=str(context.employee_id or ""),
            role=context.role,
            limit=max(1, len(rows_by_source) * context.limit_per_source),
            permissions=exact_permissions,
            reference_date=context.reference_date,
        ),
        adapters=adapters,
        session=rows_by_source,
    )
    caveats: list[str] = list(collection.caveats)
    if denied:
        caveats.append(
            "La evidencia en vivo está limitada porque algunas fuentes "
            "solicitadas no están autorizadas para este usuario."
        )
    if failed or timed_out:
        caveats.append(
            "La evidencia en vivo es parcial porque algunas fuentes no "
            "respondieron de forma segura."
        )
    collection = AnalystEvidenceCollection(
        evidence=collection.evidence,
        coverage_level=collection.coverage_level,
        caveats=_dedupe_text(caveats),
        adapter_results=collection.adapter_results,
        provider_called=True,
    )
    return LiveEvidenceAcquisition(
        collection=collection,
        enabled=True,
        attempted_sources=attempted,
        allowed_sources=allowed,
        denied_sources=denied,
        failed_sources=failed,
        timed_out_sources=timed_out,
        source_counts=counts,
    )


def build_sqlalchemy_live_evidence_rows_provider(
    session: Any,
) -> LiveEvidenceRowsProvider:
    async def provider(
        context: LiveEvidenceContext,
        sources: Set[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        output: Dict[str, Sequence[Mapping[str, Any]]] = {}
        for source in sorted(sources):
            reader = _SOURCE_READERS.get(source)
            if reader is None:
                continue
            output[source] = await reader(session, context)
        return output

    return provider


def build_isolated_sqlalchemy_live_evidence_rows_provider(
    session_maker: Any,
) -> LiveEvidenceRowsProvider:
    async def provider(
        context: LiveEvidenceContext,
        sources: Set[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        async with session_maker() as session:
            reader = build_sqlalchemy_live_evidence_rows_provider(session)
            return await reader(context, sources)

    return provider


async def _read_expenses(
    session: Any,
    context: LiveEvidenceContext,
) -> list[Mapping[str, Any]]:
    stmt = select(
        ExpenseReport.id.label("id"),
        ExpenseReport.concepto.label("concept"),
        ExpenseReport.proyecto.label("project"),
        ExpenseReport.gasto_cantidad.label("amount"),
        ExpenseReport.currency.label("currency"),
        ExpenseReport.fecha.label("date"),
        ExpenseReport.estado_gasto.label("status"),
        ExpenseReport.numero_referencia.label("reference_number"),
    ).where(ExpenseReport.estado_gasto != "cancelado")
    stmt = _owner_scope(
        stmt,
        owner_column=ExpenseReport.empleado_id,
        context=context,
    )
    requested_year = _requested_year(context.question)
    if requested_year is not None:
        stmt = stmt.where(
            func.extract("year", ExpenseReport.fecha) == requested_year
        )
    requested_match = _expense_question_match(context.question)
    ordering = []
    if requested_match is not None:
        stmt = stmt.add_columns(
            case((requested_match, True), else_=False).label("requested_match")
        )
        stmt = stmt.where(requested_match)
        ordering.append(case((requested_match, 0), else_=1).asc())
    rows = await _execute_mappings(
        session,
        stmt.order_by(
            *ordering,
            ExpenseReport.fecha.desc(),
            ExpenseReport.id.asc(),
        ).limit(context.limit_per_source),
    )
    if requested_match is not None:
        rows = [item for item in rows if item.get("requested_match") is True]
    return [
        _row(
            item,
            label=_join(item.get("concept"), item.get("project")),
            summary=_summary(
                "Gasto",
                item.get("concept"),
                item.get("project"),
                _money(item.get("amount"), item.get("currency")),
                item.get("status"),
            ),
            reference=f"samchat://gastos/{item['id']}",
            metadata_keys=(
                "amount",
                "currency",
                "concept",
                "project",
                "status",
                "reference_number",
                "requested_match",
            ),
        )
        for item in rows
    ]


async def _read_expense_accounts(
    session: Any,
    context: LiveEvidenceContext,
) -> list[Mapping[str, Any]]:
    requested_match = _requested_entity_match(
        context.question,
        "expense_accounts",
        (
            CuentaDeGastos.nombre,
            CuentaDeGastos.referencia_base,
        ),
    )
    selected = [
        CuentaDeGastos.id.label("id"),
        CuentaDeGastos.nombre.label("name"),
        CuentaDeGastos.referencia_base.label("reference_number"),
        CuentaDeGastos.estado.label("status"),
        CuentaDeGastos.created_at.label("date"),
        CuentaDeGastos.currency.label("currency"),
    ]
    ordering = []
    if requested_match is not None:
        selected.append(
            case((requested_match, True), else_=False).label("requested_match")
        )
        ordering.append(case((requested_match, 0), else_=1).asc())
    stmt = select(*selected)
    stmt = _owner_scope(
        stmt,
        owner_column=CuentaDeGastos.empleado_id,
        context=context,
    )
    if requested_match is not None:
        stmt = stmt.where(requested_match)
    rows = await _execute_mappings(
        session,
        stmt.order_by(
            *ordering,
            CuentaDeGastos.created_at.desc(),
            CuentaDeGastos.id.asc(),
        ).limit(context.limit_per_source),
    )
    if requested_match is not None:
        rows = [item for item in rows if item.get("requested_match") is True]
    return [
        _row(
            item,
            label=_join(item.get("name"), item.get("reference_number")),
            summary=_summary(
                "Cuenta de gastos",
                item.get("name"),
                item.get("reference_number"),
                item.get("status"),
            ),
            reference=f"samchat://cuentas-de-gastos/{item['id']}",
            metadata_keys=(
                "currency",
                "reference_number",
                "status",
                "requested_match",
            ),
        )
        for item in rows
    ]


async def _read_cfdi_documents(
    session: Any,
    context: LiveEvidenceContext,
) -> list[Mapping[str, Any]]:
    _require_owner_identity(context)
    requested_match = _requested_entity_match(
        context.question,
        "cfdi_documents",
        (
            CFDIReport.cfdi_uuid,
            CFDIReport.descripcion_concepto_principal,
        ),
    )
    selected = [
        CFDIReport.id.label("id"),
        CFDIReport.cfdi_uuid.label("cfdi_uuid"),
        CFDIReport.descripcion_concepto_principal.label("concept"),
        CFDIReport.total.label("total"),
        CFDIReport.moneda.label("currency"),
        CFDIReport.fecha.label("date"),
    ]
    ordering = []
    if requested_match is not None:
        selected.append(
            case((requested_match, True), else_=False).label("requested_match")
        )
        ordering.append(case((requested_match, 0), else_=1).asc())
    stmt = select(*selected)
    department_scope = _department_scope(context)
    if department_scope is not None:
        owned_cfdi = (
            select(ExpenseReport.id)
            .join(Empleado, ExpenseReport.empleado_id == Empleado.id)
            .where(
                departamento_column_matches(
                    Empleado.departamento,
                    department_scope,
                ),
                or_(
                    ExpenseReport.cfdi_report_id == CFDIReport.id,
                    and_(
                        ExpenseReport.nova_request_id.isnot(None),
                        ExpenseReport.nova_request_id
                        == CFDIReport.nova_request_id,
                    ),
                ),
            )
            .exists()
        )
        owned_document = (
            select(Documento.id)
            .join(Empleado, Documento.empleado_id == Empleado.id)
            .where(
                departamento_column_matches(
                    Empleado.departamento,
                    department_scope,
                ),
                or_(
                    Documento.cfdi_report_id == CFDIReport.id,
                    and_(
                        Documento.cfdi_uuid_manual.isnot(None),
                        CFDIReport.cfdi_uuid.isnot(None),
                        func.upper(func.trim(Documento.cfdi_uuid_manual))
                        == func.upper(func.trim(CFDIReport.cfdi_uuid)),
                    ),
                ),
            )
            .exists()
        )
        stmt = stmt.where(or_(owned_cfdi, owned_document))
    elif not _has_global_visibility(context):
        owned_cfdi = (
            select(ExpenseReport.id)
            .where(
                ExpenseReport.empleado_id == context.employee_id,
                or_(
                    ExpenseReport.cfdi_report_id == CFDIReport.id,
                    and_(
                        ExpenseReport.nova_request_id.isnot(None),
                        ExpenseReport.nova_request_id == CFDIReport.nova_request_id,
                    ),
                ),
            )
            .exists()
        )
        owned_document = (
            select(Documento.id)
            .where(
                Documento.empleado_id == context.employee_id,
                or_(
                    Documento.cfdi_report_id == CFDIReport.id,
                    and_(
                        Documento.cfdi_uuid_manual.isnot(None),
                        CFDIReport.cfdi_uuid.isnot(None),
                        func.upper(func.trim(Documento.cfdi_uuid_manual))
                        == func.upper(func.trim(CFDIReport.cfdi_uuid)),
                    ),
                ),
            )
            .exists()
        )
        stmt = stmt.where(or_(owned_cfdi, owned_document))
    if requested_match is not None:
        stmt = stmt.where(requested_match)
    rows = await _execute_mappings(
        session,
        stmt.order_by(
            *ordering,
            CFDIReport.fecha.desc().nulls_last(),
            CFDIReport.id.asc(),
        ).limit(context.limit_per_source),
    )
    if requested_match is not None:
        rows = [item for item in rows if item.get("requested_match") is True]
    return [
        _row(
            item,
            label=_join("CFDI", item.get("cfdi_uuid")),
            summary=_summary(
                "CFDI",
                item.get("concept"),
                _money(item.get("total"), item.get("currency")),
            ),
            reference=f"samchat://cfdi/{item['id']}",
            metadata_keys=(
                "cfdi_uuid",
                "concept",
                "total",
                "currency",
                "requested_match",
            ),
        )
        for item in rows
    ]


async def _read_budgets(
    session: Any,
    context: LiveEvidenceContext,
) -> list[Mapping[str, Any]]:
    edition_year = _requested_budget_edition_year(context.question)
    project_tokens = _requested_budget_project_tokens(context.question)
    edition_year_filter = (
        "AND edition_year = :edition_year"
        if edition_year is not None
        else ""
    )
    project_conditions = [
        (
            "(LOWER(COALESCE(l.tournament_name, '')) "
            f"LIKE :budget_project_{index} OR "
            "LOWER(COALESCE(l.concept_name, '')) "
            f"LIKE :budget_project_{index})"
        )
        for index, _token in enumerate(project_tokens)
    ]
    project_match = " AND ".join(project_conditions)
    project_filter = f"WHERE {project_match}" if project_match else ""
    if project_match:
        requested_match_sql = (
            f"CASE WHEN {project_match} THEN TRUE ELSE FALSE END"
        )
        project_ordering = f"{requested_match_sql} DESC,"
    elif edition_year is not None:
        requested_match_sql = "TRUE"
        project_ordering = ""
    else:
        requested_match_sql = "FALSE"
        project_ordering = ""
    statement = text(
        f"""
        WITH selected_version AS (
            SELECT
                id,
                edition_year,
                version_name,
                status
            FROM budget_versions
            WHERE status IN (
                'frozen',
                'approved',
                'submitted',
                'reforecast',
                'draft',
                'closed'
            )
            {edition_year_filter}
            ORDER BY
                edition_year DESC,
                CASE status
                    WHEN 'frozen' THEN 1
                    WHEN 'approved' THEN 2
                    WHEN 'submitted' THEN 3
                    WHEN 'reforecast' THEN 4
                    WHEN 'draft' THEN 5
                    ELSE 6
                END,
                updated_at DESC,
                id ASC
            LIMIT 1
        )
        SELECT
            l.id,
            l.budget_version_id AS budget_id,
            l.concept_name AS concept,
            l.tournament_name AS project,
            l.budget_amount,
            l.reference_amount,
            l.variance_amount,
            l.updated_at AS date,
            v.version_name,
            v.edition_year,
            v.status,
            {requested_match_sql} AS requested_match
        FROM budget_lines l
        JOIN selected_version v ON v.id = l.budget_version_id
        {project_filter}
        ORDER BY
            {project_ordering}
            l.budget_amount DESC,
            l.tournament_name ASC,
            l.concept_name ASC,
            l.id ASC
        LIMIT :limit
        """
    )
    bind_params = {"limit": context.limit_per_source}
    if edition_year is not None:
        bind_params["edition_year"] = edition_year
    for index, token in enumerate(project_tokens):
        bind_params[f"budget_project_{index}"] = f"%{token}%"
    rows = await _execute_mappings(
        session,
        statement.bindparams(**bind_params),
    )
    return [
        _row(
            item,
            label=_join(item.get("concept"), item.get("project")),
            summary=_summary(
                "Línea presupuestal",
                item.get("concept"),
                item.get("project"),
                _money(item.get("budget_amount"), "MXN"),
                item.get("status"),
            ),
            reference=f"samchat://presupuestos/{item['id']}",
            metadata_keys=(
                "budget_id",
                "budget_amount",
                "reference_amount",
                "variance_amount",
                "concept",
                "project",
                "edition_year",
                "version_name",
                "status",
                "requested_match",
            ),
        )
        for item in rows
    ]


async def _read_projects(
    session: Any,
    context: LiveEvidenceContext,
) -> list[Mapping[str, Any]]:
    requested_match = _requested_entity_match(
        context.question,
        "projects",
        (Tournament.name, Tournament.description),
    )
    selected = [
        Tournament.id.label("id"),
        Tournament.name.label("name"),
        Tournament.description.label("description"),
        Tournament.active.label("active"),
        Tournament.updated_at.label("date"),
    ]
    ordering = []
    if requested_match is not None:
        selected.append(
            case((requested_match, True), else_=False).label("requested_match")
        )
        ordering.append(case((requested_match, 0), else_=1).asc())
    stmt = select(*selected).where(
        Tournament.active.is_(True),
        _project_visibility_condition(context),
    )
    if requested_match is not None:
        stmt = stmt.where(requested_match)
    rows = await _execute_mappings(
        session,
        stmt
        .order_by(
            *ordering,
            Tournament.display_order.asc(),
            Tournament.name.asc(),
            Tournament.id.asc(),
        )
        .limit(context.limit_per_source),
    )
    if requested_match is not None:
        rows = [item for item in rows if item.get("requested_match") is True]
    return [
        _row(
            item,
            label=str(item.get("name") or "Proyecto"),
            summary=_summary(
                "Proyecto activo",
                item.get("name"),
                item.get("description"),
            ),
            reference=f"samchat://proyectos/{item['id']}",
            metadata_keys=("active", "requested_match"),
        )
        for item in rows
    ]


async def _read_registered_payments(
    session: Any,
    context: LiveEvidenceContext,
) -> list[Mapping[str, Any]]:
    requested_reference_match = _requested_payment_reference_match(
        context.question
    )
    requested_match = (
        requested_reference_match
        if requested_reference_match is not None
        else _requested_entity_match(
            context.question,
            "registered_payments",
            (
                Documento.numero_referencia,
                Documento.concepto_pago,
            ),
        )
    )
    selected = [
        Documento.id.label("id"),
        Documento.numero_referencia.label("reference_number"),
        Documento.concepto_pago.label("concept"),
        func.coalesce(
            Documento.monto_total,
            Documento.monto_solicitado,
        ).label("paid_amount"),
        Documento.currency.label("currency"),
        Documento.fecha_pago.label("payment_date"),
        Documento.pagado_en.label("paid_at"),
        Documento.estado.label("status"),
    ]
    ordering = []
    if requested_match is not None:
        selected.append(
            case((requested_match, True), else_=False).label("requested_match")
        )
        ordering.append(case((requested_match, 0), else_=1).asc())
    stmt = select(*selected).where(
        Documento.tipo == "SOLICITUD",
        or_(
            Documento.estado.in_(("pagado", "cerrado")),
            and_(
                Documento.estado == "aprobado",
                Documento.pagado_en.isnot(None),
            ),
        ),
    )
    stmt = _owner_scope(
        stmt,
        owner_column=Documento.empleado_id,
        context=context,
    )
    if requested_match is not None:
        stmt = stmt.where(requested_match)
    rows = await _execute_mappings(
        session,
        stmt.order_by(
            *ordering,
            Documento.pagado_en.desc().nulls_last(),
            Documento.fecha_pago.desc().nulls_last(),
            Documento.id.asc(),
        ).limit(context.limit_per_source),
    )
    if requested_match is not None:
        rows = [item for item in rows if item.get("requested_match") is True]
    return [
        _row(
            {
                **item,
                "date": item.get("payment_date") or item.get("paid_at"),
            },
            label=_join("Pago", item.get("reference_number")),
            summary=_summary(
                "Pago registrado",
                item.get("concept"),
                _money(item.get("paid_amount"), item.get("currency")),
            ),
            reference=f"samchat://pagos/{item['id']}",
            metadata_keys=(
                "payment_id",
                "paid_amount",
                "payment_date",
                "currency",
                "concept",
                "reference_number",
                "status",
                "requested_match",
            ),
            metadata_overrides={"payment_id": item.get("id")},
        )
        for item in rows
    ]


async def _read_vendors(
    session: Any,
    context: LiveEvidenceContext,
) -> list[Mapping[str, Any]]:
    requested_match = _requested_entity_match(
        context.question,
        "vendors",
        (ProveedorCliente.nombre, ProveedorCliente.entidad_region),
    )
    selected = [
        ProveedorCliente.id.label("id"),
        ProveedorCliente.nombre.label("name"),
        ProveedorCliente.tipo.label("vendor_type"),
        ProveedorCliente.entidad_region.label("region"),
        ProveedorCliente.actualizado_en.label("date"),
    ]
    ordering = []
    if requested_match is not None:
        selected.append(
            case((requested_match, True), else_=False).label("requested_match")
        )
        ordering.append(case((requested_match, 0), else_=1).asc())
    stmt = select(*selected).where(
        ProveedorCliente.activo.is_(True),
        ProveedorCliente.tipo == "proveedor",
    )
    if requested_match is not None:
        stmt = stmt.where(requested_match)
    rows = await _execute_mappings(
        session,
        stmt
        .order_by(
            *ordering,
            ProveedorCliente.nombre.asc(),
            ProveedorCliente.id.asc(),
        )
        .limit(context.limit_per_source),
    )
    if requested_match is not None:
        rows = [item for item in rows if item.get("requested_match") is True]
    return [
        _row(
            item,
            label=str(item.get("name") or "Proveedor"),
            summary=_summary(
                "Proveedor activo",
                item.get("name"),
                item.get("region"),
            ),
            reference=f"samchat://proveedores/{item['id']}",
            metadata_keys=("vendor_type", "region", "requested_match"),
        )
        for item in rows
    ]


async def _read_documents(
    session: Any,
    context: LiveEvidenceContext,
) -> list[Mapping[str, Any]]:
    requested_match = _requested_entity_match(
        context.question,
        "documents",
        (
            Documento.numero_referencia,
            Documento.tipo,
            Documento.concepto_pago,
        ),
    )
    selected = [
        Documento.id.label("id"),
        Documento.numero_referencia.label("reference_number"),
        Documento.tipo.label("document_type"),
        Documento.estado.label("status"),
        Documento.concepto_pago.label("concept"),
        func.coalesce(
            Documento.monto_total,
            Documento.monto_solicitado,
        ).label("amount"),
        Documento.currency.label("currency"),
        Documento.creado_en.label("date"),
    ]
    ordering = []
    if requested_match is not None:
        selected.append(
            case((requested_match, True), else_=False).label("requested_match")
        )
        ordering.append(case((requested_match, 0), else_=1).asc())
    stmt = select(*selected)
    stmt = _owner_scope(
        stmt,
        owner_column=Documento.empleado_id,
        context=context,
    )
    if requested_match is not None:
        stmt = stmt.where(requested_match)
    rows = await _execute_mappings(
        session,
        stmt.order_by(
            *ordering,
            Documento.creado_en.desc(),
            Documento.id.asc(),
        ).limit(context.limit_per_source),
    )
    if requested_match is not None:
        rows = [item for item in rows if item.get("requested_match") is True]
    return [
        _row(
            item,
            label=_join(
                item.get("document_type"),
                item.get("reference_number"),
            ),
            summary=_summary(
                "Documento",
                item.get("document_type"),
                item.get("concept"),
                item.get("status"),
                _money(item.get("amount"), item.get("currency")),
            ),
            reference=f"samchat://documentos/{item['id']}",
            metadata_keys=(
                "document_type",
                "reference_number",
                "status",
                "concept",
                "amount",
                "currency",
                "requested_match",
            ),
        )
        for item in rows
    ]


_SOURCE_READERS = {
    "expenses": _read_expenses,
    "expense_accounts": _read_expense_accounts,
    "cfdi_documents": _read_cfdi_documents,
    "budgets": _read_budgets,
    "projects": _read_projects,
    "registered_payments": _read_registered_payments,
    "vendors": _read_vendors,
    "documents": _read_documents,
}


async def _execute_mappings(session: Any, stmt: Any) -> list[Dict[str, Any]]:
    with session.no_autoflush:
        result = await session.execute(stmt)
    return [dict(row) for row in result.mappings().all()]


def _owner_scope(
    stmt: Any,
    *,
    owner_column: Any,
    context: LiveEvidenceContext,
) -> Any:
    department_scope = _department_scope(context)
    if department_scope is not None:
        return stmt.join(Empleado, owner_column == Empleado.id).where(
            departamento_column_matches(
                Empleado.departamento,
                department_scope,
            )
        )
    if _has_global_visibility(context):
        return stmt
    _require_owner_identity(context)
    return stmt.where(owner_column == context.employee_id)


def _require_owner_identity(context: LiveEvidenceContext) -> None:
    if (
        _department_scope(context) is None
        and not _has_global_visibility(context)
        and context.employee_id is None
    ):
        raise PermissionError(
            "An employee identity is required for ownership-scoped evidence."
        )


def _department_scope(context: LiveEvidenceContext) -> Optional[str]:
    role = context.role.strip().lower()
    if role not in {"admin", "coordinador"}:
        return None
    department = canonical_departamento(context.department)
    if department in SCOPED_LIST_VIEW_DEPARTAMENTOS:
        return department
    return None


def _project_visibility_condition(context: LiveEvidenceContext) -> Any:
    visibility = Tournament.form_visibility_areas
    unrestricted = or_(
        visibility.is_(None),
        visibility == [],
    )
    department = canonical_departamento(context.department)
    if department is None:
        return unrestricted
    tokens = {department}
    if department == "Operaciones":
        tokens.update(("operations", "operaciones"))
    elif department == "Finanzas":
        tokens.update(("finance", "finanzas"))
    return or_(
        unrestricted,
        *(visibility.contains([token]) for token in sorted(tokens)),
    )


def _has_global_visibility(context: LiveEvidenceContext) -> bool:
    return (
        context.role.strip().lower() in GLOBAL_READ_ROLES
        and _department_scope(context) is None
    )


def _expense_question_match(question: str) -> Optional[Any]:
    tokens = re.findall(
        r"[A-Za-z0-9][A-Za-z0-9._/-]{2,}",
        question or "",
    )
    uuid_values: list[str] = []
    reference_values: list[str] = []
    for token in tokens:
        compact = token.strip(".,:;()[]{}").lower()
        if not compact:
            continue
        try:
            uuid_values.append(str(UUID(compact)))
            continue
        except ValueError:
            pass
        if any(char.isdigit() for char in compact) and (
            any(char.isalpha() for char in compact)
            or any(separator in compact for separator in "-_/")
        ):
            reference_values.append(compact)

    conditions = []
    if uuid_values:
        conditions.extend(
            (
                cast(ExpenseReport.id, String).in_(uuid_values),
                cast(ExpenseReport.documento_id, String).in_(uuid_values),
                cast(ExpenseReport.cuenta_gastos_id, String).in_(uuid_values),
            )
        )
    if reference_values:
        conditions.extend(
            (
                func.lower(ExpenseReport.numero_referencia).in_(reference_values),
                func.lower(ExpenseReport.referencia_base).in_(reference_values),
            )
        )
    if not conditions:
        return None
    return or_(*conditions)


def _requested_payment_reference_match(question: str) -> Optional[Any]:
    reference_tokens = [
        token
        for token in _requested_entity_tokens(
            question,
            "registered_payments",
        )
        if any(char.isdigit() for char in token)
        or any(separator in token for separator in "-_/")
    ]
    if not reference_tokens:
        return None
    return and_(
        *(
            or_(
                func.lower(cast(Documento.id, String)).contains(token),
                func.lower(Documento.numero_referencia).contains(token),
            )
            for token in reference_tokens
        )
    )


def _requested_budget_edition_year(question: str) -> Optional[int]:
    year = _requested_year(question)
    if year is not None and 2024 <= year <= 2035:
        return year
    return None


def _requested_budget_project_tokens(question: str) -> list[str]:
    normalized_question = (question or "").strip().lower()
    match = re.search(
        r"\b(?:presupuesto|presupuestos|budget)\b",
        normalized_question,
    )
    if match is None:
        return []
    tokens: list[str] = []
    for token in re.findall(
        r"[^\W_][\w._/-]*",
        normalized_question[match.end():],
        flags=re.UNICODE,
    ):
        compact = token.strip(".,:;()[]{}")
        if re.fullmatch(r"20\d{2}", compact):
            continue
        normalized_compact = normalize_analyst_text(compact)
        if normalized_compact in LIVE_EVIDENCE_ENTITY_TERMINATORS:
            if normalized_compact == "para" and not tokens:
                continue
            break
        if normalized_compact in LIVE_EVIDENCE_BUDGET_MODIFIERS:
            continue
        if (
            normalized_compact in LIVE_EVIDENCE_ENTITY_STOPWORDS
            or normalized_compact
            in {
                "proyecto",
                "proyectos",
                "torneo",
                "torneos",
            }
        ):
            continue
        tokens.append(compact)
        if len(tokens) == 4:
            break
    return tokens


def _requested_year(question: str) -> Optional[int]:
    for value in re.findall(r"\b20\d{2}\b", question or ""):
        year = int(value)
        if 2000 <= year <= 2100:
            return year
    return None


def _requested_live_evidence_sources(question: str) -> Set[str]:
    normalized_question = normalize_analyst_text(question)
    requested: Set[str] = set()
    for source, tokens in LIVE_EVIDENCE_QUERY_TOKENS.items():
        if any(
            re.search(rf"\b{re.escape(token)}\b", normalized_question)
            for token in tokens
        ):
            requested.add(source)
    if re.search(r"\bcuentas? de gastos?\b", normalized_question):
        requested.add("expense_accounts")
        requested.discard("expenses")
    if re.search(r"\bsolicitud(?:es)? de pago\b", normalized_question):
        requested.add("documents")
        requested.discard("registered_payments")
    return requested


def _requested_entity_tokens(question: str, source: str) -> list[str]:
    pattern = LIVE_EVIDENCE_ENTITY_PATTERNS.get(source)
    if pattern is None:
        return []
    normalized_question = (question or "").strip().lower()
    match = re.search(pattern, normalized_question)
    if match is None:
        return []
    tail = normalized_question[match.end():]
    tokens: list[str] = []
    for token in re.findall(
        r"[^\W_][\w._/-]*",
        tail,
        flags=re.UNICODE,
    ):
        compact = token.strip(".,:;()[]{}")
        normalized_compact = normalize_analyst_text(compact)
        if normalized_compact in LIVE_EVIDENCE_ENTITY_TERMINATORS:
            break
        if normalized_compact in LIVE_EVIDENCE_ENTITY_STOPWORDS:
            continue
        tokens.append(compact)
        if len(tokens) == 4:
            break
    return tokens


def _requested_entity_match(
    question: str,
    source: str,
    columns: Sequence[Any],
) -> Optional[Any]:
    tokens = _requested_entity_tokens(question, source)
    if not tokens:
        return None
    return and_(
        *(
            or_(
                *(
                    func.lower(cast(column, String)).contains(token)
                    for column in columns
                )
            )
            for token in tokens
        )
    )


def _has_permission(
    context: LiveEvidenceContext,
    required_permission: str,
) -> bool:
    if context.role.strip().lower() in {"superadmin", "super_admin"}:
        return True
    required = required_permission.strip().lower()
    granted = {
        str(permission).strip().lower()
        for permission in context.permissions
        if str(permission).strip()
    }
    if "*" in granted or required in granted:
        return True
    return any(
        "*" in token and fnmatch.fnmatchcase(required, token) for token in granted
    )


def _source_access_allowed(
    context: LiveEvidenceContext,
    source: str,
) -> bool:
    role = context.role.strip().lower()
    if role in {"superadmin", "super_admin"}:
        return True
    if (
        role == "empleado"
        and bool(context.employee_id)
        and source in EMPLOYEE_SELF_SCOPED_SOURCES
    ):
        return True
    return any(
        _has_permission(context, permission)
        for permission in LIVE_EVIDENCE_PERMISSION_ALIASES.get(source, ())
    )


def _empty_collection() -> AnalystEvidenceCollection:
    return AnalystEvidenceCollection(
        evidence=[],
        coverage_level="none",
        caveats=[],
        adapter_results=[],
    )


def _sanitize_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    for key in (
        "id",
        "label",
        "summary",
        "date",
        "reference",
        "coverage_level",
        "relevance",
    ):
        if key in row:
            sanitized[key] = _safe_scalar(row.get(key))
    metadata = row.get("metadata")
    if isinstance(metadata, Mapping):
        sanitized["metadata"] = {
            str(key): _safe_scalar(value)
            for key, value in metadata.items()
            if isinstance(key, str)
        }
    return sanitized


def _row(
    item: Mapping[str, Any],
    *,
    label: str,
    summary: str,
    reference: str,
    metadata_keys: Sequence[str],
    metadata_overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    metadata = {
        key: _safe_scalar(item.get(key))
        for key in metadata_keys
        if item.get(key) is not None
    }
    metadata.update(
        {
            key: _safe_scalar(value)
            for key, value in (metadata_overrides or {}).items()
            if value is not None
        }
    )
    return {
        "id": str(item.get("id") or ""),
        "label": label,
        "summary": summary,
        "date": _safe_scalar(item.get("date")),
        "reference": reference,
        "coverage_level": "medium",
        "relevance": "high",
        "metadata": metadata,
    }


def _summary(*values: Any) -> str:
    return " · ".join(
        compact for compact in (str(value or "").strip() for value in values) if compact
    )


def _join(*values: Any) -> str:
    return " — ".join(
        compact for compact in (str(value or "").strip() for value in values) if compact
    )


def _money(amount: Any, currency: Any) -> str:
    if amount is None:
        return ""
    try:
        decimal_amount = (
            amount if isinstance(amount, Decimal) else Decimal(str(amount))
        )
        return f"{decimal_amount:,.2f} {str(currency or 'MXN')}"
    except (InvalidOperation, TypeError, ValueError):
        return ""


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _dedupe_text(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        compact = str(value or "").strip()
        key = compact.lower()
        if compact and key not in seen:
            seen.add(key)
            output.append(compact)
    return output


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
