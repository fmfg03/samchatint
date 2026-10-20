"""CxC and weekly alerts preserve financial ownership across distribution copies."""

from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from devnous.gastos.routes import user_routes as routes
from devnous.gastos.services import income_billing_alert_service as alerts
from samchat.budgets import distribution


class Result:
    def __init__(self, rows=(), scalar=None):
        self.rows = rows
        self.scalar = scalar

    def all(self):
        return self.rows

    def scalars(self):
        return self

    def mappings(self):
        return self

    def scalar_one(self):
        return self.scalar


async def render_view(monkeypatch, *, source="distribution_revision", invalid=False):
    selected = {"id": "clone", "source": source, "edition_year": 2026}
    session = NS(execute=AsyncMock(return_value=Result()))
    resolver = AsyncMock(return_value=("root", {"root-line": "clone-line"}))
    if invalid:
        resolver.side_effect = ValueError("Revisión sin fuente financiera acreditada")
    monkeypatch.setattr(distribution, "financial_context", resolver)
    monkeypatch.setattr(routes, "ensure_budget_schema", AsyncMock())
    monkeypatch.setattr(
        routes, "resolve_definitive_budget_version", AsyncMock(return_value=selected)
    )
    monkeypatch.setattr(
        routes,
        "resolve_budget_tournament_context",
        AsyncMock(return_value={"tournament_id": "tournament"}),
    )
    lines = AsyncMock(
        return_value=[
            {
                "id": "clone-line",
                "concept_name": "Ingreso copiado",
                "budget_amount": 100,
            }
        ]
    )
    monkeypatch.setattr(routes, "list_budget_lines", lines)
    monkeypatch.setattr(
        routes,
        "attach_cuenta_contable_to_budget_lines",
        AsyncMock(side_effect=lambda session, rows: rows),
    )
    candidates = AsyncMock(return_value=[])
    links = AsyncMock(
        return_value=[
            {
                "id": "existing-link",
                "cfdi_uuid": "EXISTING-FISCAL-UUID",
                "status": "approved",
                "amount": 70,
            }
        ]
    )
    monkeypatch.setattr(routes, "list_psp_cfdi_income_candidates", candidates)
    monkeypatch.setattr(routes, "list_budget_cfdi_income_links", links)
    monkeypatch.setattr(routes, "render_top_navigation", lambda *args: "")
    monkeypatch.setattr(routes, "_contabilidad_subnav", lambda *args: "")
    monkeypatch.setattr(routes, "_gastos_breadcrumb_html", lambda *args: "")
    monkeypatch.setattr(routes, "_cxc_can_assign_income_cfdi", lambda *args: False)
    request = Request(
        {
            "type": "http",
            "path": "/admin/contabilidad/cuentas-por-cobrar",
            "query_string": b"",
            "headers": [],
        }
    )
    args = dict(
        request=request,
        session=session,
        current_empleado=NS(),
        desde=None,
        hasta=None,
        estado="todos",
        cliente=None,
        dias_credito=0,
        torneo_id="tournament",
        edition_year=2026,
        success_msg=None,
        error_msg=None,
    )
    if invalid:
        with pytest.raises(ValueError, match="fuente financiera"):
            await routes.contabilidad_cuentas_por_cobrar_view(**args)
        candidates.assert_not_awaited()
        links.assert_not_awaited()
        return
    response = await routes.contabilidad_cuentas_por_cobrar_view(**args)
    assert "EXISTING-FISCAL-UUID" in response.body.decode()
    assert "Aprobado" in response.body.decode()
    assert "Ingreso copiado" in response.body.decode()
    assert lines.await_args.kwargs["version_id"] == "clone"
    expected = "root" if source == "distribution_revision" else "clone"
    assert candidates.await_count == 2
    assert all(
        call.kwargs["budget_version_id"] == expected
        for call in candidates.await_args_list
    )
    assert links.await_args.kwargs["budget_version_id"] == expected
    if source == "distribution_revision":
        resolver.assert_awaited_once_with(session, selected)
    else:
        resolver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["distribution_revision", "manual"])
async def test_actual_cxc_view_retains_existing_approved_links(monkeypatch, source):
    await render_view(monkeypatch, source=source)


@pytest.mark.asyncio
async def test_cxc_invalid_lineage_does_not_query_wrong_financial_owner(monkeypatch):
    await render_view(monkeypatch, invalid=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["distribution_revision", "manual"])
async def test_weekly_expected_plan_uses_clone_but_invoiced_uses_root(
    monkeypatch, source
):
    selected = {"id": "clone", "source": source, "edition_year": 2026}
    session = NS(
        execute=AsyncMock(
            side_effect=[
                Result(scalar=100),
                Result(scalar=70),
                Result([{"id": "recipient", "telegram_user_id": 123}]),
            ]
        )
    )
    monkeypatch.setattr(
        alerts, "resolve_definitive_budget_version", AsyncMock(return_value=selected)
    )
    resolver = AsyncMock(return_value=("root", {}))
    monkeypatch.setattr(distribution, "financial_context", resolver)
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(alerts, "deliver_telegram_notification", send)
    cutoff = date(2026, 10, 16)
    result = await alerts.send_weekly_unbilled_income_alert(session, as_of=cutoff)
    assert result == {
        "expected": 100.0,
        "invoiced": 70.0,
        "pending": 30.0,
        "recipients": 1,
        "sent": 1,
    }
    assert session.execute.await_args_list[0].args[1]["version_id"] == "clone"
    assert session.execute.await_args_list[1].args[1]["version_id"] == (
        "root" if source == "distribution_revision" else "clone"
    )
    assert (
        send.await_args.kwargs["notification_type"] == "income_unbilled_weekly_2026_42"
    )
    assert send.await_args.kwargs["recipient_empleado_id"] == "recipient"
    assert "Pendientes de facturar: $30.00" in send.await_args.kwargs["text"]
    if source == "manual":
        resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_weekly_alert_invalid_lineage_never_sends_or_invents_balance(monkeypatch):
    session = NS(execute=AsyncMock())
    monkeypatch.setattr(
        alerts,
        "resolve_definitive_budget_version",
        AsyncMock(return_value={"id": "clone", "source": "distribution_revision"}),
    )
    monkeypatch.setattr(
        distribution,
        "financial_context",
        AsyncMock(side_effect=ValueError("Revisión sin fuente financiera acreditada")),
    )
    send = AsyncMock()
    monkeypatch.setattr(alerts, "deliver_telegram_notification", send)
    with pytest.raises(ValueError, match="fuente financiera"):
        await alerts.send_weekly_unbilled_income_alert(
            session, as_of=date(2026, 10, 16)
        )
    session.execute.assert_not_awaited()
    send.assert_not_awaited()
