"""Real HTTP/form boundary for explicit account treatment batches."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from lxml import html

from devnous.gastos.services.amex_direct_common import DirectResult
from tests.unit.gastos.test_amex_direct_routes import (
    complete_direct_workspace as _complete_direct_workspace,
)
from tests.unit.gastos.test_amex_direct_routes import (
    direct_workspace as _direct_workspace,
)
from tests.unit.gastos.test_amex_direct_routes import workspace as _workspace

complete_direct_workspace = _complete_direct_workspace
direct_workspace = _direct_workspace
workspace = _workspace


@pytest.fixture
def account_treatment_workspace(complete_direct_workspace, monkeypatch):
    w = complete_direct_workspace
    w.debtor_id = uuid4()
    options = {
        "partner_accounts": [
            {
                "id": str(w.debtor_id),
                "code": "1170-002-099",
                "name": "Socio sintético",
                "label": "1170-002-099 · Socio sintético",
            }
        ],
        "benefit_budget_available": True,
    }
    w.treatment_manifest = {
        "source_version": 4,
        "items": [
            {
                "id": str(w.item.id),
                "row_number": 2,
                "description": '<img src=x onerror="bad()">',
                "amount": "116.00",
            }
        ],
        "account_snapshot": {"debtor": None},
    }
    monkeypatch.setattr(
        w.routes.treatments,
        "list_direct_account_treatment_options",
        AsyncMock(return_value=options),
    )
    monkeypatch.setattr(
        w.routes.treatments,
        "preview_direct_account_treatment",
        AsyncMock(
            return_value=DirectResult(
                "preview",
                progress={
                    "manifest": w.treatment_manifest,
                    "preview_hash": "exact-preview",
                },
            )
        ),
    )
    monkeypatch.setattr(
        w.routes.treatments,
        "confirm_direct_account_treatment",
        AsyncMock(return_value=DirectResult("ready")),
    )
    return w


async def client_for(w):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="http://test"
    )


async def payload(client, w, **changes):
    page = await client.get(f"{w.routes.BASE}/{w.statement.id}/directo")
    assert page.status_code == 200
    token = html.fromstring(page.text).xpath('//input[@name="_csrf_token"]/@value')[0]
    return {
        "_csrf_token": token,
        "item_ids": str(w.item.id),
        "expected_versions": json.dumps({str(w.item.id): 2}),
        "expected_source_version": "4",
        "treatment": "benefit_expense",
        "reason": "Prestación aprobada <sin proyecto>",
        **changes,
    }


@pytest.mark.asyncio
async def test_benefit_form_does_not_require_project_or_category(
    account_treatment_workspace,
):
    w = account_treatment_workspace
    async with await client_for(w) as client:
        page = await client.get(f"{w.routes.BASE}/{w.statement.id}/directo")
    tree = html.fromstring(page.text)
    form = tree.xpath('//form[contains(@action,"/tratamiento/preview")]')[0]
    assert not form.xpath(
        './/*[@name="tournament_id" or @name="project_code" or @name="budget_concept_id"]'
    )
    assert form.xpath('.//select[@name="treatment"]')
    assert "Juan Pablo asigna la partida" in form.getparent().text_content()
    assert tree.xpath('//details[@id="direct-project-classification" and not(@open)]')
    assert form.xpath('.//select[@name="debtor_account_id" and @disabled]')
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_partner_row_is_not_pending_budget_or_labelled_expense(
    account_treatment_workspace,
):
    w = account_treatment_workspace
    w.item.treatment_kind = "partner_receivable"
    w.item.budget_status = "not_applicable"
    async with await client_for(w) as client:
        page = await client.get(f"{w.routes.BASE}/{w.statement.id}/directo")
    tree = html.fromstring(page.text)
    row = tree.xpath("//tr[@data-item-id]")[0]
    assert "No aplica: cargo a la cuenta deudora" in row.text_content()
    assert "Cuenta deudora:" in row.text_content()
    assert "Categoría:" not in row.text_content()
    assert "Pendientes de presupuesto: 0" in page.text
    w.routes.budget.send_direct_items_to_budget.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_is_readonly_escaped_and_binds_exact_form(
    account_treatment_workspace,
):
    w = account_treatment_workspace
    async with await client_for(w) as client:
        data = await payload(client, w)
        response = await client.post(
            f"{w.routes.BASE}/{w.statement.id}/directo/tratamiento/preview", data=data
        )
    assert response.status_code == 200
    tree = html.fromstring(response.text)
    assert not tree.xpath("//img")
    assert 'name="expected_preview_hash" value="exact-preview"' in response.text
    assert tree.xpath('//input[@name="item_ids"]/@value') == [str(w.item.id)]
    assert tree.xpath('//input[@name="reason"]/@value') == [data["reason"]]
    kwargs = w.routes.treatments.preview_direct_account_treatment.await_args.kwargs
    assert kwargs["treatment"] == "benefit_expense"
    assert kwargs["item_ids"] == [w.item.id]
    assert kwargs["expected_versions"] == {str(w.item.id): 2}
    assert "tournament_id" not in kwargs
    w.session.commit.assert_not_awaited()
    w.session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_confirm_forwards_reviewed_identity_and_commits_once(
    account_treatment_workspace,
):
    w = account_treatment_workspace
    async with await client_for(w) as client:
        data = await payload(
            client,
            w,
            treatment="partner_receivable",
            debtor_account_id=str(w.debtor_id),
            expected_preview_hash="bound-proof",
            idempotency_key="exact-request",
            confirmed="true",
        )
        response = await client.post(
            f"{w.routes.BASE}/{w.statement.id}/directo/tratamiento", data=data
        )
    assert response.status_code == 303
    kwargs = w.routes.treatments.confirm_direct_account_treatment.await_args.kwargs
    assert kwargs["debtor_account_id"] == w.debtor_id
    assert kwargs["expected_preview_hash"] == "bound-proof"
    assert kwargs["idempotency_key"] == "exact-request"
    assert kwargs["confirmed"] is True
    w.session.commit.assert_awaited_once()
    w.routes.budget.send_direct_items_to_budget.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["tratamiento", "tratamiento/preview"])
async def test_treatment_requires_csrf_before_owner(
    account_treatment_workspace, suffix
):
    w = account_treatment_workspace
    async with await client_for(w) as client:
        response = await client.post(
            f"{w.routes.BASE}/{w.statement.id}/directo/{suffix}", data={}
        )
    assert response.status_code == 403
    w.routes.treatments.preview_direct_account_treatment.assert_not_awaited()
    w.routes.treatments.confirm_direct_account_treatment.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"project_code": "forged"},
        {"treatment": "unknown"},
        {"treatment": "partner_receivable"},
        {"debtor_account_id": str(uuid4())},
    ],
)
async def test_invalid_treatment_payload_never_reaches_owner(
    account_treatment_workspace, changes
):
    w = account_treatment_workspace
    async with await client_for(w) as client:
        response = await client.post(
            f"{w.routes.BASE}/{w.statement.id}/directo/tratamiento/preview",
            data=await payload(client, w, **changes),
        )
    assert response.status_code == 422
    w.routes.treatments.preview_direct_account_treatment.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_requires_explicit_checkbox_before_owner(
    account_treatment_workspace,
):
    w = account_treatment_workspace
    async with await client_for(w) as client:
        data = await payload(
            client, w, expected_preview_hash="proof", idempotency_key="request"
        )
        response = await client.post(
            f"{w.routes.BASE}/{w.statement.id}/directo/tratamiento", data=data
        )
    assert response.status_code == 422
    w.routes.treatments.confirm_direct_account_treatment.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_confirmation_keeps_values_and_refreshes_preview_versions(
    account_treatment_workspace,
):
    w = account_treatment_workspace
    w.routes.treatments.confirm_direct_account_treatment.return_value = DirectResult(
        "pending", "stale_item_version"
    )
    async with await client_for(w) as client:
        data = await payload(
            client,
            w,
            expected_preview_hash="old-proof",
            idempotency_key="request",
            confirmed="true",
        )
        w.item.source_version = 3
        response = await client.post(
            f"{w.routes.BASE}/{w.statement.id}/directo/tratamiento", data=data
        )
    assert response.status_code == 409
    tree = html.fromstring(response.text)
    assert tree.xpath('//input[@name="reason"]/@value') == [data["reason"]]
    assert json.loads(tree.xpath('//input[@name="expected_versions"]/@value')[0]) == {
        str(w.item.id): 3
    }
    assert tree.xpath('//input[@name="item_ids"]/@value') == [str(w.item.id)]
    assert tree.xpath('//option[@value="benefit_expense" and @selected]')
    assert not tree.xpath("//img")
    w.session.commit.assert_not_awaited()
    w.session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_owner_denial_is_not_turned_into_success(account_treatment_workspace):
    w = account_treatment_workspace
    w.routes.treatments.preview_direct_account_treatment.side_effect = PermissionError(
        "denied"
    )
    async with await client_for(w) as client:
        response = await client.post(
            f"{w.routes.BASE}/{w.statement.id}/directo/tratamiento/preview",
            data=await payload(client, w),
        )
    assert response.status_code == 403
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoke_can_review_withdrawn_account_without_reselecting_it(
    account_treatment_workspace,
):
    w = account_treatment_workspace
    w.item.account_treatment_decision_id = uuid4()
    w.item.treatment_kind = "partner_receivable"
    w.item.debtor_account_id = w.debtor_id
    w.routes.treatments.list_direct_account_treatment_options.return_value = {
        "partner_accounts": []
    }
    async with await client_for(w) as client:
        response = await client.get(
            f"{w.routes.BASE}/{w.statement.id}/directo/tratamiento/revocar?item_id={w.item.id}"
        )
    assert response.status_code == 200
    tree = html.fromstring(response.text)
    assert tree.xpath('//input[@name="decision_action"]/@value') == ["revoke"]
    assert tree.xpath('//input[@name="debtor_account_id"]/@value') == [str(w.debtor_id)]
    assert not tree.xpath('//select[@name="debtor_account_id"]')
    assert not tree.xpath("//img")
    w.session.commit.assert_not_awaited()
    w.routes.treatments.confirm_direct_account_treatment.assert_not_awaited()


@pytest.mark.asyncio
async def test_recognized_treatment_has_no_revoke_form(account_treatment_workspace):
    w = account_treatment_workspace
    w.item.account_treatment_decision_id = uuid4()
    w.item.treatment_kind = "benefit_expense"
    w.item.accounting_status = "recognized"
    async with await client_for(w) as client:
        response = await client.get(
            f"{w.routes.BASE}/{w.statement.id}/directo/tratamiento/revocar?item_id={w.item.id}"
        )
    assert response.status_code == 409
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_revocation_preview_is_explicit_and_remains_a_revocation_on_retry(
    account_treatment_workspace,
):
    w = account_treatment_workspace
    w.routes.treatments.preview_direct_account_treatment.return_value = DirectResult(
        "pending", "stale_item_version"
    )
    async with await client_for(w) as client:
        data = await payload(
            client,
            w,
            treatment="partner_receivable",
            decision_action="revoke",
            debtor_account_id=str(w.debtor_id),
        )
        response = await client.post(
            f"{w.routes.BASE}/{w.statement.id}/directo/tratamiento/preview", data=data
        )
    assert response.status_code == 409
    tree = html.fromstring(response.text)
    assert tree.xpath('//input[@name="decision_action"]/@value') == ["revoke"]
    assert tree.xpath('//input[@name="debtor_account_id"]/@value') == [str(w.debtor_id)]
    assert tree.xpath('//input[@name="reason"]/@value') == [data["reason"]]
    assert not tree.xpath('//select[@name="debtor_account_id"]')
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["preview", "confirm"])
async def test_actual_owner_conflict_shape_preserves_inputs(
    account_treatment_workspace, operation
):
    from devnous.gastos.services.amex_direct_common import DirectConflict

    w = account_treatment_workspace
    owner = (
        w.routes.treatments.preview_direct_account_treatment
        if operation == "preview"
        else w.routes.treatments.confirm_direct_account_treatment
    )
    owner.side_effect = DirectConflict("stale_item_version")
    async with await client_for(w) as client:
        data = await payload(
            client,
            w,
            expected_preview_hash="old-proof",
            idempotency_key="request",
            confirmed="true",
        )
        suffix = "tratamiento/preview" if operation == "preview" else "tratamiento"
        response = await client.post(
            f"{w.routes.BASE}/{w.statement.id}/directo/{suffix}", data=data
        )
    assert response.status_code == 409
    tree = html.fromstring(response.text)
    assert tree.xpath('//input[@name="reason"]/@value') == [data["reason"]]
    assert tree.xpath('//input[@name="item_ids"]/@value') == [str(w.item.id)]
    assert "El cargo cambió" in tree.text_content()
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfigured_new_policy_keeps_legacy_get_and_classification_available(
    account_treatment_workspace,
):
    from devnous.gastos.services.amex_direct_common import DirectConflict

    w = account_treatment_workspace
    w.routes.treatments.list_direct_account_treatment_options.side_effect = (
        DirectConflict("account_treatment_policy_unconfigured")
    )
    async with await client_for(w) as client:
        response = await client.get(f"{w.routes.BASE}/{w.statement.id}/directo")
    assert response.status_code == 200
    tree = html.fromstring(response.text)
    assert "Finanzas debe habilitar el catálogo" in tree.text_content()
    assert tree.xpath('//form[contains(@action,"/clasificar")]')
    assert not tree.xpath('//form[contains(@action,"/tratamiento/preview")]')
    assert not tree.xpath(
        '//form[contains(@action,"/clasificar")]/ancestor::fieldset[@disabled]'
    )
    w.session.commit.assert_not_awaited()
