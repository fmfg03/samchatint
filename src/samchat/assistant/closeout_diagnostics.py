"""Read-only closeout diagnostics for assistant answers.

This module turns existing Finance Platform snapshots into evidence-backed
closure statements such as: "the accounting close is blocked because two
policies do not balance." It never mutates accounting data and does not replace
Finance Platform calculations; it only summarizes their blockers for the
assistant.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from samchat.finance_platform import (
    build_finance_platform_snapshot,
    build_finance_source_snapshot,
)

from .business_diff_preview import NOT_EXECUTED


CLOSEOUT_DIAGNOSTICS_ONLY = "closeout_diagnostics_only"
CLOSEOUT_READY = "ready"
CLOSEOUT_BLOCKED = "blocked"
CLOSEOUT_SCOPE_ACCOUNTING = "accounting"
CLOSEOUT_SCOPE_FINANCE = "finance"


@dataclass(frozen=True)
class CloseoutBlocker:
    blocker_type: str
    severity: str
    title: str
    detail: str
    reference: Optional[str] = None
    amount: Optional[float] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CloseoutDiagnosticsReport:
    report_id: str
    scope: str
    status: str
    headline: str
    summary: str
    period: Dict[str, Any]
    blockers: List[CloseoutBlocker] = field(default_factory=list)
    blocker_count: int = 0
    high_priority_count: int = 0
    next_actions: List[str] = field(default_factory=list)
    source_summary: Dict[str, Any] = field(default_factory=dict)
    safety_summary: Dict[str, Any] = field(default_factory=dict)
    execution_status: str = NOT_EXECUTED
    writes_attempted: int = 0
    side_effects_detected: int = 0
    audit_language: str = CLOSEOUT_DIAGNOSTICS_ONLY

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["blockers"] = [blocker.to_dict() for blocker in self.blockers]
        return payload


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _money(value: Any) -> str:
    return f"${_safe_float(value):,.2f}"


def _poliza_reference(row: Mapping[str, Any]) -> str:
    tipo = str(row.get("tipo_poliza") or "-").strip()
    numero = str(row.get("numero_poliza") or row.get("id") or "-").strip()
    return f"{tipo}-{numero}"


def _unbalanced_poliza_blockers(platform: Mapping[str, Any]) -> List[CloseoutBlocker]:
    accounting_close = platform.get("accounting_close_center") or {}
    blockers: List[CloseoutBlocker] = []
    for row in accounting_close.get("unbalanced_polizas") or []:
        debe = _safe_float(row.get("debe"))
        haber = _safe_float(row.get("haber"))
        delta = round(debe - haber, 2)
        ref = _poliza_reference(row)
        blockers.append(
            CloseoutBlocker(
                blocker_type="unbalanced_poliza",
                severity="high",
                title=f"Póliza descuadrada {ref}",
                detail=(
                    f"Debe {_money(debe)} y Haber {_money(haber)}; "
                    f"diferencia {_money(delta)}."
                ),
                reference=ref,
                amount=abs(delta),
                evidence={
                    "id": row.get("id"),
                    "tipo_poliza": row.get("tipo_poliza"),
                    "numero_poliza": row.get("numero_poliza"),
                    "debe": debe,
                    "haber": haber,
                    "difference": delta,
                    "beneficiario_nombre": row.get("beneficiario_nombre"),
                    "origen": row.get("origen"),
                },
            )
        )
    return blockers


def _pending_coi_blockers(platform: Mapping[str, Any]) -> List[CloseoutBlocker]:
    accounting_close = platform.get("accounting_close_center") or {}
    blockers: List[CloseoutBlocker] = []
    for row in accounting_close.get("pending_coi_expenses") or []:
        ref = str(row.get("numero_referencia") or row.get("id") or "-").strip()
        missing: List[str] = []
        if not row.get("cuenta_contable_id"):
            missing.append("cuenta contable")
        if not row.get("contra_cuenta_contable_id"):
            missing.append("contrapartida")
        if not (row.get("cfdi_report_id") or row.get("cfdi_uuid_manual") or row.get("cfdi_uuid")):
            missing.append("CFDI")
        blockers.append(
            CloseoutBlocker(
                blocker_type="pending_coi_expense",
                severity="medium",
                title=f"Gasto pendiente COI {ref}",
                detail=(
                    "Falta " + ", ".join(missing) if missing else "Falta completar clasificación COI."
                ),
                reference=ref,
                amount=_safe_float(row.get("gasto_cantidad")),
                evidence={
                    "id": row.get("id"),
                    "numero_referencia": row.get("numero_referencia"),
                    "concepto": row.get("concepto"),
                    "gasto_cantidad": _safe_float(row.get("gasto_cantidad")),
                    "missing": missing,
                },
            )
        )
    return blockers


def _tax_blockers(platform: Mapping[str, Any]) -> List[CloseoutBlocker]:
    tax = platform.get("tax_readiness") or {}
    blockers: List[CloseoutBlocker] = []
    for row in tax.get("blockers") or []:
        ref = str(row.get("numero_referencia") or row.get("id") or "-").strip()
        blockers.append(
            CloseoutBlocker(
                blocker_type="missing_cfdi",
                severity="medium",
                title=f"CFDI faltante {ref}",
                detail="Documento/gasto sin CFDI o UUID vinculado para DIOT/validación fiscal.",
                reference=ref,
                amount=_safe_float(
                    row.get("monto_total")
                    or row.get("monto_solicitado")
                    or row.get("gasto_cantidad")
                ),
                evidence={
                    "entity_type": row.get("entity_type"),
                    "id": row.get("id"),
                    "numero_referencia": row.get("numero_referencia"),
                    "estado": row.get("estado") or row.get("estado_reembolso"),
                },
            )
        )
    return blockers


def build_closeout_diagnostics_from_platform(
    platform: Mapping[str, Any],
    *,
    scope: str = CLOSEOUT_SCOPE_ACCOUNTING,
    include_medium: bool = True,
) -> CloseoutDiagnosticsReport:
    """Summarize whether a finance/accounting period can be closed."""

    period = dict(platform.get("period") or {})
    blockers = _unbalanced_poliza_blockers(platform)
    if include_medium:
        blockers.extend(_pending_coi_blockers(platform))
        blockers.extend(_tax_blockers(platform))

    # Preserve severity priority and avoid duplicate references from COI/tax views.
    rank = {"high": 0, "medium": 1, "low": 2}
    blockers.sort(key=lambda item: (rank.get(item.severity, 9), item.blocker_type, item.reference or ""))
    seen: set[tuple[str, str]] = set()
    deduped: List[CloseoutBlocker] = []
    for blocker in blockers:
        key = (blocker.blocker_type, blocker.reference or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(blocker)
    blockers = deduped

    high_count = sum(1 for item in blockers if item.severity == "high")
    status = CLOSEOUT_BLOCKED if blockers else CLOSEOUT_READY
    if status == CLOSEOUT_READY:
        headline = "El cierre no tiene bloqueos detectados"
        summary = "No encontré pólizas descuadradas ni bloqueos fiscales/COI en el snapshot revisado."
        next_actions = [
            "Validar con Finanzas antes de marcar el periodo como cerrado.",
            "Generar evidencia/exportables sólo con autorizaci?n humana.",
        ]
    else:
        headline = "El cierre está bloqueado"
        if high_count:
            summary = (
                f"No conviene cerrar: hay {high_count} póliza(s) descuadrada(s) "
                f"y {len(blockers)} bloqueo(s) total(es) en el snapshot."
            )
        else:
            summary = (
                f"No conviene cerrar todavía: hay {len(blockers)} bloqueo(s) "
                "COI/fiscal(es) pendientes en el snapshot."
            )
        next_actions = [
            "Corregir primero las pólizas descuadradas de prioridad alta.",
            "Completar cuenta, contrapartida y CFDI de gastos pendientes COI.",
            "Volver a generar el diagnóstico read-only antes de cerrar.",
        ]

    accounting = platform.get("accounting_close_center") or {}
    tax = platform.get("tax_readiness") or {}
    return CloseoutDiagnosticsReport(
        report_id="finance_closeout_diagnostics_v1",
        scope=scope,
        status=status,
        headline=headline,
        summary=summary,
        period=period,
        blockers=blockers,
        blocker_count=len(blockers),
        high_priority_count=high_count,
        next_actions=next_actions,
        source_summary={
            "polizas_count": accounting.get("polizas_count", 0),
            "unbalanced_count": accounting.get("unbalanced_count", 0),
            "pending_coi_expenses_count": accounting.get("pending_coi_expenses_count", 0),
            "diot_blockers_count": tax.get("diot_blockers_count", 0),
        },
        safety_summary={
            "read_only": True,
            "writes_enabled": False,
            "source": "finance_platform_snapshot",
            "approval_required_for_close": True,
        },
        execution_status=NOT_EXECUTED,
        writes_attempted=0,
        side_effects_detected=0,
        audit_language=CLOSEOUT_DIAGNOSTICS_ONLY,
    )


async def build_finance_closeout_diagnostics(
    session: AsyncSession,
    *,
    year: Optional[int] = None,
    month: Optional[int] = None,
    scope: str = CLOSEOUT_SCOPE_ACCOUNTING,
    include_medium: bool = True,
    limit: int = 300,
) -> CloseoutDiagnosticsReport:
    source_snapshot = await build_finance_source_snapshot(
        session,
        year=year,
        month=month,
        limit=limit,
    )
    platform = build_finance_platform_snapshot(source_snapshot)
    return build_closeout_diagnostics_from_platform(
        platform,
        scope=scope,
        include_medium=include_medium,
    )


__all__ = [
    "CLOSEOUT_BLOCKED",
    "CLOSEOUT_DIAGNOSTICS_ONLY",
    "CLOSEOUT_READY",
    "CLOSEOUT_SCOPE_ACCOUNTING",
    "CLOSEOUT_SCOPE_FINANCE",
    "CloseoutBlocker",
    "CloseoutDiagnosticsReport",
    "build_closeout_diagnostics_from_platform",
    "build_finance_closeout_diagnostics",
]
