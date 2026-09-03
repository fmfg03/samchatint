from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    AccountingPoliza,
    AccountingPolizaLine,
    BudgetConcept,
    CFDIReport,
    CuentaContable,
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
    Reembolso,
)
from .amex_expense_service import employee_paid_sql_condition
from .expense_accounting_service import build_expense_accounting_preview
from .expense_accounting_service import _resolve_project_no_deducible_account


DEBTOR_ACCOUNT_ROOT_CODE = "1170-001-000"
DEBTOR_ACCOUNT_PREFIX = "1170-001-"
DEBTOR_SOCIO_ACCOUNT_ROOT_CODE = "1170-002-000"
DEBTOR_SOCIO_ACCOUNT_PREFIX = "1170-002-"
PARTNER_DEBTOR_ACCOUNT_ROOT_CODE = DEBTOR_SOCIO_ACCOUNT_ROOT_CODE
PARTNER_DEBTOR_ACCOUNT_PREFIX = DEBTOR_SOCIO_ACCOUNT_PREFIX
# Canonical catalog: 1170-001 = employees, 1170-002 = socios.  New postings do
# not tolerate the historical 1700 typo: accepting it would silently post to a
# different chart-of-accounts branch.
DEBTOR_ACCOUNT_PREFIXES = (
    DEBTOR_ACCOUNT_PREFIX,
    DEBTOR_SOCIO_ACCOUNT_PREFIX,
)
DEBTOR_ACCOUNT_ROOT_CODES = (
    DEBTOR_ACCOUNT_ROOT_CODE,
    DEBTOR_SOCIO_ACCOUNT_ROOT_CODE,
)
PETTY_CASH_DEBTOR_ACCOUNT_CODES = {
    "caja_chica_usd": "1110-001-001",
    "caja_chica_pesos": "1110-001-002",
}
PETTY_CASH_DEBTOR_ACCOUNT_CODE_SET = set(PETTY_CASH_DEBTOR_ACCOUNT_CODES.values())
SANTANDER_BANK_ACCOUNT_CODE = "1120-001-001"
PARTNER_DEBTOR_NAME_MARKERS = (
    "federico gonzalez",
    "jose odilon",
    "odilon trujillo",
    "luis angel",
)
DEBTOR_ORIGINS = {
    "deudores_anticipo",
    "deudores_comprobacion",
    "deudores_devolucion",
}
MONEY_QUANT = Decimal("0.01")


@dataclass(slots=True)
class DebtorPostingResult:
    status: str
    reason: Optional[str] = None
    poliza: Optional[AccountingPoliza] = None
    debtor_account: Optional[CuentaContable] = None
    bank_account: Optional[CuentaContable] = None
    liability_account: Optional[CuentaContable] = None


def _format_missing_expense_accounts(expenses: Iterable[ExpenseReport]) -> str:
    """Build a bounded, readable blocker reason for expenses missing accounts."""

    missing = []
    for expense in expenses:
        if getattr(expense, "cuenta_contable", None) is not None:
            continue
        ref = (getattr(expense, "numero_referencia", None) or "").strip()
        concepto = (getattr(expense, "concepto", None) or "").strip()
        if len(concepto) > 80:
            concepto = concepto[:77].rstrip() + "..."
        if ref and concepto:
            missing.append(f"{ref} {concepto}")
        elif ref:
            missing.append(ref)
        elif concepto:
            missing.append(concepto)
        else:
            missing.append(str(getattr(expense, "id", "gasto sin referencia")))
    if not missing:
        return "expense_missing_accounts"
    visible = missing[:8]
    remainder = len(missing) - len(visible)
    suffix = f"; y {remainder} más" if remainder > 0 else ""
    return "expense_missing_accounts:" + "; ".join(visible) + suffix


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _money_float(value: Any) -> float:
    return float(_money(value))


def _naive_utc_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    return datetime.combine(value, datetime.min.time())


def _normalize_text(value: Any) -> str:
    raw = str(value or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(ascii_text.split())


def _normalize_petty_cash_type(value: object) -> Optional[str]:
    raw = str(value or "").strip().lower()
    return raw if raw in PETTY_CASH_DEBTOR_ACCOUNT_CODES else None


def _is_debtor_or_petty_cash_line_code(value: object) -> bool:
    code = str(value or "")
    return code.startswith(DEBTOR_ACCOUNT_PREFIXES) or code in PETTY_CASH_DEBTOR_ACCOUNT_CODE_SET


def _account_label(account: Optional[CuentaContable]) -> str:
    if account is None:
        return "Sin cuenta"
    return f"{account.codigo} · {account.nombre}"


def _name_tokens(value: Any) -> list[str]:
    return [token for token in _normalize_text(value).split() if len(token) > 1]


def is_partner_debtor_employee(empleado: Optional[Empleado]) -> bool:
    employee_name = _normalize_text(getattr(empleado, "nombre", None))
    return any(marker in employee_name for marker in PARTNER_DEBTOR_NAME_MARKERS)


def debtor_account_prefixes_for_employee(empleado: Optional[Empleado]) -> tuple[str, ...]:
    if is_partner_debtor_employee(empleado):
        return (PARTNER_DEBTOR_ACCOUNT_PREFIX, DEBTOR_ACCOUNT_PREFIX)
    return (DEBTOR_ACCOUNT_PREFIX,)


def debtor_account_block_label_for_employee(empleado: Optional[Empleado]) -> str:
    prefixes = debtor_account_prefixes_for_employee(empleado)
    return " / ".join(prefix.rstrip("-") for prefix in prefixes)


def _is_debtor_account_code(cuenta_codigo: Any) -> bool:
    code = str(cuenta_codigo or "")
    return any(code.startswith(prefix) for prefix in DEBTOR_ACCOUNT_PREFIXES)


def _document_system_reference(
    documento: Optional[Documento] = None,
    cuenta: Optional[CuentaDeGastos] = None,
) -> str:
    doc_ref = str(getattr(documento, "numero_referencia", None) or "").strip()
    if doc_ref:
        return doc_ref
    cuenta_ref = str(getattr(cuenta, "referencia_base", None) or "").strip()
    if cuenta_ref:
        return cuenta_ref if cuenta_ref.startswith("I-") else f"I-{cuenta_ref}"
    return ""


def format_samchat_poliza_concept(
    base: Any,
    *,
    documento: Optional[Documento] = None,
    cuenta: Optional[CuentaDeGastos] = None,
) -> str:
    parts: list[str] = []
    ref_ops = str(getattr(documento, "referencia_operaciones", None) or "").strip()
    if ref_ops:
        parts.append(f"REF {ref_ops}")
    system_ref = _document_system_reference(documento=documento, cuenta=cuenta)
    if system_ref:
        parts.append(system_ref)
    prefix = " / ".join(parts)
    clean_base = str(base or "Movimiento contable").strip()
    return f"{prefix} - {clean_base}" if prefix else clean_base


def _debtor_account_match_score(employee_name: Any, account_name: Any) -> int:
    """Score an employee name against a debtor subaccount label.

    The accounting catalog often omits middle names: e.g. employee
    ``CARLOS FELIPE LOZANO PARDINAS`` may be catalogued as
    ``CARLOS LOZANO PARDINAS``. Accept that shape only when the first token,
    final surname token, and at least one additional token overlap. Ambiguous
    equal scores are still rejected by the resolver.
    """
    employee_norm = _normalize_text(employee_name)
    account_norm = _normalize_text(account_name)
    if not employee_norm or not account_norm:
        return 0
    if employee_norm == account_norm:
        return 120
    if employee_norm in account_norm or account_norm in employee_norm:
        return 110

    employee_tokens = _name_tokens(employee_norm)
    account_tokens = _name_tokens(account_norm)
    if not employee_tokens or not account_tokens:
        return 0
    employee_set = set(employee_tokens)
    account_set = set(account_tokens)
    overlap = employee_set & account_set

    if employee_set <= account_set:
        return 100

    # Allow omitted middle names while keeping enough identity anchors.
    first_matches = employee_tokens[0] in account_set
    last_matches = employee_tokens[-1] in account_set
    enough_overlap = len(overlap) >= min(3, len(employee_set))
    if first_matches and last_matches and enough_overlap:
        return 90 + len(overlap)

    # Some catalog labels may only preserve surnames plus one given name, but
    # never accept fewer than three overlapping identity tokens.
    if len(overlap) >= 3 and last_matches:
        return 80 + len(overlap)

    return 0


def _debtor_name_match_score(employee_name: Any, account_name: Any) -> int:
    return _debtor_account_match_score(employee_name, account_name)


async def resolve_employee_debtor_account(
    session: AsyncSession,
    empleado: Empleado,
) -> Optional[CuentaContable]:
    employee_name = _normalize_text(getattr(empleado, "nombre", None))
    if not employee_name:
        return None
    prefixes = debtor_account_prefixes_for_employee(empleado)
    result = await session.execute(
        select(CuentaContable)
        .where(
            CuentaContable.activo.is_(True),
            or_(*(CuentaContable.codigo.like(f"{prefix}%") for prefix in prefixes)),
            CuentaContable.codigo.notin_(DEBTOR_ACCOUNT_ROOT_CODES),
        )
        .order_by(CuentaContable.codigo.asc())
    )
    candidates = list(result.scalars().all())
    scored = [
        (_debtor_account_match_score(employee_name, account.nombre), account)
        for account in candidates
    ]
    scored = [(score, account) for score, account in scored if score > 0]
    if not scored:
        return None
    best_score = max(score for score, _account in scored)
    best_matches = [account for score, account in scored if score == best_score]
    return best_matches[0] if len(best_matches) == 1 else None


async def resolve_cuenta_debtor_empleado(
    session: AsyncSession,
    cuenta: Optional[CuentaDeGastos],
) -> Optional[Empleado]:
    """Return the employee whose debtor auxiliary owns a cuenta.

    ``CuentaDeGastos.empleado_id`` is the authenticated requester/capturer.
    For third-party expense reports and AMEX comprobación, the accounting debtor
    must be the employee beneficiary/cardholder when one is explicitly stored.
    Existing reports without a beneficiary keep the historical requester fallback.
    """
    if cuenta is None:
        return None
    beneficiary_id = getattr(cuenta, "beneficiario_empleado_id", None)
    owner_id = beneficiary_id or getattr(cuenta, "empleado_id", None)
    if owner_id is None:
        return None
    return await session.get(Empleado, owner_id)


async def resolve_cuenta_debtor_account(
    session: AsyncSession,
    cuenta: CuentaDeGastos | None,
    empleado: Empleado | None,
) -> Optional[CuentaContable]:
    petty_cash_type = _normalize_petty_cash_type(
        getattr(cuenta, "beneficiario_alterno_tipo", None) if cuenta is not None else None
    )
    if petty_cash_type:
        code = PETTY_CASH_DEBTOR_ACCOUNT_CODES[petty_cash_type]
        result = await session.execute(
            select(CuentaContable).where(
                CuentaContable.codigo == code,
                CuentaContable.activo.is_(True),
            )
        )
        return result.scalar_one_or_none()
    if empleado is None:
        return None
    return await resolve_employee_debtor_account(session, empleado)


async def resolve_default_bank_account(
    session: AsyncSession,
) -> Optional[CuentaContable]:
    """Resolve the single canonical Santander account, or fail closed.

    Accounting postings must never fall back to the first account whose type is
    ``banco``: the catalog also contains parent/summary accounts and other banks.
    """
    result = await session.execute(
        select(CuentaContable)
        .where(
            CuentaContable.activo.is_(True),
            CuentaContable.codigo == SANTANDER_BANK_ACCOUNT_CODE,
        )
    )
    return result.scalar_one_or_none()


def _event_poliza_number(event: str, entity_id: UUID) -> str:
    """Stable event identity (event type + complete entity UUID)."""
    return f"LAM-{event}-{entity_id}"


async def _existing_event_poliza(
    session: AsyncSession,
    *,
    origen: str,
    numero_poliza: str,
    legacy_numero_poliza: Optional[str] = None,
) -> Optional[AccountingPoliza]:
    existing = await _existing_poliza(
        session, origen=origen, numero_poliza=numero_poliza
    )
    if existing is None and legacy_numero_poliza:
        existing = await _existing_poliza(
            session,
            origen=origen,
            numero_poliza=legacy_numero_poliza,
        )
    return existing


def _account_from_preview(value: Any) -> tuple[Optional[str], Optional[UUID]]:
    account = value or {}
    code = str(account.get("codigo") or "").strip() or None
    raw_id = account.get("cuenta_contable_id")
    try:
        account_id = UUID(str(raw_id)) if raw_id else None
    except (TypeError, ValueError):
        account_id = None
    return code, account_id


def _preview_expense_lines(
    *,
    preview: dict[str, Any],
    expense: ExpenseReport,
    counterpart: CuentaContable,
    meta: dict[str, Any],
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Translate the canonical accounting preview into balanced journal lines.

    Every non-zero tax component must carry an explicit account binding.  This
    is intentionally fail-closed; no generic account is substituted here.
    """
    taxes = preview.get("taxes") or {}
    concept = str(getattr(expense, "concepto", None) or "Gasto")
    base_account = getattr(expense, "cuenta_contable", None)
    base_amount = _money(taxes.get("base_gasto"))
    lines: list[dict[str, Any]] = []

    if base_amount > 0:
        if base_account is None:
            return [], "missing_expense_account"
        lines.append(
            {
                "cuenta_codigo": base_account.codigo,
                "cuenta_contable_id": base_account.id,
                "concepto": concept,
                "debe": base_amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_gasto"},
            }
        )

    iva_amount = _money(taxes.get("iva_trasladado"))
    if iva_amount > 0:
        code, account_id = _account_from_preview(taxes.get("iva_account"))
        if not code or account_id is None:
            return [], "missing_iva_account"
        lines.append(
            {
                "cuenta_codigo": code,
                "cuenta_contable_id": account_id,
                "concepto": f"IVA acreditable - {concept}",
                "debe": iva_amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_iva"},
            }
        )

    for item in list(taxes.get("impuestos_locales") or []):
        amount = _money(item.get("importe"))
        if amount <= 0:
            continue
        code, account_id = _account_from_preview(item.get("account"))
        if not code or account_id is None:
            return [], "missing_local_tax_account"
        lines.append(
            {
                "cuenta_codigo": code,
                "cuenta_contable_id": account_id,
                "concepto": f"{item.get('label') or 'Impuesto local'} - {concept}",
                "debe": amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_impuesto_local"},
            }
        )

    for item in list(taxes.get("gastos_no_deducibles") or []):
        amount = _money(item.get("importe"))
        if amount <= 0:
            continue
        code, account_id = _account_from_preview(item.get("account"))
        if not code or account_id is None:
            return [], "missing_non_deductible_account"
        lines.append(
            {
                "cuenta_codigo": code,
                "cuenta_contable_id": account_id,
                "concepto": f"{item.get('label') or 'No deducible'} - {concept}",
                "debe": amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_no_deducible"},
            }
        )

    for item in list(taxes.get("retenciones") or []):
        amount = _money(item.get("importe"))
        if amount <= 0:
            continue
        code, account_id = _account_from_preview(item.get("account"))
        if not code or account_id is None:
            return [], "missing_retention_account"
        lines.append(
            {
                "cuenta_codigo": code,
                "cuenta_contable_id": account_id,
                "concepto": f"Retención {item.get('label') or 'impuesto'} - {concept}",
                "debe": 0,
                "haber": amount,
                "raw_row_json": {**meta, "movement": "haber_retencion"},
            }
        )

    net_credit = _money(taxes.get("neto_contrapartida"))
    if net_credit <= 0:
        return [], "invalid_counterpart_amount"
    lines.append(
        {
            "cuenta_codigo": counterpart.codigo,
            "cuenta_contable_id": counterpart.id,
            "concepto": concept,
            "debe": 0,
            "haber": net_credit,
            "raw_row_json": {**meta, "movement": "haber_contrapartida"},
        }
    )
    debit = sum((_money(line.get("debe")) for line in lines), Decimal("0.00"))
    credit = sum((_money(line.get("haber")) for line in lines), Decimal("0.00"))
    if debit != credit:
        return [], f"unbalanced_preview:{debit}:{credit}"
    return lines, None


async def _existing_poliza(
    session: AsyncSession,
    *,
    origen: str,
    numero_poliza: str,
) -> Optional[AccountingPoliza]:
    result = await session.execute(
        select(AccountingPoliza)
        .options(selectinload(AccountingPoliza.lines))
        .where(
            AccountingPoliza.origen == origen,
            AccountingPoliza.numero_poliza == numero_poliza,
        )
    )
    return result.scalar_one_or_none()


async def _load_poliza_lines(
    session: AsyncSession,
    poliza: AccountingPoliza,
) -> list[AccountingPolizaLine]:
    cached = getattr(poliza, "__dict__", {}).get("lines")
    if cached is not None:
        return list(cached)
    result = await session.execute(
        select(AccountingPolizaLine)
        .where(AccountingPolizaLine.poliza_id == poliza.id)
        .order_by(AccountingPolizaLine.line_no.asc())
    )
    return list(result.scalars().all())


async def _create_poliza(
    session: AsyncSession,
    *,
    origen: str,
    numero_poliza: str,
    fecha: datetime,
    beneficiario_nombre: str,
    concepto: str,
    lines: list[dict[str, Any]],
) -> AccountingPoliza:
    poliza = AccountingPoliza(
        id=uuid4(),
        source_file="samchat:auto_deudores",
        source_sheet=origen,
        source_row_start=None,
        tipo_poliza="Diario",
        numero_poliza=numero_poliza,
        fecha_poliza=_naive_utc_datetime(fecha),
        beneficiario_nombre=beneficiario_nombre,
        concepto=concepto,
        concepto_resumen=concepto,
        line_count_declared=len(lines),
        line_count_actual=len(lines),
        origen=origen,
    )
    session.add(poliza)
    await session.flush()
    for idx, line in enumerate(lines, start=1):
        session.add(
            AccountingPolizaLine(
                id=uuid4(),
                poliza=poliza,
                line_no=idx,
                cuenta_codigo=line["cuenta_codigo"],
                cuenta_contable_id=line.get("cuenta_contable_id"),
                concepto=line.get("concepto"),
                movimiento_no=str(idx),
                debe=_money_float(line.get("debe")),
                haber=_money_float(line.get("haber")),
                raw_row_json=line.get("raw_row_json") or {},
            )
        )
    return poliza


def _common_meta(
    *,
    source_key: str,
    empleado: Empleado,
    cuenta_gastos_id: Optional[UUID],
    documento_id: Optional[UUID],
) -> dict[str, Any]:
    return {
        "origin": source_key,
        "empleado_id": str(empleado.id),
        "empleado_nombre": empleado.nombre,
        "cuenta_gastos_id": str(cuenta_gastos_id) if cuenta_gastos_id else None,
        "documento_id": str(documento_id) if documento_id else None,
    }


async def _load_budget_accounts(
    session: AsyncSession,
    documento: Documento,
) -> tuple[Optional[BudgetConcept], Optional[CuentaContable], Optional[CuentaContable]]:
    concept_id = getattr(documento, "budget_concept_id", None)
    if concept_id is None:
        return None, None, None
    result = await session.execute(
        select(BudgetConcept)
        .options(
            selectinload(BudgetConcept.cuenta_contable),
            selectinload(BudgetConcept.pasivo_cuenta_contable),
        )
        .where(BudgetConcept.id == concept_id, BudgetConcept.active.is_(True))
    )
    concept = result.scalar_one_or_none()
    if concept is None:
        return None, None, None
    expense_account = getattr(concept, "cuenta_contable", None)
    liability_account = getattr(concept, "pasivo_cuenta_contable", None)
    if expense_account is not None and not getattr(expense_account, "activo", True):
        expense_account = None
    if liability_account is not None and not getattr(liability_account, "activo", True):
        liability_account = None
    return concept, expense_account, liability_account


async def ensure_provider_approval_posting(
    session: AsyncSession,
    *,
    documento: Documento,
) -> DebtorPostingResult:
    """Accrue an approved third-party request against its budget liability.

    Employee reimbursement/advance documents may carry a provider mirror for UI
    compatibility.  They are explicitly excluded here and remain no-op on
    approval as required by the accounting flow.
    """
    if (
        getattr(documento, "tipo", None) != "SOLICITUD"
        or getattr(documento, "proveedor_cliente_id", None) is None
        or getattr(documento, "beneficiario_empleado_id", None) is not None
    ):
        return DebtorPostingResult(status="skipped", reason="not_provider_request")

    numero_poliza = _event_poliza_number("PROV-APR", documento.id)
    existing = await _existing_event_poliza(
        session,
        origen="proveedor_aprobacion",
        numero_poliza=numero_poliza,
    )
    if existing is not None:
        return DebtorPostingResult(status="exists", poliza=existing)

    concept, expense_account, liability = await _load_budget_accounts(
        session, documento
    )
    if concept is None:
        return DebtorPostingResult(status="pending", reason="missing_budget_concept")
    if expense_account is None:
        return DebtorPostingResult(status="pending", reason="missing_expense_account")
    if liability is None:
        return DebtorPostingResult(status="pending", reason="missing_budget_liability")

    amount = _money(documento.monto_solicitado or documento.monto_total)
    if amount <= 0:
        return DebtorPostingResult(status="skipped", reason="invalid_amount")
    cfdi = None
    if getattr(documento, "cfdi_report_id", None):
        cfdi = await session.get(CFDIReport, documento.cfdi_report_id)

    synthetic = ExpenseReport(
        proyecto=str(getattr(concept, "tournament_name", None) or "Solicitud"),
        concepto=str(
            getattr(documento, "concepto_pago", None)
            or getattr(documento, "notas", None)
            or "Solicitud a proveedor"
        ),
        gasto_cantidad=float(amount),
        fecha=getattr(documento, "aprobado_en", None) or datetime.utcnow(),
        tipo_gasto="manual",
        metodo_pago=getattr(documento, "metodo_pago", None) or "TRANSFERENCIA",
        iva=None,
        budget_concept_id=concept.id,
        cuenta_contable_id=expense_account.id,
        cfdi_report_id=getattr(documento, "cfdi_report_id", None),
    )
    synthetic.cuenta_contable = expense_account
    synthetic.cfdi_report = cfdi

    # Without CFDI the whole request is non-deductible until a factura is linked.
    if cfdi is None:
        no_deductible = await _resolve_project_no_deducible_account(
            session, [], synthetic
        )
        if no_deductible is None:
            return DebtorPostingResult(
                status="pending", reason="missing_non_deductible_account"
            )
        synthetic.cuenta_contable_id = no_deductible.id
        synthetic.cuenta_contable = no_deductible

    preview = await build_expense_accounting_preview(
        session,
        synthetic,
        contra_cuenta_contable_id=str(liability.id),
        contra_cuenta_codigo=liability.codigo,
    )
    meta = {
        "origin": "proveedor_aprobacion",
        "documento_id": str(documento.id),
        "budget_concept_id": str(concept.id),
        "event": "provider_approved",
    }
    lines, error = _preview_expense_lines(
        preview=preview,
        expense=synthetic,
        counterpart=liability,
        meta=meta,
    )
    if error:
        return DebtorPostingResult(status="pending", reason=error)
    concepto = f"Proveedor aprobado - {documento.numero_referencia or documento.id}"
    poliza = await _create_poliza(
        session,
        origen="proveedor_aprobacion",
        numero_poliza=numero_poliza,
        fecha=getattr(documento, "aprobado_en", None) or datetime.utcnow(),
        beneficiario_nombre=str(
            getattr(getattr(documento, "proveedor_cliente", None), "nombre", None)
            or "Proveedor"
        ),
        concepto=concepto,
        lines=lines,
    )
    return DebtorPostingResult(
        status="created", poliza=poliza, liability_account=liability
    )


async def ensure_provider_payment_posting(
    session: AsyncSession,
    *,
    documento: Documento,
    fecha_pago: date | datetime,
) -> DebtorPostingResult:
    if (
        getattr(documento, "tipo", None) != "SOLICITUD"
        or getattr(documento, "proveedor_cliente_id", None) is None
        or getattr(documento, "beneficiario_empleado_id", None) is not None
    ):
        return DebtorPostingResult(status="skipped", reason="not_provider_request")
    numero_poliza = _event_poliza_number("PROV-PAY", documento.id)
    existing = await _existing_event_poliza(
        session,
        origen="proveedor_pago",
        numero_poliza=numero_poliza,
    )
    if existing is not None:
        return DebtorPostingResult(status="exists", poliza=existing)

    # Legacy approved documents may predate the approval hook.  Materialize the
    # missing accrual in the same transaction before clearing it; never clear a
    # liability that has no source posting.
    approval = await ensure_provider_approval_posting(session, documento=documento)
    if approval.status == "pending":
        return approval
    _, _, liability = await _load_budget_accounts(session, documento)
    if liability is None:
        return DebtorPostingResult(status="pending", reason="missing_budget_liability")
    bank = await resolve_default_bank_account(session)
    if bank is None:
        return DebtorPostingResult(status="pending", reason="missing_santander_account")

    # The payable equals the credit posted to the configured liability account.
    approval_poliza = approval.poliza
    if approval_poliza is None:
        approval_poliza = await _existing_poliza(
            session,
            origen="proveedor_aprobacion",
            numero_poliza=_event_poliza_number("PROV-APR", documento.id),
        )
    if approval_poliza is None:
        return DebtorPostingResult(status="pending", reason="missing_provider_accrual")
    approval_lines = await _load_poliza_lines(session, approval_poliza)
    amount = sum(
        (
            _money(line.haber)
            for line in approval_lines
            if str(getattr(line, "cuenta_codigo", "")) == liability.codigo
        ),
        Decimal("0.00"),
    )
    if amount <= 0:
        return DebtorPostingResult(status="pending", reason="missing_liability_credit")
    fecha_dt = (
        fecha_pago
        if isinstance(fecha_pago, datetime)
        else datetime.combine(fecha_pago, datetime.min.time())
    )
    concepto = f"Pago a proveedor - {documento.numero_referencia or documento.id}"
    meta = {
        "origin": "proveedor_pago",
        "documento_id": str(documento.id),
        "event": "provider_paid",
    }
    poliza = await _create_poliza(
        session,
        origen="proveedor_pago",
        numero_poliza=numero_poliza,
        fecha=fecha_dt,
        beneficiario_nombre=str(
            getattr(getattr(documento, "proveedor_cliente", None), "nombre", None)
            or "Proveedor"
        ),
        concepto=concepto,
        lines=[
            {
                "cuenta_codigo": liability.codigo,
                "cuenta_contable_id": liability.id,
                "concepto": concepto,
                "debe": amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_pasivo"},
            },
            {
                "cuenta_codigo": bank.codigo,
                "cuenta_contable_id": bank.id,
                "concepto": concepto,
                "debe": 0,
                "haber": amount,
                "raw_row_json": {**meta, "movement": "haber_banco"},
            },
        ],
    )
    return DebtorPostingResult(
        status="created",
        poliza=poliza,
        bank_account=bank,
        liability_account=liability,
    )


async def ensure_debtor_payment_posting_for_document(
    session: AsyncSession,
    *,
    documento: Documento,
    empleado: Empleado,
    fecha_pago: date | datetime,
    require_employee_beneficiary: bool = True,
) -> DebtorPostingResult:
    cuenta_gastos_id = getattr(documento, "cuenta_gastos_id", None)
    if cuenta_gastos_id is None:
        return DebtorPostingResult(status="skipped", reason="not_cuenta_payment")
    if (
        require_employee_beneficiary
        and getattr(documento, "beneficiario_empleado_id", None) is None
    ):
        return DebtorPostingResult(status="skipped", reason="not_employee_cuenta_payment")
    numero_poliza = _event_poliza_number("DEU-PAY", documento.id)
    existing = await _existing_event_poliza(
        session,
        origen="deudores_anticipo",
        numero_poliza=numero_poliza,
        legacy_numero_poliza=f"DEU-PAG-{str(documento.id)[:8]}",
    )
    if existing is not None:
        return DebtorPostingResult(status="exists", poliza=existing)

    cuenta = await session.get(CuentaDeGastos, cuenta_gastos_id)
    debtor = await resolve_cuenta_debtor_account(session, cuenta, empleado)
    if debtor is None:
        return DebtorPostingResult(status="pending", reason="missing_employee_debtor_account")
    bank = await resolve_default_bank_account(session)
    if bank is None:
        return DebtorPostingResult(status="pending", reason="missing_santander_account")

    amount = _money(documento.monto_solicitado or documento.monto_total)
    if amount <= 0:
        return DebtorPostingResult(status="skipped", reason="invalid_amount")
    fecha_dt = (
        fecha_pago
        if isinstance(fecha_pago, datetime)
        else datetime.combine(fecha_pago, datetime.min.time())
    )
    meta = _common_meta(
        source_key="deudores_anticipo",
        empleado=empleado,
        cuenta_gastos_id=cuenta_gastos_id,
        documento_id=documento.id,
    )
    concepto = format_samchat_poliza_concept(
        "Pago a deudor empleado",
        documento=documento,
    )
    poliza = await _create_poliza(
        session,
        origen="deudores_anticipo",
        numero_poliza=numero_poliza,
        fecha=fecha_dt,
        beneficiario_nombre=empleado.nombre,
        concepto=concepto,
        lines=[
            {
                "cuenta_codigo": debtor.codigo,
                "cuenta_contable_id": debtor.id,
                "concepto": concepto,
                "debe": amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_deudor"},
            },
            {
                "cuenta_codigo": bank.codigo,
                "cuenta_contable_id": bank.id,
                "concepto": concepto,
                "debe": 0,
                "haber": amount,
                "raw_row_json": {**meta, "movement": "haber_banco"},
            },
        ],
    )
    return DebtorPostingResult(
        status="created", poliza=poliza, debtor_account=debtor, bank_account=bank
    )


async def ensure_debtor_comprobacion_posting_for_informe(
    session: AsyncSession,
    *,
    informe_documento: Documento,
) -> DebtorPostingResult:
    cuenta_gastos_id = getattr(informe_documento, "cuenta_gastos_id", None)
    if cuenta_gastos_id is None or informe_documento.tipo != "INFORME":
        return DebtorPostingResult(status="skipped", reason="not_informe_cuenta")
    numero_poliza = _event_poliza_number("DEU-COMP", informe_documento.id)
    existing = await _existing_event_poliza(
        session,
        origen="deudores_comprobacion",
        numero_poliza=numero_poliza,
        legacy_numero_poliza=f"DEU-COMP-{str(cuenta_gastos_id)[:8]}",
    )
    if existing is not None:
        return DebtorPostingResult(status="exists", poliza=existing)

    cuenta = await session.get(CuentaDeGastos, cuenta_gastos_id)
    empleado = await resolve_cuenta_debtor_empleado(session, cuenta)
    if empleado is None:
        return DebtorPostingResult(status="pending", reason="missing_employee")
    debtor = await resolve_cuenta_debtor_account(session, cuenta, empleado)
    if debtor is None:
        return DebtorPostingResult(status="pending", reason="missing_employee_debtor_account")

    result = await session.execute(
        select(ExpenseReport)
        .options(
            selectinload(ExpenseReport.cuenta_contable),
            selectinload(ExpenseReport.cuenta_iva),
            selectinload(ExpenseReport.cfdi_report),
        )
        .where(
            ExpenseReport.cuenta_gastos_id == cuenta_gastos_id,
            ExpenseReport.estado_gasto != "cancelado",
            employee_paid_sql_condition(),
        )
        .order_by(ExpenseReport.fecha.asc(), ExpenseReport.created_at.asc())
    )
    expenses = list(result.scalars().all())
    if not expenses:
        return DebtorPostingResult(status="skipped", reason="no_employee_paid_expenses")

    lines: list[dict[str, Any]] = []
    total_credit = Decimal("0.00")
    meta_base = _common_meta(
        source_key="deudores_comprobacion",
        empleado=empleado,
        cuenta_gastos_id=cuenta_gastos_id,
        documento_id=informe_documento.id,
    )
    missing_account_reason = _format_missing_expense_accounts(expenses)
    if missing_account_reason != "expense_missing_accounts":
        return DebtorPostingResult(
            status="pending",
            reason=missing_account_reason,
            debtor_account=debtor,
        )

    for expense in expenses:
        preview = await build_expense_accounting_preview(
            session,
            expense,
            contra_cuenta_contable_id=str(debtor.id),
            contra_cuenta_codigo=debtor.codigo,
        )
        expense_meta = {**meta_base, "expense_id": str(expense.id)}
        expense_lines, error = _preview_expense_lines(
            preview=preview,
            expense=expense,
            counterpart=debtor,
            meta=expense_meta,
        )
        if error:
            return DebtorPostingResult(
                status="pending",
                reason=f"expense_accounting_incomplete:{expense.id}:{error}",
                debtor_account=debtor,
            )
        for line in expense_lines:
            line["concepto"] = format_samchat_poliza_concept(
                line.get("concepto"),
                documento=informe_documento,
                cuenta=cuenta,
            )
        lines.extend(expense_lines)
        total_credit += sum(
            (
                _money(line.get("haber"))
                for line in expense_lines
                if line.get("cuenta_codigo") == debtor.codigo
            ),
            Decimal("0.00"),
        )
    if total_credit <= 0:
        return DebtorPostingResult(status="skipped", reason="invalid_total")

    concepto = format_samchat_poliza_concept(
        "Comprobación de gastos",
        documento=informe_documento,
        cuenta=cuenta,
    )
    # Each expense already includes its own exact debtor credit, keeping the
    # evidence binding and fiscal split local to that expense.
    poliza = await _create_poliza(
        session,
        origen="deudores_comprobacion",
        numero_poliza=numero_poliza,
        fecha=informe_documento.aprobado_en or datetime.utcnow(),
        beneficiario_nombre=empleado.nombre,
        concepto=concepto,
        lines=lines,
    )
    return DebtorPostingResult(status="created", poliza=poliza, debtor_account=debtor)


async def ensure_debtor_settlement_posting(
    session: AsyncSession,
    *,
    reembolso: Reembolso,
    cuenta: CuentaDeGastos,
) -> DebtorPostingResult:
    if (reembolso.estado or "") == "cancelado":
        return DebtorPostingResult(status="skipped", reason="cancelled")
    numero_poliza = _event_poliza_number("DEU-LIQ", reembolso.id)
    existing = await _existing_event_poliza(
        session,
        origen="deudores_devolucion",
        numero_poliza=numero_poliza,
        legacy_numero_poliza=f"DEU-LIQ-{str(reembolso.id)[:8]}",
    )
    if existing is not None:
        return DebtorPostingResult(status="exists", poliza=existing)
    empleado = await resolve_cuenta_debtor_empleado(session, cuenta)
    if empleado is None:
        return DebtorPostingResult(status="pending", reason="missing_employee")
    debtor = await resolve_cuenta_debtor_account(session, cuenta, empleado)
    if debtor is None:
        return DebtorPostingResult(status="pending", reason="missing_employee_debtor_account")
    bank = await resolve_default_bank_account(session)
    if bank is None:
        return DebtorPostingResult(status="pending", reason="missing_santander_account")
    amount = _money(reembolso.monto)
    if amount <= 0:
        return DebtorPostingResult(status="skipped", reason="invalid_amount")
    fecha_dt = reembolso.fecha_pago or reembolso.creado_en or datetime.utcnow()
    meta = _common_meta(
        source_key="deudores_devolucion",
        empleado=empleado,
        cuenta_gastos_id=cuenta.id,
        documento_id=reembolso.documento_id,
    )
    meta["reembolso_id"] = str(reembolso.id)
    tipo = (reembolso.tipo or "reembolso").strip().lower()
    documento = (
        await session.get(Documento, reembolso.documento_id)
        if reembolso.documento_id
        else None
    )
    concepto = format_samchat_poliza_concept(
        f"{'Devolución' if tipo == 'devolucion' else 'Reembolso'} de informe",
        documento=documento,
        cuenta=cuenta,
    )
    if tipo == "devolucion":
        lines = [
            {
                "cuenta_codigo": bank.codigo,
                "cuenta_contable_id": bank.id,
                "concepto": concepto,
                "debe": amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_banco"},
            },
            {
                "cuenta_codigo": debtor.codigo,
                "cuenta_contable_id": debtor.id,
                "concepto": concepto,
                "debe": 0,
                "haber": amount,
                "raw_row_json": {**meta, "movement": "haber_deudor"},
            },
        ]
    else:
        lines = [
            {
                "cuenta_codigo": debtor.codigo,
                "cuenta_contable_id": debtor.id,
                "concepto": concepto,
                "debe": amount,
                "haber": 0,
                "raw_row_json": {**meta, "movement": "debe_deudor"},
            },
            {
                "cuenta_codigo": bank.codigo,
                "cuenta_contable_id": bank.id,
                "concepto": concepto,
                "debe": 0,
                "haber": amount,
                "raw_row_json": {**meta, "movement": "haber_banco"},
            },
        ]
    poliza = await _create_poliza(
        session,
        origen="deudores_devolucion",
        numero_poliza=numero_poliza,
        fecha=fecha_dt,
        beneficiario_nombre=empleado.nombre,
        concepto=concepto,
        lines=lines,
    )
    return DebtorPostingResult(
        status="created", poliza=poliza, debtor_account=debtor, bank_account=bank
    )


async def build_cuenta_debtor_auxiliary(
    session: AsyncSession,
    *,
    cuenta_id: UUID,
) -> dict[str, Any]:
    cuenta = await session.get(CuentaDeGastos, cuenta_id)
    empleado = await resolve_cuenta_debtor_empleado(session, cuenta)
    debtor = await resolve_cuenta_debtor_account(session, cuenta, empleado)
    line_conditions = [
        AccountingPoliza.origen.in_(DEBTOR_ORIGINS),
        AccountingPolizaLine.raw_row_json.contains({"cuenta_gastos_id": str(cuenta_id)}),
    ]
    lines = (
        await session.execute(
            select(AccountingPolizaLine)
            .join(AccountingPoliza, AccountingPoliza.id == AccountingPolizaLine.poliza_id)
            .options(selectinload(AccountingPolizaLine.poliza))
            .where(and_(*line_conditions))
            .order_by(
                AccountingPoliza.fecha_poliza.asc(),
                AccountingPoliza.numero_poliza.asc(),
                AccountingPolizaLine.line_no.asc(),
            )
        )
    ).scalars().all()
    debe = sum(float(line.debe or 0) for line in lines if _is_debtor_or_petty_cash_line_code(line.cuenta_codigo))
    haber = sum(float(line.haber or 0) for line in lines if _is_debtor_or_petty_cash_line_code(line.cuenta_codigo))
    saldo = round(debe - haber, 2)
    status = "saldado" if abs(saldo) < 0.01 and lines else "pendiente"
    if debtor is None:
        status = "sin_subcuenta"
    elif lines and abs(saldo) >= 0.01:
        status = "diferencia_contable"
    return {
        "cuenta": cuenta,
        "empleado": empleado,
        "debtor_account": debtor,
        "debtor_account_label": _account_label(debtor),
        "debtor_account_block_label": debtor_account_block_label_for_employee(empleado),
        "lines": lines,
        "debe": round(debe, 2),
        "haber": round(haber, 2),
        "saldo": saldo,
        "status": status,
    }


async def build_debtors_admin_snapshot(
    session: AsyncSession,
    *,
    year: int,
    month: int,
    q: str = "",
) -> dict[str, Any]:
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    conditions = [
        AccountingPoliza.origen.in_(DEBTOR_ORIGINS),
        AccountingPoliza.fecha_poliza >= start,
        AccountingPoliza.fecha_poliza < end,
    ]
    selected_q = (q or "").strip()
    if selected_q:
        token = f"%{selected_q}%"
        conditions.append(
            or_(
                AccountingPoliza.beneficiario_nombre.ilike(token),
                AccountingPoliza.numero_poliza.ilike(token),
                AccountingPolizaLine.cuenta_codigo.ilike(token),
                AccountingPolizaLine.concepto.ilike(token),
            )
        )
    lines = (
        await session.execute(
            select(AccountingPolizaLine)
            .join(AccountingPoliza, AccountingPoliza.id == AccountingPolizaLine.poliza_id)
            .options(selectinload(AccountingPolizaLine.poliza))
            .where(and_(*conditions))
            .order_by(
                AccountingPoliza.fecha_poliza.desc(),
                AccountingPoliza.numero_poliza.asc(),
                AccountingPolizaLine.line_no.asc(),
            )
        )
    ).scalars().all()

    summary_by_employee: dict[str, dict[str, Any]] = {}
    for line in lines:
        raw = line.raw_row_json or {}
        employee_id = str(raw.get("empleado_id") or "")
        employee_name = str(raw.get("empleado_nombre") or line.poliza.beneficiario_nombre or "Sin empleado")
        bucket = summary_by_employee.setdefault(
            employee_id or employee_name,
            {
                "empleado_id": employee_id,
                "empleado_nombre": employee_name,
                "cuenta_codigo": "",
                "cuenta_nombre": "",
                "debe": 0.0,
                "haber": 0.0,
                "saldo": 0.0,
                "movimientos": 0,
            },
        )
        if _is_debtor_or_petty_cash_line_code(line.cuenta_codigo):
            bucket["cuenta_codigo"] = line.cuenta_codigo
            bucket["cuenta_nombre"] = ""
            bucket["debe"] += float(line.debe or 0)
            bucket["haber"] += float(line.haber or 0)
            bucket["movimientos"] += 1
            bucket["saldo"] = round(bucket["debe"] - bucket["haber"], 2)

    missing = await list_employees_missing_debtor_account(session)
    if selected_q:
        normalized = _normalize_text(selected_q)
        missing = [
            emp for emp in missing if normalized in _normalize_text(emp.nombre)
        ]
    return {
        "lines": lines,
        "summary": sorted(
            summary_by_employee.values(),
            key=lambda item: (item["empleado_nombre"], item["cuenta_codigo"]),
        ),
        "missing_employees": missing,
        "period": {"year": year, "month": month},
    }


async def list_employees_missing_debtor_account(
    session: AsyncSession,
) -> list[Empleado]:
    employee_ids = set(
        row[0]
        for row in (
            await session.execute(
                select(Documento.empleado_id)
                .where(
                    Documento.cuenta_gastos_id.isnot(None),
                    Documento.tipo == "SOLICITUD",
                    Documento.estado.in_(["aprobado", "pagado"]),
                )
                .distinct()
            )
        ).all()
        if row[0]
    )
    employee_ids.update(
        row[0]
        for row in (
            await session.execute(
                select(CuentaDeGastos.empleado_id)
                .where(CuentaDeGastos.estado.in_(["abierta", "cerrada"]))
                .distinct()
            )
        ).all()
        if row[0]
    )
    if not employee_ids:
        return []
    employees = (
        await session.execute(
            select(Empleado)
            .where(Empleado.id.in_(employee_ids), Empleado.activo.is_(True))
            .order_by(Empleado.nombre.asc())
        )
    ).scalars().all()
    missing = []
    for empleado in employees:
        if await resolve_employee_debtor_account(session, empleado) is None:
            missing.append(empleado)
    return missing


__all__ = [
    "DEBTOR_ACCOUNT_PREFIX",
    "DEBTOR_ACCOUNT_PREFIXES",
    "DEBTOR_ACCOUNT_ROOT_CODE",
    "DEBTOR_SOCIO_ACCOUNT_PREFIX",
    "DEBTOR_SOCIO_ACCOUNT_ROOT_CODE",
    "PARTNER_DEBTOR_ACCOUNT_PREFIX",
    "PARTNER_DEBTOR_ACCOUNT_ROOT_CODE",
    "PETTY_CASH_DEBTOR_ACCOUNT_CODES",
    "build_cuenta_debtor_auxiliary",
    "build_debtors_admin_snapshot",
    "debtor_account_block_label_for_employee",
    "ensure_debtor_comprobacion_posting_for_informe",
    "ensure_debtor_payment_posting_for_document",
    "ensure_debtor_settlement_posting",
    "format_samchat_poliza_concept",
    "list_employees_missing_debtor_account",
    "resolve_employee_debtor_account",
    "resolve_cuenta_debtor_account",
    "resolve_cuenta_debtor_empleado",
]
