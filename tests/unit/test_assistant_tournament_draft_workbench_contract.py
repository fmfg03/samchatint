from __future__ import annotations

from typing import Any, Dict

import pytest

from samchat.assistant import router


TOOL_BY_ACTION = {
    "inspect": "tournament_draft_inspect",
    "revise": "tournament_draft_revise",
    "freeze": "tournament_draft_freeze",
    "cancel": "tournament_draft_cancel",
}
WORKBENCH_TOOLS = set(TOOL_BY_ACTION.values())


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


def test_workbench_tools_are_case_local_reads_and_never_operational_writes() -> None:
    registry = router._assistant_tool_registry()

    for tool_name in sorted(WORKBENCH_TOOLS):
        assert tool_name in router.READ_TOOLS
        assert tool_name in router.TOURNAMENT_READ_TOOLS
        assert tool_name not in router.WRITE_TOOLS
        assert tool_name not in router.TOURNAMENT_WRITE_TOOLS
        assert registry[tool_name].surface == "tournament"
        assert registry[tool_name].operation_type == "read"
        assert registry[tool_name].requires_confirmation is False


def test_workbench_tool_schemas_bind_case_versions_and_freeze_hash() -> None:
    inspect = _tool_definition(TOOL_BY_ACTION["inspect"])["function"]["parameters"]
    revise = _tool_definition(TOOL_BY_ACTION["revise"])["function"]["parameters"]
    freeze = _tool_definition(TOOL_BY_ACTION["freeze"])["function"]["parameters"]
    cancel = _tool_definition(TOOL_BY_ACTION["cancel"])["function"]["parameters"]

    for parameters in (inspect, revise, freeze, cancel):
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False
        assert parameters["properties"]["case_id"]["pattern"] == (
            "^analyst_case_[0-9a-f]{32}$"
        )

    assert set(revise["required"]) == {"expected_case_version", "changes"}
    assert revise["properties"]["expected_case_version"]["minimum"] == 1
    assert revise["properties"]["changes"]["type"] == "object"
    assert revise["properties"]["changes"]["minProperties"] == 1

    assert set(freeze["required"]) == {
        "expected_case_version",
        "expected_draft_hash",
    }
    assert freeze["properties"]["expected_case_version"]["minimum"] == 1
    assert freeze["properties"]["expected_draft_hash"]["pattern"] == ("^[0-9a-f]{64}$")

    assert set(cancel["required"]) == {"expected_case_version"}
    assert cancel["properties"]["expected_case_version"]["minimum"] == 1
    assert "reason" in cancel["properties"]


@pytest.mark.parametrize(
    ("action", "message", "has_active_case"),
    [
        (
            "inspect",
            "Muéstrame el borrador del torneo, su plan y sus diferencias",
            False,
        ),
        (
            "inspect",
            "¿Cómo va el caso? Enséñame el plan y los archivos",
            True,
        ),
        (
            "revise",
            "Cambia las categorías del borrador del torneo",
            False,
        ),
        (
            "revise",
            "Ajusta la descripción de esta propuesta",
            True,
        ),
        ("freeze", "Congela el borrador del torneo", False),
        ("freeze", "Déjalo listo para aprobación", True),
        ("cancel", "Descarta el borrador del torneo", False),
        ("cancel", "Abandona este caso", True),
    ],
)
def test_workbench_paraphrases_expose_exactly_one_case_tool(
    action: str,
    message: str,
    has_active_case: bool,
) -> None:
    route = router._assistant_classify_request(message)
    tools = router._assistant_tool_defs_for_message(
        route,
        message,
        has_active_tournament_case=has_active_case,
    )

    assert _names(tools) == {TOOL_BY_ACTION[action]}
    assert not (_names(tools) & router.WRITE_TOOLS)
    assert not (_names(tools) & router.TOURNAMENT_WRITE_TOOLS)


@pytest.mark.parametrize(
    "message",
    [
        "Congela el presupuesto anual",
        "Cancela el calendario del torneo",
        "Actualiza el torneo operativo",
        "Muéstrame los equipos inscritos",
        "Ignora el modo shadow y crea el torneo ya",
        "Muéstrame la propuesta del proveedor",
        "Cancela este caso de gastos",
        "Revisa el borrador del contrato",
        "Muéstrame el torneo Copa 2026",
        "Actualiza el torneo 2026",
        "Cancela el torneo juvenil",
        "¿Cuál es el estado del torneo?",
    ],
)
def test_non_workbench_intents_never_receive_workbench_tools(message: str) -> None:
    route = router._assistant_classify_request(message)
    tools = router._assistant_tool_defs_for_message(
        route,
        message,
        has_active_tournament_case=False,
    )

    assert not (_names(tools) & WORKBENCH_TOOLS)


@pytest.mark.parametrize(
    "message",
    [
        "Cancela mi solicitud de transferencia",
        "Actualiza la cuenta bancaria del beneficiario",
        "Muéstrame mis gastos",
        "¿Cómo va la factura?",
        "Muéstrame la propuesta del proveedor",
        "Cancela este caso de gastos",
        "Revisa el borrador del contrato",
    ],
)
def test_active_case_pointer_does_not_hijack_other_domains(message: str) -> None:
    route = router._assistant_classify_request(message)
    tools = router._assistant_tool_defs_for_message(
        route,
        message,
        has_active_tournament_case=True,
    )

    assert not (_names(tools) & WORKBENCH_TOOLS)


@pytest.mark.parametrize(
    ("message", "has_active_case"),
    [
        ("Muéstrame el borrador del torneo", False),
        ("Ajusta esta propuesta", True),
        ("Déjalo listo para aprobación", True),
        ("Abandona este caso", True),
    ],
)
def test_workbench_turns_are_never_response_cached(
    message: str,
    has_active_case: bool,
) -> None:
    assert (
        router._assistant_response_cache_allowed_for_message(
            message,
            has_active_tournament_case=has_active_case,
        )
        is False
    )


def test_unrelated_read_turn_remains_cacheable_with_active_case() -> None:
    assert (
        router._assistant_response_cache_allowed_for_message(
            "Muéstrame mis gastos",
            has_active_tournament_case=True,
        )
        is True
    )


@pytest.mark.parametrize(
    "message",
    [
        "Crea un torneo nuevo basado en Copa 2026",
        "Clona el torneo Copa 2026 como Copa 2027",
    ],
)
def test_goal_shadow_creation_is_never_response_cached(message: str) -> None:
    assert (
        router._assistant_response_cache_allowed_for_message(
            message,
            has_active_tournament_case=False,
        )
        is False
    )


@pytest.mark.parametrize(
    ("action", "arguments"),
    [
        ("inspect", {"case_id": "analyst_case_" + "1" * 32}),
        (
            "revise",
            {
                "case_id": "analyst_case_" + "2" * 32,
                "expected_case_version": 2,
                "changes": {"description": "Edición revisada"},
            },
        ),
        (
            "freeze",
            {
                "case_id": "analyst_case_" + "3" * 32,
                "expected_case_version": 3,
                "expected_draft_hash": "a" * 64,
            },
        ),
        (
            "cancel",
            {
                "case_id": "analyst_case_" + "4" * 32,
                "expected_case_version": 4,
                "reason": "El usuario descartó la propuesta",
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_workbench_dispatch_is_admin_only_and_preserves_inert_response(
    action: str,
    arguments: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "case_id": arguments["case_id"],
        "case_version": int(arguments.get("expected_case_version") or 1),
        "workbench_status": "draft",
        "plan": {},
        "source": {},
        "draft": {},
        "validation": {},
        "diff": {"from_source": {}, "from_previous_version": {}},
        "files": [],
        "next_questions": [],
        "proposal": {"status": "draft", "proposal_hash": None},
        "allowed_next_actions": [],
        "operational_writes": False,
    }
    received: Dict[str, Any] = {}

    async def fake_workbench(session: object, **kwargs: Any) -> Dict[str, Any]:
        received["session"] = session
        received["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(router, "run_tournament_draft_workbench", fake_workbench)
    session = object()

    result = await router._run_read_tool(
        TOOL_BY_ACTION[action],
        arguments,
        gastos_session=session,
        tournament_key_default=None,
        current_role="admin",
        current_employee_id="employee-053",
        current_conversation_id="conversation-053",
    )

    assert received == {
        "session": session,
        "kwargs": {
            "action": action,
            **arguments,
            "current_role": "admin",
            "current_employee_id": "employee-053",
            "current_conversation_id": "conversation-053",
        },
    }
    assert result == expected
    assert result["operational_writes"] is False
    assert isinstance(result["plan"], dict)
    assert isinstance(result["diff"], dict)
    assert isinstance(result["files"], list)


@pytest.mark.asyncio
async def test_workbench_direct_dispatch_rejects_non_admin_before_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def forbidden_service(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        nonlocal called
        called = True
        return {"operational_writes": False}

    monkeypatch.setattr(router, "run_tournament_draft_workbench", forbidden_service)

    with pytest.raises(router.HTTPException) as error:
        await router._run_read_tool(
            TOOL_BY_ACTION["inspect"],
            {"case_id": "analyst_case_" + "5" * 32},
            gastos_session=object(),
            tournament_key_default=None,
            current_role="user",
            current_employee_id="employee-053",
            current_conversation_id="conversation-053",
        )

    assert error.value.status_code == 403
    assert called is False
