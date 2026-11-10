"""Approved isolated audit branch never repairs schema or leaves login authority."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from devnous.gastos.services import customer_success_audit as audit

COLUMNS = {
    "id",
    "created_at",
    "action",
    "surface",
    "actor_empleado_id",
    "target_empleado_id",
    "documento_id",
    "entity_type",
    "entity_id",
    "documento_referencia",
    "ip_address",
    "user_agent",
    "request_path",
    "request_method",
    "summary",
    "metadata_json",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [None, "marker", "schema", "columns", "insert", "commit"]
)
async def test_isolated_audit_is_preprovisioned_and_fail_closed(monkeypatch, failure):
    ensure = AsyncMock()
    monkeypatch.setattr(audit, "ensure_customer_success_audit_schema", ensure)
    session = SimpleNamespace(
        info={"batisenal_preprovisioned_audit": "bs_test_v01_" + "a" * 32},
        scalar=AsyncMock(return_value=failure != "schema"),
        execute=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    if failure == "marker":
        session.info["batisenal_preprovisioned_audit"] = "public"
    columns = COLUMNS - {"id"} if failure == "columns" else COLUMNS
    session.execute.side_effect = [
        SimpleNamespace(scalars=lambda: columns),
        RuntimeError("insert denied") if failure == "insert" else None,
    ]
    if failure == "commit":
        session.commit.side_effect = RuntimeError("commit failed")
    request = SimpleNamespace(session={"empleado_id": "synthetic"}, headers={})
    if failure:
        with pytest.raises(RuntimeError, match="^ISOLATED_AUDIT_FAILED$"):
            await audit.record_customer_success_audit_event(
                session, action="auth.login.success", request=request, commit=True
            )
        assert request.session == {}
        session.rollback.assert_awaited_once()
    else:
        await audit.record_customer_success_audit_event(
            session, action="auth.login.success", request=request, commit=True
        )
        assert request.session == {"empleado_id": "synthetic"}
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
    ensure.assert_not_awaited()
    assert all(
        "CREATE " not in str(call.args[0]) for call in session.execute.call_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,strict", [(False, False), (True, False), (True, True)]
)
async def test_ordinary_audit_keeps_existing_behavior(monkeypatch, failure, strict):
    ensure = AsyncMock()
    monkeypatch.setattr(audit, "ensure_customer_success_audit_schema", ensure)
    session = SimpleNamespace(
        info={}, execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock()
    )
    if failure:
        session.execute.side_effect = ValueError("ordinary failure")
    request = SimpleNamespace(session={"empleado_id": "ordinary"}, headers={})
    if failure and strict:
        with pytest.raises(ValueError, match="ordinary failure"):
            await audit.record_customer_success_audit_event(
                session, action="x", request=request, strict=True
            )
    else:
        await audit.record_customer_success_audit_event(
            session, action="x", request=request, strict=strict
        )
    ensure.assert_awaited_once_with(session)
    assert request.session == {"empleado_id": "ordinary"}
    assert session.rollback.await_count == int(failure)


def test_context_limits_and_uuid_validation():
    from uuid import uuid4

    assert audit.is_superadmin_role(" SUPER_ADMIN ")
    assert not audit.is_superadmin_role("empleado")
    identifier = str(uuid4())
    assert audit._uuid_or_none(identifier) == identifier
    for invalid in (None, "", "  ", "not-an-id"):
        assert audit._uuid_or_none(invalid) is None
    request = SimpleNamespace(
        headers={"x-forwarded-for": "192.0.2.1, 192.0.2.2", "user-agent": "a" * 600},
        client=SimpleNamespace(host="192.0.2.3"),
        url=SimpleNamespace(path="/login"),
        method="POST",
    )
    ctx = audit.audit_context_from_request(request)
    assert ctx.ip_address == "192.0.2.1"
    assert ctx.user_agent == "a" * 500
    assert ctx.request_path == "/login"
    request.headers = {"x-real-ip": "192.0.2.4"}
    assert audit.audit_context_from_request(request).ip_address == "192.0.2.4"
    request.headers = {}
    assert audit.audit_context_from_request(request).ip_address == "192.0.2.3"
    assert audit._parse_date_start(None) is None
    assert audit._parse_date_end("") is None
    with pytest.raises(ValueError):
        audit._parse_date_start("not-a-date")


@pytest.mark.asyncio
async def test_ordinary_schema_is_explicit_and_blank_action_is_noop():
    session = SimpleNamespace(execute=AsyncMock())
    await audit.record_customer_success_audit_event(session, action="   ")
    session.execute.assert_not_awaited()
    await audit.ensure_customer_success_audit_schema(session)
    statements = [str(call.args[0]) for call in session.execute.call_args_list]
    assert len(statements) == 7
    assert statements[0].strip().startswith("CREATE TABLE IF NOT EXISTS")
    assert all(s.startswith("CREATE INDEX IF NOT EXISTS") for s in statements[1:])


@pytest.mark.asyncio
@pytest.mark.parametrize("with_document,limit", [(True, 9999), (False, 1)])
async def test_report_binds_actor_target_filters_and_document_notifications(
    monkeypatch, with_document, limit
):
    from uuid import uuid4

    actor, target, document = map(str, (uuid4(), uuid4(), uuid4()))
    rows = [
        {"documento_id": document if with_document else None, "action": "synthetic"}
    ]
    notifications = [{"documento_id": document, "status": "sent"}]

    def result(values):
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: values))

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[result(rows), result(notifications)])
    )
    ensure = AsyncMock()
    monkeypatch.setattr(audit, "ensure_customer_success_audit_schema", ensure)
    report = await audit.build_customer_success_audit_report(
        session,
        actor_empleado_id=actor,
        target_empleado_id=target,
        action="x' OR true",
        documento_referencia="S-test",
        ip_address="192.0.2.",
        date_from="2026-11-01",
        date_to="2026-11-10",
        limit=limit,
    )
    ensure.assert_awaited_once_with(session)
    statement, parameters = session.execute.call_args_list[0].args
    assert "x' OR true" not in str(statement)
    assert parameters["actor_empleado_id"] == actor
    assert parameters["target_empleado_id"] == target
    assert parameters["action"] == "%x' OR true%"
    assert parameters["date_from"].hour == 0
    assert parameters["date_to"].hour == 23
    assert report["limit"] == (500 if limit > 500 else 10)
    assert report["events"] == rows
    assert report["telegram_by_documento"] == (
        {document: notifications} if with_document else {}
    )
    if with_document:
        assert session.execute.call_args_list[1].args[1] == {
            "documento_ids": [document]
        }
