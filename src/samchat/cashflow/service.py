"""Read-only cashflow report over canonical financial facts.

Amounts from the workbook are never sources. Missing evidence stays unavailable;
collections and budget measures explain cash but never create a second movement.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

ZERO = Decimal("0")
MEASURES = (
    "actual_cash_in",
    "actual_cash_out",
    "actual_cash_net",
    "approved_obligations",
    "submitted_obligations",
    "recognized_income",
    "collected_income",
    "expected_uncollected_income",
    "planned_budget_income",
    "planned_budget_expense",
    "forecast_net",
)


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _total(values: Any) -> Decimal | None:
    result = ZERO
    for value in values:
        number = _number(value)
        if number is None:
            return None
        result += number
    return result


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _month_add(value: date, count: int) -> date:
    index = value.year * 12 + value.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def _period_key(day: date, grouping: str) -> str:
    if grouping == "semanal":
        return (day - timedelta(days=day.weekday())).isoformat()
    if grouping == "trimestral":
        return f"{day.year}-T{(day.month - 1) // 3 + 1}"
    if grouping == "semestral":
        return f"{day.year}-S{(day.month - 1) // 6 + 1}"
    if grouping == "anual":
        return str(day.year)
    return day.strftime("%Y-%m")


def _currency(row: dict[str, Any]) -> str:
    if "currency_status" in row and row["currency_status"] != "known":
        return "SIN_MONEDA"
    value = str(row.get("currency") or row.get("moneda") or "").strip().upper()
    return value if len(value) == 3 and value.isalpha() else "SIN_MONEDA"


def _unique(
    rows: list[dict[str, Any]], notes: list[str], lane: str
) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        identity = str(row.get("id") or row.get("ar_item_id") or "")
        if not identity:
            notes.append(f"{lane}: registro sin identidad; total incompleto.")
            identity = f"missing-{index}"
        if identity in result:
            if result[identity] != row:
                notes.append(f"{lane}: identidad contradictoria; total incompleto.")
            continue
        result[identity] = row
    return list(result.values())


def _cashflow_project_options(sources: dict[str, Any]) -> list[dict[str, str]]:
    """Keep choices from authorized facts, independently of pending AP/filtering.

    No global catalog is read: parent scope belongs to the source readers. An
    explicit company identity or server tournament cap may only narrow choices.
    """
    cap = sources.get("authorized_tournament_ids")
    allowed = None if cap is None else {str(value) for value in cap}
    company = str(sources.get("company_id") or "")
    rows = [
        {
            **row,
            "project_id": row.get("key")
            or row.get("project_id")
            or row.get("tournament_id"),
            "project_name": row.get("label")
            or row.get("project_name")
            or row.get("tournament_name"),
        }
        for row in sources.get("project_options") or []
    ]
    for lane in ("documents", "bank_rows", "budget_plan"):
        rows.extend(sources.get(lane) or [])
    ar = sources.get("ar_payload") or {}
    for lane in (
        "expected_income",
        "issued_linked",
        "issued_unlinked",
        "unlinked_income_links",
    ):
        rows.extend(ar.get(lane) or [])
    choices: dict[str, str] = {}
    for row in rows:
        if row.get("company_id") and str(row["company_id"]) != company:
            continue
        identity = str(row.get("project_id") or row.get("tournament_id") or "").strip()
        if (
            not identity
            or identity == "all"
            or (allowed is not None and identity not in allowed)
        ):
            continue
        label = row.get("project_name") or row.get("tournament_name")
        if label and choices.get(identity) in (None, "Proyecto sin nombre"):
            choices[identity] = str(label)
        else:
            choices.setdefault(identity, "Proyecto sin nombre")
    return [{"key": key, "label": label} for key, label in sorted(choices.items())]


def _account_key(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode()).hexdigest()[:20]


def _account(value: Any) -> str:
    text = str(value or "").strip()
    return ("•••• " + text[-4:]) if text else "Sin cuenta identificada"


def build_cashflow_report(
    sources: dict[str, Any],
    *,
    start: date,
    end: date,
    cutoff: date,
    forecast_end: date,
    grouping: str = "mensual",
    account: str = "all",
    project: str = "all",
    currency: str = "all",
    page: int = 1,
    page_size: int = 50,
    detail_currency: str | None = None,
    detail_period: str | None = None,
    detail_direction: str | None = None,
) -> dict[str, Any]:
    """Project already-authorized facts; paginate details, never financial totals."""
    if start >= end or forecast_end <= cutoff:
        raise ValueError("El intervalo de flujo es inválido")
    if grouping not in {"semanal", "mensual", "trimestral", "semestral", "anual"}:
        raise ValueError("Agrupación inválida")
    project_options = _cashflow_project_options(sources)
    notes = list(sources.get("source_notes") or [])
    status = dict(sources.get("source_status") or {})
    status.setdefault(
        "payable_risk",
        {
            "available": False,
            "complete": False,
            "reason": "CxP detectada sin captura: no hay una fuente completa "
            "acreditada para este corte y ámbito.",
        },
    )
    for lane in ("bank", "obligations", "ar", "budget"):
        status.setdefault(
            lane,
            {"available": False, "complete": False, "reason": "Fuente no acreditada"},
        )
        if not status[lane].get("available") or not status[lane].get("complete"):
            notes.append(f"{lane}: {status[lane].get('reason') or 'Fuente incompleta'}")
    # Incompleteness is separate from the absence of rows in a verified query.
    available = {
        lane: bool(value.get("available") and value.get("complete"))
        for lane, value in status.items()
    }

    def incomplete(lane, reason):
        available[lane] = False
        status[lane] = {**status[lane], "complete": False, "reason": reason}
        if lane == "budget":
            # Measure coverage never overrides an account/currency/scope gap.
            status[lane]["direction_complete"] = {"income": False, "expense": False}
        if reason not in notes:
            notes.append(reason)

    raw_bank = _unique(list(sources.get("bank_rows") or []), notes, "bank")
    account_keys = {
        _account_key(row.get("cuenta_bancaria") or row.get("account"))
        for row in raw_bank
    }
    if account != "all" and account not in account_keys:
        if available["bank"]:
            raise ValueError("cashflow_account_selection_not_found")
        incomplete("bank", "Cuenta seleccionada sin evidencia bancaria disponible.")
    selected_account_ids = {
        str(row["bank_account_id"])
        for row in raw_bank
        if row.get("bank_account_id")
        and _account_key(row.get("cuenta_bancaria") or row.get("account")) == account
    }

    def account_rows(rows: list[dict[str, Any]], lane: str) -> list[dict[str, Any]]:
        if account == "all":
            return rows
        result = []
        for row in rows:
            identity = str(row.get("bank_account_id") or "")
            if identity and selected_account_ids:
                if identity in selected_account_ids:
                    result.append(row)
            else:
                result.append(row)
                incomplete(
                    lane,
                    f"{lane}: ámbito empresarial sin cuenta acreditada; "
                    "el detalle no representa exclusivamente la cuenta seleccionada.",
                )
        return result

    movements: list[dict[str, Any]] = []
    unassigned_count = 0
    for row in raw_bank:
        instant = _day(row.get("fecha") or row.get("date"))
        if instant is None:
            notes.append("bank: fecha ausente; cobertura incompleta.")
            available["bank"] = False
            continue
        if not start <= instant < min(end, cutoff + timedelta(days=1)):
            continue
        raw_account = str(row.get("cuenta_bancaria") or row.get("account") or "")
        if account != "all" and _account_key(raw_account) != account:
            continue
        row_project = str(row.get("project_id") or "")
        if project != "all" and row_project != project:
            unassigned_count += 1
            continue
        curr = _currency(row)
        if curr == "SIN_MONEDA":
            incomplete("bank", "Banco con moneda no acreditada; cobertura incompleta.")
        if currency != "all" and curr != currency:
            continue
        direction = {"+": "in", "-": "out"}.get(str(row.get("signo") or "").strip())
        amount = _number(row.get("importe"))
        if direction is None or amount is None or amount < ZERO:
            notes.append("bank: signo o importe no acreditado; total incompleto.")
            available["bank"] = False
        party = (
            row.get("nombre_ordenante")
            if direction == "in"
            else row.get("nombre_beneficiario")
        )
        movements.append(
            dict(
                id=str(row.get("id") or ""),
                date=instant.isoformat(),
                account=_account(raw_account),
                account_key=_account_key(raw_account),
                currency=curr,
                direction=direction,
                amount=amount,
                party=party or "Sin identificar",
                concept=(
                    row.get("concepto_banco")
                    or row.get("descripcion")
                    or "Sin concepto"
                ),
                project=row.get("project_name") or "Sin atribución acreditada",
                reference=row.get("referencia_bancaria") or str(row.get("id") or ""),
                reconciliation=row.get("conciliacion_estado") or "Sin conciliar",
                source="bank_movements",
                period_key=_period_key(instant, grouping),
            )
        )
    if project != "all":
        if not movements:
            available["bank"] = False
            status["bank"] = {
                "available": False,
                "complete": False,
                "reason": "Sin atribución bancaria acreditada para el proyecto.",
            }
        notes.append(
            "Banco por proyecto: sólo atribuciones acreditadas. "
            f"{unassigned_count} movimientos fuera del detalle seleccionado; "
            "el banco global no se mezcla con este proyecto."
        )
    if any(m["currency"] == "SIN_MONEDA" for m in movements):
        notes.append(
            "Hay movimientos sin moneda acreditada: sus importes no se agregan."
        )
    if any(n.startswith("bank:") and "incomplet" in n for n in notes):
        available["bank"] = False
    documents = _unique(list(sources.get("documents") or []), notes, "obligations")
    if project != "all":
        documents = [
            row for row in documents if str(row.get("project_id") or "") == project
        ]
    documents = account_rows(documents, "obligations")
    if any(n.startswith("obligations:") for n in notes):
        available["obligations"] = False
    obligations: list[dict[str, Any]] = []
    for row in documents:
        state = str(row.get("estado") or row.get("state") or "").lower()
        if state not in {"aprobado", "en_proceso_pago", "enviado"} or row.get(
            "pagado_en"
        ):
            continue
        instant = _day(row.get("fecha_pago") or row.get("date"))
        if instant and instant >= forecast_end:
            continue
        if project != "all" and str(row.get("project_id") or "") != project:
            continue
        curr = _currency(row)
        if curr == "SIN_MONEDA":
            incomplete("obligations", "Obligaciones con moneda no acreditada.")
        if currency != "all" and curr != currency:
            continue
        obligations.append(
            dict(
                id=str(row.get("id") or ""),
                date=instant.isoformat() if instant else None,
                currency=curr,
                amount=_number(row.get("payable_amount")),
                state=state,
                party=row.get("party") or "Sin identificar",
                concept=row.get("concepto_pago") or "Sin concepto",
                project=row.get("project_name") or "Sin atribución acreditada",
                reference=row.get("numero_referencia") or str(row.get("id") or ""),
                window=(
                    "undated"
                    if instant is None
                    else "overdue" if instant < cutoff else "upcoming"
                ),
            )
        )
    ar = sources.get("ar_payload") or {}
    ar_coverage = ar.get("source_coverage") or {}
    ar_forecast_unknown = bool(ar.get("unlinked_income_links")) or any(
        ar_coverage.get(key)
        for key in (
            "candidates_year_missing",
            "currency_gap",
            "income_lines_truncated",
            "candidates_truncated",
            "unlinked_accounting_count",
            "unknown_balance_count",
            "gross_amount_gap",
            "recognized_income_gap",
            "collection_conflict_count",
        )
    )
    if ar_forecast_unknown:
        notes.append("ar_forecast_basis_and_schedule_unproven")
    if ar_coverage.get("currency_gap"):
        incomplete("ar", "ar_currency_coverage_missing")
    ar_rows = _unique(
        [*(ar.get("issued_linked") or []), *(ar.get("issued_unlinked") or [])],
        notes,
        "ar",
    )
    if any(row.get("collection_status") == "collection_conflict" for row in ar_rows):
        ar_forecast_unknown = True
        notes.append("ar_collection_conflict")
    budget_plan = list(sources.get("budget_plan") or [])
    if project != "all":
        ar_rows = [
            row for row in ar_rows if str(row.get("tournament_id") or "") == project
        ]
        budget_plan = [
            row
            for row in budget_plan
            if str(row.get("tournament_id") or row.get("project_id") or "") == project
        ]
    ar_rows = account_rows(ar_rows, "ar")
    budget_plan = account_rows(budget_plan, "budget")
    for lane, rows in (("ar", ar_rows), ("budget", budget_plan)):
        if any(_currency(row) == "SIN_MONEDA" for row in rows):
            incomplete(lane, f"{lane}: moneda no acreditada; cobertura incompleta.")
    receivables: list[dict[str, Any]] = []
    for row in ar_rows:
        curr = _currency(row)
        if currency != "all" and curr != currency:
            continue
        if project != "all" and str(row.get("tournament_id") or "") != project:
            continue
        instant = _day(row.get("due_date"))
        amount = (
            _number(row.get("outstanding_amount"))
            if row.get("outstanding_amount_status") == "known"
            else None
        )
        if instant and instant >= forecast_end:
            continue
        receivables.append(
            dict(
                id=str(row.get("ar_item_id") or row.get("id") or ""),
                date=instant.isoformat() if instant else None,
                currency=curr,
                amount=amount,
                state=row.get("collection_status") or "collection_unknown",
                party=row.get("payer_name")
                or row.get("payer_rfc")
                or "Sin identificar",
                reference=row.get("cfdi_uuid") or row.get("ar_item_id") or "",
                window=(
                    "undated"
                    if instant is None
                    else "overdue" if instant < cutoff else "upcoming"
                ),
            )
        )
    currencies = sorted(
        {r["currency"] for r in movements + obligations + receivables}
        | {_currency(r) for r in ar_rows}
        | {_currency(r) for r in budget_plan}
    )
    if currency != "all":
        currencies = [currency]
    if not currencies:
        currencies = ["SIN_MONEDA"]
    days: dict[str, date] = {}
    historical_keys: set[str] = set()
    forecast_keys: set[str] = set()
    for interval_start, interval_end, target in (
        (start, end, historical_keys),
        (cutoff, forecast_end, forecast_keys),
    ):
        cursor = interval_start
        while cursor < interval_end:
            key = _period_key(cursor, grouping)
            days[key] = min(days.get(key, cursor), cursor)
            if target is forecast_keys or cursor <= cutoff:
                target.add(key)
            cursor += timedelta(days=1)
    buckets: list[dict[str, Any]] = []
    for curr in currencies:
        for key, instant in sorted(days.items(), key=lambda item: item[1]):
            historical = key in historical_keys
            bucket: dict[str, Any] = dict(
                year=instant.year,
                month=instant.month,
                key=key,
                currency=curr,
                opening_balance=None,
                closing_balance=None,
                **{field: ZERO for field in MEASURES},
            )
            members = [
                m for m in movements if m["currency"] == curr and m["period_key"] == key
            ]
            for direction, field in (
                ("in", "actual_cash_in"),
                ("out", "actual_cash_out"),
            ):
                bucket[field] = (
                    _total(m["amount"] for m in members if m["direction"] == direction)
                    if available["bank"] and curr != "SIN_MONEDA" and historical
                    else None
                )
            debit, credit = bucket["actual_cash_in"], bucket["actual_cash_out"]
            bucket["actual_cash_net"] = (
                debit - credit if debit is not None and credit is not None else None
            )
            scheduled = [
                r
                for r in obligations
                if r["currency"] == curr
                and (target_day := _day(r["date"])) is not None
                and target_day >= cutoff
                and _period_key(target_day, grouping) == key
            ]
            for states, field in (
                ({"aprobado", "en_proceso_pago"}, "approved_obligations"),
                ({"enviado"}, "submitted_obligations"),
            ):
                bucket[field] = (
                    _total(r["amount"] for r in scheduled if r["state"] in states)
                    if available["obligations"] and curr != "SIN_MONEDA"
                    else None
                )
            incoming = [
                r
                for r in receivables
                if r["currency"] == curr
                and (target_day := _day(r["date"])) is not None
                and target_day >= cutoff
                and _period_key(target_day, grouping) == key
            ]
            bucket["expected_uncollected_income"] = (
                _total(r["amount"] for r in incoming)
                if available["ar"]
                and not ar_forecast_unknown
                and curr != "SIN_MONEDA"
                and not any(
                    r["currency"] == curr and (r["amount"] is None or not r["date"])
                    for r in receivables
                )
                else None
            )
            # Facturation/cobranza describe stages; neither is an extra cash entry.
            for field, date_field, amount_field in (
                (
                    "recognized_income",
                    "recognized_income_date",
                    "recognized_income_amount",
                ),
                ("collected_income", "collection_date", "collected_amount"),
            ):
                events = []
                valid = available["ar"] and curr != "SIN_MONEDA"
                for row in ar_rows:
                    if _currency(row) != curr:
                        continue
                    if field == "recognized_income":
                        if row.get("recognized_income_date") is None:
                            continue
                        if ar_coverage.get("recognized_income_gap"):
                            valid = False
                        entries = [(row.get(date_field), row.get(amount_field))]
                    else:
                        if row.get("collection_status") == "collection_conflict":
                            valid = False
                            continue
                        if row.get("collection_status") not in {
                            "matched_collected",
                            "partially_collected",
                            "over_collected_review",
                        }:
                            continue
                        # Current AR exposes each active accepted receipt; the
                        # company-scoped DTO also retains its status-bearing matches.
                        proofs = row.get("collection_matches") or [
                            {**receipt, "status": "accepted_collection_match"}
                            for receipt in row.get("collection_receipts") or []
                            if row.get("collection_source") == "accepted_match"
                        ]
                        accepted = [
                            proof
                            for proof in proofs
                            if proof.get("status") == "accepted_collection_match"
                        ]
                        if accepted and _total(
                            proof.get("accepted_amount") for proof in accepted
                        ) != _number(row.get("collected_amount")):
                            valid = False
                        entries = [
                            (p.get("collection_date"), p.get("accepted_amount"))
                            for p in proofs
                            if p.get("status") == "accepted_collection_match"
                        ]
                        if (
                            _number(row.get("collected_amount")) not in (None, ZERO)
                            and not entries
                        ):
                            valid = False
                    for event_date, event_amount in entries:
                        event_day = _day(event_date)
                        amount = _number(event_amount)
                        if event_day is None or amount is None:
                            valid = False
                        elif (
                            start <= event_day < min(end, cutoff + timedelta(days=1))
                            and _period_key(event_day, grouping) == key
                        ):
                            events.append(amount)
                bucket[field] = _total(events) if valid and historical else None
            for direction, field in (
                ("income", "planned_budget_income"),
                ("expense", "planned_budget_expense"),
            ):
                plan = [
                    r
                    for r in budget_plan
                    if _currency(r) == curr
                    and r.get("direction") == direction
                    and r.get("year")
                    and r.get("month")
                    and _period_key(date(int(r["year"]), int(r["month"]), 1), grouping)
                    == key
                ]
                bucket[field] = (
                    _total(r.get("amount") for r in plan)
                    if status["budget"].get("available")
                    and status["budget"]
                    .get("direction_complete", {})
                    .get(direction, available["budget"])
                    and curr != "SIN_MONEDA"
                    else None
                )
            expected, approved = (
                bucket["expected_uncollected_income"],
                bucket["approved_obligations"],
            )
            actual = bucket["actual_cash_net"] if historical else ZERO
            undated = any(
                r["currency"] == curr
                and r["state"] != "enviado"
                and ((target_day := _day(r["date"])) is None or target_day < cutoff)
                and (r["amount"] is None or r["amount"] > ZERO)
                for r in obligations
            )
            overdue_ar = any(
                r["currency"] == curr
                and r["window"] == "overdue"
                and (r["amount"] is None or r["amount"] > ZERO)
                for r in receivables
            )
            if undated or overdue_ar:
                note = (
                    "Proyección N/D: hay pendientes vencidos o sin fecha; "
                    "requieren una fecha canónica de reprogramación."
                )
                if note not in notes:
                    notes.append(note)
            bucket["forecast_net"] = (
                actual + expected - approved
                if not undated
                and not overdue_ar
                and all(x is not None for x in (actual, expected, approved))
                else None
            )
            if key not in forecast_keys:
                for field in (
                    "approved_obligations",
                    "submitted_obligations",
                    "expected_uncollected_income",
                ):
                    bucket[field] = None
            buckets.append(bucket)
    summary_by_currency: dict[str, dict[str, Any]] = {}
    for curr in currencies:
        rows = [r for r in buckets if r["currency"] == curr]
        summary = {field: _total(r[field] for r in rows) for field in MEASURES}
        for field in (
            "actual_cash_in",
            "actual_cash_out",
            "actual_cash_net",
            "recognized_income",
            "collected_income",
        ):
            historical_rows = [r for r in rows if r["key"] in historical_keys]
            summary[field] = (
                _total(r[field] for r in historical_rows) if historical_rows else None
            )
        for states, field in (
            ({"aprobado", "en_proceso_pago"}, "approved_obligations"),
            ({"enviado"}, "submitted_obligations"),
        ):
            summary[field] = (
                _total(
                    r["amount"]
                    for r in obligations
                    if r["currency"] == curr and r["state"] in states
                )
                if available["obligations"] and curr != "SIN_MONEDA"
                else None
            )
        summary["expected_uncollected_income"] = _total(
            row["expected_uncollected_income"]
            for row in rows
            if row["key"] in forecast_keys
        )
        summary["forecast_net"] = _total(
            row["forecast_net"]
            for row in rows
            if row["key"] in historical_keys | forecast_keys
        )
        summary.update(opening_balance=None, closing_balance=None)
        summary_by_currency[curr] = summary
    notes.append(
        "Saldo inicial y final N/D: falta apertura bancaria y cobertura acreditadas."
    )
    notes.append(
        "Presupuesto y facturación se mantienen separados; "
        "no se suman al banco ni entre sí."
    )
    groups = defaultdict(list)
    for row in movements:
        groups[(row["direction"], row["currency"], row["party"])].append(row["amount"])
    party_groups = [
        dict(
            direction=k[0],
            currency=k[1],
            party=k[2],
            count=len(v),
            amount=(_total(v) if available["bank"] and k[1] != "SIN_MONEDA" else None),
        )
        for k, v in groups.items()
    ]
    selected = [
        r
        for r in movements
        if (not detail_currency or r["currency"] == detail_currency)
        and (not detail_period or r["period_key"] == detail_period)
        and (not detail_direction or r["direction"] == detail_direction)
    ]
    selected.sort(key=lambda r: (r["date"], r["id"]))
    page_size = max(1, min(int(page_size), 100))
    pages = max(1, (len(selected) + page_size - 1) // page_size)
    page = max(1, min(int(page), pages))
    summary = (
        next(iter(summary_by_currency.values()))
        if len(currencies) == 1
        else {field: None for field in MEASURES}
    )
    state_as_of = _day(sources.get("state_as_of"))
    if state_as_of is not None and state_as_of > cutoff:
        reason = (
            "No existe reconstrucción de pendientes al corte histórico; "
            "detalle documental refleja el estado vigente al "
            f"{state_as_of.isoformat()}."
        )
        status["forecast"] = {"available": False, "complete": False, "reason": reason}
        notes.append(reason)
        future_fields = (
            "expected_uncollected_income",
            "approved_obligations",
            "submitted_obligations",
            "forecast_net",
        )
        for row in buckets:
            for field in future_fields:
                row[field] = None
        for row in summary_by_currency.values():
            for field in future_fields:
                row[field] = None
        for field in future_fields:
            summary[field] = None
    for lane, valid in available.items():
        if not valid:
            status[lane] = {
                **status[lane],
                "complete": False,
                "reason": status[lane].get("reason")
                or (
                    f"{lane}: cobertura incompleta "
                    "por evidencia faltante o contradictoria."
                ),
            }
    return dict(
        ok=True,
        read_only=True,
        company_id=sources.get("company_id"),
        state_as_of=sources.get("state_as_of"),
        company_label=(
            sources.get("company_label") or "Ámbito heredado sin empresa atribuida"
        ),
        currency=currency,
        period=dict(
            year=start.year,
            month=start.month,
            start=start.isoformat(),
            end=end.isoformat(),
            cutoff=cutoff.isoformat(),
            forecast_start=cutoff.isoformat(),
            forecast_end=forecast_end.isoformat(),
            horizon_months=(end.year - start.year) * 12 + end.month - start.month,
            grouping=grouping,
        ),
        summary=summary,
        summary_by_currency=summary_by_currency,
        monthly_buckets=buckets,
        source_notes=list(dict.fromkeys(notes)),
        source_status=status,
        movements=movements,
        obligations=obligations,
        receivables=receivables,
        party_groups=party_groups,
        budget_plan=budget_plan,
        account_options=[
            {"key": _account_key(a), "label": _account(a)}
            for a in sorted({str(r.get("cuenta_bancaria") or "") for r in raw_bank})
        ],
        project_options=project_options,
        details=dict(
            page=page,
            page_size=page_size,
            total=len(selected),
            pages=pages,
            rows=selected[(page - 1) * page_size : page * page_size],
        ),
    )


async def build_cashflow_planning_read_model(
    session: Any,
    *,
    budget_version_id: str | None = None,
    edition_year: int | None = None,
    year: int | None = None,
    month: int | None = None,
    horizon_months: int = 3,
    limit: int = 500,
    company_context: Any = None,
    account: str = "all",
    project: str = "all",
    currency: str = "all",
    horizon_days: int | None = None,
    cutoff: date | None = None,
    credit_days: int = 30,
    grouping: str = "mensual",
    page: int = 1,
    detail_currency: str | None = None,
    detail_period: str | None = None,
    detail_direction: str | None = None,
    tournament_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build one report for HTML/XLSX; limit affects detail size only."""
    today = datetime.now(timezone.utc).date()
    start = date(int(year or edition_year or today.year), int(month or today.month), 1)
    horizon = max(1, min(int(horizon_months), 24))
    end = _month_add(start, horizon)
    cut = cutoff or min(today, end - timedelta(days=1))
    forecast_end = (
        cut + timedelta(days=max(15, min(int(horizon_days), 180)))
        if horizon_days is not None
        else max(end, _month_add(cut.replace(day=1), horizon))
    )
    sources = await load_cashflow_sources(
        session,
        start=start,
        end=min(end, cut + timedelta(days=1)),
        forecast_end=forecast_end,
        budget_version_id=budget_version_id,
        company_context=company_context,
        tournament_ids=tournament_ids,
        credit_days=credit_days,
        edition_year=edition_year,
    )
    result = build_cashflow_report(
        sources,
        start=start,
        end=end,
        cutoff=cut,
        forecast_end=forecast_end,
        grouping=grouping,
        account=account,
        project=project,
        currency=currency,
        page=page,
        page_size=limit,
        detail_currency=detail_currency,
        detail_period=detail_period,
        detail_direction=detail_direction,
    )
    result.update(
        budget_version_id=sources.get("budget_version_id", budget_version_id),
        budget_version_options=sources.get("budget_version_options") or [],
        edition_year=edition_year,
    )
    return result


_BANK_FIELDS = (
    "id",
    "company_id",
    "currency",
    "bank_account_id",
    "import_run_id",
    "fecha",
    "signo",
    "importe",
    "cuenta_bancaria",
    "nombre_ordenante",
    "nombre_beneficiario",
    "concepto_banco",
    "descripcion",
    "referencia_bancaria",
    "conciliacion_estado",
    "related_poliza_id",
    "matched_aux_entry_id",
    "matched_expense_id",
)
_DOCUMENT_FIELDS = (
    "id",
    "company_id",
    "currency",
    "bank_account_id",
    "tipo",
    "estado",
    "numero_referencia",
    "referencia_operaciones",
    "monto_total",
    "monto_solicitado",
    "concepto_pago",
    "fecha_pago",
    "pagado_en",
    "torneo_id",
    "proyecto_otro",
    "cuenta_gastos_id",
    "gasto_generado_id",
    "budget_concept_id",
    "cfdi_report_id",
    "empleado_id",
    "proveedor_cliente_id",
    "beneficiario_empleado_id",
    "beneficiario_proveedor_cliente_id",
)


def _cashflow_source_state(available: bool, complete: bool, reason: str = ""):
    return {"available": available, "complete": complete, "reason": reason}


async def _cashflow_source_savepoint(session, operation):
    """Recover unavailable SQL sources without flushing/rolling back caller work."""
    with session.no_autoflush:
        connection = await session.connection()
        transaction = await connection.begin_nested()
        try:
            result = await operation()
        except SQLAlchemyError:
            await transaction.rollback()
            raise
        except BaseException:
            await transaction.rollback()
            raise
        else:
            await transaction.commit()
            return result


async def _cashflow_legacy_rows(session, table, fields, condition, params):
    # Internal identifiers only. JSON extraction keeps legacy/migrated schemas
    # compatible while preventing a missing company column from becoming global.
    optional = {"company_id", "currency", "bank_account_id"}
    projection = ", ".join(
        f"to_jsonb(t)->>'{field}' AS {field}" if field in optional else f"t.{field}"
        for field in fields
    )
    statement = text(
        f"SELECT {projection} FROM {table} t "
        "WHERE to_jsonb(t)->>'company_id' IS NULL AND " + condition
    ).execution_options(autoflush=False)
    with session.no_autoflush:
        result = await session.execute(statement, params)
    return [dict(row) for row in result.mappings()]


async def _cashflow_legacy_parent(session, table, identity):
    if not identity:
        return
    row = (
        (
            await session.execute(
                text(
                    f"SELECT to_jsonb(t)->>'company_id' AS company_id FROM {table} t "
                    "WHERE t.id=CAST(:identity AS uuid)"
                ).execution_options(autoflush=False),
                {"identity": str(identity)},
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["company_id"] is not None:
        raise ValueError("cashflow_source_parent_scope_mismatch")


async def _cashflow_name(session, table, identity, column="nombre"):
    if not identity:
        return None
    # Names are shared-directory attributes, never a financial ownership proof.
    return await session.scalar(
        text(
            f"SELECT {column} FROM {table} WHERE id=CAST(:identity AS uuid)"
        ).execution_options(autoflush=False),
        {"identity": str(identity)},
    )


async def load_cashflow_sources(
    session: Any,
    *,
    start: date,
    end: date,
    forecast_end: date,
    budget_version_id: str | None = None,
    company_context: Any = None,
    tournament_ids: list[str] | None = None,
    credit_days: int = 30,
    edition_year: int | None = None,
) -> dict[str, Any]:
    """Read complete bank/AP universes and honest coverage for existing AR/plan.

    Financial write owners are not called. Company owner imports are lazy so
    this consumer remains importable on the serving legacy release. A supplied
    CompanyContext never degrades to global or legacy reads.
    """
    from devnous.gastos.services.document_amount_service import (
        resolve_payable_document_amount,
    )

    result: dict[str, Any] = {
        "bank_rows": [],
        "documents": [],
        "ar_payload": None,
        "budget_plan": [],
        "source_status": {},
        "source_notes": [],
    }
    status = result["source_status"]
    result["state_as_of"] = date.today().isoformat()
    result["company_id"] = None
    result["company_label"] = "Ámbito heredado sin empresa atribuida"
    helpers: Any = None
    snapshot = None
    company = None
    if company_context is None:
        try:
            from devnous.gastos.services.company_operation_scope import (
                company_id_for_operation,
            )
        except ModuleNotFoundError as exc:
            if exc.name != "devnous.gastos.services.company_operation_scope":
                raise
        else:
            # Activated installations require a server-resolved context even
            # when legacy rows still exist. Do not bypass activation by omission.
            company_id_for_operation(None, "read")
    if company_context is not None:
        try:
            from devnous.gastos.services.company_scope_service import CompanyScopeError
            from samchat.finance_platform import service as finance_helpers

            helpers = finance_helpers

            required = (
                "_read_company",
                "_read_snapshot",
                "_source_rows",
                "_read_bank_parent",
                "_read_import_parent",
                "_read_parent",
                "_document_parents",
            )
            if any(not hasattr(helpers, name) for name in required):
                raise ImportError("cashflow_company_read_owner_unavailable")
        except ImportError as exc:
            raise ValueError("cashflow_company_read_owner_unavailable") from exc
        with session.no_autoflush:
            snapshot = await helpers._read_snapshot(session)
            company = await helpers._read_company(session, company_context)
            result["company_id"] = company
            result["company_label"] = next(
                (
                    item.display_name
                    for item in company_context.companies
                    if str(item.id) == str(company)
                ),
                str(company),
            )
    allowed_tournaments = (
        None if tournament_ids is None else {str(value) for value in tournament_ids}
    )
    result["authorized_tournament_ids"] = (
        None if allowed_tournaments is None else sorted(allowed_tournaments)
    )
    if allowed_tournaments == set():
        for key in ("bank", "obligations", "ar", "budget"):
            status[key] = _cashflow_source_state(True, True, "Ámbito de torneos vacío.")
        if helpers is not None:
            await helpers._read_company(session, company_context)
            if snapshot != await helpers._read_snapshot(session):
                raise CompanyScopeError("cashflow_source_changed", 409)
        return result

    async def rows(table, fields, condition, params):
        if helpers is not None:
            return await helpers._source_rows(
                session, table, fields, company, condition, params
            )
        return await _cashflow_legacy_rows(session, table, fields, condition, params)

    async def bank_operation():
        bank = await rows(
            "bank_movements",
            _BANK_FIELDS,
            "t.fecha >= :start AND t.fecha < :end ORDER BY t.fecha, t.id",
            {
                "start": datetime.combine(start, datetime.min.time()),
                "end": datetime.combine(end, datetime.min.time()),
            },
        )
        for row in bank:
            if helpers is not None:
                if row.get("bank_account_id") is None:
                    raise CompanyScopeError("cashflow_bank_source_missing", 409)
                await helpers._read_bank_parent(
                    session, row["bank_account_id"], company, row.get("currency")
                )
                await helpers._read_import_parent(
                    session, row.get("import_run_id"), company, row.get("currency")
                )
                for field, table in (
                    ("related_poliza_id", "accounting_polizas"),
                    ("matched_aux_entry_id", "aux_ledger_entries"),
                    ("matched_expense_id", "expense_reports"),
                ):
                    await helpers._read_parent(session, table, row.get(field), company)
            else:
                for field, table in (
                    ("import_run_id", "accounting_import_runs"),
                    ("bank_account_id", "company_bank_accounts"),
                    ("related_poliza_id", "accounting_polizas"),
                    ("matched_aux_entry_id", "aux_ledger_entries"),
                    ("matched_expense_id", "expense_reports"),
                ):
                    await _cashflow_legacy_parent(session, table, row.get(field))
        return bank

    with session.no_autoflush:
        if allowed_tournaments is not None:
            # The bank owner has no canonical tournament attribution contract.
            # A tournament cap must never reveal installation-wide cash rows.
            status["bank"] = _cashflow_source_state(
                False,
                False,
                "Movimientos bancarios sin atribución de torneo acreditada.",
            )
        else:
            try:
                result["bank_rows"] = await _cashflow_source_savepoint(
                    session, bank_operation
                )
                status["bank"] = _cashflow_source_state(True, True)
            except SQLAlchemyError as exc:
                status["bank"] = _cashflow_source_state(
                    False,
                    False,
                    f"Fuente bancaria no disponible ({type(exc).__name__}).",
                )

        async def document_operation():
            params: dict[str, Any] = {"forecast_end": forecast_end}
            condition = (
                "t.tipo='SOLICITUD' AND t.estado IN "
                "('aprobado','en_proceso_pago','enviado') AND t.pagado_en IS NULL "
                "AND (t.fecha_pago IS NULL OR t.fecha_pago < :forecast_end)"
            )
            if allowed_tournaments is not None:
                condition += (
                    " AND CAST(COALESCE(t.torneo_id, (SELECT c.torneo_id "
                    "FROM cuentas_de_gastos c WHERE c.id=t.cuenta_gastos_id)) "
                    "AS text)=ANY(CAST(:tournaments AS text[]))"
                )
                params["tournaments"] = sorted(allowed_tournaments)
            docs = await rows("documentos", _DOCUMENT_FIELDS, condition, params)
            for row in docs:
                if helpers is not None:
                    await helpers._document_parents(session, row, company)
                else:
                    for field, table in (
                        ("cuenta_gastos_id", "cuentas_de_gastos"),
                        ("gasto_generado_id", "expense_reports"),
                        ("budget_concept_id", "budget_concepts"),
                        ("bank_account_id", "company_bank_accounts"),
                    ):
                        await _cashflow_legacy_parent(session, table, row.get(field))
                    if row.get("cfdi_report_id"):
                        installed = await session.scalar(
                            text("SELECT to_regclass('cfdi_company_links')")
                        )
                        if installed is not None:
                            assigned = await session.scalar(
                                text(
                                    "SELECT EXISTS(SELECT 1 FROM cfdi_company_links "
                                    "WHERE cfdi_report_id=CAST(:identity AS uuid))"
                                ),
                                {"identity": str(row["cfdi_report_id"])},
                            )
                            if assigned:
                                raise ValueError(
                                    "cashflow_fiscal_parent_scope_mismatch"
                                )
                if not row.get("torneo_id") and row.get("cuenta_gastos_id"):
                    row["torneo_id"] = await session.scalar(
                        text(
                            "SELECT torneo_id FROM cuentas_de_gastos "
                            "WHERE id=CAST(:identity AS uuid)"
                        ).execution_options(autoflush=False),
                        {"identity": str(row["cuenta_gastos_id"])},
                    )
                selected = row.get("monto_total")
                if selected is None:
                    selected = row.get("monto_solicitado")
                try:
                    amount = Decimal(str(selected)) if selected is not None else None
                    row["payable_amount"] = (
                        resolve_payable_document_amount(SimpleNamespace(**row))
                        if amount is not None and amount.is_finite()
                        else None
                    )
                except (InvalidOperation, TypeError, ValueError):
                    row["payable_amount"] = None
                provider = row.get("beneficiario_proveedor_cliente_id")
                employee = row.get("beneficiario_empleado_id")
                row["party"] = (
                    await _cashflow_name(session, "proveedores_clientes", provider)
                    if provider
                    else (
                        await _cashflow_name(session, "empleados", employee)
                        if employee
                        else await _cashflow_name(
                            session,
                            "proveedores_clientes",
                            row.get("proveedor_cliente_id"),
                        )
                    )
                )
                row["project_id"] = str(
                    row.get("torneo_id") or row.get("proyecto_otro") or ""
                )
                row["project_name"] = (
                    await _cashflow_name(
                        session, "tournaments", row.get("torneo_id"), "name"
                    )
                    if row.get("torneo_id")
                    else row.get("proyecto_otro")
                )
            return docs

        try:
            result["documents"] = await _cashflow_source_savepoint(
                session, document_operation
            )
            status["obligations"] = _cashflow_source_state(True, True)
        except SQLAlchemyError as exc:
            status["obligations"] = _cashflow_source_state(
                False, False, f"Obligaciones no disponibles ({type(exc).__name__})."
            )

        async def version_operation():
            from samchat.budgets.service import (
                list_budget_versions,
                resolve_definitive_budget_version_from_versions,
            )

            scope = (
                {} if company_context is None else {"company_context": company_context}
            )
            versions = await list_budget_versions(
                session,
                edition_year=edition_year or start.year,
                ensure_schema=False,
                **scope,
            )
            if company_context is None:
                allowed = await _cashflow_legacy_rows(
                    session,
                    "budget_versions",
                    ("id",),
                    "t.edition_year=:year",
                    {"year": edition_year or start.year},
                )
                identities = {str(row["id"]) for row in allowed}
                versions = [
                    version
                    for version in versions
                    if str(version.get("id")) in identities
                ]
            selected = None
            if budget_version_id:
                selected = next(
                    (
                        version
                        for version in versions
                        if str(version.get("id")) == str(budget_version_id)
                    ),
                    None,
                )
                if selected is None:
                    raise ValueError("cashflow_budget_version_selection_denied")
            else:
                selected = resolve_definitive_budget_version_from_versions(versions)
            return selected, [
                {
                    "id": str(version["id"]),
                    "version_name": version.get("version_name"),
                    "status": version.get("status"),
                    "edition_year": version.get("edition_year"),
                }
                for version in versions
            ]

        result["budget_version_options"] = []
        try:
            selected_version, options = await _cashflow_source_savepoint(
                session, version_operation
            )
            result["budget_version_options"] = options
            if selected_version is not None:
                budget_version_id = str(selected_version["id"])
        except SQLAlchemyError:
            # A missing version source must not abort bank/AP reads. Explicit
            # requests cannot silently pick another or an unowned version.
            if budget_version_id:
                raise ValueError("cashflow_budget_version_source_unavailable")
        result["budget_version_id"] = budget_version_id
        if not budget_version_id:
            for key in ("ar", "budget"):
                status[key] = _cashflow_source_state(
                    False, False, "No se seleccionó versión presupuestal."
                )
        else:
            # These owners are deliberately preserved. Their capped DTOs must
            # advertise coverage; a consumer never upgrades a preview to a total.
            from samchat.ar.service import build_ar_read_model
            from samchat.budgets.service import (
                get_budget_version,
                list_budget_lines,
                list_monthly_plan_for_lines,
            )

            scope = (
                {} if company_context is None else {"company_context": company_context}
            )
            tournament = (
                next(iter(allowed_tournaments))
                if allowed_tournaments and len(allowed_tournaments) == 1
                else None
            )
            if allowed_tournaments is not None and len(allowed_tournaments) != 1:
                for key in ("ar", "budget"):
                    status[key] = _cashflow_source_state(
                        False,
                        False,
                        "El owner requiere seleccionar un torneo para esta lectura.",
                    )
            else:

                async def budget_operation():
                    version = await get_budget_version(
                        session,
                        version_id=budget_version_id,
                        ensure_schema=False,
                        **scope,
                    )
                    if not isinstance(version, dict):
                        raise ValueError("cashflow_budget_version_unavailable")
                    lines = await list_budget_lines(
                        session,
                        version_id=budget_version_id,
                        tournament_id=tournament,
                        limit=5000,
                        ensure_schema=False,
                        **scope,
                    )
                    # Project visibility does not depend on a pending request or
                    # a complete weekly schedule. These lines are owner-scoped.
                    result["project_options"] = _cashflow_project_options(
                        {
                            "company_id": result["company_id"],
                            "authorized_tournament_ids": result[
                                "authorized_tournament_ids"
                            ],
                            "budget_plan": lines,
                        }
                    )
                    if len(lines) >= 5000:
                        return [], {"income": False, "expense": False}
                    plans = await list_monthly_plan_for_lines(
                        session,
                        line_ids=[str(row["id"]) for row in lines],
                        ensure_schema=False,
                        **scope,
                    )
                    from samchat.budgets.distribution import schedule_state, week_bounds
                    from samchat.budgets.tournament_weekly_overlay import (
                        effective_plans_for_lines,
                    )

                    overlay_failed = False
                    conflicts = {}
                    try:

                        async def overlay_operation():
                            return await effective_plans_for_lines(
                                session,
                                version_id=budget_version_id,
                                line_ids=[str(row["id"]) for row in lines],
                                baseline_plans=plans,
                            )

                        plans, conflicts = await _cashflow_source_savepoint(
                            session, overlay_operation
                        )
                    except (SQLAlchemyError, ValueError, TypeError) as exc:
                        overlay_failed = True
                        result["source_notes"].append(
                            f"budget_plan_unavailable:{type(exc).__name__}"
                        )
                    if conflicts:
                        result["source_notes"].append(
                            "budget_distribution_requires_reconciliation"
                        )
                    edition = version.get("edition_year")
                    if edition is None:
                        return [], {"income": False, "expense": False}
                    direction_complete = {
                        direction: edition is not None
                        and all(
                            bool(plans.get(str(line["id"])))
                            for line in lines
                            if (
                                line.get("line_direction")
                                or line.get("budget_direction")
                            )
                            == direction
                        )
                        for direction in ("income", "expense")
                    }
                    direction_complete["expense"] = direction_complete[
                        "expense"
                    ] and all(
                        schedule_state(line, plans.get(str(line["id"])) or {})["status"]
                        == "complete"
                        for line in lines
                        if (line.get("line_direction") or line.get("budget_direction"))
                        == "expense"
                    )
                    if conflicts or overlay_failed:
                        direction_complete["expense"] = False
                    records = []
                    for line in lines:
                        weekly = plans.get(str(line["id"])) or {}
                        for week, values in weekly.items():
                            try:
                                week_start, _ = week_bounds(int(edition), int(week))
                            except (TypeError, ValueError):
                                return [], {"income": False, "expense": False}
                            direction = line.get("line_direction") or line.get(
                                "budget_direction"
                            )
                            records.append(
                                {
                                    "budget_line_id": str(line["id"]),
                                    "year": week_start.year,
                                    "month": week_start.month,
                                    "currency": line.get("currency"),
                                    "direction": direction,
                                    "amount": (
                                        values.get("expected_income_amount")
                                        if direction == "income"
                                        else values.get("budget_expense_amount")
                                    ),
                                    "tournament_id": line.get("tournament_id"),
                                }
                            )
                    return records, direction_complete

                try:
                    plan, direction_complete = await _cashflow_source_savepoint(
                        session, budget_operation
                    )
                    complete = all(direction_complete.values())
                    result["budget_plan"] = plan
                    currency_known = bool(plan) and all(
                        row.get("currency")
                        and row.get("year") is not None
                        and _number(row.get("amount")) is not None
                        for row in plan
                    )
                    status["budget"] = _cashflow_source_state(
                        True,
                        complete and currency_known,
                        (
                            "Falta plan mensual completo o la consulta está truncada; "
                            "total no acreditado."
                            if not complete
                            else (
                                "Falta plan mensual acreditado."
                                if not plan
                                else (
                                    "Moneda del plan no acreditada; "
                                    "se conserva como plan nominal separado."
                                    if not currency_known
                                    else ""
                                )
                            )
                        ),
                    )
                    status["budget"]["direction_complete"] = {
                        direction: valid and currency_known
                        for direction, valid in direction_complete.items()
                    }
                except SQLAlchemyError as exc:
                    status["budget"] = _cashflow_source_state(
                        False, False, f"Plan no disponible ({type(exc).__name__})."
                    )

                async def ar_operation():
                    return await build_ar_read_model(
                        session,
                        budget_version_id=budget_version_id,
                        tournament_id=tournament,
                        calendar_year=start.year,
                        limit=5000,
                        ensure_schema=False,
                        credit_days_default=max(0, min(int(credit_days), 365)),
                        **scope,
                    )

                try:
                    ar = await _cashflow_source_savepoint(session, ar_operation)
                    result["ar_payload"] = ar
                    coverage = (
                        ar.get("source_status")
                        or (ar.get("summary") or {}).get("source_status")
                        or {}
                    )
                    complete = (
                        company_context is not None
                        and bool(coverage)
                        and not coverage.get("income_lines_truncated", True)
                        and not coverage.get("candidates_truncated", True)
                    )
                    status["ar"] = _cashflow_source_state(
                        True,
                        complete,
                        (
                            ""
                            if complete
                            else "Cobertura completa de CxC no acreditada "
                            "por el owner; detalle parcial."
                        ),
                    )
                except SQLAlchemyError as exc:
                    status["ar"] = _cashflow_source_state(
                        False, False, f"CxC no disponible ({type(exc).__name__})."
                    )
        if helpers is not None:
            await helpers._read_company(session, company_context)
            if snapshot != await helpers._read_snapshot(session):
                raise CompanyScopeError("cashflow_source_changed", 409)
    return result


async def build_cashflow_request_payload(request, actor, session, **filters):
    """Resolve company through the installed request owner and preserve context."""
    import os
    from urllib.parse import urlencode

    from fastapi import HTTPException

    authority_errors: tuple[type[Any], ...]
    try:
        from devnous.gastos.services.company_scope_service import CompanyScopeError
    except ModuleNotFoundError as exc:
        if exc.name != "devnous.gastos.services.company_scope_service":
            raise
        authority_errors = ()
    else:
        authority_errors = (CompanyScopeError,)

    try:
        from devnous.gastos.routes.company_dependencies import (
            get_current_company_context,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "devnous.gastos.routes.company_dependencies":
            raise
        if os.getenv("SAMCHAT_MULTIEMPRESA_ENABLED", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise HTTPException(503, "multiempresa_activation_pending") from None
        company_context = None
    else:
        try:
            company_context = await get_current_company_context(
                request, actor=actor, session=session
            )
        except authority_errors as exc:
            raise HTTPException(exc.status_code, exc.code) from None
    project = str(filters.get("project") or "all")
    if project.startswith(("tournament:", "otro:")):
        project = project.split(":", 1)[1]
    filters["project"] = project
    if filters.get("currency", "all") != "all":
        curr = filters["currency"]
        if len(curr) != 3 or not curr.isalpha():
            raise HTTPException(422, "Moneda inválida")
        filters["currency"] = curr.upper()
    try:
        payload = await build_cashflow_planning_read_model(
            session,
            company_context=company_context,
            **filters,
        )
    except authority_errors as exc:
        raise HTTPException(exc.status_code, exc.code) from None
    except ValueError as exc:
        # Domain authority errors retain their status; never return exception text.
        raise HTTPException(
            getattr(exc, "status_code", 409),
            getattr(exc, "code", "cashflow_context_or_source_invalid"),
        ) from None
    if payload.get("budget_version_id"):
        filters["budget_version_id"] = payload["budget_version_id"]
    period = payload["period"]
    query = dict(
        year=period["year"],
        month=period["month"],
        limit=payload["details"]["page_size"],
        horizon_months=filters.get("horizon_months", 3),
        periodo=period["grouping"],
        currency=filters.get("currency", "all"),
        cuenta_bancaria=filters.get("account", "all"),
        proyecto_scope=filters.get("project", "all"),
    )
    for source, target in (
        ("horizon_days", "horizon_days"),
        ("credit_days", "dias_credito"),
        ("budget_version_id", "budget_version_id"),
        ("edition_year", "edition_year"),
    ):
        if filters.get(source) is not None:
            query[target] = filters[source]
    path = str(request.url.path).removesuffix("/export.xlsx")
    allowed = {"detail_currency", "detail_period", "detail_direction"}
    selection = {k: v for k, v in filters.items() if k in allowed and v}

    def link(updates=None, *, fragment="cashflow-movements"):
        return (
            path
            + "?"
            + urlencode({**query, **selection, **(updates or {})})
            + "#"
            + fragment
        )

    details = payload["details"]
    payload["page_links"] = {
        "previous": (
            link({"page": details["page"] - 1}) if details["page"] > 1 else None
        ),
        "next": (
            link({"page": details["page"] + 1})
            if details["page"] < details["pages"]
            else None
        ),
    }
    payload["detail_links"] = {}
    for row in payload["monthly_buckets"]:
        for direction in ("in", "out"):
            payload["detail_links"][f"{row['currency']}:{row['key']}:{direction}"] = (
                link(
                    dict(
                        detail_currency=row["currency"],
                        detail_period=row["key"],
                        detail_direction=direction,
                        page=1,
                    )
                )
            )
    for curr in payload["summary_by_currency"]:
        for direction in ("in", "out"):
            payload["detail_links"][f"{curr}:total:{direction}"] = link(
                dict(
                    detail_currency=curr,
                    detail_period="",
                    detail_direction=direction,
                    page=1,
                )
            )
    payload["payables_url"] = (
        "/admin/contabilidad/cuentas-por-pagar" if company_context is None else None
    )
    payload["filters"] = query
    payload["export_url"] = "/admin/contabilidad/cash-flow/export.xlsx?" + urlencode(
        query
    )
    payload["detail_selection"] = " · ".join(str(value) for value in selection.values())
    return payload
