from unittest.mock import AsyncMock

import pytest

from samchat.ar import service
from samchat.ar.admin_ui import render_ar_read_model_html


@pytest.mark.asyncio
async def test_pending_invoice_remains_visible_until_approved_without_financial_recognition(monkeypatch):
    link = {
        "id": "link-1", "cfdi_report_id": "invoice-1", "cfdi_uuid": "uuid-1",
        "budget_line_id": "line-1", "budget_version_id": "version-1",
        "tournament_id": "tournament-1", "concept_name": "Patrocinio",
        "amount": 1160, "receptor_nombre": "Cliente", "status": "pending_approval",
    }
    monkeypatch.setattr(service, "list_budget_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "list_monthly_plan_for_lines", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "list_ar_collection_matches", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "list_psp_cfdi_income_candidates", AsyncMock(return_value=[{"id": "invoice-1", "total": 1160}]))

    async def links(session, **kwargs):
        assert kwargs["budget_version_id"] == "version-1"
        assert kwargs["tournament_id"] == "tournament-1"
        if kwargs["approved_only"]:
            return [link] if link["status"] == "approved" else []
        return [link]

    monkeypatch.setattr(service, "list_budget_cfdi_income_links", links)
    pending = await service.build_ar_read_model(
        object(), budget_version_id="version-1", tournament_id="tournament-1",
        ensure_schema=False,
    )
    assert pending["pending_links"] == [link]
    assert pending["summary"]["pending_link_count"] == 1
    assert pending["summary"]["invoiced_total"] == 0
    assert pending["summary"]["collected_total"] == 0
    assert pending["issued_linked"] == []
    assert pending["issued_unlinked"] == []
    html = render_ar_read_model_html(
        pending, base_url="/admin/finanzas/cuentas-por-cobrar?edition_year=2025"
    )
    assert "uuid-1" in html
    assert "Pendiente de aprobación" in html
    assert "/admin/presupuestos/torneo/tournament-1" in html
    assert "version_id=version-1" in html
    assert "edition_year=2025#presupuesto-ingresos" in html
    assert 'method="POST"' not in html

    link["status"] = "approved"
    approved = await service.build_ar_read_model(
        object(), budget_version_id="version-1", tournament_id="tournament-1",
        ensure_schema=False,
    )
    assert approved["pending_links"] == []
    assert len(approved["issued_linked"]) == 1
    assert approved["issued_unlinked"] == []
    assert approved["summary"]["invoiced_total"] == 1160
    assert approved["summary"]["collected_total"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status,unlinked", [("rejected", None), ("pending_approval", "2026-10-09"), ("unknown", None)])
async def test_inactive_or_unknown_links_do_not_enter_pending_queue(monkeypatch, status, unlinked):
    monkeypatch.setattr(service, "list_budget_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "list_monthly_plan_for_lines", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "list_ar_collection_matches", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "list_psp_cfdi_income_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "list_budget_cfdi_income_links", AsyncMock(side_effect=[[], [{"id": "link", "status": status, "unlinked_at": unlinked}]]))
    payload = await service.build_ar_read_model(object(), budget_version_id="version", ensure_schema=False)
    assert payload["pending_links"] == []
    assert payload["summary"]["invoiced_total"] == 0


def test_pending_links_escape_invoice_and_client_and_do_not_invent_tournament():
    html = render_ar_read_model_html({
        "pending_links": [{"cfdi_uuid": "<script>", "receptor_nombre": "<cliente>", "amount": 116}],
    })
    assert "&lt;script&gt;" in html
    assert "&lt;cliente&gt;" in html
    assert "Falta torneo" in html
    assert "/admin/presupuestos/torneo/" not in html


@pytest.mark.asyncio
@pytest.mark.parametrize("link_tournament", ["other-tournament", None])
async def test_strict_scope_rejects_pending_links_outside_selected_tournament(
    monkeypatch, link_tournament
):
    monkeypatch.setattr(service, "list_budget_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        service, "list_monthly_plan_for_lines", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        service, "list_psp_cfdi_income_candidates", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        service, "list_budget_cfdi_income_links",
        AsyncMock(side_effect=[[], [{
            "id": "pending-link", "cfdi_report_id": "pending-invoice",
            "status": "pending_approval", "tournament_id": link_tournament,
        }]]),
    )
    collections = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "list_ar_collection_matches", collections)
    with pytest.raises(ValueError, match="income links escaped tournament scope"):
        await service.build_ar_read_model(
            object(), budget_version_id="version-1", tournament_id="tournament-1",
            strict_tournament_scope=True, ensure_schema=False,
        )
    collections.assert_not_awaited()
