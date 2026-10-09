from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.services.monthly_diot_service import (
    build_monthly_diot_scope_from_records,
    monthly_period_bounds,
)


def _cfdi(*, total=1160, currency="MXN", rfc="AAA010101AAA"):
    return SimpleNamespace(
        id=uuid4(),
        cfdi_uuid=str(uuid4()).upper(),
        emisor_rfc=rfc,
        moneda=currency,
        tipo_de_comprobante="I",
        total=total,
    )


def _expense(*, cfdi=None, account_id=None, shared=False, amount=1160):
    cfdi = cfdi if cfdi is not None else _cfdi(total=amount)
    return SimpleNamespace(
        id=uuid4(),
        numero_referencia=f"O-{uuid4().hex[:8]}",
        estado_gasto="activo",
        coi_estado="pendiente",
        cfdi_report_id=getattr(cfdi, "id", None),
        cfdi_report=cfdi,
        cuenta_gastos_id=account_id,
        cfdi_compartido_confirmado=shared,
        gasto_cantidad=amount,
        propina_no_deducible=0,
    )


def test_month_bounds_validate_calendar():
    assert monthly_period_bounds(2026, 2) == (
        date(2026, 2, 1),
        date(2026, 2, 28),
    )


def test_scope_prefers_direct_date_then_unique_account_fallback():
    direct = _expense()
    account_id = uuid4()
    fallback = _expense(account_id=account_id)

    scope = build_monthly_diot_scope_from_records(
        [direct, fallback],
        year=2026,
        month=9,
        direct_payment_dates={str(direct.id): [date(2026, 9, 22)]},
        account_payment_dates={str(account_id): [date(2026, 9, 10)]},
    )

    assert [item.id for item in scope.eligible_expenses] == [
        fallback.id,
        direct.id,
    ]
    assert scope.payment_date_sources[str(direct.id)] == "documento_directo"
    assert (
        scope.payment_date_sources[str(fallback.id)]
        == "cuenta_gastos_fecha_unica"
    )
    assert scope.can_export_txt is True


def test_scope_does_not_guess_missing_or_ambiguous_dates():
    missing = _expense()
    account_id = uuid4()
    ambiguous = _expense(account_id=account_id)

    scope = build_monthly_diot_scope_from_records(
        [missing, ambiguous],
        year=2026,
        month=9,
        direct_payment_dates={},
        account_payment_dates={
            str(account_id): [date(2026, 9, 1), date(2026, 9, 2)]
        },
    )

    assert scope.eligible_expenses == []
    assert {issue.code for issue in scope.undated} == {
        "fecha_pago_faltante",
        "fecha_pago_ambigua",
    }


def test_period_blockers_disable_txt_without_hiding_excel_scope():
    expense = _expense(
        cfdi=SimpleNamespace(id=uuid4(), cfdi_uuid="X", emisor_rfc="")
    )

    scope = build_monthly_diot_scope_from_records(
        [expense],
        year=2026,
        month=9,
        direct_payment_dates={str(expense.id): [date(2026, 9, 5)]},
        account_payment_dates={},
    )

    assert scope.eligible_expenses == []
    assert [issue.code for issue in scope.blockers] == ["rfc_emisor_faltante"]
    assert scope.can_export_txt is False


def test_unconfirmed_multiuse_cfdi_is_blocked():
    cfdi = _cfdi(total=1000)
    first = _expense(cfdi=cfdi, amount=500)
    second = _expense(cfdi=cfdi, amount=500)
    payment_dates = {
        str(first.id): [date(2026, 9, 3)],
        str(second.id): [date(2026, 9, 4)],
    }

    scope = build_monthly_diot_scope_from_records(
        [first, second],
        year=2026,
        month=9,
        direct_payment_dates=payment_dates,
        account_payment_dates={},
    )

    assert len(scope.blockers) == 2
    assert {issue.code for issue in scope.blockers} == {
        "cfdi_duplicado_no_confirmado"
    }


def test_out_of_period_and_reversed_expenses_are_excluded():
    outside = _expense()
    reversed_expense = _expense()
    reversed_expense.coi_estado = "reversar"

    scope = build_monthly_diot_scope_from_records(
        [outside, reversed_expense],
        year=2026,
        month=9,
        direct_payment_dates={
            str(outside.id): [date(2026, 8, 31)],
            str(reversed_expense.id): [date(2026, 9, 1)],
        },
        account_payment_dates={},
    )

    assert scope.eligible_expenses == []
    assert scope.blockers == []
