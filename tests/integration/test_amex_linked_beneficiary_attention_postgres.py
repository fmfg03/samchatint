"""Real report selectors distinguish one linked party from beneficiary conflicts."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text

from devnous.gastos import models
from devnous.gastos.services.amex_attention_policy import (
    EXCLUDED_EMPLOYEE_IDS,
    load_amex_attention_context,
)

# Initialize the runtime package before importing the legacy fixture chain.
# isort: split
import test_amex_statement_workflow_postgres as workflow_fixture

approved_pg = workflow_fixture.approved_pg
legacy_pg = workflow_fixture.legacy_pg
pg = workflow_fixture.pg
setup_report = workflow_fixture.setup_report
statement = workflow_fixture.statement


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_link,alternate,expected_visible",
    [
        ("same", None, False),
        ("different", None, True),
        ("missing", None, True),
        ("same", "proveedor", True),
    ],
)
async def test_linked_employee_provider_in_all_real_report_queues(
    pg, monkeypatch, provider_link, alternate, expected_visible
):
    from devnous.gastos.routes import admin_amex_accounting_routes, user_routes
    from devnous.gastos.services import amex_statement_workflow_service as workflow

    data = await setup_report(pg)
    statement_id = await statement(pg, data)
    odilon = UUID(EXCLUDED_EMPLOYEE_IDS[0])
    provider_id = uuid4()
    async with pg.factory.begin() as session:
        session.add(
            models.ProveedorCliente(
                id=provider_id,
                nombre="Explicit linked party",
                tipo="empleado",
                empleado_id=(
                    odilon
                    if provider_link == "same"
                    else pg.actor if provider_link == "different" else None
                ),
                activo=True,
            )
        )
        await session.flush()
        report = await session.get(models.Documento, data.informe.id)
        account = await session.get(models.CuentaDeGastos, report.cuenta_gastos_id)
        for row in [report, account]:
            row.beneficiario_empleado_id = odilon
            row.beneficiario_proveedor_cliente_id = provider_id
            row.beneficiario_alterno_tipo = alternate

    monkeypatch.setattr(
        user_routes, "suggest_amex_cfdi_matches", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        user_routes, "suggest_pase_monthly_cfdi_matches", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_a, **_k: "")
    async with pg.factory() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        actor = await session.get(models.Empleado, pg.actor)
        report = await session.get(models.Documento, data.informe.id)
        expense = await session.get(models.ExpenseReport, data.reports[0].id)
        bank = await session.get(models.ExpenseReport, data.imports[0].id)
        context = await load_amex_attention_context(
            session, documents=[report], expenses=[expense, bank]
        )
        assert context.policy.valid
        assert context.document_decisions[report.id].visible is expected_visible
        assert context.expense_decisions[expense.id].visible is expected_visible
        assert context.expense_decisions[bank.id].visible
        view = await workflow.statement_workflow_view(
            session, statement_id, actor=actor
        )
        assert bool(view["reports"]) is expected_visible
        assert len(view["items"]) == 1
        page = await user_routes.amex_conciliacion_view(
            request=None,
            session=session,
            current_empleado=actor,
            year=2026,
            month=10,
            msg=None,
            error_msg=None,
        )
        assert (f'<option value="{expense.id}">' in page) is expected_visible
        assert f'name="imported_expense_id" value="{bank.id}"' in page
        reports_page = await admin_amex_accounting_routes.amex_accounting_reports(
            session=session, current_empleado=actor
        )
        assert ("I-TEST" in reports_page.body.decode()) is expected_visible
        assert (
            await session.scalar(
                select(func.count()).select_from(models.AccountingPoliza)
            )
            == 0
        )
        assert report.beneficiario_empleado_id == odilon
        assert report.beneficiario_proveedor_cliente_id == provider_id
        assert expense.estado_gasto == "activo"
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_amex_entry_navigation_obeys_real_direct_read_rule(
    pg, monkeypatch, denied
):
    from starlette.requests import Request

    from devnous.gastos.routes import client_executive_routes, user_routes

    # Direction and approval-position catalogs are outside this AMEX fixture.
    # Access rules and the AMEX destination owner remain real PostgreSQL reads.
    monkeypatch.setattr(
        client_executive_routes,
        "direction_entry_visible",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        user_routes, "_can_review_pending_approvals", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(user_routes, "_is_budget_control_user", lambda *_: False)
    monkeypatch.setattr(user_routes, "_is_configuration_panel_user", lambda *_: False)

    if denied:
        async with pg.factory.begin() as session:
            session.add(
                models.AccessControlRule(
                    tool_key="amex_direct",
                    action_key="ver",
                    role_key="finanzas",
                    area_key="",
                    allowed=False,
                    active=True,
                )
            )
    expected = (
        "/admin/contabilidad/amex/estados"
        if denied
        else "/admin/contabilidad/amex/estados/directo"
    )
    forbidden = (
        "/admin/contabilidad/amex/estados/directo"
        if denied
        else "/admin/contabilidad/amex/estados"
    )
    async with pg.factory() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        actor = await session.get(models.Empleado, pg.actor)
        request = Request({"type": "http", "query_string": b"", "session": {}})
        panel = await user_routes.panel(request, session, actor)
        batch = await user_routes.carga_masiva_amex_get(request, session, actor)
        for html in [panel, batch]:
            assert f'href="{expected}"' in html
            assert f'href="{forbidden}"' not in html
        assert 'action="/gastos/carga-masiva-amex"' in batch
        assert 'href="/admin/gastos/amex/conciliacion"' in batch
        await session.rollback()
