"""Reconcile a posted Sports AMEX debtor charge with 1218 payroll and banking."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from ..amex_direct_models import AmexPartnerPayrollEvent as Event
from ..models import (
    AccountingPoliza,
    AccountingPolizaLine,
    AmexDirectAccountingCut,
    AmexDirectCutItem,
    AmexStatementDirectContext,
    BankMovement,
    CuentaContable,
    PayrollEmployee,
    PayrollEmployerRegistration,
    PayrollRun,
    PayrollRunLine,
)
from .amex_direct_common import (
    DirectAccessError,
    DirectConflict,
    DirectSchemaUnavailable,
    digest,
    load_statement,
    money,
)
from .coi_poliza_exporter import ExpenseCFDI, generate_coi_poliza_xlsx

SPORTS = "sports"
P1218 = "p1218"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
POLICY_ENV = "AMEX_PARTNER_PAYROLL_1218_CONFIG"


@dataclass(frozen=True)
class Account:
    id: UUID
    code: str


@dataclass(frozen=True)
class Policy:
    sports_actor_ids: frozenset[UUID]
    p1218_actor_ids: frozenset[UUID]
    employer_id: UUID
    sports_rfc: str
    p1218_rfc: str
    partners: dict[UUID, UUID]
    p1218_payroll_journals: dict[UUID, UUID]
    sports_receivable: Account
    p1218_payable: Account
    p1218_withholding: Account
    sports_bank: Account
    p1218_bank: Account
    sports_bank_number: str
    p1218_bank_number: str
    deduction_concept: str
    fingerprint: str


@dataclass(frozen=True)
class Charge:
    cut_item_id: UUID
    debtor_id: UUID
    debtor_code: str
    total: Decimal
    date: date
    concept: str


@dataclass(frozen=True)
class Balances:
    total: Decimal
    withheld: Decimal
    transferred: Decimal

    @property
    def debtor(self) -> Decimal:
        return self.total - self.withheld

    @property
    def intercompany(self) -> Decimal:
        return self.withheld - self.transferred


@dataclass(frozen=True)
class Request:
    kind: str
    amount: Decimal
    operation_id: UUID
    first_id: UUID
    second_id: UUID
    deduction_index: int | None = None
    receipt_reference: str | None = None


def _read_policy() -> Policy:
    try:
        data = json.loads(os.environ[POLICY_ENV])

        def account(key: str) -> Account:
            entry = data[key]
            code = entry["code"]
            if not re.fullmatch(r"[0-9][0-9.\-]{0,98}", code):
                raise ValueError(key)
            return Account(UUID(entry["id"]), code)

        policy = Policy(
            sports_actor_ids=frozenset(map(UUID, data["sports_actor_ids"])),
            p1218_actor_ids=frozenset(map(UUID, data["p1218_actor_ids"])),
            employer_id=UUID(data["employer_id"]),
            sports_rfc=data["sports_rfc"],
            p1218_rfc=data["p1218_rfc"],
            partners={UUID(k): UUID(v) for k, v in data["partners"].items()},
            p1218_payroll_journals={
                UUID(k): UUID(v)
                for k, v in data.get("p1218_payroll_journals", {}).items()
            },
            sports_receivable=account("sports_receivable"),
            p1218_payable=account("p1218_payable"),
            p1218_withholding=account("p1218_withholding"),
            sports_bank=account("sports_bank"),
            p1218_bank=account("p1218_bank"),
            sports_bank_number=data["sports_bank_number"],
            p1218_bank_number=data["p1218_bank_number"],
            deduction_concept=data["deduction_concept"],
            fingerprint=digest(data),
        )
        if (
            not policy.sports_actor_ids
            or not policy.p1218_actor_ids
            or not policy.partners
            or not re.fullmatch(r"[a-z0-9_]{1,80}", policy.deduction_concept)
            or not all(
                re.fullmatch(r"[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}", rfc)
                for rfc in (policy.sports_rfc, policy.p1218_rfc)
            )
            or policy.sports_rfc == policy.p1218_rfc
            or not all(
                re.fullmatch(r"[0-9]{4,32}", number)
                for number in (policy.sports_bank_number, policy.p1218_bank_number)
            )
            or policy.sports_bank_number == policy.p1218_bank_number
            or policy.sports_receivable == policy.sports_bank
            or policy.p1218_payable == policy.p1218_bank
            or policy.p1218_withholding == policy.p1218_bank
        ):
            raise ValueError("incomplete company policy")
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise DirectSchemaUnavailable("partner_payroll_policy_unconfigured") from exc
    return policy


def can_discover(actor_id: UUID) -> bool:
    """Hide the action until both company scopes are configured for the actor."""
    try:
        policy = _read_policy()
    except DirectSchemaUnavailable:
        return False
    return actor_id in policy.sports_actor_ids & policy.p1218_actor_ids


async def _charge(
    session: AsyncSession,
    actor_id: UUID,
    import_id: UUID,
    item_id: UUID,
    action: str,
    *,
    lock: bool = False,
) -> tuple[Policy, Charge]:
    policy = _read_policy()
    if actor_id not in policy.sports_actor_ids & policy.p1218_actor_ids:
        raise DirectAccessError("partner_payroll_company_scope_required")
    await load_statement(session, actor_id, import_id, action, finance=True)
    if not await session.scalar(
        text("SELECT to_regclass('amex_partner_payroll_events') IS NOT NULL")
    ):
        raise DirectSchemaUnavailable("partner_payroll_schema_missing")

    query = (
        select(AmexDirectCutItem, AmexDirectAccountingCut, AmexStatementDirectContext)
        .join(
            AmexDirectAccountingCut,
            AmexDirectAccountingCut.id == AmexDirectCutItem.cut_id,
        )
        .join(
            AmexStatementDirectContext,
            AmexStatementDirectContext.id == AmexDirectAccountingCut.context_id,
        )
        .where(
            AmexDirectAccountingCut.import_id == import_id,
            AmexDirectCutItem.direct_item_id == item_id,
            AmexDirectCutItem.coverage_kind == "partner_receivable",
        )
    )
    if lock:
        query = query.with_for_update(of=AmexDirectCutItem)
    row = (await session.execute(query)).one_or_none()
    if row is None:
        raise DirectConflict("partner_payroll_cut_required")
    item, _, context = row
    if context.receiver_rfc != policy.sports_rfc or context.currency != "MXN":
        raise DirectAccessError("partner_payroll_company_scope_required")
    fact = item.snapshot_json
    debtor_lines = [
        line
        for line in fact.get("journal_lines", [])
        if line.get("raw_row_json", {}).get("movement") == "debe_deudor_socio_amex"
    ]
    if len(debtor_lines) != 1:
        raise DirectConflict("partner_payroll_cut_required")
    debtor = debtor_lines[0]
    debtor_id = UUID(debtor["cuenta_contable_id"])
    if debtor_id not in policy.partners:
        raise DirectAccessError("partner_payroll_person_unconfigured")
    total = money(fact["amount"])
    if total <= 0 or money(debtor["debe"]) != total or money(debtor["haber"]) != 0:
        raise DirectConflict("partner_payroll_cut_required")
    try:
        charge_date = datetime.fromisoformat(fact["source"]["date"]).date()
    except (KeyError, TypeError, ValueError) as exc:
        raise DirectConflict("partner_payroll_cut_required") from exc
    return policy, Charge(
        item.id,
        debtor_id,
        debtor["cuenta_codigo"],
        total,
        charge_date,
        str(fact.get("concepto") or ""),
    )


async def _applied(session: AsyncSession, *criteria) -> Decimal:
    return money(
        await session.scalar(
            select(func.coalesce(func.sum(Event.amount), 0)).where(*criteria)
        )
    )


async def _balances(session: AsyncSession, charge: Charge) -> Balances:
    grouped = await session.execute(
        select(Event.kind, func.sum(Event.amount))
        .where(Event.cut_item_id == charge.cut_item_id)
        .group_by(Event.kind)
    )
    by_kind = {kind: amount for kind, amount in grouped}
    return Balances(
        charge.total,
        money(by_kind.get("payroll", 0)),
        money(by_kind.get("transfer", 0)),
    )


async def state(
    session: AsyncSession,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_id: UUID,
    include_choices: bool = False,
) -> tuple[Charge, Balances, list[Event], dict[str, list[tuple[UUID, str]]]]:
    """Read the per-charge trail under current Sports and 1218 grants."""
    policy, charge = await _charge(session, actor_id, import_id, item_id, "ver")
    events = (
        await session.scalars(
            select(Event)
            .where(Event.cut_item_id == charge.cut_item_id)
            .order_by(Event.created_at, Event.id)
        )
    ).all()
    totals = {
        kind: sum((event.amount for event in events if event.kind == kind), Decimal(0))
        for kind in ("payroll", "transfer")
    }
    balances = Balances(charge.total, totals["payroll"], totals["transfer"])
    choices = (
        await _source_choices(session, policy, charge, balances)
        if include_choices
        else {}
    )
    return charge, balances, list(events), choices


async def _source_choices(
    session: AsyncSession, policy: Policy, charge: Charge, balances: Balances
) -> dict[str, list[tuple[UUID, str]]]:
    choices: dict[str, list[tuple[UUID, str]]] = {}
    if balances.debtor > 0:
        rows = (
            await session.execute(
                select(PayrollRunLine, PayrollRun)
                .join(PayrollRun, PayrollRun.id == PayrollRunLine.run_id)
                .options(selectinload(PayrollRun.period))
                .join(
                    PayrollEmployee,
                    PayrollEmployee.id == PayrollRunLine.payroll_employee_id,
                )
                .join(
                    PayrollEmployerRegistration,
                    PayrollEmployerRegistration.id
                    == PayrollEmployee.employer_registration_id,
                )
                .where(
                    PayrollRunLine.payroll_employee_id
                    == policy.partners[charge.debtor_id],
                    PayrollRun.status == "posted",
                    PayrollEmployerRegistration.payroll_employer_id
                    == policy.employer_id,
                    PayrollEmployerRegistration.active.is_(True),
                )
                .order_by(PayrollRun.created_at.desc())
                .limit(30)
            )
        ).all()
        run_ids = set()
        choices["payroll-first"] = []
        for row, run in rows:
            period = run.period
            if (
                period.status != "closed"
                or (period.payment_date or period.end_date) < charge.date
            ):
                continue
            matches = [
                str(i)
                for i, deduction in enumerate(row.deductions_json or [], 1)
                if isinstance(deduction, dict)
                and deduction.get("concept_key") == policy.deduction_concept
            ]
            if matches:
                run_ids.add(run.id)
                choices["payroll-first"].append(
                    (row.id, f"{period.end_date} · Deducción {', '.join(matches)}")
                )
        approved_ids = {
            policy.p1218_payroll_journals[run_id]
            for run_id in run_ids
            if run_id in policy.p1218_payroll_journals
        }
        lines = (
            await session.execute(
                select(AccountingPolizaLine, AccountingPoliza.numero_poliza)
                .join(
                    AccountingPoliza,
                    AccountingPoliza.id == AccountingPolizaLine.poliza_id,
                )
                .where(
                    AccountingPoliza.id.in_(approved_ids)
                    | (
                        AccountingPoliza.source_file.in_(
                            {f"payroll_run:{run_id}" for run_id in run_ids}
                        )
                        & (AccountingPoliza.origen == "payroll")
                    ),
                    AccountingPolizaLine.cuenta_contable_id.in_(
                        {
                            policy.p1218_payable.id,
                            policy.p1218_withholding.id,
                        }
                    ),
                    AccountingPolizaLine.haber > 0,
                )
                .order_by(AccountingPoliza.fecha_poliza.desc(), AccountingPolizaLine.id)
                .limit(30)
            )
        ).all()
        choices["payroll-second"] = [
            (line.id, f"{number} · {line.cuenta_codigo} · {money(line.haber):,.2f} MXN")
            for line, number in lines
        ]
    if balances.intercompany > 0:
        for key, number, sign, other, other_sign in (
            (
                "transfer-first",
                policy.p1218_bank_number,
                "-",
                policy.sports_bank_number,
                "+",
            ),
            (
                "transfer-second",
                policy.sports_bank_number,
                "+",
                policy.p1218_bank_number,
                "-",
            ),
        ):
            counterpart = aliased(BankMovement)
            rows = (
                await session.scalars(
                    select(BankMovement)
                    .where(
                        BankMovement.cuenta_bancaria == number,
                        BankMovement.signo == sign,
                        BankMovement.clave_rastreo.is_not(None),
                        BankMovement.importe.is_not(None),
                        BankMovement.fecha
                        >= datetime.combine(charge.date, datetime.min.time()),
                        select(counterpart.id)
                        .where(
                            counterpart.cuenta_bancaria == other,
                            counterpart.signo == other_sign,
                            counterpart.clave_rastreo == BankMovement.clave_rastreo,
                            func.abs(counterpart.importe)
                            == func.abs(BankMovement.importe),
                        )
                        .exists(),
                    )
                    .order_by(BankMovement.fecha.desc(), BankMovement.id)
                    .limit(30)
                )
            ).all()
            choices[key] = [
                (
                    cast(UUID, row.id),
                    f"{row.fecha.date()} · {row.clave_rastreo} · "
                    f"{abs(money(row.importe)):,.2f} MXN",
                )
                for row in rows
            ]
    return choices


def _require_available(available: Decimal, requested: Decimal) -> None:
    if requested > available:
        raise DirectConflict("partner_payroll_amount_exceeds_balance")


def _policy_lines(day: date, debit: str, credit: str) -> dict:
    if debit == credit:
        raise DirectConflict("partner_payroll_ambiguous_accounts")
    return {"date": day.isoformat(), "debit": debit, "credit": credit}


async def _accounts(session: AsyncSession, *accounts: Account) -> None:
    mapped = {
        cast(UUID, row.id): cast(str, row.codigo)
        for row in (
            await session.scalars(
                select(CuentaContable).where(
                    CuentaContable.id.in_({account.id for account in accounts}),
                    CuentaContable.activo.is_(True),
                )
            )
        ).all()
    }
    if any(mapped.get(account.id) != account.code for account in accounts):
        raise DirectConflict("partner_payroll_ambiguous_accounts")


async def _payroll_source(
    session: AsyncSession, policy: Policy, charge: Charge, request: Request, lock: bool
) -> dict:
    query = (
        select(PayrollRunLine)
        .join(PayrollRun, PayrollRun.id == PayrollRunLine.run_id)
        .options(
            selectinload(PayrollRunLine.run).selectinload(PayrollRun.period),
            selectinload(PayrollRunLine.payroll_employee)
            .selectinload(PayrollEmployee.employer_registration)
            .selectinload(PayrollEmployerRegistration.employer),
        )
        .where(PayrollRunLine.id == request.first_id)
    )
    if lock:
        query = query.with_for_update(of=(PayrollRunLine, PayrollRun))
    line: Any = await session.scalar(query)
    if line is None or line.payroll_employee_id != policy.partners[charge.debtor_id]:
        raise DirectConflict("partner_payroll_source_mismatch")
    run = line.run
    registration = line.payroll_employee.employer_registration
    employer = registration.employer if registration else None
    deductions = line.deductions_json
    index = request.deduction_index
    if (
        run.status != "posted"
        or run.period.status != "closed"
        or registration is None
        or not registration.active
        or employer is None
        or not employer.active
        or employer.id != policy.employer_id
        or employer.rfc != policy.p1218_rfc
        or not isinstance(deductions, list)
        or index is None
        or not 0 <= index < len(deductions)
        or not isinstance(deductions[index], dict)
        or deductions[index].get("concept_key") != policy.deduction_concept
    ):
        raise DirectConflict("partner_payroll_source_mismatch")
    day = run.period.payment_date or run.period.end_date
    if day < charge.date:
        raise DirectConflict("partner_payroll_source_mismatch")
    deduction = money(deductions[index].get("amount"))
    _require_available(
        deduction
        - await _applied(
            session, Event.payroll_line_id == line.id, Event.deduction_index == index
        ),
        request.amount,
    )

    journal_query = (
        select(AccountingPolizaLine, AccountingPoliza)
        .join(AccountingPoliza, AccountingPoliza.id == AccountingPolizaLine.poliza_id)
        .where(AccountingPolizaLine.id == request.second_id)
    )
    if lock:
        journal_query = journal_query.with_for_update(of=AccountingPolizaLine)
    original = (await session.execute(journal_query)).one_or_none()
    if original is None:
        raise DirectConflict("partner_payroll_withholding_unproven")
    withholding, policy_header = original
    allowed = {
        account.id: account.code
        for account in (policy.p1218_payable, policy.p1218_withholding)
    }
    native_journal = (
        policy_header.origen == "payroll"
        and policy_header.source_file == f"payroll_run:{run.id}"
    )
    approved_coi_journal = policy.p1218_payroll_journals.get(run.id) == policy_header.id
    if (
        not (native_journal or approved_coi_journal)
        or withholding.cuenta_contable_id not in allowed
        or allowed[withholding.cuenta_contable_id] != withholding.cuenta_codigo
        or money(withholding.debe or 0) != 0
    ):
        raise DirectConflict("partner_payroll_withholding_unproven")
    credit = money(withholding.haber or 0)
    _require_available(
        credit - await _applied(session, Event.withholding_line_id == withholding.id),
        request.amount,
    )
    await _accounts(
        session,
        Account(charge.debtor_id, charge.debtor_code),
        policy.sports_receivable,
        policy.p1218_withholding,
        policy.p1218_payable,
    )
    books = {
        SPORTS: _policy_lines(day, policy.sports_receivable.code, charge.debtor_code)
    }
    if withholding.cuenta_contable_id != policy.p1218_payable.id:
        books[P1218] = _policy_lines(
            day, withholding.cuenta_codigo, policy.p1218_payable.code
        )
    return {
        "date": day.isoformat(),
        "books": books,
        "payroll_run": str(run.id),
        "payroll_period": str(run.period_id),
        "payroll_deduction": str(deduction),
        "withholding_credit": str(credit),
        "withholding_poliza": str(policy_header.id),
        "receipt_reference": request.receipt_reference,
        "payroll_employer": str(employer.id),
    }


async def _bank_book(
    session: AsyncSession,
    movement: Any,
    debit: Account,
    credit: Account,
    total: Decimal,
    *,
    lock: bool,
) -> dict | None:
    if movement.related_poliza_id is None:
        if movement.matched_aux_entry_id or movement.matched_expense_id:
            raise DirectConflict("partner_payroll_bank_posting_conflict")
        return _policy_lines(movement.fecha.date(), debit.code, credit.code)
    # An existing posting is evidence only when both sides are exact. Never
    # export another bank movement for receipts already covered by that journal.
    query = select(
        AccountingPolizaLine.cuenta_contable_id,
        AccountingPolizaLine.cuenta_codigo,
        AccountingPolizaLine.debe,
        AccountingPolizaLine.haber,
    ).where(AccountingPolizaLine.poliza_id == movement.related_poliza_id)
    if lock:
        query = query.with_for_update()
    lines = (await session.execute(query)).all()
    actual = {(a, c, money(d or 0), money(h or 0)) for a, c, d, h in lines}
    expected = {
        (debit.id, debit.code, total, Decimal(0)),
        (credit.id, credit.code, Decimal(0), total),
    }
    if len(lines) != 2 or actual != expected:
        raise DirectConflict("partner_payroll_bank_posting_conflict")
    return None


async def _transfer_source(
    session: AsyncSession, policy: Policy, charge: Charge, request: Request, lock: bool
) -> dict:
    query = (
        select(BankMovement)
        .where(BankMovement.id.in_([request.first_id, request.second_id]))
        .order_by(BankMovement.id)
    )
    if lock:
        query = query.with_for_update()
    rows: dict[UUID, Any] = {
        cast(UUID, row.id): row for row in (await session.scalars(query)).all()
    }
    debit, credit = rows.get(request.first_id), rows.get(request.second_id)
    if (
        debit is None
        or credit is None
        or debit.id == credit.id
        or debit.signo != "-"
        or credit.signo != "+"
        or debit.cuenta_bancaria != policy.p1218_bank_number
        or credit.cuenta_bancaria != policy.sports_bank_number
        or not debit.clave_rastreo
        or debit.clave_rastreo != credit.clave_rastreo
        or debit.fecha is None
        or credit.fecha is None
        or min(debit.fecha.date(), credit.fecha.date()) < charge.date
        or debit.codigo_devolucion not in {None, "", "0", "00"}
        or credit.codigo_devolucion not in {None, "", "0", "00"}
    ):
        raise DirectConflict("partner_payroll_bank_source_mismatch")
    total = abs(money(debit.importe))
    if total == 0 or total != abs(money(credit.importe)):
        raise DirectConflict("partner_payroll_bank_source_mismatch")
    bank_key = digest(
        {
            "trace": debit.clave_rastreo,
            "from": policy.p1218_bank_number,
            "to": policy.sports_bank_number,
        }
    )
    if lock:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "amex-partner-payroll:" + bank_key},
        )
    _require_available(
        total
        - await _applied(
            session,
            Event.kind == "transfer",
            Event.snapshot["bank_key"].astext == bank_key,
        ),
        request.amount,
    )
    _require_available(
        total - await _applied(session, Event.debit_bank_id == debit.id), request.amount
    )
    _require_available(
        total - await _applied(session, Event.credit_bank_id == credit.id),
        request.amount,
    )
    await _accounts(
        session,
        policy.p1218_payable,
        policy.p1218_bank,
        policy.sports_bank,
        policy.sports_receivable,
    )

    books = {}
    p1218 = await _bank_book(
        session, debit, policy.p1218_payable, policy.p1218_bank, total, lock=lock
    )
    sports = await _bank_book(
        session, credit, policy.sports_bank, policy.sports_receivable, total, lock=lock
    )
    if p1218:
        books[P1218] = p1218
    if sports:
        books[SPORTS] = sports
    return {
        "date": max(debit.fecha, credit.fecha).date().isoformat(),
        "books": books,
        "bank_amount": str(total),
        "bank_key": bank_key,
        "bank_reference": debit.clave_rastreo,
        "bank_polizas": {
            P1218: str(debit.related_poliza_id) if debit.related_poliza_id else None,
            SPORTS: str(credit.related_poliza_id) if credit.related_poliza_id else None,
        },
    }


async def prepare(
    session: AsyncSession,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_id: UUID,
    request: Request,
    confirm_hash: str | None = None,
) -> dict:
    """Preview or confirm the same evidence. Caller owns the transaction."""
    policy, charge = await _charge(
        session,
        actor_id,
        import_id,
        item_id,
        "contabilizar",
        lock=confirm_hash is not None,
    )
    if (
        request.kind not in {"payroll", "transfer"}
        or request.amount <= 0
        or request.amount != money(request.amount)
        or (
            request.kind == "transfer"
            and (
                request.deduction_index is not None
                or request.receipt_reference is not None
            )
        )
    ):
        raise DirectConflict("invalid_amount")
    if request.kind == "payroll" and not re.fullmatch(
        r"[A-Za-z0-9_./-]{1,100}", request.receipt_reference or ""
    ):
        raise DirectConflict("partner_payroll_receipt_required")
    identity = digest(
        {"actor": actor_id, "cut_item": charge.cut_item_id, "request": asdict(request)}
    )
    existing = await session.get(Event, request.operation_id)
    if existing is not None:
        if (
            existing.request_hash != identity
            or existing.snapshot["hash"] != confirm_hash
        ):
            raise DirectConflict("partner_payroll_request_conflict")
        return dict(existing.snapshot)

    balances = await _balances(session, charge)
    is_payroll = request.kind == "payroll"
    _require_available(
        balances.debtor if is_payroll else balances.intercompany, request.amount
    )
    if is_payroll:
        source = await _payroll_source(
            session, policy, charge, request, confirm_hash is not None
        )
    else:
        source = await _transfer_source(
            session, policy, charge, request, confirm_hash is not None
        )
    result = {
        **source,
        "reference": f"AMEX-{item_id}",
        "amount": str(request.amount),
        "kind": request.kind,
    }
    result["hash"] = digest(
        {
            "identity": identity,
            "source": result,
            "policy": policy.fingerprint,
            "balances": asdict(balances),
        }
    )
    if confirm_hash is None:
        return result
    if confirm_hash != result["hash"]:
        raise DirectConflict("partner_payroll_preview_changed")

    query = (
        insert(Event)
        .values(
            id=request.operation_id,
            cut_item_id=charge.cut_item_id,
            actor_id=actor_id,
            kind=request.kind,
            amount=request.amount,
            request_hash=identity,
            snapshot=result,
            payroll_line_id=request.first_id if is_payroll else None,
            deduction_index=request.deduction_index,
            withholding_line_id=request.second_id if is_payroll else None,
            debit_bank_id=None if is_payroll else request.first_id,
            credit_bank_id=None if is_payroll else request.second_id,
        )
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(Event.id)
    )
    if await session.scalar(query) is None:
        raise DirectConflict("partner_payroll_request_conflict")
    return result


async def export(
    session: AsyncSession,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_id: UUID,
    event_id: UUID,
    company: str,
) -> tuple[bytes, str]:
    """Emit only the confirmed, frozen COI movements for the selected company."""
    _, charge = await _charge(session, actor_id, import_id, item_id, "exportar")
    event = await session.scalar(
        select(Event).where(
            Event.id == event_id, Event.cut_item_id == charge.cut_item_id
        )
    )
    if company not in {SPORTS, P1218} or event is None:
        raise DirectConflict("partner_payroll_export_unavailable")
    book = event.snapshot["books"].get(company)
    if book is None:
        raise DirectConflict("partner_payroll_export_unavailable")
    amount = float(event.amount)
    reference = event.snapshot["reference"]
    description = (
        "Descuento AMEX por nómina" if event.kind == "payroll" else "Liquidación AMEX"
    )
    company_name = "Plataforma Sports" if company == SPORTS else "Plataforma 1218"
    concept = f"{description} / {reference} / {event.id}"
    item = ExpenseCFDI(
        fecha=datetime.fromisoformat(book["date"]),
        total=amount,
        iva_amount=0,
        subtotal_amount=amount,
        concepto=concept,
        cuenta_contable=book["debit"],
        cuenta_contrapartida=book["credit"],
        poliza_description=f"{company_name} / {concept}",
        poliza_operation_reference=str(event.id),
        missing_cfdi_warning="Movimiento de balance sin registro fiscal.",
    )
    contents = generate_coi_poliza_xlsx(
        [item],
        lote_manifest_rows=[
            ["Empresa", company_name],
            ["Cargo", reference],
            ["Operación", str(event.id)],
            ["Fecha contable", book["date"]],
        ],
    )
    return contents, f"AMEX-{company}-{event.id}.xlsx"
