from pathlib import Path
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.responses import RedirectResponse, Response

from devnous.gastos.routes import user_routes
from devnous.gastos.services.monthly_diot_service import (
    MonthlyDiotIssue,
    MonthlyDiotScope,
)


ROUTES = Path("src/devnous/gastos/routes/user_routes.py")


def test_monthly_diot_routes_and_navigation_are_registered():
    source = ROUTES.read_text(encoding="utf-8")

    assert '("/admin/contabilidad/diot", "DIOT", "diot")' in source
    assert (
        '@router.get("/admin/contabilidad/diot", response_class=HTMLResponse)'
        in source
    )
    assert (
        '@router.get("/admin/contabilidad/diot/export.txt", '
        'response_model=None)' in source
    )
    assert (
        '@router.get("/admin/contabilidad/diot/export.xlsx", '
        'response_model=None)' in source
    )
    guard = "current_empleado: Empleado = require_admin_finanzas()"
    assert source.count(guard) >= 3
    assert "TXT bloqueado" in source
    assert "Sin fecha efectiva" in source


def _scope(*, eligible=True, blockers=None, undated=None):
    scope = MonthlyDiotScope(
        year=2026,
        month=9,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
        blockers=list(blockers or []),
        undated=list(undated or []),
    )
    if eligible:
        expense = SimpleNamespace(
            id=uuid4(),
            numero_referencia="S-26000416",
            gasto_cantidad=Decimal("11600"),
            moneda="MXN",
            cfdi_report=SimpleNamespace(
                cfdi_uuid="UUID",
                emisor_rfc="AAA010101AAA",
            ),
        )
        scope.eligible_expenses = [expense]
        scope.effective_payment_dates[str(expense.id)] = date(2026, 9, 10)
        scope.payment_date_sources[str(expense.id)] = "documento_directo"
    return scope


def _patch_view_dependencies(monkeypatch, scope):
    async def fake_scope(*_args, **_kwargs):
        return scope

    monkeypatch.setattr(user_routes, "build_monthly_diot_scope", fake_scope)
    monkeypatch.setattr(
        user_routes,
        "build_diot_export",
        lambda *_args, **_kwargs: SimpleNamespace(
            summary_rows=[
                SimpleNamespace(
                    amounts={
                        "base_16": Decimal("10000"),
                        "iva_acreditable_16": Decimal("1600"),
                        "iva_retenido": Decimal("0"),
                    }
                )
            ]
        ),
    )
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_: "NAV")
    monkeypatch.setattr(user_routes, "_contabilidad_subnav", lambda *_: "SUBNAV")
    monkeypatch.setattr(user_routes, "currency_for", lambda *_: "MXN")
    monkeypatch.setattr(
        user_routes, "format_currency", lambda amount, *_: f"${amount}"
    )


@pytest.mark.asyncio
async def test_monthly_diot_view_renders_eligible_blocker_and_undated(monkeypatch):
    blocker = MonthlyDiotIssue(
        expense_id="b",
        reference="O-BLOCK",
        code="cfdi_faltante",
        message="Falta CFDI",
        effective_payment_date=date(2026, 9, 11),
    )
    undated = MonthlyDiotIssue(
        expense_id="u",
        reference="O-UNDATED",
        code="fecha_pago_faltante",
        message="Falta fecha",
    )
    scope = _scope(blockers=[blocker], undated=[undated])
    _patch_view_dependencies(monkeypatch, scope)

    html = await user_routes.contabilidad_diot_mensual_view(
        request=SimpleNamespace(query_params={}),
        session=SimpleNamespace(),
        current_empleado=SimpleNamespace(),
        year=2026,
        month=9,
    )

    assert "S-26000416" in html
    assert "O-BLOCK" in html
    assert "O-UNDATED" in html
    assert "TXT bloqueado" in html


@pytest.mark.asyncio
async def test_monthly_diot_view_recovers_from_invalid_period(monkeypatch):
    scope = _scope(eligible=False)
    calls = 0

    async def fake_scope(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("Periodo inválido")
        return scope

    _patch_view_dependencies(monkeypatch, scope)
    monkeypatch.setattr(user_routes, "build_monthly_diot_scope", fake_scope)

    html = await user_routes.contabilidad_diot_mensual_view(
        request=SimpleNamespace(query_params={}),
        session=SimpleNamespace(),
        current_empleado=SimpleNamespace(),
        year=2026,
        month=13,
    )

    assert "Periodo inválido" in html
    assert calls == 2


@pytest.mark.asyncio
async def test_monthly_diot_txt_success_and_blocking_redirects(monkeypatch):
    scope = _scope()

    async def fake_scope(*_args, **_kwargs):
        return scope

    monkeypatch.setattr(user_routes, "build_monthly_diot_scope", fake_scope)
    monkeypatch.setattr(
        user_routes, "build_diot_export", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(user_routes, "generate_diot_txt", lambda *_: b"DIOT")

    response = await user_routes.exportar_diot_mensual_txt(
        session=SimpleNamespace(),
        current_empleado=SimpleNamespace(),
        year=2026,
        month=9,
    )
    assert isinstance(response, Response)
    assert response.body == b"DIOT"

    scope.blockers = [
        MonthlyDiotIssue("b", "O-1", "cfdi_faltante", "Falta CFDI")
    ]
    blocked = await user_routes.exportar_diot_mensual_txt(
        session=SimpleNamespace(),
        current_empleado=SimpleNamespace(),
        year=2026,
        month=9,
    )
    assert isinstance(blocked, RedirectResponse)

    scope.blockers = []
    scope.eligible_expenses = []
    empty = await user_routes.exportar_diot_mensual_txt(
        session=SimpleNamespace(),
        current_empleado=SimpleNamespace(),
        year=2026,
        month=9,
    )
    assert isinstance(empty, RedirectResponse)


@pytest.mark.asyncio
async def test_monthly_diot_exports_redirect_invalid_period_and_create_excel(
    monkeypatch,
):
    async def invalid_scope(*_args, **_kwargs):
        raise ValueError("Periodo inválido")

    monkeypatch.setattr(user_routes, "build_monthly_diot_scope", invalid_scope)
    invalid_txt = await user_routes.exportar_diot_mensual_txt(
        session=SimpleNamespace(),
        current_empleado=SimpleNamespace(),
        year=2026,
        month=13,
    )
    invalid_xlsx = await user_routes.exportar_diot_mensual_excel(
        session=SimpleNamespace(),
        current_empleado=SimpleNamespace(),
        year=2026,
        month=13,
    )
    assert isinstance(invalid_txt, RedirectResponse)
    assert isinstance(invalid_xlsx, RedirectResponse)

    scope = _scope(eligible=False)

    async def valid_scope(*_args, **_kwargs):
        return scope

    monkeypatch.setattr(user_routes, "build_monthly_diot_scope", valid_scope)
    monkeypatch.setattr(
        user_routes, "build_diot_export", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        user_routes,
        "create_diot_excel",
        lambda *_args, **_kwargs: b"XLSX",
    )
    response = await user_routes.exportar_diot_mensual_excel(
        session=SimpleNamespace(),
        current_empleado=SimpleNamespace(),
        year=2026,
        month=9,
    )

    assert isinstance(response, Response)
    assert response.body == b"XLSX"
