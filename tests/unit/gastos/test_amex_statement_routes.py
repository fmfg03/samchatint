"""ASGI authority, explicit uploads and transactional statement navigation."""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from devnous.gastos.routes import admin_amex_statement_routes as routes
from devnous.gastos.routes.dependencies import get_current_empleado, get_db_session
from tests.unit.gastos.test_amex_accounting_identity_context import (
    identity_review_fixture,
    render_identity_review,
)


@pytest.fixture
def workspace(monkeypatch):
    session = NS(commit=AsyncMock(), rollback=AsyncMock())
    actor = NS(id=uuid4(), rol="finanzas", activo=True)
    statement = NS(
        id=uuid4(),
        filename="<script>bad()</script>.csv",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
    )
    item = NS(
        id=uuid4(),
        row_number=2,
        status="imported",
        reason_code=None,
        imported_expense_id=uuid4(),
        normalized_json={
            "date": "2026-08-31",
            "amount": "229.98",
            "description": "<img src=x onerror=bad()>",
        },
    )
    review_session, informe, expenses, accounts = identity_review_fixture()
    view = {
        "import": statement,
        "items": [item],
        "reports": [informe],
        "selected_reports": [],
        "cuts": [],
        "warnings": [],
        "blockers": [],
        "imported_totals": {"MXN": Decimal("229.98")},
        "operational_pending_count": 1,
        "bank_pending_count": 1,
        "financial_progress": {
            "linked_cut_count": 0,
            "selected_source_count": None,
            "selected_generated_count": None,
        },
    }

    async def read(session, import_id, *, actor, selected_informe_ids=()):
        return {
            **view,
            "selected_reports": (
                [{"document": informe, "preview": review_session.preview}]
                if selected_informe_ids
                else []
            ),
        }

    async def render(*args):
        # Each GET has fresh read results; the browser revisits the report after
        # confirmation without exhausting a one-request synthetic fixture.
        review_session.execute.side_effect = [
            NS(
                scalars=lambda rows=rows: NS(all=lambda: rows),
                all=lambda rows=rows: rows,
            )
            for rows in (accounts, [], review_session.identity_rows, [])
        ]
        return await render_identity_review(review_session, informe)

    monkeypatch.setattr(routes, "statement_workflow_view", AsyncMock(side_effect=read))
    monkeypatch.setattr(routes, "amex_accounting_report_review", render)
    monkeypatch.setattr(
        routes,
        "list_statement_workflows",
        AsyncMock(
            return_value=[
                {
                    "import": statement,
                    "items_count": 1,
                    "imported_count": 1,
                    "skipped_count": 0,
                    "linked_cut_count": 0,
                    "imported_totals": {"MXN": Decimal("229.98")},
                    "operational_pending_count": 1,
                    "bank_pending_count": 1,
                    "financial_progress": {
                        "linked_cut_count": 0,
                        "selected_source_count": None,
                        "selected_generated_count": None,
                    },
                }
            ]
        ),
    )
    monkeypatch.setattr(
        routes,
        "list_amex_card_accounts",
        AsyncMock(
            return_value=[NS(id=uuid4(), card_label="Tarjeta <prueba>", last4="5007")]
        ),
    )
    session.execute = AsyncMock(
        return_value=NS(
            scalars=lambda: NS(
                all=lambda: [NS(id=actor.id, nombre="<script>persona</script>")]
            )
        )
    )
    monkeypatch.setattr(
        routes,
        "import_amex_statement",
        AsyncMock(return_value={"status": "imported", "import": statement}),
    )
    monkeypatch.setattr(
        routes,
        "confirm_statement_informe",
        AsyncMock(return_value=NS(status="created", reason=None)),
    )
    monkeypatch.setattr(routes, "incorporate_statement_cut", AsyncMock())
    monkeypatch.setattr(
        routes, "export_statement", AsyncMock(return_value=b"frozen-policies")
    )
    session.get = AsyncMock(return_value=NS(informe_id=informe.id))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_empleado] = lambda: actor
    app.dependency_overrides[get_db_session] = lambda: session
    return NS(
        app=app,
        session=session,
        actor=actor,
        statement=statement,
        item=item,
        view=view,
        informe=informe,
        preview=review_session.preview,
        expenses=expenses,
        review_session=review_session,
    )


async def request(workspace, method, path, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=workspace.app), base_url="http://test.local"
    ) as client:
        return await client.request(method, path, **kwargs)


def confirmation_data(workspace):
    import json

    return {
        "selected_informe_ids": json.dumps([str(workspace.informe.id)]),
        "expected_versions": json.dumps(workspace.preview.current_versions),
        "accounting_date": "2026-09-30",
        "confirmed": "true",
        "confirmation_mode": "approved_informe",
        "expected_source_key": workspace.preview.source_key,
        "decisions": "[]",
        "reason": "Atestación explícita",
    }


@pytest.mark.asyncio
async def test_reads_never_write_and_escape(workspace):
    for suffix in (
        "",
        "/nuevo",
        f"/{workspace.statement.id}/pendientes",
        f"/{workspace.statement.id}/polizas",
    ):
        response = await request(workspace, "GET", routes.BASE + suffix)
        assert response.status_code == 200
        assert "<script>bad()" not in response.text
        assert "<script>persona" not in response.text
        assert "<img src=x" not in response.text
    workspace.session.commit.assert_not_awaited()
    workspace.session.rollback.assert_not_awaited()
    pending = await request(
        workspace, "GET", f"{routes.BASE}/{workspace.statement.id}/pendientes"
    )
    assert "229.98" in pending.text and "2026-08-31" in pending.text
    assert "Falta informe aprobado" in pending.text
    assert 'href="/informes-de-gastos"' in pending.text


@pytest.mark.asyncio
@pytest.mark.parametrize("active,role", [(False, "finanzas"), (True, "usuario")])
async def test_all_routes_reject_inactive_nonfinance(workspace, active, role):
    workspace.actor.activo, workspace.actor.rol = active, role
    for suffix in (
        "",
        "/nuevo",
        f"/{workspace.statement.id}/pendientes",
        f"/{workspace.statement.id}/polizas",
        f"/{workspace.statement.id}/exportar.csv",
    ):
        assert (
            await request(workspace, "GET", routes.BASE + suffix)
        ).status_code == 403
    routes.statement_workflow_view.assert_not_awaited()
    workspace.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_context_and_redirect(workspace):
    response = await request(
        workspace,
        "POST",
        routes.BASE + "/nuevo",
        data={
            "card_account_id": str(uuid4()),
            "employee_id": str(workspace.actor.id),
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
        },
        files={"file": ("estado.csv", b"date,amount\n2026-08-31,229.98", "text/csv")},
    )
    assert response.status_code == 303
    assert f"/{workspace.statement.id}/pendientes" in response.headers["location"]
    kwargs = routes.import_amex_statement.await_args.kwargs
    assert kwargs["content"].startswith(b"date,amount")
    assert kwargs["period_start"] == date(2026, 9, 1)
    assert kwargs["additional_confirmed"] is False
    workspace.session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["invalid", "context_conflict", "additional_confirmation_required"]
)
async def test_upload_warning_no_commit(workspace, status):
    routes.import_amex_statement.return_value = {"status": status}
    response = await request(
        workspace,
        "POST",
        routes.BASE + "/nuevo",
        data={
            "card_account_id": str(uuid4()),
            "employee_id": str(workspace.actor.id),
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
        },
        files={"file": ("estado.csv", b"bad", "text/csv")},
    )
    assert response.status_code == 200 and 'role="status"' in response.text
    workspace.session.commit.assert_not_awaited()
    workspace.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_full_report_renderer_posts_workflow_not_legacy(workspace):
    response = await request(
        workspace,
        "GET",
        f"{routes.BASE}/{workspace.statement.id}/polizas?"
        f"informe_id={workspace.informe.id}",
    )
    assert (
        f'action="{routes.BASE}/{workspace.statement.id}/informes/'
        f'{workspace.informe.id}/confirmar"' in response.text
    )
    assert 'name="selected_informe_ids"' in response.text
    assert 'name="expected_source_key"' in response.text
    assert "todas sus tarjetas y fechas" in response.text
    assert 'id="amex-cut-confirmation"' in response.text


@pytest.mark.asyncio
async def test_confirm_preserves_explicit_whole_source(workspace):
    response = await request(
        workspace,
        "POST",
        f"{routes.BASE}/{workspace.statement.id}/informes/"
        f"{workspace.informe.id}/confirmar",
        data=confirmation_data(workspace),
    )
    assert response.status_code == 303
    assert f"informe_id={workspace.informe.id}" in response.headers["location"]
    kwargs = routes.confirm_statement_informe.await_args.kwargs
    assert kwargs["selected_informe_ids"] == [workspace.informe.id]
    assert kwargs["expected_versions"] == workspace.preview.current_versions
    assert kwargs["confirmed"] is True
    workspace.session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["pending", "malformed", "exception"])
async def test_confirmation_failure_rolls_back(workspace, failure):
    data = confirmation_data(workspace)
    if failure == "malformed":
        data["selected_informe_ids"] = "{}"
    elif failure == "pending":
        routes.confirm_statement_informe.return_value = NS(
            status="pending", reason="stale_amex_finance_preview"
        )
    else:
        routes.confirm_statement_informe.side_effect = RuntimeError("synthetic failure")
    if failure == "exception":
        with pytest.raises(RuntimeError):
            await request(
                workspace,
                "POST",
                f"{routes.BASE}/{workspace.statement.id}/informes/"
                f"{workspace.informe.id}/confirmar",
                data=data,
            )
    else:
        assert (
            await request(
                workspace,
                "POST",
                f"{routes.BASE}/{workspace.statement.id}/informes/"
                f"{workspace.informe.id}/confirmar",
                data=data,
            )
        ).status_code == 303
    workspace.session.rollback.assert_awaited_once()
    workspace.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["xlsx", "csv", "zip"])
async def test_export_uses_snapshots_without_writes(workspace, fmt):
    response = await request(
        workspace, "GET", f"{routes.BASE}/{workspace.statement.id}/exportar.{fmt}"
    )
    assert response.content == b"frozen-policies"
    assert "attachment;" in response.headers["content-disposition"]
    workspace.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_fails_closed(workspace):
    routes.export_statement.side_effect = routes.WorkflowConflict("synthetic")
    response = await request(
        workspace, "GET", f"{routes.BASE}/{workspace.statement.id}/exportar.csv"
    )
    assert response.status_code == 409
    assert (
        await request(
            workspace, "GET", f"{routes.BASE}/{workspace.statement.id}/exportar.html"
        )
    ).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_frozen_incorporation_transaction(workspace, failure):
    import json

    if failure:
        routes.incorporate_statement_cut.side_effect = routes.WorkflowConflict(
            "synthetic"
        )
    response = await request(
        workspace,
        "POST",
        f"{routes.BASE}/{workspace.statement.id}/cortes/{uuid4()}/incorporar",
        data={
            "selected_informe_ids": json.dumps([str(workspace.informe.id)]),
            "confirmed": "true",
        },
    )
    assert response.status_code == 303
    if failure:
        workspace.session.rollback.assert_awaited_once()
        workspace.session.commit.assert_not_awaited()
    else:
        workspace.session.commit.assert_awaited_once()
        assert routes.incorporate_statement_cut.await_args.kwargs["confirmed"]


@pytest.mark.asyncio
async def test_failed_view_safe_and_partial_bank_still_visible(workspace):
    workspace.item.status = "skipped"
    workspace.item.imported_expense_id = None
    workspace.item.reason_code = "invalid_date"
    response = await request(
        workspace, "GET", f"{routes.BASE}/{workspace.statement.id}/pendientes"
    )
    assert "Carga parcial" in response.text and "formato válido" in response.text
    assert "invalid_date" not in response.text
    routes.statement_workflow_view.side_effect = routes.WorkflowConflict(
        "private reason"
    )
    response = await request(
        workspace, "GET", f"{routes.BASE}/{workspace.statement.id}/polizas"
    )
    assert response.status_code == 409 and "private reason" not in response.text


@pytest.mark.asyncio
async def test_incorporated_cut_download_buttons_only_real_links(workspace):
    workspace.view["linked_cut_ids"] = {uuid4()}
    response = await request(
        workspace, "GET", f"{routes.BASE}/{workspace.statement.id}/polizas"
    )
    assert f"/{workspace.statement.id}/exportar.xlsx" in response.text
    workspace.view["linked_cut_ids"] = set()
    response = await request(
        workspace, "GET", f"{routes.BASE}/{workspace.statement.id}/polizas"
    )
    assert f"/{workspace.statement.id}/exportar.xlsx" not in response.text


@pytest.mark.asyncio
async def test_totals_and_progress_remain_separate_factual_counts(workspace):
    response = await request(workspace, "GET", routes.BASE)
    assert "$229.98 MXN" in response.text
    assert "1 operativos" in response.text
    assert "1 por vincular" in response.text
    assert "Falta declarar las fuentes aprobadas" in response.text
    workspace.view["totals_unknown_count"] = 1
    workspace.view["imported_totals"] = {
        "MXN": Decimal("229.98"),
        "USD": Decimal("12.30"),
    }
    workspace.view["financial_progress"] = {
        "linked_cut_count": 2,
        "selected_source_count": 3,
        "selected_generated_count": 2,
    }
    workspace.view["item_states"] = {
        workspace.item.id: {
            "reason": "Asociación bancaria registrada.",
            "blockers": ["Falta informe aprobado"],
        }
    }
    response = await request(
        workspace, "GET", f"{routes.BASE}/{workspace.statement.id}/pendientes"
    )
    assert "$229.98 MXN" in response.text and "$12.30 USD" in response.text
    assert "$242.28" not in response.text
    assert "Total incompleto: 1 cargos sin importe verificable" in response.text
    assert "Fuentes seleccionadas: 2/3 con póliza" in response.text
    assert "Falta informe aprobado." in response.text
    assert "Asociación bancaria registrada." in response.text
    assert "Pendientes operativos: 1" in response.text
    assert "Banco por vincular: 1" in response.text


@pytest.mark.asyncio
async def test_complete_multicard_multiperiod_source_keeps_original_dates(workspace):
    workspace.expenses[0].fecha = datetime(2026, 8, 2)
    workspace.expenses[1].fecha = datetime(2026, 10, 4)
    workspace.review_session.identity_rows[1] = (
        workspace.expenses[1].id,
        uuid4(),
        "Otro titular explícito",
        "9009",
    )
    response = await request(
        workspace,
        "GET",
        f"{routes.BASE}/{workspace.statement.id}/polizas?"
        f"informe_id={workspace.informe.id}",
    )
    assert "2026-08-02" in response.text and "2026-10-04" in response.text
    assert "••••1234" in response.text and "••••9009" in response.text
    assert "Otro titular explícito" in response.text
    assert response.text.count('class="amex-batch-item"') == 2
    assert response.text.count('name="expected_versions"') == 1
    assert f"return_to={routes.BASE.replace('/', '%2F')}" in response.text


@pytest.mark.asyncio
async def test_resume_saved_explicit_source_after_bare_policy_get(workspace):
    workspace.view["selected_informe_ids"] = [workspace.informe.id]
    workspace.view["financial_progress"] = {
        "linked_cut_count": 1,
        "selected_source_count": 1,
        "selected_generated_count": 1,
    }
    response = await request(
        workspace, "GET", f"{routes.BASE}/{workspace.statement.id}/polizas"
    )
    assert response.status_code == 200
    assert "Fuentes seleccionadas: 1/1 con póliza" in response.text
    assert "Falta declarar las fuentes" not in response.text
    assert (
        f'action="{routes.BASE}/{workspace.statement.id}/informes/'
        f'{workspace.informe.id}/confirmar"' in response.text
    )
    assert 'id="amex-cut-confirmation"' in response.text
    assert f"%3Finforme_id%3D{workspace.informe.id}" in response.text
    assert routes.statement_workflow_view.await_args.kwargs["selected_informe_ids"] == [
        workspace.informe.id
    ]
    workspace.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_incorporation_returns_verified_cut_report_context(workspace):
    import json

    response = await request(
        workspace,
        "POST",
        f"{routes.BASE}/{workspace.statement.id}/cortes/{uuid4()}/incorporar",
        data={
            "selected_informe_ids": json.dumps([str(workspace.informe.id)]),
            "confirmed": "true",
        },
    )
    assert response.status_code == 303
    assert f"informe_id={workspace.informe.id}" in response.headers["location"]
    workspace.session.get.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,label",
    [
        ("amex_attention_missing_beneficiary", "Falta beneficiario explícito"),
        ("amex_attention_beneficiary_conflict", "Beneficiarios contradictorios"),
        ("amex_attention_ambiguous_beneficiary", "Beneficiario ambiguo"),
        (
            "amex_attention_multiple_or_missing_informes",
            "Informe asociado ausente o ambiguo",
        ),
        ("amex_attention_normal", ""),
    ],
)
async def test_informe_selector_explains_visible_identity_gaps(
    workspace, reason, label
):
    from lxml import html

    workspace.view["attention_decisions"] = {
        workspace.informe.id: NS(visible=True, reason=reason)
    }
    response = await request(
        workspace, "GET", f"{routes.BASE}/{workspace.statement.id}/polizas"
    )
    tree = html.fromstring(response.text)
    option = tree.xpath(
        f'//select[@name="informe_id"]/option[@value="{workspace.informe.id}"]'
    )[0]
    assert workspace.informe.numero_referencia in option.text_content()
    assert label in option.text_content()
    assert reason not in response.text
    workspace.session.commit.assert_not_awaited()
