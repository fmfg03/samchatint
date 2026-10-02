"""Visibility never substitutes Operations approval authority."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.routes import user_routes
from devnous.gastos.services import documento_telegram as telegram
from devnous.gastos.services import project_authorization_service as routing
from devnous.gastos.services.telegram_document_runtime import TelegramDocumentRuntime


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 50])
async def test_route_authority_batch_uses_at_most_one_query(count):
    """Queue growth must not multiply canonical authority database calls."""
    ids = [uuid4() for _ in range(count)]
    allowed = ids[::2]
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: allowed))
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    actual = await routing.actor_route_approver_document_ids(
        session, actor_id=uuid4(), documento_ids=ids
    )
    assert actual == set(allowed)
    assert session.execute.await_count == int(bool(count))
    if count:
        query = session.execute.await_args.args[0]
        assert routing.document_route_approver_sql() in str(query)
        assert set(query.compile().params["id_1"]) == set(ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["superadmin", "super_admin"])
@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
async def test_web_observer_rows_have_no_decision_or_bulk_selection(
    monkeypatch, role, allowed, mixed
):
    """Observer rows remain selectable only when canonical authority permits it."""
    operations = SimpleNamespace(
        id=uuid4(), referencia_operaciones="299", monto_total=10,
        monto_solicitado=10, enviado_en=None, creado_en=None,
    )
    natural = SimpleNamespace(**{**vars(operations), "id": uuid4(),
                                "referencia_operaciones": None})
    documents = [operations, natural] if mixed else [operations]
    result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(
            unique=lambda: SimpleNamespace(all=lambda: documents)
        )
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    authority = AsyncMock(return_value={operations.id} if allowed else set())
    monkeypatch.setattr(user_routes, "actor_route_approver_document_ids", authority)
    monkeypatch.setattr(
        user_routes, "_can_review_pending_approvals", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        user_routes, "fetch_documento_aprobador_display_batch",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(user_routes, "currency_for", lambda _: "MXN")
    monkeypatch.setattr(user_routes, "documento_project_name", lambda *_: "Project")
    monkeypatch.setattr(
        user_routes, "_documentos_todos_reporting_row_values",
        lambda doc, **_: dict(
            numero_referencia=str(doc.id), enviado="-", creado="-", concepto="test",
            beneficiario="test", proveedor="-", solicitante="test",
            tipo_documento="Solicitud", estado="En revisión", monto_total="10",
        ),
    )
    for name in ("render_top_navigation", "_gastos_workspace_nav_html",
                 "_gastos_breadcrumb_html"):
        monkeypatch.setattr(user_routes, name, lambda *_: "")
    monkeypatch.setattr(
        user_routes, "_render_workspace_hero", lambda **kw: kw["side_html"]
    )
    actor = SimpleNamespace(id=uuid4(), rol=role)
    html = await user_routes.documentos_pendientes(
        SimpleNamespace(query_params={}), session, actor
    )
    assert f'/documentos/{operations.id}?next=' in html
    assert (f'/documentos/{operations.id}/aprobar' in html) is allowed
    assert (f'/documentos/{operations.id}/rechazar' in html) is allowed
    assert (f'name="documento_ids" value="{operations.id}"' in html) is allowed
    assert ('value="approve" class="button primary">Aprobar seleccionados' in html) is (
        allowed or mixed
    )
    assert ("Solo consulta" in html) is (not allowed)
    assert "Documentos esperando tu decisión" not in html
    if mixed:
        assert f'name="documento_ids" value="{natural.id}"' in html
        assert f'/documentos/{natural.id}/aprobar' in html
    authority.assert_awaited_once_with(
        session, actor_id=actor.id, documento_ids={operations.id}
    )


@pytest.mark.asyncio
async def test_telegram_pending_instruction_offers_consultation(monkeypatch):
    """Observer queue entries must offer detail without promising a decision."""
    session = SimpleNamespace()

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_):
            return None

    actor = SimpleNamespace(id=uuid4(), rol="superadmin")
    document = SimpleNamespace(id=uuid4(), numero_referencia="S-test", tipo="solicitud")
    gateway = SimpleNamespace(
        _get_authorized_empleado=AsyncMock(return_value=actor),
        _resolve_auth_session_maker=lambda: SessionContext,
        send_message=AsyncMock(),
    )
    monkeypatch.setattr(
        telegram, "query_pending_documentos_for_approver",
        AsyncMock(return_value=[document]),
    )
    await TelegramDocumentRuntime(gateway).send_pendientes(20, 10)
    message = gateway.send_message.await_args.args[1]
    assert "ver el detalle." in message
    assert "decidir" not in message
    markup = gateway.send_message.await_args.kwargs["reply_markup"]
    assert markup["inline_keyboard"][0][0]["callback_data"] == telegram.list_detail_callback_data(document.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["superadmin", "super_admin", "admin", "finanzas", "empleado"])
@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("callback", ["detail", "approve", "reject"])
async def test_telegram_observers_can_open_details_but_cannot_decide(
    monkeypatch, role, allowed, callback
):
    """Consultation does not relax the live guard for either decision callback."""
    actor = SimpleNamespace(id=uuid4(), rol=role)
    document = SimpleNamespace(id=uuid4(), estado="enviado", referencia_operaciones="299")
    session = SimpleNamespace()

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_):
            return None

    gateway = SimpleNamespace(
        _get_authorized_empleado=AsyncMock(return_value=actor),
        _resolve_auth_session_maker=lambda: SessionContext,
        answer_callback_query=AsyncMock(), send_message=AsyncMock(),
        _gastos_reject_pending={},
    )
    monkeypatch.setattr(
        telegram, "load_documento_for_telegram", AsyncMock(return_value=document)
    )
    monkeypatch.setattr(
        telegram, "build_documento_telegram_context", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        telegram, "format_documento_resumen_es",
        lambda *_args, **kw: "actions" if kw["include_actions_hint"] else "read only",
    )
    authority = AsyncMock(return_value=allowed)
    monkeypatch.setattr(routing, "actor_is_route_approver", authority)
    runtime = TelegramDocumentRuntime(gateway)
    runtime.execute_approve = AsyncMock(return_value=False)
    data = {
        "detail": telegram.list_detail_callback_data(document.id),
        "approve": f"{telegram.CB_APPROVE}{document.id}",
        "reject": f"{telegram.CB_REJECT}{document.id}",
    }[callback]
    assert await runtime.handle_callback(
        {"id": "callback", "data": data, "from": {"id": 10},
         "message": {"chat": {"id": 20}, "message_id": 30}}
    )
    authority.assert_awaited_once_with(
        session, actor_id=actor.id, documento_id=document.id
    )
    if callback == "detail":
        visible = allowed or role in {"superadmin", "super_admin"}
        assert gateway.send_message.await_count == int(visible)
        if visible:
            markup = gateway.send_message.await_args.kwargs["reply_markup"]
            assert bool(markup) is allowed
            assert ("read only" in gateway.send_message.await_args.args[1]) is (not allowed)
    elif callback == "approve":
        assert runtime.execute_approve.await_count == int(allowed)
    else:
        assert bool(gateway._gastos_reject_pending) is allowed
