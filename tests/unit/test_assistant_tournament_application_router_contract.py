from __future__ import annotations

import uuid
from typing import Any, Dict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from samchat.assistant import router


APPROVE_TOOL = "tournament_proposal_approve"
APPLY_TOOL = "tournament_proposal_apply"
APPLICATION_TOOLS = {APPROVE_TOOL, APPLY_TOOL}
CASE_ID = "analyst_case_" + "1" * 32
PROPOSAL_HASH = "sha256:" + "a" * 64
APPROVAL_HASH = "sha256:" + "b" * 64


class _IsolatedSession:
    def __init__(self) -> None:
        self.connection_options: list[dict[str, Any]] = []

    async def connection(self, *, execution_options: dict[str, Any]) -> object:
        self.connection_options.append(execution_options)
        return object()


class _IsolatedSessionContext:
    def __init__(self, session: _IsolatedSession) -> None:
        self.session = session

    async def __aenter__(self) -> _IsolatedSession:
        return self.session

    async def __aexit__(self, *_args: Any) -> None:
        return None


def _patch_isolated_session(
    monkeypatch: pytest.MonkeyPatch,
) -> _IsolatedSession:
    isolated = _IsolatedSession()
    monkeypatch.setattr(
        router,
        "get_expenses_session_maker",
        lambda: lambda: _IsolatedSessionContext(isolated),
    )
    return isolated


def _tool_definition(name: str) -> Dict[str, Any]:
    matches = [
        tool_def
        for tool_def in router._tool_defs()
        if (tool_def.get("function") or {}).get("name") == name
    ]
    assert len(matches) == 1
    return matches[0]


def _names(tool_defs: list[Dict[str, Any]]) -> set[str]:
    return {
        str((tool_def.get("function") or {}).get("name") or "")
        for tool_def in tool_defs
    }


def _selected_tools(message: str, *, active_status: str | None) -> list[Dict[str, Any]]:
    route = router._assistant_classify_request(message)
    return router._assistant_tool_defs_for_message(
        route,
        message,
        has_active_tournament_case=active_status is not None,
        active_tournament_case_status=active_status,
    )


def test_application_tools_are_confirmed_tournament_writes_for_admins_only() -> None:
    registry = router._assistant_tool_registry()

    for tool_name in sorted(APPLICATION_TOOLS):
        assert tool_name in router.WRITE_TOOLS
        assert tool_name in router.TOURNAMENT_WRITE_TOOLS
        assert tool_name not in router.READ_TOOLS
        assert tool_name not in router.TOURNAMENT_READ_TOOLS
        assert registry[tool_name].surface == "tournament"
        assert registry[tool_name].operation_type == "write"
        assert registry[tool_name].risk_level == "high"
        assert registry[tool_name].requires_confirmation is True
        assert registry[tool_name].allowed_roles == ("admin", "superadmin")


def test_application_tool_schemas_bind_frozen_and_approved_receipts() -> None:
    approve = _tool_definition(APPROVE_TOOL)["function"]
    apply = _tool_definition(APPLY_TOOL)["function"]

    for function in (approve, apply):
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False
        assert parameters["properties"]["case_id"]["pattern"] == (
            "^analyst_case_[0-9a-f]{32}$"
        )
        assert parameters["properties"]["expected_case_version"]["minimum"] == 1
        assert parameters["properties"]["expected_proposal_hash"]["pattern"] == (
            "^sha256:[0-9a-f]{64}$"
        )
        assert not (
            {"confirmed", "confirmation", "payload", "draft", "target_name"}
            & set(parameters["properties"])
        )

    assert set(approve["parameters"]["required"]) == {
        "expected_case_version",
        "expected_proposal_hash",
    }
    assert set(apply["parameters"]["required"]) == {
        "expected_case_version",
        "expected_proposal_hash",
        "expected_approval_hash",
    }
    assert (
        apply["parameters"]["properties"]["expected_approval_hash"]["pattern"]
        == "^sha256:[0-9a-f]{64}$"
    )
    assert "no crea" in approve["description"].casefold()
    assert "torneo local" in apply["description"].casefold()


@pytest.mark.parametrize(
    ("tool_name", "message", "active_status"),
    [
        (
            APPROVE_TOOL,
            f"Aprueba la propuesta del torneo {CASE_ID}",
            None,
        ),
        (APPROVE_TOOL, "Autorizo esta propuesta", "frozen"),
        (APPLY_TOOL, "Aplica la propuesta aprobada del torneo", "approved"),
        (APPLY_TOOL, "Crea el torneo con este caso aprobado", "approved"),
        (APPLY_TOOL, f"Aplica el caso aprobado {CASE_ID}", None),
    ],
)
def test_application_intents_expose_exactly_one_state_appropriate_tool(
    tool_name: str,
    message: str,
    active_status: str | None,
) -> None:
    tools = _selected_tools(message, active_status=active_status)

    assert _names(tools) == {tool_name}
    assert _names(tools) <= router.WRITE_TOOLS
    assert _names(tools) <= router.TOURNAMENT_WRITE_TOOLS


@pytest.mark.parametrize(
    ("message", "active_status"),
    [
        ("Aprueba", "frozen"),
        ("/ok", "frozen"),
        ("Aprueba la factura", "frozen"),
        ("Aprueba el presupuesto anual", "frozen"),
        ("Aplica el pago al proveedor", "approved"),
        ("Crea un torneo nuevo basado en Copa 2026", None),
        ("Congela el borrador del torneo", "drafting"),
        ("Aprueba esta propuesta", "drafting"),
        ("Aprueba esta propuesta", "approved"),
        ("Aplica esta propuesta", "frozen"),
        ("Aplica esta propuesta", "applied"),
        ("Cancela el calendario del torneo", "approved"),
        ("Muestra las estadisticas del torneo", "approved"),
        ("Muestra los equipos del torneo", "approved"),
        ("Ensename informacion del torneo", "frozen"),
        ("Muéstrame los equipos inscritos", "approved"),
    ],
)
def test_unrelated_or_wrong_state_intents_never_expose_application_tools(
    message: str,
    active_status: str | None,
) -> None:
    tools = _selected_tools(message, active_status=active_status)

    assert not (_names(tools) & APPLICATION_TOOLS)


@pytest.mark.parametrize(
    "message",
    [
        "Aprueba el caso analyst_case_invalido",
        "Aprueba el caso analyst_case_1234",
        "Aprueba el caso analyst_case_" + "1" * 33,
        "Aprueba el caso analyst_case_" + "1" * 32 + "g",
        "Aprueba el caso xxanalyst_case_" + "1" * 32,
    ],
)
@pytest.mark.asyncio
async def test_malformed_explicit_case_never_falls_back_to_active_pointer(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    review = AsyncMock()
    monkeypatch.setattr(router, "review_tournament_proposal", review)
    conversation = SimpleNamespace(
        id=uuid.UUID("10000000-0000-0000-0000-000000000054"),
        metadata_={
            "active_tournament_goal_case": {
                "case_id": CASE_ID,
                "case_version": 5,
                "status": "frozen",
            }
        },
    )

    with pytest.raises(router.HTTPException) as caught:
        await router._build_tournament_application_pending(
            raw_message=message,
            conversation=conversation,
            empleado_id=uuid.UUID("00000000-0000-0000-0000-000000000054"),
            session=object(),
        )

    assert caught.value.status_code == 400
    review.assert_not_awaited()


@pytest.mark.parametrize(
    ("message", "active_status"),
    [
        ("Aprueba esta propuesta", "frozen"),
        ("Aplica esta propuesta aprobada", "approved"),
        (f"Aprueba el caso {CASE_ID}", None),
        (f"Aplica el caso aprobado {CASE_ID}", None),
    ],
)
def test_application_turns_are_never_response_cached(
    message: str,
    active_status: str | None,
) -> None:
    assert (
        router._assistant_response_cache_allowed_for_message(
            message,
            has_active_tournament_case=active_status is not None,
            active_tournament_case_status=active_status,
        )
        is False
    )


def test_readonly_runtime_filter_removes_selected_application_write() -> None:
    selected = _selected_tools("Aprueba esta propuesta", active_status="frozen")
    readonly_visible = [
        tool_def
        for tool_def in selected
        if str((tool_def.get("function") or {}).get("name") or "") in router.READ_TOOLS
    ]

    assert _names(selected) == {APPROVE_TOOL}
    assert readonly_visible == []


@pytest.mark.parametrize("tool_name", sorted(APPLICATION_TOOLS))
def test_application_confirmation_guard_is_admin_only(tool_name: str) -> None:
    assert router._can_confirm_write(tool_name, "admin") is True
    assert router._can_confirm_write(tool_name, "superadmin") is True
    assert router._can_confirm_write(tool_name, "super_admin") is True
    assert router._can_confirm_write(tool_name, "user") is False
    assert router._can_confirm_write(tool_name, "finanzas") is False
    assert router._can_confirm_write(tool_name, None) is False
    assert router._write_requires_verification(tool_name, {}) is True


def test_tournament_approval_phrase_does_not_confirm_unrelated_pending_run() -> None:
    assert (
        router._is_explicit_approval_message("Aprueba esta propuesta de torneo")
        is False
    )
    assert (
        router._is_explicit_approval_message("Aplica esta propuesta aprobada") is False
    )
    assert router._is_explicit_approval_message("/ok") is True


@pytest.mark.asyncio
async def test_approve_dispatch_preserves_authority_only_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = {
        "case_id": CASE_ID,
        "expected_case_version": 5,
        "expected_proposal_hash": PROPOSAL_HASH,
    }
    expected = {
        "case_id": CASE_ID,
        "case_version": 6,
        "workbench_status": "approved",
        "proposal": {"status": "approved", "proposal_hash": PROPOSAL_HASH},
        "approval": {
            "approval_id": "tournament_approval_054",
            "approval_hash": APPROVAL_HASH,
            "approved_by_employee_id": "00000000-0000-0000-0000-000000000054",
            "approved_role": "admin",
            "proposal_hash": PROPOSAL_HASH,
        },
        "allowed_next_actions": ["inspect", "apply"],
        "operational_writes": False,
    }
    received: Dict[str, Any] = {}

    async def fake_approve(session: object, **kwargs: Any) -> Dict[str, Any]:
        received["session"] = session
        received["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(
        router,
        "approve_tournament_proposal",
        fake_approve,
        raising=False,
    )
    isolated = _patch_isolated_session(monkeypatch)
    session = object()
    employee_id = uuid.UUID("00000000-0000-0000-0000-000000000054")
    conversation_id = uuid.UUID("10000000-0000-0000-0000-000000000054")

    result = await router._execute_write_tool(
        APPROVE_TOOL,
        arguments,
        gastos_session=session,
        conversation_id=conversation_id,
        empleado_id=employee_id,
        tournament_key_default=None,
    )

    assert received == {
        "session": isolated,
        "kwargs": {
            **arguments,
            "current_employee_id": str(employee_id),
            "current_conversation_id": str(conversation_id),
        },
    }
    assert result == expected
    assert result["workbench_status"] == "approved"
    assert result["operational_writes"] is False
    assert result["allowed_next_actions"] == ["inspect", "apply"]
    assert isolated.connection_options == [{"isolation_level": "SERIALIZABLE"}]


@pytest.mark.asyncio
async def test_apply_dispatch_returns_hash_bound_single_tournament_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = {
        "case_id": CASE_ID,
        "expected_case_version": 6,
        "expected_proposal_hash": PROPOSAL_HASH,
        "expected_approval_hash": APPROVAL_HASH,
    }
    expected = {
        "case_id": CASE_ID,
        "case_version": 7,
        "workbench_status": "applied",
        "proposal": {"status": "applied", "proposal_hash": PROPOSAL_HASH},
        "approval": {"approval_hash": APPROVAL_HASH},
        "application": {
            "application_id": "tournament_application_054",
            "application_hash": "sha256:" + "c" * 64,
            "status": "applied",
            "target": {
                "tournament_id": "20000000-0000-0000-0000-000000000054",
                "name": "Copa 2027",
            },
            "bindings": {
                "approval_hash": APPROVAL_HASH,
                "proposal_hash": PROPOSAL_HASH,
                "draft_hash": "d" * 64,
                "source_authority_hash": "sha256:" + "e" * 64,
            },
            "write_set": {
                "inserted": {"tournaments": 1},
                "updated": {},
                "deleted": {},
            },
            "idempotent_replay": False,
            "operational_write_performed_this_call": True,
        },
        "allowed_next_actions": ["inspect"],
        "operational_writes": True,
    }
    received: Dict[str, Any] = {}

    async def fake_apply(session: object, **kwargs: Any) -> Dict[str, Any]:
        received["session"] = session
        received["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(
        router,
        "apply_tournament_proposal",
        fake_apply,
        raising=False,
    )
    isolated = _patch_isolated_session(monkeypatch)
    session = object()
    employee_id = uuid.UUID("00000000-0000-0000-0000-000000000054")
    conversation_id = uuid.UUID("10000000-0000-0000-0000-000000000054")

    result = await router._execute_write_tool(
        APPLY_TOOL,
        arguments,
        gastos_session=session,
        conversation_id=conversation_id,
        empleado_id=employee_id,
        tournament_key_default=None,
    )

    assert received == {
        "session": isolated,
        "kwargs": {
            **arguments,
            "current_employee_id": str(employee_id),
            "current_conversation_id": str(conversation_id),
        },
    }
    assert result == expected
    assert result["operational_writes"] is True
    assert result["application"]["write_set"] == {
        "inserted": {"tournaments": 1},
        "updated": {},
        "deleted": {},
    }
    assert result["allowed_next_actions"] == ["inspect"]
    assert isolated.connection_options == [{"isolation_level": "SERIALIZABLE"}]


@pytest.mark.asyncio
async def test_serializable_conflict_is_reported_as_retryable_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SerializationFailure(RuntimeError):
        sqlstate = "40001"

    async def fail_serialization(_session: object, **_kwargs: Any) -> Dict[str, Any]:
        raise router.DBAPIError(
            "apply tournament",
            {},
            SerializationFailure("concurrent change"),
            False,
        )

    monkeypatch.setattr(router, "apply_tournament_proposal", fail_serialization)
    _patch_isolated_session(monkeypatch)

    with pytest.raises(router.HTTPException) as caught:
        await router._execute_write_tool(
            APPLY_TOOL,
            {
                "case_id": CASE_ID,
                "expected_case_version": 6,
                "expected_proposal_hash": PROPOSAL_HASH,
                "expected_approval_hash": APPROVAL_HASH,
            },
            gastos_session=object(),
            conversation_id=uuid.UUID("10000000-0000-0000-0000-000000000054"),
            empleado_id=uuid.UUID("00000000-0000-0000-0000-000000000054"),
            tournament_key_default=None,
        )

    assert caught.value.status_code == 409
    assert "concurrent" in str(caught.value.detail).casefold()


@pytest.mark.asyncio
async def test_deterministic_pending_hydrates_hashes_and_informed_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review = {
        "case_id": CASE_ID,
        "case_version": 6,
        "workbench_status": "approved",
        "proposal": {
            "proposal_hash": PROPOSAL_HASH,
            "draft_hash": "d" * 64,
            "source_authority_hash": "sha256:" + "e" * 64,
            "target": {"name": "Copa 2027"},
            "business_diff": {"entries": [{"field": "name", "after": "Copa 2027"}]},
        },
        "approval": {"approval_hash": APPROVAL_HASH},
        "decision": {
            "current_employee_is_owner": False,
            "can_approve": False,
            "can_apply": True,
        },
    }
    monkeypatch.setattr(
        router,
        "review_tournament_proposal",
        AsyncMock(return_value=review),
    )
    conversation = SimpleNamespace(
        id=uuid.UUID("10000000-0000-0000-0000-000000000054"),
        metadata_={
            "active_tournament_goal_case": {
                "case_id": CASE_ID,
                "case_version": 6,
                "status": "approved",
            }
        },
    )

    pending = await router._build_tournament_application_pending(
        raw_message="Aplica esta propuesta aprobada",
        conversation=conversation,
        empleado_id=uuid.UUID("00000000-0000-0000-0000-000000000054"),
        session=object(),
    )

    assert pending is not None
    tool_name, args, message = pending
    assert tool_name == APPLY_TOOL
    assert args["expected_case_version"] == 6
    assert args["expected_proposal_hash"] == PROPOSAL_HASH
    assert args["expected_approval_hash"] == APPROVAL_HASH
    assert args["__authoritative_summary"] == message
    assert "Copa 2027" in message
    assert "exactamente 1 fila" in message
    assert "No crea calendario" in message


@pytest.mark.asyncio
async def test_confirmed_application_finishes_deterministically_without_followup_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = SimpleNamespace(
        id=uuid.uuid4(),
        status="pending_confirmation",
        pending_tool_name=APPLY_TOOL,
        pending_tool_args={
            "case_id": CASE_ID,
            "expected_case_version": 6,
            "expected_proposal_hash": PROPOSAL_HASH,
            "expected_approval_hash": APPROVAL_HASH,
            "__authoritative_summary": "authoritative",
        },
        tool_trace=[],
        assistant_message="pending",
    )
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = run
    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result)
    session.add = MagicMock()
    conversation = SimpleNamespace(
        id=uuid.uuid4(), metadata_={}, tournament_key=None, updated_at=None
    )
    employee = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000054"),
        rol="admin",
    )
    result = {
        "case_id": CASE_ID,
        "case_version": 7,
        "application": {
            "application_hash": "sha256:" + "c" * 64,
            "target": {
                "tournament_id": "20000000-0000-0000-0000-000000000054",
                "name": "Copa 2027",
            },
            "postcommit_verification": {"status": "verified"},
        },
    }
    monkeypatch.setattr(
        router,
        "_assistant_verify_sensitive_operation",
        AsyncMock(return_value={"verdict": "pass"}),
    )
    monkeypatch.setattr(router, "_execute_write_tool", AsyncMock(return_value=result))
    followup = AsyncMock(side_effect=AssertionError("follow-up LLM must not run"))
    monkeypatch.setattr(router, "_assistant_text_response", followup)

    response = await router._confirm_pending_run(
        run=run,
        conversation=conversation,
        approve=True,
        assistant_mode=None,
        openai_api_key=None,
        current_empleado=employee,
        session=session,
    )

    assert response.pending_confirmation is None
    assert "Copa 2027" in response.assistant_message
    assert run.status == "completed"
    assert run.pending_tool_name is None
    session.commit.assert_awaited_once()
    followup.assert_not_awaited()
