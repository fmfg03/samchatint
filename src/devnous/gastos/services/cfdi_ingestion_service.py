"""Canonical CFDI XML ingestion and entity linking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CFDIReport, Documento, ExpenseReport
from .cfdi_expense_link_service import (
    find_cfdi_report_by_fiscal_uuid,
    normalize_cfdi_uuid_to_canonical,
)
from .cfdi_parser import parse_cfdi_xml
from .cfdi_upload_resolver import resolve_cfdi_upload

CFDIEntity = Union[ExpenseReport, Documento]

_CFDI_DATA_FIELDS = (
    "version",
    "serie",
    "folio",
    "fecha",
    "sello",
    "forma_pago",
    "no_certificado",
    "certificado",
    "subtotal",
    "descuento",
    "moneda",
    "tipo_cambio",
    "total",
    "tipo_de_comprobante",
    "metodo_pago",
    "lugar_expedicion",
    "exportacion",
    "emisor_rfc",
    "emisor_nombre",
    "emisor_regimen_fiscal",
    "receptor_rfc",
    "receptor_nombre",
    "receptor_uso_cfdi",
    "receptor_domicilio_fiscal",
    "receptor_regimen_fiscal",
    "timbre_version",
    "fecha_timbrado",
    "rfc_prov_certif",
    "sello_cfd",
    "no_certificado_sat",
    "sello_sat",
    "total_impuestos_trasladados",
    "conceptos",
    "descripcion_concepto_principal",
    "impuestos_detalle",
)

# Differences in these fields mean two XMLs claiming the same UUID represent
# materially different fiscal documents. Formatting-only XML differences do not.
_MATERIAL_IDENTITY_FIELDS = (
    "fecha",
    "subtotal",
    "total",
    "moneda",
    "tipo_de_comprobante",
    "emisor_rfc",
    "receptor_rfc",
    "sello",
    "no_certificado",
    "sello_cfd",
    "no_certificado_sat",
)


def _report_has_canonical_xml(report: CFDIReport) -> bool:
    return bool((getattr(report, "xml_raw", None) or "").strip())


def _entity_linked_to_report(
    entity: Optional[CFDIEntity],
    report: CFDIReport,
) -> bool:
    return bool(
        entity is not None
        and getattr(entity, "cfdi_report_id", None) == report.id
    )


@dataclass(frozen=True)
class _ExistingIngestPlan:
    conflicts: List[str]
    apply_fiscal_data: bool
    overwrite: bool
    pdf_attach_only: bool


def _plan_existing_cfdi_ingest(
    report: CFDIReport,
    parsed: Dict[str, Any],
    *,
    xml_raw: Optional[str],
    entity: Optional[CFDIEntity],
) -> _ExistingIngestPlan:
    """Decide whether incoming upload may enrich, replace, or only re-link."""
    conflicts = _material_conflicts(report, parsed)
    incoming_xml = bool(xml_raw and str(xml_raw).strip())
    existing_xml = _report_has_canonical_xml(report)
    same_entity = _entity_linked_to_report(entity, report)

    # PDF is an attachment when canonical XML already exists; never override XML.
    if not incoming_xml and existing_xml:
        return _ExistingIngestPlan(
            conflicts=[],
            apply_fiscal_data=False,
            overwrite=False,
            pdf_attach_only=True,
        )

    # Same solicitud/gasto already linked: allow companion PDF/XML uploads.
    if same_entity:
        if incoming_xml and conflicts:
            return _ExistingIngestPlan(
                conflicts=[],
                apply_fiscal_data=True,
                overwrite=True,
                pdf_attach_only=False,
            )
        return _ExistingIngestPlan(
            conflicts=[],
            apply_fiscal_data=incoming_xml,
            overwrite=False,
            pdf_attach_only=not incoming_xml,
        )

    # Uploaded XML replaces provisional PDF/text data for the same UUID.
    if incoming_xml and conflicts and not existing_xml:
        return _ExistingIngestPlan(
            conflicts=[],
            apply_fiscal_data=True,
            overwrite=True,
            pdf_attach_only=False,
        )

    return _ExistingIngestPlan(
        conflicts=conflicts,
        apply_fiscal_data=True,
        overwrite=False,
        pdf_attach_only=False,
    )


class CFDIIngestionError(ValueError):
    """Base error for invalid CFDI ingestion."""


class CFDIConflictError(CFDIIngestionError):
    """Raised when an existing UUID has materially different XML data."""

    def __init__(self, cfdi_uuid: str, conflicting_fields: List[str]):
        self.cfdi_uuid = cfdi_uuid
        self.conflicting_fields = conflicting_fields
        super().__init__(
            "El CFDI ya existe con datos fiscales diferentes: "
            + ", ".join(conflicting_fields)
        )


class CFDIDuplicateLinkError(CFDIIngestionError):
    """Raised when a CFDI is already linked and sharing was not confirmed."""

    def __init__(self, cfdi_uuid: str):
        self.cfdi_uuid = cfdi_uuid
        super().__init__(
            "La factura ya está vinculada a otro gasto o solicitud. "
            "Confirme explícitamente que es una factura compartida para continuar."
        )


@dataclass
class CFDIIngestionResult:
    status: str
    cfdi_report: CFDIReport
    cfdi_uuid: str
    linked: bool
    warnings: List[str] = field(default_factory=list)


def _normalized_material_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().upper()
    if isinstance(value, float):
        return round(value, 6)
    return value


def _material_conflicts(existing: CFDIReport, parsed: Dict[str, Any]) -> List[str]:
    conflicts: List[str] = []
    for name in _MATERIAL_IDENTITY_FIELDS:
        stored = getattr(existing, name, None)
        incoming = parsed.get(name)
        if stored in (None, "") or incoming in (None, ""):
            continue
        if _normalized_material_value(stored) != _normalized_material_value(incoming):
            conflicts.append(name)
    return conflicts


def _apply_parsed_data(
    report: CFDIReport, parsed: Dict[str, Any], *, overwrite: bool
) -> bool:
    changed = False
    for name in _CFDI_DATA_FIELDS:
        incoming = parsed.get(name)
        current = getattr(report, name, None)
        if incoming in (None, ""):
            continue
        if overwrite or current in (None, "", [], {}):
            if current != incoming:
                setattr(report, name, incoming)
                changed = True
    return changed


async def _other_expense_links(
    session: AsyncSession, report_id: Any, entity: Optional[CFDIEntity]
) -> int:
    conditions = [ExpenseReport.cfdi_report_id == report_id]
    if isinstance(entity, ExpenseReport) and getattr(entity, "id", None):
        conditions.append(ExpenseReport.id != entity.id)
    result = await session.execute(
        select(ExpenseReport.id).where(and_(*conditions)).limit(1)
    )
    return 1 if result.scalar_one_or_none() is not None else 0


async def has_existing_cfdi_usage(
    session: AsyncSession,
    report_id: Any,
    entity: Optional[CFDIEntity] = None,
) -> bool:
    """Return True when the CFDI is already linked to another expense/document."""
    expense_conditions = [ExpenseReport.cfdi_report_id == report_id]
    if isinstance(entity, ExpenseReport) and getattr(entity, "id", None):
        expense_conditions.append(ExpenseReport.id != entity.id)
    expense_result = await session.execute(
        select(ExpenseReport.id).where(and_(*expense_conditions)).limit(1)
    )
    if expense_result.scalar_one_or_none() is not None:
        return True

    documento_conditions = [Documento.cfdi_report_id == report_id]
    if isinstance(entity, Documento) and getattr(entity, "id", None):
        documento_conditions.append(Documento.id != entity.id)
    documento_result = await session.execute(
        select(Documento.id).where(and_(*documento_conditions)).limit(1)
    )
    return documento_result.scalar_one_or_none() is not None


async def _ingest_cfdi_parsed(
    session: AsyncSession,
    parsed: Dict[str, Any],
    *,
    source: str,
    entity: Optional[CFDIEntity] = None,
    nova_request_id: Optional[str] = None,
    numero_referencia: Optional[str] = None,
    allow_shared: bool = False,
    require_shared_confirmation: bool = False,
    xml_raw: Optional[str] = None,
) -> CFDIIngestionResult:
    if not parsed or not parsed.get("cfdi_uuid"):
        raise CFDIIngestionError(
            "El CFDI no es válido o no contiene UUID"
        )

    try:
        canonical_uuid = normalize_cfdi_uuid_to_canonical(parsed["cfdi_uuid"])
    except ValueError as exc:
        raise CFDIIngestionError("El CFDI contiene un UUID inválido") from exc
    parsed["cfdi_uuid"] = canonical_uuid

    report = await find_cfdi_report_by_fiscal_uuid(session, canonical_uuid)
    warnings: List[str] = []
    status = "reused"

    if report is None:
        report = CFDIReport(
            id=uuid4(),
            cfdi_uuid=canonical_uuid,
            nova_request_id=nova_request_id,
            numero_referencia=numero_referencia,
            origen=source,
            xml_parsed=True,
            parsed_at=datetime.utcnow(),
            xml_raw=xml_raw,
        )
        _apply_parsed_data(report, parsed, overwrite=True)
        session.add(report)
        status = "created"
    else:
        plan = _plan_existing_cfdi_ingest(
            report,
            parsed,
            xml_raw=xml_raw,
            entity=entity,
        )
        if plan.conflicts:
            raise CFDIConflictError(canonical_uuid, plan.conflicts)

        changed = False
        if plan.apply_fiscal_data:
            changed = _apply_parsed_data(
                report, parsed, overwrite=plan.overwrite
            )
        if xml_raw and (plan.overwrite or not report.xml_raw):
            report.xml_raw = xml_raw
            changed = True
        if not report.xml_parsed:
            report.xml_parsed = True
            changed = True
        if not report.parsed_at:
            report.parsed_at = datetime.utcnow()
            changed = True
        if not report.nova_request_id and nova_request_id:
            report.nova_request_id = nova_request_id
            changed = True
        if not report.numero_referencia and numero_referencia:
            report.numero_referencia = numero_referencia
            changed = True
        if changed:
            status = "enriched"

    linked = False
    if entity is not None:
        already_linked = getattr(entity, "cfdi_report_id", None) == report.id
        if already_linked:
            status = "already_linked"
        else:
            has_existing_usage = await has_existing_cfdi_usage(
                session, report.id, entity
            )
            if has_existing_usage and require_shared_confirmation and not allow_shared:
                raise CFDIDuplicateLinkError(canonical_uuid)
            if has_existing_usage and not allow_shared:
                warnings.append(
                    "El CFDI ya está vinculado a otro gasto o solicitud; "
                    "se creó un vínculo compartido."
                )
            entity.cfdi_uuid_manual = canonical_uuid
            entity.cfdi_report_id = report.id
            linked = True
            if status == "reused":
                status = "linked"

    return CFDIIngestionResult(
        status=status,
        cfdi_report=report,
        cfdi_uuid=canonical_uuid,
        linked=linked,
        warnings=warnings,
    )


async def ingest_cfdi_from_upload(
    session: AsyncSession,
    *,
    xml_bytes: Optional[bytes] = None,
    pdf_bytes: Optional[bytes] = None,
    source: str,
    entity: Optional[CFDIEntity] = None,
    nova_request_id: Optional[str] = None,
    numero_referencia: Optional[str] = None,
    allow_shared: bool = False,
    require_shared_confirmation: bool = False,
) -> Optional[CFDIIngestionResult]:
    """
    Parse uploaded CFDI bytes and upsert/link a canonical report.

    Non-empty XML takes precedence over PDF. Returns None when neither file
    contains ingestible CFDI data.
    """
    resolved, error = resolve_cfdi_upload(
        xml_bytes=xml_bytes,
        pdf_bytes=pdf_bytes,
    )
    if error:
        raise CFDIIngestionError(error)
    if resolved is None:
        return None

    if resolved.xml_text:
        return await _ingest_cfdi_parsed(
            session,
            resolved.parsed,
            source=source,
            entity=entity,
            nova_request_id=nova_request_id,
            numero_referencia=numero_referencia,
            allow_shared=allow_shared,
            require_shared_confirmation=require_shared_confirmation,
            xml_raw=resolved.xml_text,
        )

    return await _ingest_cfdi_parsed(
        session,
        resolved.parsed,
        source=source,
        entity=entity,
        nova_request_id=nova_request_id,
        numero_referencia=numero_referencia,
        allow_shared=allow_shared,
        require_shared_confirmation=require_shared_confirmation,
        xml_raw=None,
    )


async def ingest_cfdi_parsed(
    session: AsyncSession,
    parsed: Dict[str, Any],
    *,
    source: str,
    entity: Optional[CFDIEntity] = None,
    nova_request_id: Optional[str] = None,
    numero_referencia: Optional[str] = None,
    allow_shared: bool = False,
    require_shared_confirmation: bool = False,
    xml_raw: Optional[str] = None,
) -> CFDIIngestionResult:
    """Upsert and optionally link one canonical CFDI from parser output."""
    return await _ingest_cfdi_parsed(
        session,
        parsed,
        source=source,
        entity=entity,
        nova_request_id=nova_request_id,
        numero_referencia=numero_referencia,
        allow_shared=allow_shared,
        require_shared_confirmation=require_shared_confirmation,
        xml_raw=xml_raw,
    )


async def ingest_cfdi_xml(
    session: AsyncSession,
    xml_content: str,
    *,
    source: str,
    entity: Optional[CFDIEntity] = None,
    nova_request_id: Optional[str] = None,
    numero_referencia: Optional[str] = None,
    allow_shared: bool = False,
    require_shared_confirmation: bool = False,
) -> CFDIIngestionResult:
    """
    Parse, upsert, and optionally link one canonical CFDI by fiscal UUID.

    This function never commits or rolls back. The caller owns the transaction.
    """
    parsed = parse_cfdi_xml(xml_content)
    if not parsed or not parsed.get("cfdi_uuid"):
        raise CFDIIngestionError(
            "El archivo XML no es un CFDI válido o no contiene UUID"
        )

    return await _ingest_cfdi_parsed(
        session,
        parsed,
        source=source,
        entity=entity,
        nova_request_id=nova_request_id,
        numero_referencia=numero_referencia,
        allow_shared=allow_shared,
        require_shared_confirmation=require_shared_confirmation,
        xml_raw=xml_content,
    )
