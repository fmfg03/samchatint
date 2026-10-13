"""Consolidated Finance consent and late bank association HTTP/UI contracts."""

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lxml import html
from starlette.requests import Request

from devnous.gastos.routes import admin_amex_accounting_routes as accounting
from devnous.gastos.routes import dependencies, user_routes


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


def actor(role="finanzas", active=True, name="Finanzas actual"):
    return SimpleNamespace(
        id=uuid4(), rol=role, activo=active, nombre=name, permisos=[]
    )


def fixture(monkeypatch, *, approval=True, operational=True, fresh=True, amount=116):
    person = actor(name="Aprobadora operativa <real>")
    reviewer = actor(name="Finanzas original")
    document = SimpleNamespace(
        id=uuid4(),
        tipo="INFORME",
        numero_referencia="<I-001>",
        cuenta_gastos_id=uuid4(),
        beneficiario_empleado=SimpleNamespace(nombre="Beneficiario real"),
    )
    expenses = [
        SimpleNamespace(
            id=uuid4(),
            numero_referencia=f"G-{index}",
            concepto="Alimentos <evidencia>",
            fecha=datetime(2026, 10, 5),
            gasto_cantidad=amount,
            currency="MXN",
            metodo_pago="TARJETA CREDITO AMEX",
            ultimos_4_digitos="1005",
            cfdi_report_id=None,
            cfdi_report=None,
            cfdi_uuid_manual=None,
            cuenta_contable=SimpleNamespace(codigo="601-001"),
        )
        for index in range(2)
    ]
    review = SimpleNamespace(
        expense_id=expenses[0].id,
        actor_id=reviewer.id,
        reviewed_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
        version=3,
        treatment="partner_receivable",
        debtor_account_id=uuid4(),
        reason='Motivo "real"',
        source_evidence_id=uuid4(),
        source_key="prior-key",
    )
    approval_event = (
        SimpleNamespace(
            id=uuid4(),
            aprobador_id=person.id,
            fecha=datetime(2026, 10, 7, tzinfo=timezone.utc),
        )
        if approval
        else None
    )
    source = SimpleNamespace(
        status="ready",
        reason=None,
        informe=document,
        expenses=expenses,
        reviews={expenses[0].id: review},
        fresh_review_ids={expenses[0].id} if fresh else set(),
        current_versions={str(expenses[0].id): 3, str(expenses[1].id): 0},
        approval_event=approval_event,
        approval_version=1 if operational else None,
        operational_evidence=SimpleNamespace(id=uuid4()) if operational else None,
        source_key="a" * 64,
        exceptions=(
            {} if operational else {"source": "missing_amex_operational_attestation"}
        ),
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=document),
        execute=AsyncMock(
            side_effect=[
                Rows(
                    [
                        SimpleNamespace(
                            id=review.debtor_account_id,
                            codigo="1170-002-001",
                            nombre="Socio explícito",
                        )
                    ]
                ),
                Rows([]),
                Rows([person, reviewer]),
            ]
        ),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        flush=AsyncMock(),
        add=AsyncMock(),
    )
    monkeypatch.setattr(
        accounting, "preview_amex_approved_informe", AsyncMock(return_value=source)
    )
    monkeypatch.setattr(
        accounting, "load_amex_card_display_identities", AsyncMock(return_value={})
    )
    return session, source, review


@pytest.mark.asyncio
async def test_preview_is_pure_operational_source_with_one_finance_confirmation(
    monkeypatch,
):
    session, source, review = fixture(monkeypatch)
    before = dict(review.__dict__)
    response = await accounting.amex_accounting_report_review(
        source.informe.id,
        Request({"type": "http", "query_string": b""}),
        session,
        actor(),
    )
    body = response.body.decode()
    tree = html.fromstring(body)
    assert "Aprobación operativa: Aprobadora operativa &lt;real&gt;" in body
    assert "Finanzas original" in body and "versión 3" in body
    assert "Decisión vigente conservada" in body
    assert "&lt;evidencia&gt;" in body and "&lt;I-001&gt;" in body
    assert "••••1005" in body and "601-001" in body
    assert not tree.xpath('//input[@name="reviewed"]')
    assert not tree.xpath('//form[contains(@action,"/partidas/")]')
    assert len(tree.xpath('//input[@name="confirmed"]')) == 1
    assert not tree.xpath('//select[@id="amex-batch-treatment"]/option[@selected]')
    assert (
        tree.xpath('//select[@id="amex-batch-treatment"]/option')[0].get("value") == ""
    )
    assert not tree.xpath('//select[@id="amex-batch-debtor"]/option[@selected]')
    versions = json.loads(
        tree.xpath('//input[@name="expected_versions"]')[0].get("value")
    )
    assert versions == source.current_versions
    assert len(tree.xpath('//input[@class="amex-batch-item"]')) == 2
    assert tree.xpath('//input[@name="decisions"]')[0].get("value") == "[]"
    assert "Generar una póliza del corte" in body
    assert review.__dict__ == before
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_approval_has_no_fabricated_attestation_and_requires_reason(
    monkeypatch,
):
    session, source, _ = fixture(monkeypatch, operational=False)
    response = await accounting.amex_accounting_report_review(
        source.informe.id,
        Request({"type": "http", "query_string": b""}),
        session,
        actor(),
    )
    tree = html.fromstring(response.body)
    assert "Falta atestación operativa versionada" in response.body.decode()
    assert "Aprobación operativa:" in response.body.decode()
    assert tree.xpath('//input[@name="reason"]')[0].get("required") is not None
    assert (
        tree.xpath('//input[@name="expected_approval_version"]')[0].get("value") == ""
    )


@pytest.mark.asyncio
async def test_missing_amount_and_historical_block_remain_visible(monkeypatch):
    session, source, _ = fixture(monkeypatch, amount=None)
    source.exceptions["activation"] = "historical_amex_representation_requires_review"
    response = await accounting.amex_accounting_report_review(
        source.informe.id,
        Request({"type": "http", "query_string": b""}),
        session,
        actor(),
    )
    body = response.body.decode()
    assert "Sin importe" in body and "Importe total no disponible" in body
    assert "$0.00" not in body
    assert "Requiere revisión histórica AMEX separada" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("role, active", [("empleado", True), ("finanzas", False)])
async def test_preview_denies_role_or_inactive_before_source_read(
    monkeypatch, role, active
):
    from fastapi import HTTPException

    service = AsyncMock()
    monkeypatch.setattr(accounting, "preview_amex_approved_informe", service)
    with pytest.raises(HTTPException) as error:
        await accounting.amex_accounting_report_review(
            uuid4(), None, None, actor(role, active)
        )
    assert error.value.status_code == 403
    service.assert_not_awaited()


def accounting_client(monkeypatch, *, status="created", financial_actor=None):
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    service = AsyncMock(
        return_value=SimpleNamespace(status=status, reason="stale_amex_review_source")
    )
    legacy = AsyncMock(return_value=SimpleNamespace(status="created"))
    monkeypatch.setattr(accounting, "confirm_amex_approved_informe_cut", service)
    monkeypatch.setattr(accounting, "create_amex_accounting_cut", legacy)
    app = FastAPI()
    app.include_router(accounting.router)
    app.dependency_overrides[dependencies.get_current_empleado] = (
        lambda: financial_actor or actor()
    )
    app.dependency_overrides[dependencies.get_db_session] = lambda: session
    return TestClient(app), session, service, legacy


def payload():
    first, second = uuid4(), uuid4()
    return {
        "confirmation_mode": "approved_informe",
        "expected_source_key": "a" * 64,
        "expected_versions": json.dumps({str(first): 3, str(second): 0}),
        "expected_approval_id": str(uuid4()),
        "expected_approval_version": "1",
        "accounting_date": "2026-10-05",
        "confirmed": "true",
        "decisions": json.dumps(
            [
                {
                    "expense_ids": [str(second)],
                    "treatment": "expense",
                    "debtor_account_id": None,
                    "reason": "",
                }
            ]
        ),
    }


@pytest.mark.parametrize("status", ["created", "exists", "pending"])
def test_consolidated_http_passes_complete_proof_and_decisions_atomically(
    monkeypatch, status
):
    client, session, service, legacy = accounting_client(monkeypatch, status=status)
    fields = payload()
    response = client.post(
        f"{accounting.BASE}/informes/{uuid4()}/cortes",
        data=fields,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert service.await_args.kwargs["expected_versions"] == json.loads(
        fields["expected_versions"]
    )
    assert service.await_args.kwargs["decisions"] == json.loads(fields["decisions"])
    assert service.await_args.kwargs["expected_approval_id"] == UUID(
        fields["expected_approval_id"]
    )
    assert (
        service.await_args.kwargs["expected_source_key"]
        == fields["expected_source_key"]
    )
    legacy.assert_not_awaited()
    (session.rollback if status == "pending" else session.commit).assert_awaited_once()


@pytest.mark.parametrize(
    "change",
    [
        "mode",
        "key",
        "decisions",
        "overlap",
        "foreign",
        "approval",
        "version",
        "unconfirmed",
    ],
)
def test_partial_or_tampered_consolidated_payload_never_falls_back(monkeypatch, change):
    client, session, service, legacy = accounting_client(monkeypatch)
    fields = payload()
    if change in {"mode", "key", "decisions"}:
        fields.pop(
            {
                "mode": "confirmation_mode",
                "key": "expected_source_key",
                "decisions": "decisions",
            }[change]
        )
    elif change == "overlap":
        groups = json.loads(fields["decisions"])
        fields["decisions"] = json.dumps(groups + groups)
    elif change == "foreign":
        groups = json.loads(fields["decisions"])
        groups[0]["expense_ids"] = [str(uuid4())]
        fields["decisions"] = json.dumps(groups)
    elif change == "approval":
        fields["expected_approval_id"] = "bad"
    elif change == "version":
        fields["expected_versions"] = json.dumps({str(uuid4()): True})
    else:
        fields["confirmed"] = "false"
    response = client.post(
        f"{accounting.BASE}/informes/{uuid4()}/cortes",
        data=fields,
        follow_redirects=False,
    )
    assert response.status_code == 303 and "error_msg" in response.headers["location"]
    service.assert_not_awaited()
    legacy.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_old_cut_shape_retains_legacy_guarded_service(monkeypatch):
    client, _session, service, legacy = accounting_client(monkeypatch)
    response = client.post(
        f"{accounting.BASE}/informes/{uuid4()}/cortes",
        data={
            "expected_versions": json.dumps({str(uuid4()): 1}),
            "accounting_date": "2026-10-05",
            "confirmed": "true",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    legacy.assert_awaited_once()
    service.assert_not_awaited()


def bank_client(monkeypatch, *, status="bound"):
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    bridge = AsyncMock(
        return_value=SimpleNamespace(status=status, reason="amex_bridge_source_changed")
    )
    legacy = AsyncMock(return_value=SimpleNamespace(status="bound"))
    monkeypatch.setattr(user_routes, "bind_amex_report_bank_consumptions", bridge)
    monkeypatch.setattr(user_routes, "bind_amex_consumption", legacy)
    app = FastAPI()
    app.add_api_route(
        "/bank", user_routes.amex_conciliacion_bind_consumption, methods=["POST"]
    )
    app.dependency_overrides[dependencies.get_current_empleado] = lambda: actor()
    app.dependency_overrides[user_routes.get_db_session] = lambda: session
    return TestClient(app), session, bridge, legacy


@pytest.mark.parametrize("status", ["bound", "exists", "pending"])
def test_bank_bridge_http_explicit_full_set_reuses_only_existing_receipts(
    monkeypatch, status
):
    client, session, bridge, legacy = bank_client(monkeypatch, status=status)
    charge, targets = uuid4(), [uuid4(), uuid4()]
    response = client.post(
        "/bank",
        data={
            "imported_expense_id": str(charge),
            "report_consumption_ids": [str(item) for item in targets],
            "bank_association_confirmed": "true",
            "year": "2026",
            "month": "9",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert bridge.await_args.kwargs["report_consumption_ids"] == targets
    assert bridge.await_args.kwargs["imported_expense_id"] == charge
    legacy.assert_not_awaited()
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["year"] == ["2026"] and params["month"] == ["9"]
    assert urlsplit(response.headers["location"]).fragment == "consumos"
    (session.rollback if status == "pending" else session.commit).assert_awaited_once()


@pytest.mark.parametrize(
    "invalid", ["duplicate", "mixed", "unconfirmed", "empty", "statement"]
)
def test_bank_bridge_rejects_incomplete_or_mixed_selection(monkeypatch, invalid):
    client, session, bridge, legacy = bank_client(monkeypatch)
    target = str(uuid4())
    fields = {
        "imported_expense_id": str(uuid4()),
        "report_consumption_ids": [target],
        "bank_association_confirmed": "true",
    }
    if invalid == "duplicate":
        fields["report_consumption_ids"] = [target, target]
    elif invalid == "mixed":
        fields["report_expense_ids"] = [str(uuid4())]
    elif invalid == "unconfirmed":
        fields["bank_association_confirmed"] = "false"
    elif invalid == "empty":
        fields["report_consumption_ids"] = []
    else:
        fields["statement_only"] = "true"
    response = client.post("/bank", data=fields, follow_redirects=False)
    assert response.status_code == 303 and "error_msg" in response.headers["location"]
    bridge.assert_not_awaited()
    legacy.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_statement_only_requires_explicit_exception_reason(monkeypatch):
    client, _session, bridge, legacy = bank_client(monkeypatch)
    response = client.post(
        "/bank",
        data={"imported_expense_id": str(uuid4()), "statement_only": "true"},
        follow_redirects=False,
    )
    assert "error_msg" in response.headers["location"]
    legacy.assert_not_awaited()
    bridge.assert_not_awaited()


async def bank_page(monkeypatch, *, bound=False):
    charge = SimpleNamespace(
        id=uuid4(),
        fecha=datetime(2026, 10, 5),
        gasto_cantidad=116,
        currency="MXN",
        numero_referencia="AMEX-001",
        concepto="Alimentos",
        ultimos_4_digitos="1005",
        empleado=None,
        empleado_id=uuid4(),
        cfdi_report_id=None,
        cfdi_report=None,
        cfdi_uuid_manual=None,
    )
    card = SimpleNamespace(
        id=uuid4(),
        active=True,
        card_label="Tarjeta",
        cardholder_key="FGV",
        last4="1005",
        liability_cuenta_contable=SimpleNamespace(codigo="201-001"),
    )
    source = SimpleNamespace(
        id=uuid4(), numero_referencia="G-SOURCE", gasto_cantidad=116
    )
    receipt = SimpleNamespace(
        id=uuid4(),
        economic_date=date(2026, 10, 5),
        amount=116,
        currency="MXN",
        card_account_id=card.id,
        accounting_poliza_id=uuid4(),
    )
    cut = SimpleNamespace(informe_id=uuid4(), id=uuid4())
    header = SimpleNamespace(id=uuid4(), binding_mode="report_bridge")
    context = {
        "headers": {charge.id: header} if bound else {},
        "associated": {header.id: [receipt.id]} if bound else {},
        "available": [] if bound else [(receipt, source, cut)],
        "receipt_links": {header.id: [(source, cut)]} if bound else {},
    }
    session = SimpleNamespace(execute=AsyncMock(side_effect=[Rows([charge]), Rows([])]))
    monkeypatch.setattr(
        user_routes, "_load_amex_bank_report_context", AsyncMock(return_value=context)
    )
    monkeypatch.setattr(
        user_routes, "list_amex_card_accounts", AsyncMock(return_value=[card])
    )
    monkeypatch.setattr(
        user_routes, "suggest_amex_cfdi_matches", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        user_routes, "suggest_pase_monthly_cfdi_matches", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(user_routes, "render_top_navigation", lambda *_a, **_k: "")
    page = await user_routes.amex_conciliacion_view(
        None,
        session,
        actor(),
        year=2026,
        month=10,
        msg=None,
        error_msg=None,
    )
    return page, receipt, source, cut


@pytest.mark.asyncio
async def test_bank_get_displays_recognized_report_cut_and_explicit_selection(
    monkeypatch,
):
    page, receipt, _source, cut = await bank_page(monkeypatch)
    tree = html.fromstring(page)
    assert "Banco pendiente de asociar" in page
    assert f"/admin/contabilidad/amex/informes/{cut.informe_id}" in page
    options = tree.xpath('//select[@name="report_consumption_ids"]/option')
    assert [option.get("value") for option in options] == [str(receipt.id)]
    assert not tree.xpath('//select[@name="report_consumption_ids"]/option[@selected]')
    assert "Asociar banco sin otra póliza" in page


@pytest.mark.asyncio
async def test_already_associated_bank_charge_does_not_ask_for_second_link(monkeypatch):
    page, _receipt, _source, cut = await bank_page(monkeypatch, bound=True)
    tree = html.fromstring(page)
    assert "Banco asociado a fuente contable ya reconocida" in page
    assert f"/admin/contabilidad/amex/cortes/{cut.id}.xlsx" in page
    assert not tree.xpath('//form[contains(@action,"vincular-consumo")]')


@pytest.mark.asyncio
async def test_bank_get_rejects_owner_before_corporate_reads():
    from fastapi import HTTPException

    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await user_routes.amex_conciliacion_view(None, session, actor("empleado"))
    assert error.value.status_code == 403
    session.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "reason, phrase",
    [
        ("amex_bridge_source_changed", "La fuente cambió"),
        ("amex_bridge_partition_mismatch", "conjunto diferente"),
        ("amex_card_currency_date_mismatch", "moneda y día"),
        ("amex_representation_amount_mismatch", "cargo completo"),
        ("invalid_amex_liability", "cuenta pasivo"),
        ("unauthorized_amex_accounting_actor", "perfil activo de Finanzas"),
    ],
)
def test_bank_pending_business_reasons_are_explained_in_spanish(
    monkeypatch, reason, phrase
):
    client, _session, bridge, _legacy = bank_client(monkeypatch, status="pending")
    bridge.return_value.reason = reason
    response = client.post(
        "/bank",
        data={
            "imported_expense_id": str(uuid4()),
            "report_consumption_ids": [str(uuid4())],
            "bank_association_confirmed": "true",
        },
        follow_redirects=False,
    )
    message = parse_qs(urlsplit(response.headers["location"]).query)["error_msg"][0]
    assert phrase in message
    assert reason not in message
