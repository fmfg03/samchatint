"""
Helpers to resolve expense counterpart accounts and tax-side posting hints.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CFDIReport, CuentaContable, ExpenseReport
from .accounting_constants import (
    DEFAULT_IVA_ACCOUNT_CODE,
    DEFAULT_IVA_RETENTION_ACCOUNT_CODE,
    DEFAULT_ISR_RETENTION_ACCOUNT_CODE,
)
from .hospedaje_tax_service import resolve_hospedaje_local_tax

_RESTAURANT_KEYWORDS = (
    "restaurante",
    "restaurant",
    "alimento",
    "alimentos",
    "comida",
    "cafe",
    "café",
    "bar",
    "consumo",
)


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


async def _resolve_cuenta_by_id_or_code(
    session: AsyncSession,
    *,
    cuenta_contable_id: Optional[str] = None,
    cuenta_codigo: Optional[str] = None,
) -> Optional[CuentaContable]:
    if cuenta_contable_id:
        try:
            cuenta_uuid = UUID(str(cuenta_contable_id))
        except (TypeError, ValueError):
            cuenta_uuid = None
        if cuenta_uuid is not None:
            result = await session.execute(
                select(CuentaContable).where(
                    CuentaContable.id == cuenta_uuid,
                    CuentaContable.activo.is_(True),
                )
            )
            cuenta = result.scalar_one_or_none()
            if cuenta:
                return cuenta

    if cuenta_codigo:
        result = await session.execute(
            select(CuentaContable).where(
                CuentaContable.codigo == str(cuenta_codigo).strip(),
                CuentaContable.activo.is_(True),
            )
        )
        return result.scalar_one_or_none()

    return None


async def _load_active_accounts(session: AsyncSession) -> List[CuentaContable]:
    result = await session.execute(
        select(CuentaContable)
        .where(CuentaContable.activo.is_(True))
        .order_by(CuentaContable.codigo)
    )
    return result.scalars().all()


def _score_account(
    account: CuentaContable,
    *,
    preferred_types: List[str],
    keywords: List[str],
) -> int:
    score = 0
    tipo = str(account.tipo or "").strip().lower()
    haystack = " ".join(
        [str(account.codigo or "").lower(), str(account.nombre or "").lower(), tipo]
    )
    for idx, item in enumerate(preferred_types):
        if tipo == item:
            score += max(1, 10 - idx)
    for keyword in keywords:
        if keyword and keyword in haystack:
            score += 3
    return score


def _pick_best_account(
    accounts: List[CuentaContable],
    *,
    preferred_types: List[str],
    keywords: List[str],
    min_unique_score: int = 9,
) -> Optional[CuentaContable]:
    scored: List[Tuple[int, CuentaContable]] = []
    for account in accounts:
        score = _score_account(
            account,
            preferred_types=preferred_types,
            keywords=keywords,
        )
        if score > 0:
            scored.append((score, account))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], str(item[1].codigo or "")))
    top_score = scored[0][0]
    top = [item for item in scored if item[0] == top_score]
    if len(top) != 1 and top_score < min_unique_score:
        return None
    return top[0][1]


def _is_generic_payable_account(account: Optional[CuentaContable]) -> bool:
    if account is None:
        return False
    text = " ".join(
        [
            str(account.codigo or "").lower(),
            str(account.nombre or "").lower(),
            str(account.tipo or "").lower(),
        ]
    )
    return any(
        token in text
        for token in (
            "acreedores",
            "proveedor",
            "cuentas por pagar",
            "pasivo",
            "2120-000-000",
        )
    )


def _should_prefer_paid_transfer_account(expense: ExpenseReport) -> bool:
    origin = str(getattr(expense, "origen", "") or "").strip().lower()
    payment = str(getattr(expense, "metodo_pago", "") or "").strip().lower()
    return origin in {"solicitud_terceros", "solicitud_personal"} and (
        "transfer" in payment or "spei" in payment
    )


def _is_amex_expense(expense: ExpenseReport) -> bool:
    origin = str(getattr(expense, "origen", "") or "").strip().lower()
    payment = str(getattr(expense, "metodo_pago", "") or "").strip().lower()
    return "amex" in origin or "amex" in payment or "american express" in payment


def _is_restaurant_expense(expense: ExpenseReport) -> bool:
    text = " ".join(
        [
            str(getattr(expense, "concepto", "") or "").lower(),
            str(getattr(expense, "proyecto", "") or "").lower(),
        ]
    )
    return any(keyword in text for keyword in _RESTAURANT_KEYWORDS)


def _calculate_amex_tip_amount(
    expense: ExpenseReport,
    cfdi_report: Optional[CFDIReport],
) -> float:
    if not _is_amex_expense(expense) or not _is_restaurant_expense(expense):
        return 0.0
    expense_total = _money(getattr(expense, "gasto_cantidad", 0))
    cfdi_subtotal = _money(getattr(cfdi_report, "subtotal", None))
    cfdi_iva = summarize_cfdi_tax_components(cfdi_report)["iva_trasladado"]
    cfdi_consumo_mas_iva = _money(cfdi_subtotal + cfdi_iva)
    if cfdi_consumo_mas_iva <= 0:
        cfdi_consumo_mas_iva = _money(getattr(cfdi_report, "total", None))
    if expense_total <= 0 or cfdi_consumo_mas_iva <= 0:
        return 0.0
    # AMEX restaurant vouchers include the tip, but the CFDI does not.
    delta = _money(expense_total - cfdi_consumo_mas_iva)
    if delta <= 1.0:
        return 0.0
    tip_cap = max(30.0, _money(expense_total * 0.18))
    return delta if delta <= tip_cap else 0.0


async def resolve_counterpart_account(
    session: AsyncSession,
    *,
    metodo_pago: Optional[str],
    contra_cuenta_contable_id: Optional[str] = None,
    contra_cuenta_codigo: Optional[str] = None,
) -> Tuple[Optional[CuentaContable], Optional[str]]:
    explicit = await _resolve_cuenta_by_id_or_code(
        session,
        cuenta_contable_id=contra_cuenta_contable_id,
        cuenta_codigo=contra_cuenta_codigo,
    )
    if explicit:
        return explicit, "explicit"

    payment = str(metodo_pago or "").strip().lower()
    env_candidates: List[str] = []
    if "amex" in payment:
        env_candidates.append(
            os.getenv("ASSISTANT_ACCOUNTING_COUNTERPART_AMEX", "").strip()
        )
    if "empresa" in payment or "corporativa" in payment:
        env_candidates.append(
            os.getenv("ASSISTANT_ACCOUNTING_COUNTERPART_TARJETA_EMPRESA", "").strip()
        )
    if "personal" in payment:
        env_candidates.append(
            os.getenv("ASSISTANT_ACCOUNTING_COUNTERPART_TARJETA_PERSONAL", "").strip()
        )
    if "efectivo" in payment or "cash" in payment:
        env_candidates.append(
            os.getenv("ASSISTANT_ACCOUNTING_COUNTERPART_EFECTIVO", "").strip()
        )
    if "transfer" in payment:
        env_candidates.append(
            os.getenv("ASSISTANT_ACCOUNTING_COUNTERPART_TRANSFERENCIA", "").strip()
        )
    env_candidates.append(
        os.getenv("ASSISTANT_ACCOUNTING_COUNTERPART_DEFAULT", "").strip()
    )
    for code in env_candidates:
        account = await _resolve_cuenta_by_id_or_code(
            session, cuenta_codigo=code or None
        )
        if account:
            return account, "env"

    accounts = await _load_active_accounts(session)
    if not accounts:
        return None, None

    keywords: List[str] = []
    preferred_types: List[str] = []
    if "amex" in payment or "empresa" in payment or "corporativa" in payment:
        keywords = ["amex", "tarjeta", "corporativa", "banco", "pasivo"]
        preferred_types = ["pasivo", "banco", "proveedor"]
    elif "personal" in payment or "efectivo" in payment or "cash" in payment:
        keywords = [
            "reembolso",
            "empleado",
            "acreedores",
            "deudores",
            "gastos por comprobar",
            "pasivo",
            "anticipo",
        ]
        preferred_types = ["pasivo", "anticipo", "proveedor"]
    elif "transfer" in payment:
        keywords = ["banco", "transferencia", "santander", "banamex", "banorte"]
        preferred_types = ["banco", "pasivo", "proveedor"]
    else:
        keywords = ["pasivo", "banco", "acreedores", "reembolso"]
        preferred_types = ["pasivo", "banco", "anticipo", "proveedor"]

    picked = _pick_best_account(
        accounts,
        preferred_types=preferred_types,
        keywords=keywords,
    )
    return (picked, "heuristic") if picked else (None, None)


def summarize_cfdi_tax_components(
    cfdi_report: Optional[CFDIReport],
    *,
    fallback_iva: Optional[float] = None,
) -> Dict[str, Any]:
    impuestos = getattr(cfdi_report, "impuestos_detalle", None) or {}
    traslados = list(impuestos.get("traslados") or [])
    retenciones = list(impuestos.get("retenciones") or [])

    iva_trasladado = _money(
        sum(
            _money(item.get("importe"))
            for item in traslados
            if str(item.get("impuesto") or "").strip() == "002"
        )
    )
    if iva_trasladado <= 0:
        iva_trasladado = _money(
            getattr(cfdi_report, "total_impuestos_trasladados", None) or fallback_iva
        )

    normalized_retentions: List[Dict[str, Any]] = []
    for item in retenciones:
        amount = _money(item.get("importe"))
        if amount <= 0:
            continue
        code = str(item.get("impuesto") or "").strip()
        normalized_retentions.append(
            {
                "impuesto": code,
                "label": {"001": "ISR", "002": "IVA", "003": "IEPS"}.get(
                    code, f"Impuesto {code or 'retenido'}"
                ),
                "importe": amount,
            }
        )
    retenciones_total = _money(sum(item["importe"] for item in normalized_retentions))

    # Some imported CFDIs have subtotal/total persisted correctly but an incomplete
    # impuestos_detalle payload and total_impuestos_trasladados=0. In that case the
    # fiscal IVA needed by COI can be recovered from the SAT equation:
    # Total = SubTotal + Trasladados - Retenciones.
    subtotal = _money(getattr(cfdi_report, "subtotal", None))
    total = _money(getattr(cfdi_report, "total", None))
    derived_trasladados = _money(total - subtotal + retenciones_total)
    if subtotal > 0 and total > 0 and derived_trasladados > iva_trasladado:
        iva_trasladado = derived_trasladados

    return {
        "iva_trasladado": iva_trasladado,
        "retenciones": normalized_retentions,
        "retenciones_total": retenciones_total,
    }


async def _resolve_iva_account(
    session: AsyncSession,
    accounts: List[CuentaContable],
) -> Optional[CuentaContable]:
    env_code = os.getenv("ASSISTANT_ACCOUNTING_IVA_CUENTA", "").strip()
    if env_code:
        explicit = await _resolve_cuenta_by_id_or_code(session, cuenta_codigo=env_code)
        if explicit:
            return explicit
    for account in accounts:
        if str(account.codigo or "").strip() == DEFAULT_IVA_ACCOUNT_CODE:
            return account
    return _pick_best_account(
        accounts,
        preferred_types=["iva"],
        keywords=["iva acreditable", "iva", "impuestos acreditables"],
    )


_DEFAULT_RETENTION_ACCOUNT_CODES = {
    "001": DEFAULT_ISR_RETENTION_ACCOUNT_CODE,
    "002": DEFAULT_IVA_RETENTION_ACCOUNT_CODE,
}


async def _resolve_retention_account(
    session: AsyncSession,
    accounts: List[CuentaContable],
    impuesto_code: str,
) -> Optional[CuentaContable]:
    env_var_map = {
        "001": "ASSISTANT_ACCOUNTING_RETENTION_ISR_CUENTA",
        "002": "ASSISTANT_ACCOUNTING_RETENTION_IVA_CUENTA",
        "003": "ASSISTANT_ACCOUNTING_RETENTION_IEPS_CUENTA",
    }
    env_code = os.getenv(env_var_map.get(impuesto_code, ""), "").strip()
    if env_code:
        explicit = await _resolve_cuenta_by_id_or_code(session, cuenta_codigo=env_code)
        if explicit:
            return explicit

    default_code = _DEFAULT_RETENTION_ACCOUNT_CODES.get(impuesto_code)
    if default_code:
        for account in accounts:
            if str(account.codigo or "").strip() == default_code:
                return account
        explicit = await _resolve_cuenta_by_id_or_code(
            session,
            cuenta_codigo=default_code,
        )
        if explicit:
            return explicit

    if impuesto_code == "001":
        return _pick_best_account(
            accounts,
            preferred_types=["pasivo", "retencion"],
            keywords=[
                "retenciones isr",
                "ret isr",
                "isr retenido",
            ],
        )
    if impuesto_code == "002":
        return _pick_best_account(
            accounts,
            preferred_types=["pasivo", "retencion"],
            keywords=[
                "retenciones iva",
                "iva retenido",
            ],
        )
    return _pick_best_account(
        accounts,
        preferred_types=["pasivo", "retencion"],
        keywords=[
            "retencion",
            "impuestos por pagar",
        ],
    )


async def _resolve_explicit_retention_account(
    session: AsyncSession,
    accounts: List[CuentaContable],
    expense: ExpenseReport,
    impuesto_code: str,
) -> Optional[CuentaContable]:
    overrides = getattr(expense, "retencion_cuentas_json", None) or {}
    raw_id = str(overrides.get(impuesto_code) or "").strip()
    if not raw_id:
        return None
    explicit = await _resolve_cuenta_by_id_or_code(
        session,
        cuenta_contable_id=raw_id,
    )
    if explicit is not None:
        return explicit
    for account in accounts:
        if str(getattr(account, "id", "")) == raw_id and getattr(
            account, "activo", True
        ):
            return account
    return None


async def _resolve_hospedaje_tax_account(
    session: AsyncSession,
    accounts: List[CuentaContable],
) -> Optional[CuentaContable]:
    env_code = os.getenv("ASSISTANT_ACCOUNTING_HOSPEDAJE_TAX_CUENTA", "").strip()
    if env_code:
        explicit = await _resolve_cuenta_by_id_or_code(session, cuenta_codigo=env_code)
        if explicit:
            return explicit

    return _pick_best_account(
        accounts,
        preferred_types=["gasto", "pasivo"],
        keywords=[
            "impuesto sobre hospedaje",
            "impuesto hospedaje",
            "impuestos locales",
            "ish",
            "hospedaje",
            "impuestos y derechos",
        ],
    )


async def _resolve_no_deducible_account(
    session: AsyncSession,
    accounts: List[CuentaContable],
    expense: ExpenseReport,
) -> Optional[CuentaContable]:
    env_code = os.getenv("ASSISTANT_ACCOUNTING_NO_DEDUCIBLE_CUENTA", "").strip()
    if env_code:
        explicit = await _resolve_cuenta_by_id_or_code(session, cuenta_codigo=env_code)
        if explicit:
            return explicit

    expense_account = getattr(expense, "cuenta_contable", None)
    expense_code = str(getattr(expense_account, "codigo", "") or "")
    code_parts = expense_code.split("-")
    preferred_prefix = "-".join(code_parts[:2]) if len(code_parts) >= 2 else ""
    same_area_accounts = [
        account
        for account in accounts
        if "deduc" in str(account.nombre or "").lower()
        and (
            not preferred_prefix
            or str(account.codigo or "").startswith(preferred_prefix)
        )
    ]
    if same_area_accounts:
        same_area_accounts.sort(key=lambda account: str(account.codigo or ""))
        return same_area_accounts[0]

    return _pick_best_account(
        accounts,
        preferred_types=["gasto"],
        keywords=["gastos no deducibles", "no deducible", "propina"],
    )


async def build_expense_accounting_preview(
    session: AsyncSession,
    expense: ExpenseReport,
    *,
    contra_cuenta_contable_id: Optional[str] = None,
    contra_cuenta_codigo: Optional[str] = None,
) -> Dict[str, Any]:
    cfdi_report = getattr(expense, "cfdi_report", None)
    if cfdi_report is None and getattr(expense, "cfdi_report_id", None):
        cfdi_report = await session.get(CFDIReport, expense.cfdi_report_id)
    if cfdi_report is None and getattr(expense, "nova_request_id", None):
        result = await session.execute(
            select(CFDIReport).where(
                CFDIReport.nova_request_id == expense.nova_request_id
            )
        )
        cfdi_report = result.scalar_one_or_none()

    taxes = summarize_cfdi_tax_components(
        cfdi_report,
        fallback_iva=getattr(expense, "iva", None),
    )

    contra_account = getattr(expense, "contra_cuenta_contable", None)
    contra_source = "stored" if contra_account else None
    if (
        contra_account is not None
        and _should_prefer_paid_transfer_account(expense)
        and _is_generic_payable_account(contra_account)
    ):
        contra_account = None
        contra_source = None
    if contra_account is None:
        contra_account, contra_source = await resolve_counterpart_account(
            session,
            metodo_pago=getattr(expense, "metodo_pago", None),
            contra_cuenta_contable_id=contra_cuenta_contable_id,
            contra_cuenta_codigo=contra_cuenta_codigo,
        )

    active_accounts = await _load_active_accounts(session)
    tip_amount = _calculate_amex_tip_amount(expense, cfdi_report)
    iva_account = getattr(expense, "cuenta_iva", None)
    if iva_account is None and getattr(expense, "cuenta_iva_id", None):
        explicit_iva = await session.get(CuentaContable, expense.cuenta_iva_id)
        if explicit_iva is not None and getattr(explicit_iva, "activo", True):
            iva_account = explicit_iva
    if taxes["iva_trasladado"] > 0 and iva_account is None:
        iva_account = await _resolve_iva_account(session, active_accounts)

    retention_lines: List[Dict[str, Any]] = []
    notes: List[str] = []
    local_tax_lines: List[Dict[str, Any]] = []
    for item in taxes["retenciones"]:
        account = await _resolve_explicit_retention_account(
            session,
            active_accounts,
            expense,
            item["impuesto"],
        )
        if account is None:
            account = await _resolve_retention_account(
                session, active_accounts, item["impuesto"]
            )
        retention_lines.append(
            {
                **item,
                "account": (
                    {
                        "cuenta_contable_id": str(account.id),
                        "codigo": account.codigo,
                        "nombre": account.nombre,
                    }
                    if account
                    else None
                ),
            }
        )
        if account is None:
            notes.append(f"No se encontró cuenta de retención para {item['label']}.")

    local_tax = resolve_hospedaje_local_tax(
        expense,
        cfdi_report=cfdi_report,
        iva_amount=taxes["iva_trasladado"],
        retenciones_total=taxes["retenciones_total"],
    )
    local_tax_account = None
    local_tax_account_source = None
    if local_tax["amount"] > 0:
        local_tax_account = await _resolve_hospedaje_tax_account(
            session, active_accounts
        )
        if local_tax_account is not None:
            local_tax_account_source = "heuristic"
        elif getattr(expense, "cuenta_contable", None) is not None:
            local_tax_account_source = "expense_account_fallback"
        else:
            notes.append(
                "Hay impuesto local de hospedaje pero no se resolvió cuenta "
                "específica; revisar catálogo."
            )
        local_tax_lines.append(
            {
                "kind": "hospedaje",
                "label": "Impuesto sobre hospedaje",
                "importe": _money(local_tax["amount"]),
                "tasa": local_tax.get("rate"),
                "tasa_pct": local_tax.get("rate_pct"),
                "entidad": local_tax.get("entity"),
                "confirmado": bool(local_tax.get("confirmed")),
                "source": local_tax.get("source"),
                "account": (
                    {
                        "cuenta_contable_id": str(local_tax_account.id),
                        "codigo": local_tax_account.codigo,
                        "nombre": local_tax_account.nombre,
                        "source": local_tax_account_source,
                    }
                    if local_tax_account
                    else (
                        {
                            "cuenta_contable_id": (
                                str(expense.cuenta_contable.id)
                                if getattr(expense, "cuenta_contable", None)
                                else None
                            ),
                            "codigo": (
                                expense.cuenta_contable.codigo
                                if getattr(expense, "cuenta_contable", None)
                                else None
                            ),
                            "nombre": (
                                expense.cuenta_contable.nombre
                                if getattr(expense, "cuenta_contable", None)
                                else None
                            ),
                            "source": local_tax_account_source,
                        }
                        if getattr(expense, "cuenta_contable", None)
                        else None
                    )
                ),
            }
        )
        if local_tax.get("notes"):
            notes.extend(list(local_tax.get("notes") or []))

    non_deductible_lines: List[Dict[str, Any]] = []
    if tip_amount > 0:
        non_deductible_account = await _resolve_no_deducible_account(
            session,
            active_accounts,
            expense,
        )
        non_deductible_lines.append(
            {
                "kind": "propina",
                "label": "Propina no deducible",
                "importe": tip_amount,
                "account": (
                    {
                        "cuenta_contable_id": str(non_deductible_account.id),
                        "codigo": non_deductible_account.codigo,
                        "nombre": non_deductible_account.nombre,
                    }
                    if non_deductible_account
                    else None
                ),
            }
        )
        if non_deductible_account is None:
            notes.append(
                "Hay propina AMEX no deducible pero no se resolvió cuenta "
                "de no deducibles."
            )

    total_amount = _money(getattr(expense, "gasto_cantidad", 0))
    base_amount = _money(
        total_amount
        - taxes["iva_trasladado"]
        - sum(float(item.get("importe") or 0.0) for item in local_tax_lines)
        - sum(float(item.get("importe") or 0.0) for item in non_deductible_lines)
        + taxes["retenciones_total"]
    )
    # `gasto_cantidad` proviene del Total del CFDI, que por definición del SAT
    # ya viene neto de retenciones (Total = Subtotal + Trasladados - Retenidos).
    # La contrapartida (lo realmente pagado al proveedor/banco) es ese neto, y es
    # el valor que cuadra la póliza: base + IVA + locales + no deducibles -
    # retenciones == total_amount. Restar las retenciones de nuevo las contaría
    # dos veces.
    net_credit = _money(max(0.0, total_amount))
    if taxes["retenciones_total"] > 0:
        notes.append(
            "El monto del gasto ya viene neto de retenciones (es el Total del "
            f"CFDI). Contrapartida al neto: {net_credit:.2f} "
            f"(retenciones {taxes['retenciones_total']:.2f} ya descontadas en el "
            "Total)."
        )
    if taxes["iva_trasladado"] > 0 and iva_account is None:
        notes.append(
            "Hay IVA trasladado pero no se resolvió cuenta de IVA acreditable."
        )

    return {
        "contra_account": (
            {
                "cuenta_contable_id": str(contra_account.id),
                "codigo": contra_account.codigo,
                "nombre": contra_account.nombre,
                "source": contra_source,
            }
            if contra_account
            else None
        ),
        "taxes": {
            "base_gasto": base_amount,
            "iva_trasladado": taxes["iva_trasladado"],
            "iva_account": (
                {
                    "cuenta_contable_id": str(iva_account.id),
                    "codigo": iva_account.codigo,
                    "nombre": iva_account.nombre,
                }
                if iva_account
                else None
            ),
            "retenciones": retention_lines,
            "retenciones_total": taxes["retenciones_total"],
            "impuestos_locales": local_tax_lines,
            "impuestos_locales_total": _money(
                sum(float(item.get("importe") or 0.0) for item in local_tax_lines)
            ),
            "gastos_no_deducibles": non_deductible_lines,
            "gastos_no_deducibles_total": _money(
                sum(float(item.get("importe") or 0.0) for item in non_deductible_lines)
            ),
            "neto_contrapartida": net_credit,
        },
        "notes": notes,
    }


__all__ = [
    "build_expense_accounting_preview",
    "resolve_counterpart_account",
    "summarize_cfdi_tax_components",
]
