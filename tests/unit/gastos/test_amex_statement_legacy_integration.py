"""Legacy queue and safe-return integration for the approved statement workflow."""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from devnous.gastos.routes import user_routes
from devnous.gastos.services.amex_attention_policy import (
    AttentionContext,
    AttentionPolicy,
    classify_amex_attention,
)
from tests.unit.gastos.test_amex_card_identity_routes import item, render

ODILON = UUID("f9aebcf3-ef28-423c-8a77-f41c9e4e885a")


@pytest.mark.asyncio
async def test_manual_queue_omits_explicit_odilon_but_keeps_bank_and_other_beneficiary(
    monkeypatch,
):
    excluded = item(last4="1005")
    excluded.numero_referencia = "G-ODILON"
    other = item(last4="1005")
    other.numero_referencia = "G-OTHER"
    unknown = item(last4="")
    unknown.numero_referencia = "G-UNKNOWN"
    policy = AttentionPolicy(True, frozenset({ODILON}))
    context = AttentionContext(policy)
    for expense, beneficiary in [(excluded, ODILON), (other, uuid4()), (unknown, None)]:
        document = SimpleNamespace(beneficiario_empleado_id=beneficiary)
        account = SimpleNamespace(beneficiario_empleado_id=beneficiary)
        context.expense_decisions[expense.id] = classify_amex_attention(
            document, account, policy=policy
        )
    page, _ = await render(
        monkeypatch, candidates=[excluded, other, unknown], attention_context=context
    )
    assert "G-ODILON" not in page
    assert "G-OTHER" in page
    assert "G-UNKNOWN" in page
    assert "Falta beneficiario explícito" in page
    # The independently created bank row remains available for reconciliation.
    assert 'name="imported_expense_id"' in page
    assert "Partidas AMEX sin tarjeta" in page


@pytest.mark.parametrize("stage", ["pendientes", "polizas"])
def test_workflow_return_accepts_only_known_local_statement_steps(stage):
    statement = uuid4()
    expected = f"/admin/contabilidad/amex/estados/{statement}/{stage}"
    assert user_routes._amex_statement_return_to(expected) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "https://example.test/",
        "//example.test/",
        "/admin/contabilidad/amex/estados/not-a-uuid/pendientes",
        f"/admin/contabilidad/amex/estados/{uuid4()}/unknown",
        f"/admin/contabilidad/amex/estados/{uuid4()}/pendientes?next=//example.test",
        f"/admin/contabilidad/amex/estados/{uuid4()}/polizas#fragment",
        f"/admin/contabilidad/amex/estados/{uuid4()}/polizas/..",
        f"/admin/contabilidad/amex/estados/{uuid4()}/pendientes\n",
    ],
)
def test_workflow_return_rejects_external_ambiguous_or_unapproved_paths(value):
    assert user_routes._amex_statement_return_to(value) is None


def test_workflow_return_preserves_one_valid_explicit_informe_selection():
    statement, report = uuid4(), uuid4()
    target = f"/admin/contabilidad/amex/estados/{statement}/polizas?informe_id={report}"
    assert user_routes._amex_statement_return_to(target) == target


@pytest.mark.parametrize(
    "query",
    [
        "informe_id=not-a-uuid",
        "informe_id=",
        "informe_id=0&next=//example.test",
        "informe_id=0&informe_id=1",
    ],
)
def test_workflow_return_rejects_invalid_or_ambiguous_source_selections(query):
    target = f"/admin/contabilidad/amex/estados/{uuid4()}/polizas?{query}"
    assert user_routes._amex_statement_return_to(target) is None


@pytest.mark.asyncio
async def test_bank_view_retains_valid_statement_return_on_actions(monkeypatch):
    target = f"/admin/contabilidad/amex/estados/{uuid4()}/pendientes"
    page, _ = await render(
        monkeypatch,
        candidates=[item(last4="1005")],
        request=SimpleNamespace(query_params={"return_to": target}),
    )
    assert f'href="{target}"' in page
    assert "Volver al estado AMEX" in page
    assert f'name="return_to" value="{target}"' in page


@pytest.mark.parametrize("status", ["bound", "pending"])
@pytest.mark.parametrize("valid", [True, False])
def test_bank_post_preserves_only_allowlisted_return(monkeypatch, status, valid):
    from urllib.parse import parse_qs, urlsplit

    from tests.unit.gastos.test_amex_approved_informe_routes import bank_client

    client, _session, _bridge, _legacy = bank_client(monkeypatch, status=status)
    target = f"/admin/contabilidad/amex/estados/{uuid4()}/pendientes"
    supplied = target if valid else "https://example.test/"
    response = client.post(
        "/bank",
        data={
            "imported_expense_id": str(uuid4()),
            "report_consumption_ids": [str(uuid4())],
            "bank_association_confirmed": "true",
            "year": "2026",
            "month": "9",
            "return_to": supplied,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    query = parse_qs(location.query)
    assert query.get("return_to") == ([target] if valid else None)
    assert location.fragment == "consumos"
