"""Direct AMEX real HTTP boundary and original-source UI contracts."""

import httpx
import pytest
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import admin_amex_statement_routes as statements
from tests.unit.gastos.test_amex_statement_routes import workspace as _workspace

workspace = _workspace


@pytest.fixture
def direct_workspace(workspace, monkeypatch):
    workspace.app.add_middleware(SessionMiddleware, secret_key="synthetic-direct-csrf")
    from unittest.mock import AsyncMock

    from devnous.gastos.routes import admin_amex_direct_routes as routes

    options = {
        "cards": [{"id": workspace.actor.id, "label": "Tarjeta real", "last4": "5007"}],
        "people": [{"id": workspace.actor.id, "name": "Titular explícito"}],
        "contexts": [
            {
                "company_code": "TEST",
                "receiver_rfc": "AAA010101AAA",
                "label": "Entidad autorizada",
            }
        ],
    }
    monkeypatch.setattr(
        routes.context, "list_direct_intake_options", AsyncMock(return_value=options)
    )
    return workspace


@pytest.mark.asyncio
async def test_direct_upload_explicit_context_and_consent(direct_workspace):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=direct_workspace.app), base_url="http://test"
    ) as client:
        response = await client.get(statements.BASE + "/nuevo?modo=directo")
    assert response.status_code == 200
    assert 'name="profile"' in response.text
    assert 'name="receiver_rfc"' in response.text
    assert 'name="sat_consented"' in response.text
    assert 'name="_csrf_token"' in response.text
    assert 'name="informe_id"' not in response.text


@pytest.mark.asyncio
async def test_direct_intake_without_csrf_never_imports(direct_workspace):
    w = direct_workspace
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="http://test"
    ) as client:
        response = await client.post(
            statements.BASE + "/nuevo",
            data={
                "workflow_mode": "direct",
                "card_account_id": str(w.actor.id),
                "employee_id": str(w.actor.id),
                "period_start": "2026-09-01",
                "period_end": "2026-09-30",
                "profile": "debit_negative",
                "company_code": "TEST",
                "receiver_rfc": "AAA010101AAA",
                "currency": "MXN",
                "confirmed": "true",
                "idempotency_key": "test",
            },
            files={
                "file": ("synthetic.csv", b"date,amount\n2026-09-01,-116", "text/csv")
            },
        )
    assert response.status_code == 403
    statements.import_amex_statement.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.fixture
def complete_direct_workspace(direct_workspace, monkeypatch):
    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from devnous.gastos.routes import admin_amex_direct_routes as routes
    from devnous.gastos.services.amex_direct_common import DirectResult

    w = direct_workspace
    w.routes = routes
    w.statement.workflow_mode = "direct"
    w.item = NS(
        id=uuid4(),
        row_number=2,
        movement_kind="debit",
        raw_amount="-116.00",
        raw_signed_amount=Decimal("-116"),
        profile="debit_negative",
        fecha=date(2026, 9, 3),
        descriptor="<img src=x onerror=bad()>",
        amount=Decimal("116"),
        currency="MXN",
        source_version=2,
        source_hash="item-proof",
        match_status="matched",
        match_reason=None,
        cfdi_id=uuid4(),
        cfdi_uuid=str(uuid4()),
        status_receipt_id=uuid4(),
        sat_status="vigente",
        status_observed_at="2026-10-15",
        tournament_id=uuid4(),
        project_code=None,
        edition_year=2026,
        budget_concept_id=uuid4(),
        budget_line_id=uuid4(),
        budget_version_id=uuid4(),
        budget_status="assigned",
        accounting_status="pending",
        expense_id=uuid4(),
        account_code="6000-001",
        liability_code="1170-002-003",
        recognized_poliza_ids=[],
    )
    w.view = DirectResult(
        "ready",
        statement=w.statement,
        context=NS(
            company_code="TEST",
            receiver_rfc="AAA010101AAA",
            currency="MXN",
            profile="debit_negative",
        ),
        card=NS(card_label="Tarjeta real", last4="5007"),
        items=[w.item],
        source_version=4,
        progress={"observed": 1, "total": 51, "request_key": "server-request"},
    )
    w.preview = DirectResult(
        "ready",
        statement=w.statement,
        items=[w.item],
        source_key="full-source-proof",
        source_version=4,
    )
    w.success = DirectResult("created", statement=w.statement, source_version=4)
    catalogs = {
        "tournaments": [{"id": w.item.tournament_id, "name": "Torneo autorizado"}],
        "projects": [{"code": "EXACT", "name": "Proyecto autorizado"}],
        "concepts": [{"id": w.item.budget_concept_id, "name": "Categoría real"}],
        "contexts": [{"company_code": "TEST", "receiver_rfc": "AAA010101AAA"}],
        "edition_years": [2026],
    }
    values = {
        "context": {
            "list_direct_statement": w.view,
            "list_direct_statements": [
                {
                    "statement": w.statement,
                    "card": w.view.card,
                    "counts": {
                        "debit": 1,
                        "credit": 0,
                        "unknown": 0,
                        "skipped": 0,
                        "matched": 1,
                        "classified": 1,
                        "sent": 0,
                        "assigned": 1,
                        "recognized": 0,
                    },
                    "cut": None,
                }
            ],
            "list_direct_classification_options": catalogs,
            "set_direct_context": w.success,
        },
        "intake": {
            "import_direct_statement": DirectResult(
                "imported", statement=w.statement, source_version=4
            ),
            "download_direct_source": (b"original-exact", "original.csv", "text/csv"),
        },
        "matching": {
            name: w.success
            for name in [
                "confirm_merchant_alias",
                "reconcile_direct_statement",
                "link_direct_cfdi_manual",
                "exclude_direct_credit",
            ]
        },
        "budget": {
            **{
                name: w.success
                for name in [
                    "classify_direct_items",
                    "send_direct_items_to_budget",
                    "assign_direct_budget_line",
                    "reject_direct_budget_item",
                ]
            },
            "list_direct_budget_queue": [w.item],
            "list_direct_budget_lines": [
                {
                    "id": w.item.budget_line_id,
                    "label": "Partida real · versión2026 aprobada",
                }
            ],
        },
        "accounting": {
            "preview_direct_accounting": w.preview,
            "confirm_direct_accounting": w.success,
            "export_direct_cut": (b"frozen-exact", "poliza.csv", "text/csv"),
        },
        "status": {
            "consult_direct_statement_status": DirectResult("matched", source_version=4)
        },
    }
    w.calls = []
    for module_name, methods in values.items():
        module = getattr(routes, module_name)
        for name, result in methods.items():
            mock = AsyncMock(return_value=result)
            monkeypatch.setattr(module, name, mock)
            w.calls.append(mock)
    monkeypatch.setattr(routes.dependencies, "_db_session_maker", object())
    return w


async def _client(w):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="http://test"
    )


async def _token(client, w, *, queue=False):
    from lxml import html

    path = w.routes.QUEUE if queue else f"{statements.BASE}/{w.statement.id}/directo"
    response = await client.get(path)
    assert response.status_code == 200
    return html.fromstring(response.text).xpath('//input[@name="_csrf_token"]/@value')[
        0
    ]


@pytest.mark.asyncio
async def test_direct_get_complete_rows_signed_economics_and_no_effects(
    complete_direct_workspace,
):
    from types import SimpleNamespace as NS
    from uuid import uuid4

    from lxml import html

    w = complete_direct_workspace
    base = vars(w.item)
    w.view.items += [
        NS(
            **{
                **base,
                "id": uuid4(),
                "row_number": row,
                "movement_kind": kind,
                "raw_amount": raw,
                "raw_signed_amount": raw,
                "amount": None,
                "cfdi_id": None,
                "cfdi_uuid": None,
                "match_status": "pending",
                "match_reason": "unknown_movement",
                "sat_status": None,
            }
        )
        for row, kind, raw in [
            (3, "credit", "116"),
            (4, "unknown", None),
            (5, "skipped", "invalid"),
        ]
    ]
    async with await _client(w) as client:
        response = await client.get(f"{statements.BASE}/{w.statement.id}/directo")
    assert response.status_code == 200
    tree = html.fromstring(response.text)
    assert len(tree.xpath("//tr[@data-item-id]")) == 4
    assert "-116.00" in response.text and "Crédito" in response.text
    assert "Signo desconocido" in response.text and "Fila omitida" in response.text
    assert not tree.xpath("//img")
    assert "6000-001" in response.text and "1170-002-003" in response.text
    assert "vigente" in response.text and "2026-10-15" in response.text
    assert 'name="informe_id"' not in response.text
    assert "Programar pago de AMEX" in response.text
    assert "/original" in response.text
    assert not tree.xpath("//select/option[@selected]")
    assert len(tree.xpath('//*[@class="container"]')) == 1
    assert len(tree.xpath('//nav[@aria-label="Ruta de navegación"]')) == 1
    w.session.commit.assert_not_awaited()
    w.routes.status.consult_direct_statement_status.assert_not_awaited()
    w.routes.matching.reconcile_direct_statement.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,active",
    [("usuario", True), ("control_presupuestal", True), ("finanzas", False)],
)
async def test_finance_surface_denied_before_source_queries(
    complete_direct_workspace, role, active
):
    w = complete_direct_workspace
    w.actor.rol, w.actor.activo = role, active
    paths = [
        f"{statements.BASE}/{w.statement.id}/directo/original",
        f"{statements.BASE}/{w.statement.id}/directo/exportar.csv",
        statements.BASE + "/nuevo?modo=directo",
    ]
    if not active:
        paths.append(f"{statements.BASE}/{w.statement.id}/directo")
    async with await _client(w) as client:
        for path in paths:
            assert (await client.get(path)).status_code == 403
    for call in w.calls:
        call.assert_not_awaited()
    w.session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "contexto",
        "conciliar",
        "alias",
        "vigencia",
        "clasificar",
        "enviar-presupuesto",
        "vincular",
        "excluir-credito",
        "confirmar",
    ],
)
async def test_every_direct_action_rejects_missing_csrf(
    complete_direct_workspace, action
):
    w = complete_direct_workspace
    async with await _client(w) as client:
        response = await client.post(
            f"{statements.BASE}/{w.statement.id}/directo/{action}",
            data={"expected_source_version": "4"},
        )
    assert response.status_code == 403
    for call in w.calls:
        call.assert_not_awaited()
    w.session.commit.assert_not_awaited()


def _payload(w, action):
    import json

    data = {
        "expected_source_version": "4",
        "confirmed": "true",
        "reason": "Evidencia explícita",
    }
    if action == "contexto":
        data.update(
            company_code="TEST",
            receiver_rfc="AAA010101AAA",
            currency="MXN",
            profile="debit_negative",
            request_key="context-request",
        )
    elif action == "alias":
        data.update(descriptor="COMERCIO EXACTO", issuer_rfc="AAA010101AAA")
    elif action == "vigencia":
        data.update(request_key="server-request")
    elif action in {"clasificar", "enviar-presupuesto"}:
        data.update(
            item_ids=str(w.item.id),
            expected_versions=json.dumps({str(w.item.id): 2}),
            tournament_id=str(w.item.tournament_id),
            project_code="",
            budget_concept_id=str(w.item.budget_concept_id),
            edition_year="2026",
        )
    elif action == "vincular":
        data.update(
            item_id=str(w.item.id),
            cfdi_id=str(w.item.cfdi_id),
            applied_amount="116",
            tip_amount="0",
            shared_confirmed="true",
        )
    elif action == "excluir-credito":
        data.update(item_id=str(w.item.id))
    elif action == "confirmar":
        data.update(
            expected_source_key="full-source-proof",
            accounting_date="2026-09-10",
            idempotency_key="confirm-once",
        )
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,module,name",
    [
        ("contexto", "context", "set_direct_context"),
        ("conciliar", "matching", "reconcile_direct_statement"),
        ("alias", "matching", "confirm_merchant_alias"),
        ("clasificar", "budget", "classify_direct_items"),
        ("enviar-presupuesto", "budget", "send_direct_items_to_budget"),
        ("vincular", "matching", "link_direct_cfdi_manual"),
        ("excluir-credito", "matching", "exclude_direct_credit"),
        ("confirmar", "accounting", "confirm_direct_accounting"),
    ],
)
@pytest.mark.parametrize("outcome", ["success", "pending", "denied", "exception"])
async def test_action_canonical_service_and_transaction(
    complete_direct_workspace, action, module, name, outcome
):
    from devnous.gastos.services.amex_direct_common import DirectResult

    w = complete_direct_workspace
    service = getattr(getattr(w.routes, module), name)
    if outcome == "pending":
        service.return_value = DirectResult("pending", "source_changed")
    elif outcome == "denied":
        service.side_effect = PermissionError("private scope")
    elif outcome == "exception":
        service.side_effect = RuntimeError("synthetic rollback")
    async with await _client(w) as client:
        token = await _token(client, w)
        data = {**_payload(w, action), "_csrf_token": token}
        if outcome == "exception":
            with pytest.raises(RuntimeError):
                await client.post(
                    f"{statements.BASE}/{w.statement.id}/directo/{action}", data=data
                )
        else:
            response = await client.post(
                f"{statements.BASE}/{w.statement.id}/directo/{action}", data=data
            )
            assert response.status_code == (403 if outcome == "denied" else 303)
            assert "private scope" not in response.text
    service.assert_awaited_once()
    kwargs = service.await_args.kwargs
    assert kwargs["actor_id"] == w.actor.id
    assert kwargs["import_id"] == w.statement.id
    if outcome == "success":
        w.session.commit.assert_awaited_once()
    else:
        w.session.rollback.assert_awaited_once()
        w.session.commit.assert_not_awaited()
    if action in {"clasificar", "enviar-presupuesto"}:
        assert kwargs["item_ids"] == [w.item.id]
        assert kwargs["expected_versions"] == {str(w.item.id): 2}
    elif action not in {"alias"}:
        assert kwargs["expected_source_version"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_source_key", ""),
        ("accounting_date", "invalid"),
        ("idempotency_key", ""),
        ("expected_source_version", "0"),
    ],
)
async def test_cut_partial_or_invalid_metadata_fails_closed(
    complete_direct_workspace, field, value
):
    w = complete_direct_workspace
    async with await _client(w) as client:
        token = await _token(client, w)
        data = {**_payload(w, "confirmar"), "_csrf_token": token, field: value}
        response = await client.post(
            f"{statements.BASE}/{w.statement.id}/directo/confirmar", data=data
        )
    assert response.status_code == 422
    w.routes.accounting.confirm_direct_accounting.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sat_continuation_uses_server_key_after_releasing_auth_transaction(
    complete_direct_workspace,
):
    w = complete_direct_workspace

    async def observe(*args, **kwargs):
        w.session.rollback.assert_awaited_once()
        assert kwargs["request_key"] == "server-request"
        assert kwargs["expected_source_version"] == 4 and kwargs["consented"] is True
        assert "cursor" not in kwargs
        return w.success

    w.routes.status.consult_direct_statement_status.side_effect = observe
    async with await _client(w) as client:
        token = await _token(client, w)
        response = await client.post(
            f"{statements.BASE}/{w.statement.id}/directo/vigencia",
            data={**_payload(w, "vigencia"), "_csrf_token": token, "cursor": "999"},
        )
    assert response.status_code == 303
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["asignar", "rechazar"])
@pytest.mark.parametrize(
    "outcome", ["success", "pending", "denied", "extra_context", "no_csrf"]
)
async def test_cp_line_only_scope_and_transaction(
    complete_direct_workspace, action, outcome
):
    from devnous.gastos.services.amex_direct_common import DirectResult

    w = complete_direct_workspace
    w.actor.rol = (
        "usuario"  # No nominal/global Finance grant; owner validates configured scope.
    )
    name = (
        "assign_direct_budget_line"
        if action == "asignar"
        else "reject_direct_budget_item"
    )
    service = getattr(w.routes.budget, name)
    if outcome == "pending":
        service.return_value = DirectResult("pending", "source_changed")
    elif outcome == "denied":
        service.side_effect = PermissionError("same name has no authority")
    async with await _client(w) as client:
        token = await _token(client, w, queue=True)
        data = {"_csrf_token": token, "expected_source_version": "2"}
        if action == "asignar":
            data.update(
                budget_line_id=str(w.item.budget_line_id),
                expected_source_hash="item-proof",
                idempotency_key="assign-once",
            )
        else:
            data["reason"] = "Devolver a Contabilidad"
        if outcome == "extra_context":
            data["tournament_id"] = str(w.item.tournament_id)
        elif outcome == "no_csrf":
            data.pop("_csrf_token")
        response = await client.post(
            f"{w.routes.QUEUE}/{w.item.id}/{action}", data=data
        )
    assert response.status_code == (
        422
        if outcome == "extra_context"
        else 403 if outcome in {"denied", "no_csrf"} else 303
    )
    if outcome in {"extra_context", "no_csrf"}:
        service.assert_not_awaited()
    else:
        assert service.await_args.kwargs["actor_id"] == w.actor.id
        assert "tournament_id" not in service.await_args.kwargs
        assert "budget_concept_id" not in service.await_args.kwargs
    if outcome == "success":
        w.session.commit.assert_awaited_once()
    else:
        w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cp_queue_has_only_line_fields_and_explicit_versions(
    complete_direct_workspace,
):
    from lxml import html

    w = complete_direct_workspace
    w.actor.rol = "usuario"
    async with await _client(w) as client:
        response = await client.get(w.routes.QUEUE)
    tree = html.fromstring(response.text)
    form = tree.xpath('//form[contains(@action,"/asignar")]')[0]
    assert {el.get("name") for el in form.xpath(".//*[@name]")} == {
        "_csrf_token",
        "expected_source_version",
        "expected_source_hash",
        "idempotency_key",
        "budget_line_id",
    }
    assert "versión2026 aprobada" in response.text
    assert not tree.xpath("//select/option[@selected]")
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", [None, "xlsx", "csv", "zip", "html"])
async def test_private_original_and_frozen_export_http(complete_direct_workspace, fmt):
    w = complete_direct_workspace
    suffix = "original" if fmt is None else "exportar." + fmt
    async with await _client(w) as client:
        response = await client.get(
            f"{statements.BASE}/{w.statement.id}/directo/{suffix}"
        )
    assert response.status_code == (404 if fmt == "html" else 200)
    if fmt != "html":
        assert response.content == (
            b"original-exact" if fmt is None else b"frozen-exact"
        )
        assert "private, no-store" in response.headers["cache-control"]
        assert "attachment;" in response.headers["content-disposition"]
    w.session.commit.assert_not_awaited()
    w.routes.status.consult_direct_statement_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_fail_closed_and_filename_no_header_injection(
    complete_direct_workspace,
):
    w = complete_direct_workspace
    w.routes.intake.download_direct_source.return_value = (
        b"original",
        'a/..\\evil"\r\n名.csv',
        "text/csv",
    )
    async with await _client(w) as client:
        response = await client.get(
            f"{statements.BASE}/{w.statement.id}/directo/original"
        )
        assert response.status_code == 200
        assert "\r" not in response.headers["content-disposition"]
        assert "\n" not in response.headers["content-disposition"]
        w.routes.accounting.export_direct_cut.side_effect = ValueError(
            "private-frozen-invalid"
        )
        response = await client.get(
            f"{statements.BASE}/{w.statement.id}/directo/exportar.csv"
        )
    assert response.status_code == 409
    assert "private-frozen-invalid" not in response.text
    assert "content-disposition" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "consented,outcome",
    [
        (False, "imported"),
        (True, "imported"),
        (False, "exists"),
        (False, "pending"),
        (False, "exception"),
    ],
)
async def test_intake_original_and_commit_precede_optional_sat(
    complete_direct_workspace, consented, outcome
):
    from devnous.gastos.services.amex_direct_common import DirectResult

    w = complete_direct_workspace
    w.routes.intake.import_direct_statement.return_value = DirectResult(
        outcome,
        "contexto_fiscal_pendiente" if outcome == "pending" else None,
        statement=w.statement,
        source_version=4,
    )
    if outcome == "exception":
        w.routes.intake.import_direct_statement.side_effect = RuntimeError(
            "synthetic intake failure"
        )

    async def observe(*args, **kwargs):
        assert w.session.commit.await_count == 2
        assert kwargs["request_key"] == "intake-once"
        return w.success

    w.routes.status.consult_direct_statement_status.side_effect = observe
    async with await _client(w) as client:
        response = await client.get(statements.BASE + "/nuevo?modo=directo")
        from lxml import html

        token = html.fromstring(response.text).xpath(
            '//input[@name="_csrf_token"]/@value'
        )[0]
        data = {
            "workflow_mode": "direct",
            "_csrf_token": token,
            "employee_id": str(w.actor.id),
            "card_account_id": str(w.actor.id),
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
            "profile": "debit_negative",
            "company_code": "TEST",
            "receiver_rfc": "AAA010101AAA",
            "currency": "MXN",
            "idempotency_key": "intake-once",
            "confirmed": "true",
        }
        if consented:
            data["sat_consented"] = "true"
        files = {
            "file": (
                "original.csv",
                b"date,description,amount\n2026-09-03,COMERCIO,-116.00",
                "text/csv",
            )
        }
        if outcome == "exception":
            with pytest.raises(RuntimeError):
                await client.post(statements.BASE + "/nuevo", data=data, files=files)
        else:
            response = await client.post(
                statements.BASE + "/nuevo", data=data, files=files
            )
            assert response.status_code == (409 if outcome == "pending" else 303)
    kwargs = w.routes.intake.import_direct_statement.await_args.kwargs
    assert (
        kwargs["content"] == files["file"][1] and kwargs["profile"] == "debit_negative"
    )
    assert kwargs["actor_id"] == w.actor.id
    statements.import_amex_statement.assert_not_awaited()
    if outcome in {"pending", "exception"}:
        w.session.commit.assert_not_awaited()
        assert w.session.rollback.await_count >= 1
    else:
        assert w.session.commit.await_count == (2 if outcome == "imported" else 1)
    if consented:
        w.routes.status.consult_direct_statement_status.assert_awaited_once()
    else:
        w.routes.status.consult_direct_statement_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_parses_real_signed_rows_without_business_writes(
    complete_direct_workspace,
):
    from lxml import html

    w = complete_direct_workspace
    async with await _client(w) as client:
        response = await client.get(statements.BASE + "/nuevo?modo=directo")
        tree = html.fromstring(response.text)
        assert tree.xpath('//*[@id="amex-direct-import" and @disabled]')
        token = tree.xpath('//input[@name="_csrf_token"]/@value')[0]
        response = await client.post(
            statements.BASE + "/directo/previsualizar",
            data={"_csrf_token": token, "profile": "debit_negative", "currency": "MXN"},
            files={
                "file": (
                    "original.csv",
                    b"date,description,amount\n2026-09-03,COMERCIO,-116.00\n2026-09-04,ABONO,20.00",
                    "text/csv",
                )
            },
        )
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert rows[0]["raw_signed_amount"] == "-116.00" and rows[0]["amount"] == "116.00"
    assert rows[0]["movement_kind"] == "debit" and rows[1]["movement_kind"] == "credit"
    assert rows[1]["amount"] is None
    w.routes.intake.import_direct_statement.assert_not_awaited()
    w.session.commit.assert_not_awaited()
    w.routes.status.consult_direct_statement_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_configuration_no_guess_selectors(complete_direct_workspace):
    w = complete_direct_workspace
    w.routes.context.list_direct_intake_options.return_value = {
        "contexts": [],
        "cards": [],
        "people": [],
    }
    async with await _client(w) as client:
        response = await client.get(statements.BASE + "/nuevo?modo=directo")
    assert response.status_code == 200
    assert "configurar el contexto fiscal" in response.text
    assert 'name="receiver_rfc"' not in response.text
    assert 'name="card_account_id"' not in response.text
    assert "Recorrido de informes existente" in response.text
    w.session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["upload", "preview", "detail", "queue"])
async def test_explicit_action_denial_never_exposes_source(
    complete_direct_workspace, target
):
    w = complete_direct_workspace
    async with await _client(w) as client:
        token = await _token(client, w)
        if target in {"upload", "preview"}:
            w.routes.context.list_direct_intake_options.side_effect = PermissionError(
                "private denial"
            )
            if target == "upload":
                response = await client.get(statements.BASE + "/nuevo?modo=directo")
            else:
                response = await client.post(
                    statements.BASE + "/directo/previsualizar",
                    data={
                        "_csrf_token": token,
                        "profile": "debit_negative",
                        "currency": "MXN",
                    },
                    files={
                        "file": (
                            "x.csv",
                            b"date,description,amount\n2026-09-03,CARGO,-116.00",
                            "text/csv",
                        )
                    },
                )
        elif target == "detail":
            w.routes.context.list_direct_statement.side_effect = PermissionError(
                "private denial"
            )
            response = await client.get(f"{statements.BASE}/{w.statement.id}/directo")
        else:
            w.routes.budget.list_direct_budget_queue.side_effect = PermissionError(
                "private denial"
            )
            response = await client.get(w.routes.QUEUE)
    assert response.status_code == 403 and "private denial" not in response.text
    assert w.item.descriptor not in response.text
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cut_exports_visible_only_with_real_cut(complete_direct_workspace):
    from datetime import date
    from types import SimpleNamespace as NS

    w = complete_direct_workspace
    async with await _client(w) as client:
        path = f"{statements.BASE}/{w.statement.id}/directo"
        response = await client.get(path)
        assert "directo/exportar.csv" not in response.text
        w.preview.status, w.preview.reason = "pending", "statement_incomplete"
        response = await client.get(path)
        assert "Generar póliza directa completa" not in response.text
        assert "Hay cargos pendientes" in response.text
        w.preview.cut = NS(accounting_date=date(2026, 9, 10))
        response = await client.get(path)
    assert "Corte congelado: 2026-09-10" in response.text
    assert "directo/exportar.csv" in response.text
    assert "Generar póliza directa completa" not in response.text


@pytest.mark.asyncio
async def test_configured_accounting_actor_reads_and_classifies_without_finance_authority(
    complete_direct_workspace,
):
    from lxml import html

    w = complete_direct_workspace
    w.actor.rol = "contabilidad"
    async with await _client(w) as client:
        response = await client.get(f"{statements.BASE}/{w.statement.id}/directo")
        assert response.status_code == 200
        tree = html.fromstring(response.text)
        assert "Guardar clasificación" in response.text
        assert not tree.xpath('//form[contains(@action,"/confirmar")]')
        assert not tree.xpath('//a[contains(@href,"/original")]')
        assert not tree.xpath('//a[contains(@href,"/exportar.")]')
        token = tree.xpath('//input[@name="_csrf_token"]/@value')[0]
        for action in ("clasificar", "enviar-presupuesto"):
            response = await client.post(
                f"{statements.BASE}/{w.statement.id}/directo/{action}",
                data={**_payload(w, action), "_csrf_token": token},
            )
            assert response.status_code == 303
        for action in (
            "contexto",
            "alias",
            "conciliar",
            "vigencia",
            "vincular",
            "excluir-credito",
            "confirmar",
        ):
            response = await client.post(
                f"{statements.BASE}/{w.statement.id}/directo/{action}",
                data={**_payload(w, action), "_csrf_token": token},
            )
            assert response.status_code == 403
    w.routes.budget.classify_direct_items.assert_awaited_once()
    w.routes.budget.send_direct_items_to_budget.assert_awaited_once()
    w.routes.accounting.confirm_direct_accounting.assert_not_awaited()
    w.routes.matching.reconcile_direct_statement.assert_not_awaited()


@pytest.mark.asyncio
async def test_classification_denial_keeps_authorized_read_only(
    complete_direct_workspace,
):
    from lxml import html

    w = complete_direct_workspace
    w.routes.context.list_direct_classification_options.side_effect = PermissionError(
        "specific action denied"
    )
    async with await _client(w) as client:
        response = await client.get(f"{statements.BASE}/{w.statement.id}/directo")
    assert response.status_code == 200
    assert "no tienes autorización para clasificar" in response.text
    tree = html.fromstring(response.text)
    assert tree.xpath('//fieldset[@disabled]//form[contains(@action,"/clasificar")]')
    assert len(tree.xpath("//tr[@data-item-id]")) == 1
    assert "specific action denied" not in response.text
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_download_query_cannot_switch_export_owner(
    complete_direct_workspace,
):
    w = complete_direct_workspace
    async with await _client(w) as client:
        response = await client.get(
            f"{statements.BASE}/{w.statement.id}/directo/original?export_format=csv"
        )
    assert response.content == b"original-exact"
    w.routes.accounting.export_direct_cut.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"item_ids": "invalid"},
        {"expected_versions": "invalid-json"},
        {"expected_versions": "[]"},
        {"expected_versions": '{"unrelated":1}'},
        {"budget_concept_id": "invalid"},
        {"edition_year": "wrong"},
    ],
)
async def test_classification_payload_invalid_before_service(
    complete_direct_workspace, changes
):
    w = complete_direct_workspace
    async with await _client(w) as client:
        token = await _token(client, w)
        response = await client.post(
            f"{statements.BASE}/{w.statement.id}/directo/clasificar",
            data={**_payload(w, "clasificar"), **changes, "_csrf_token": token},
        )
    assert response.status_code == 422
    w.routes.budget.classify_direct_items.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_scalar_and_selection_fields_rejected(
    complete_direct_workspace,
):
    from urllib.parse import urlencode

    w = complete_direct_workspace
    async with await _client(w) as client:
        token = await _token(client, w)
        entries = list({**_payload(w, "confirmar"), "_csrf_token": token}.items())
        response = await client.post(
            f"{statements.BASE}/{w.statement.id}/directo/confirmar",
            content=urlencode(entries + [("expected_source_version", "999")]),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 422
        entries = list({**_payload(w, "clasificar"), "_csrf_token": token}.items())
        response = await client.post(
            f"{statements.BASE}/{w.statement.id}/directo/clasificar",
            content=urlencode(entries + [("item_ids", str(w.item.id))]),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 422
    w.routes.accounting.confirm_direct_accounting.assert_not_awaited()
    w.routes.budget.classify_direct_items.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["asignar", "rechazar"])
async def test_cp_inactive_and_unknown_action_no_write(
    complete_direct_workspace, action
):
    w = complete_direct_workspace
    w.actor.activo = False
    async with await _client(w) as client:
        response = await client.post(f"{w.routes.QUEUE}/{w.item.id}/{action}", data={})
        assert response.status_code == 403
        response = await client.get(w.routes.QUEUE)
        assert response.status_code == 403
    w.routes.budget.assign_direct_budget_line.assert_not_awaited()
    w.routes.budget.reject_direct_budget_item.assert_not_awaited()
    w.routes.budget.list_direct_budget_queue.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_concurrent_cut_responses_defer_stale_detection_to_owner(
    complete_direct_workspace,
):
    import asyncio

    from devnous.gastos.services.amex_direct_common import DirectResult

    w = complete_direct_workspace
    w.routes.accounting.confirm_direct_accounting.side_effect = [
        w.success,
        DirectResult("pending", "source_changed"),
    ]
    async with await _client(w) as client:
        token = await _token(client, w)
        payload = {**_payload(w, "confirmar"), "_csrf_token": token}
        responses = await asyncio.gather(
            *[
                client.post(
                    f"{statements.BASE}/{w.statement.id}/directo/confirmar",
                    data=payload,
                )
                for _ in range(2)
            ]
        )
    assert all(response.status_code == 303 for response in responses)
    assert w.routes.accounting.confirm_direct_accounting.await_count == 2
    w.session.commit.assert_awaited_once()
    w.session.rollback.assert_awaited_once()
    assert any(
        "Los+datos+cambiaron" in response.headers["location"] for response in responses
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["finanzas", "contabilidad"])
async def test_scoped_direct_discovery_counts_no_informe_fiction(
    complete_direct_workspace, role
):
    w = complete_direct_workspace
    w.actor.rol = role
    async with await _client(w) as client:
        response = await client.get(statements.BASE + "/directo")
    assert response.status_code == 200
    assert "Créditos: 0" in response.text and "CFDI confirmado: 1" in response.text
    assert "Partida asignada: 1" in response.text
    assert "Sin corte directo confirmado" in response.text
    assert "Falta informe aprobado" not in response.text
    assert ("Cargar estado directo</a>" in response.text) == (role == "finanzas")
    assert "Recorrido de informes existente" in response.text
    assert f"/{w.statement.id}/directo" in response.text
    assert (
        w.routes.context.list_direct_statements.await_args.kwargs["actor_id"]
        == w.actor.id
    )
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_discovery_scope_denial_and_empty_never_global_fallback(
    complete_direct_workspace,
):
    w = complete_direct_workspace
    async with await _client(w) as client:
        w.routes.context.list_direct_statements.return_value = []
        response = await client.get(statements.BASE + "/directo")
        assert response.status_code == 200
        assert "No hay estados directos dentro de tu ámbito" in response.text
        w.routes.context.list_direct_statements.side_effect = PermissionError(
            "private denied scope"
        )
        response = await client.get(statements.BASE + "/directo")
    assert response.status_code == 403 and "private denied scope" not in response.text
    statements.list_statement_workflows.assert_not_awaited()


@pytest.mark.asyncio
async def test_schema_unavailable_is_clear503_without_source_error_leak(
    complete_direct_workspace,
):
    w = complete_direct_workspace
    w.routes.context.list_direct_statement.side_effect = (
        w.routes.DirectSchemaUnavailable("SQL/private/source")
    )
    async with await _client(w) as client:
        response = await client.get(f"{statements.BASE}/{w.statement.id}/directo")
    assert response.status_code == 503
    assert "pendiente de instalación o verificación" in response.text
    assert "SQL/private/source" not in response.text
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_canonical_direct_intake_route_and_scoped_form_action(
    complete_direct_workspace,
):
    from lxml import html

    w = complete_direct_workspace
    async with await _client(w) as client:
        response = await client.get(statements.BASE + "/directo/nuevo")
        assert response.status_code == 200
        tree = html.fromstring(response.text)
        form = tree.xpath('//form[@id="amex-direct-intake"]')[0]
        assert form.get("action") == statements.BASE + "/directo/nuevo"
        assert not tree.xpath("//select/option[@selected]")
        token = form.xpath('.//input[@name="_csrf_token"]/@value')[0]
        response = await client.post(
            statements.BASE + "/directo/nuevo",
            data={
                "_csrf_token": token,
                "employee_id": str(w.actor.id),
                "card_account_id": str(w.actor.id),
                "period_start": "2026-09-01",
                "period_end": "2026-09-30",
                "confirmed": "true",
                "idempotency_key": "canonical-intake",
                "profile": "debit_negative",
                "currency": "MXN",
                "company_code": "TEST",
                "receiver_rfc": "AAA010101AAA",
            },
            files={
                "file": (
                    "original.csv",
                    b"date,description,amount\n2026-09-03,CARGO,-116.00",
                    "text/csv",
                )
            },
        )
    assert response.status_code == 303
    w.routes.intake.import_direct_statement.assert_awaited_once()
    statements.import_amex_statement.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["missing_file", "wrong_type", "too_large", "parse_error"]
)
async def test_preview_boundary_preserves_no_business_effects(
    complete_direct_workspace, monkeypatch, failure
):
    w = complete_direct_workspace
    async with await _client(w) as client:
        token = await _token(client, w)
        files = {
            "file": (
                "x.csv",
                b"date,description,amount\n2026-09-03,CARGO,-116.00",
                "text/csv",
            )
        }
        if failure == "missing_file":
            files = {}
        elif failure == "wrong_type":
            files = {"file": ("x.png", b"unexpected", "image/png")}
        elif failure == "too_large":
            monkeypatch.setattr(w.routes, "_MAX_BYTES", 5)
        else:
            files = {"file": ("x.csv", b"not,a,statement", "text/csv")}
        response = await client.post(
            statements.BASE + "/directo/previsualizar",
            data={"_csrf_token": token, "profile": "debit_negative", "currency": "MXN"},
            files=files,
        )
    assert response.status_code == (413 if failure == "too_large" else 422)
    w.routes.intake.import_direct_statement.assert_not_awaited()
    w.routes.status.consult_direct_statement_status.assert_not_awaited()
    w.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_subset_versions_sent_exactly_to_owner(
    complete_direct_workspace,
):
    import json
    from uuid import uuid4

    w = complete_direct_workspace
    async with await _client(w) as client:
        token = await _token(client, w)
        data = {
            **_payload(w, "clasificar"),
            "_csrf_token": token,
            "expected_versions": json.dumps({str(w.item.id): 2, str(uuid4()): 6}),
        }
        response = await client.post(
            f"{statements.BASE}/{w.statement.id}/directo/clasificar", data=data
        )
    assert response.status_code == 303
    kwargs = w.routes.budget.classify_direct_items.await_args.kwargs
    assert kwargs["expected_versions"] == {str(w.item.id): 2}
    assert kwargs["item_ids"] == [w.item.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["pending", "denied", "exception"])
async def test_local_matching_failure_preserves_committed_intake_and_skips_soap(
    complete_direct_workspace, outcome
):
    from devnous.gastos.services.amex_direct_common import DirectResult

    w = complete_direct_workspace
    service = w.routes.matching.reconcile_direct_statement
    if outcome == "pending":
        service.return_value = DirectResult("pending", "stale_source_version")
    elif outcome == "denied":
        service.side_effect = PermissionError("private action denial")
    else:
        service.side_effect = RuntimeError("synthetic local matching failure")
    async with await _client(w) as client:
        token = await _token(client, w)
        data = {
            "_csrf_token": token,
            "employee_id": str(w.actor.id),
            "card_account_id": str(w.actor.id),
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
            "confirmed": "true",
            "idempotency_key": "saved-intake",
            "profile": "debit_negative",
            "currency": "MXN",
            "company_code": "TEST",
            "receiver_rfc": "AAA010101AAA",
            "sat_consented": "true",
        }
        files = {
            "file": (
                "original.csv",
                b"date,description,amount\n2026-09-03,CARGO,-116.00",
                "text/csv",
            )
        }
        if outcome == "exception":
            with pytest.raises(RuntimeError):
                await client.post(
                    statements.BASE + "/directo/nuevo", data=data, files=files
                )
        else:
            response = await client.post(
                statements.BASE + "/directo/nuevo", data=data, files=files
            )
            assert response.status_code == 303
            assert "private action denial" not in response.text
    # The source admission has committed; only the follow-up matching rolls back.
    w.session.commit.assert_awaited_once()
    w.session.rollback.assert_awaited_once()
    w.routes.status.consult_direct_statement_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_and_report_shared_hero_keep_exact_workflow_instructions(
    complete_direct_workspace,
):
    from lxml import html

    w = complete_direct_workspace
    async with await _client(w) as client:
        response = await client.get(f"{statements.BASE}/{w.statement.id}/directo")
        hero = html.fromstring(response.text).xpath(
            '//*[contains(@class,"workspace-hero")]'
        )[0]
        assert "informe completo" not in hero.text_content()
        assert "Control Presupuestal asigna la partida" in hero.text_content()
        assert hero.xpath(f'.//a[@href="{statements.BASE}/directo"]')
        response = await client.get(statements.BASE + "/nuevo")
        hero = html.fromstring(response.text).xpath(
            '//*[contains(@class,"workspace-hero")]'
        )[0]
    assert "Del estado de cuenta al informe completo" in hero.text_content()
    assert "confirma cada informe completo" in hero.text_content()
    assert hero.xpath(f'.//a[@href="{statements.BASE}"]')


@pytest.mark.parametrize(
    "code,expected",
    [
        ("explicit_open_accounting_period_required", "período contable abierto"),
        ("closed_amex_accounting_period", "período contable está cerrado"),
        ("expense_date_outside_budget_edition", "fecha original del cargo"),
        ("accounting_date_outside_budget_edition", "edición presupuestal"),
        ("retention_account_mapping_conflict", "cuenta de retención"),
    ],
)
def test_new_accounting_blockers_explain_owner_and_required_evidence(code, expected):
    from devnous.gastos.routes import admin_amex_direct_routes as direct

    explanation = direct._reason(code)
    assert expected in explanation
    assert "Contabilidad" in explanation
