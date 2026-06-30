from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..models import CFDIReport, ExpenseReport, InvoiceReport


@dataclass(frozen=True)
class CFDIThreeWayMatchResult:
    status: str
    exceptions: List[str] = field(default_factory=list)
    has_reference: bool = False
    has_support: bool = False
    has_cfdi: bool = False


@dataclass(frozen=True)
class CFDIARStatusResult:
    status: str
    next_action: str
    detail: Optional[str] = None


def _has_supporting_evidence(expense: ExpenseReport) -> bool:
    return bool(
        getattr(expense, "archivo_data", None)
        or getattr(expense, "archivo_path", None)
        or getattr(expense, "link_pdf", None)
        or getattr(expense, "link_xml", None)
    )


def _has_origin_reference(expense: ExpenseReport) -> bool:
    return bool(
        getattr(expense, "numero_referencia", None)
        or getattr(expense, "documento_id", None)
        or getattr(expense, "solicitud_documento_id", None)
        or getattr(expense, "informe_documento_id", None)
        or getattr(expense, "referencia_base", None)
    )


def _has_cfdi_signal(expense: ExpenseReport, cfdi: Optional[CFDIReport]) -> bool:
    return bool(
        cfdi
        or getattr(expense, "cfdi_report_id", None)
        or getattr(expense, "cfdi_uuid_manual", None)
        or getattr(expense, "nova_request_id", None)
    )


def evaluate_three_way_match(
    expense: ExpenseReport,
    *,
    cfdi: Optional[CFDIReport] = None,
) -> CFDIThreeWayMatchResult:
    has_reference = _has_origin_reference(expense)
    has_support = _has_supporting_evidence(expense)
    has_cfdi = _has_cfdi_signal(expense, cfdi)
    exceptions: List[str] = []

    if cfdi and cfdi.total is not None and expense.gasto_cantidad is not None:
        expense_amount = round(float(expense.gasto_cantidad or 0), 2)
        cfdi_amount = round(float(cfdi.total or 0), 2)
        if abs(expense_amount - cfdi_amount) > 1.0:
            exceptions.append(
                f"Monto inconsistente: gasto {expense_amount:.2f} vs CFDI {cfdi_amount:.2f}"
            )

    if (
        cfdi
        and getattr(cfdi, "numero_referencia", None)
        and getattr(expense, "numero_referencia", None)
        and str(cfdi.numero_referencia).strip() != str(expense.numero_referencia).strip()
    ):
        exceptions.append("Referencia origen inconsistente entre gasto y CFDI")

    if (
        cfdi
        and getattr(cfdi, "nova_request_id", None)
        and getattr(expense, "nova_request_id", None)
        and str(cfdi.nova_request_id).strip() != str(expense.nova_request_id).strip()
    ):
        exceptions.append("Vínculo CFDI inconsistente con la solicitud de origen")

    if exceptions:
        status = "con_excepciones"
    else:
        evidence_count = sum(1 for value in (has_reference, has_support, has_cfdi) if value)
        if evidence_count == 3:
            status = "match_ok"
        elif evidence_count >= 2:
            status = "parcial"
        else:
            status = "pendiente"

    return CFDIThreeWayMatchResult(
        status=status,
        exceptions=exceptions,
        has_reference=has_reference,
        has_support=has_support,
        has_cfdi=has_cfdi,
    )


def evaluate_ar_status(
    expense: ExpenseReport,
    *,
    cfdi: Optional[CFDIReport] = None,
    invoice: Optional[InvoiceReport] = None,
) -> CFDIARStatusResult:
    raw_status = (getattr(expense, "estado_factura", None) or "").strip().lower()
    invoice_status = (getattr(invoice, "estado_factura", None) or "").strip().lower()

    if cfdi or getattr(expense, "cfdi_report_id", None):
        return CFDIARStatusResult(
            status="emitido",
            next_action="revisar_excepciones"
            if evaluate_three_way_match(expense, cfdi=cfdi).status == "con_excepciones"
            else "sin_accion",
        )

    if raw_status == "error" or invoice_status == "error":
        return CFDIARStatusResult(
            status="error",
            next_action="reintentar_emision",
            detail=getattr(expense, "mensaje_error", None)
            or getattr(invoice, "mensaje_error", None),
        )

    if getattr(expense, "nova_request_id", None) or invoice_status in {
        "en_proceso",
        "completada",
    }:
        return CFDIARStatusResult(
            status="solicitado",
            next_action="esperar_cfdi",
        )

    if expense.tipo_gasto == "ticket":
        if _has_supporting_evidence(expense):
            return CFDIARStatusResult(
                status="pendiente",
                next_action="solicitar_cfdi",
            )
        return CFDIARStatusResult(
            status="pendiente",
            next_action="adjuntar_comprobante",
        )

    if getattr(expense, "cfdi_uuid_manual", None):
        return CFDIARStatusResult(
            status="pendiente",
            next_action="vincular_cfdi",
        )

    return CFDIARStatusResult(
        status="pendiente",
        next_action="capturar_uuid_cfdi",
    )
