"""The HWC exception applies only to its exact account code across COI paths."""

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devnous.gastos.routes import admin_routes
from devnous.gastos.services import expense_accounting_cleanup_service as cleanup
from devnous.gastos.services import expense_coi_export_service as export


@pytest.mark.parametrize(
    "code,name,allowed",
    [
        ("5300-016-003", "Operacion HWC", True),
        (" 5300-016-003 ", "OPERACIÓN HWC", True),
        ("5300-016-004", "Operacion HWC", False),
        ("5300-016-0030", "Operacion HWC", False),
        (None, "Operacion HWC", False),
        ("5300-016-004", "Sin requisitos fiscales", True),
        (None, " NO DEDUCIBLE ", True),
        (None, "Gastos no deducibles", True),
        (None, None, False),
    ],
)
def test_account_exception_is_consistent(code, name, allowed):
    account = SimpleNamespace(codigo=code, nombre=name)
    expense = SimpleNamespace(cuenta_contable=account)
    assert cleanup._allows_cleanup_without_cfdi(expense) is allowed
    assert export.allows_coi_without_cfdi(account) is allowed
    assert admin_routes._allows_coi_without_cfdi(account) is allowed
    assert admin_routes._allows_coi_without_cfdi_name(name, code) is allowed


def _expense(code="5300-016-003", *, contra=True):
    return SimpleNamespace(
        id=uuid4(),
        cuenta_gastos_id=None,
        documento_id=None,
        informe_documento_id=None,
        solicitud_documento_id=None,
        cuenta_contable_id=uuid4(),
        cuenta_contable=SimpleNamespace(codigo=code, nombre="Operacion HWC"),
        contra_cuenta_contable_id=uuid4() if contra else None,
        contra_cuenta_contable=(
            SimpleNamespace(codigo="2120-000-000") if contra else None
        ),
        cfdi_report_id=None,
        cfdi_report=None,
        gasto_cantidad=100.0,
        fecha=datetime(2026, 10, 22),
        concepto="Operacion HWC",
        numero_referencia="HWC-TEST",
        proyecto="Homeless World Cup Mexico",
    )


async def _preview(*_args):
    return {"taxes": {"iva_trasladado": 0, "retenciones": []}}


@pytest.mark.asyncio
async def test_hwc_cleanup_and_individual_export_without_cfdi(monkeypatch):
    monkeypatch.setattr(cleanup, "build_expense_accounting_preview", _preview)
    monkeypatch.setattr(export, "build_expense_accounting_preview", _preview)
    expense = _expense()
    state = await cleanup.build_cleanup_preview(None, expense)
    assert state["status"] == "Listo COI"
    row = await export.build_expense_cfdi_for_export(_Session([]), expense)
    assert row.cuenta_contable == "5300-016-003"
    assert row.allows_missing_cfdi is True
    assert row.cfdi_uuid is None
    assert row.total == 100
    assert row.cuenta_contrapartida == "2120-000-000"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,contra,issue",
    [
        ("5300-016-004", True, "Falta CFDI vinculado"),
        ("5300-016-003", False, "Falta contrapartida"),
    ],
)
async def test_export_keeps_cfdi_and_counterpart_guards(
    monkeypatch, code, contra, issue
):
    monkeypatch.setattr(cleanup, "build_expense_accounting_preview", _preview)
    expense = _expense(code, contra=contra)
    state = await cleanup.build_cleanup_preview(None, expense)
    assert state["status"] == "Pendiente"
    assert issue in state["issues"]
    with pytest.raises(ValueError, match=issue):
        await export.build_expense_cfdi_for_export(_Session([]), expense)


class _Session:
    def __init__(self, rows):
        self.rows = rows

    async def scalar(self, query):
        assert "amex_recognition_consumptions" in str(query)
        return None

    async def execute(self, _query):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self.rows))


@pytest.mark.asyncio
async def test_batch_export_filters_other_operating_accounts(monkeypatch):
    monkeypatch.setattr(cleanup, "build_expense_accounting_preview", _preview)
    monkeypatch.setattr(export, "build_expense_accounting_preview", _preview)
    session = _Session([_expense(), _expense("5300-016-004")])
    year, month, rows = await admin_routes._build_finance_coi_batch_expenses(
        session, year=2026, month=10
    )
    assert (year, month) == (2026, 10)
    assert len(rows) == 1
    assert rows[0].cuenta_contable == "5300-016-003"
    assert rows[0].allows_missing_cfdi is True
    _, _, summary = await admin_routes._build_finance_coi_batch_blocker_summary(
        session, year=2026, month=10
    )
    assert summary["non_fiscal_without_cfdi"] == 1


@pytest.mark.asyncio
async def test_finance_page_labels_hwc_from_exact_catalog_code(monkeypatch):
    import samchat.finance_platform as platform

    account_id = uuid4()
    item = {
        "id": str(uuid4()),
        "cuenta_contable_id": str(account_id),
        "cuenta_contable_nombre": "Operacion HWC",
    }

    async def snapshot(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(platform, "build_finance_source_snapshot", snapshot)
    monkeypatch.setattr(
        platform, "build_finance_platform_snapshot",
        lambda _: {"accounting_close_center": {"pending_coi_expenses": [item]}},
    )
    session = _Session([
        SimpleNamespace(id=account_id, codigo="5300-016-003", nombre="Operacion HWC")
    ])
    response = await admin_routes.admin_finance_platform(
        request=SimpleNamespace(query_params={}),
        current_empleado=SimpleNamespace(id=uuid4(), nombre="Test", rol="superadmin"),
        session=session, year=2026, month=10, expense_id=None,
    )
    assert "No deducible sin CFDI" in response.body.decode()
