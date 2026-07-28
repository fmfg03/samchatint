from __future__ import annotations

from typing import Any, Dict

import pytest

from samchat.assistant import router


TOOL_NAME = "tournament_goal_shadow"
OPTIONAL_ARGUMENTS = {
    "case_id",
    "expected_case_version",
    "target_name",
    "description",
    "active",
    "display_order",
    "account",
    "etapas",
    "categorias",
    "visibility_departments",
}


def _tool_definition() -> Dict[str, Any]:
    matches = [
        tool_def
        for tool_def in router._tool_defs()
        if (tool_def.get("function") or {}).get("name") == TOOL_NAME
    ]
    assert len(matches) == 1
    return matches[0]


def test_tournament_goal_shadow_is_exposed_only_as_read_only_tournament_tool() -> None:
    assert TOOL_NAME in router.READ_TOOLS
    assert TOOL_NAME in router.TOURNAMENT_READ_TOOLS
    assert TOOL_NAME not in router.WRITE_TOOLS
    assert TOOL_NAME not in router.TOURNAMENT_WRITE_TOOLS

    spec = router._assistant_tool_registry()[TOOL_NAME]
    assert spec.surface == "tournament"
    assert spec.operation_type == "read"
    assert spec.requires_confirmation is False
    assert spec.allowed_roles == ("admin", "superadmin")


def test_tournament_goal_shadow_schema_requires_goal_and_exactly_one_source() -> None:
    parameters = _tool_definition()["function"]["parameters"]

    assert parameters["type"] == "object"
    assert parameters["additionalProperties"] is False
    assert set(parameters["required"]) == {"goal"}
    assert parameters["properties"]["goal"]["minLength"] == 1

    source_choices = parameters.get("oneOf")
    assert source_choices == [
        {"required": ["source_tournament_id"]},
        {"required": ["source_tournament_name"]},
    ]

    properties = set(parameters["properties"])
    assert {"goal", "source_tournament_id", "source_tournament_name"} <= properties
    assert OPTIONAL_ARGUMENTS <= properties


def test_tournament_goal_shadow_is_available_on_tournament_read_route() -> None:
    tool_names = {
        (tool_def.get("function") or {}).get("name")
        for tool_def in router._assistant_tool_defs(
            {"route": "reporting", "domain": "tournament"}
        )
    }

    assert TOOL_NAME in tool_names
    assert not (tool_names & router.TOURNAMENT_WRITE_TOOLS)


@pytest.mark.asyncio
async def test_tournament_goal_shadow_dispatch_preserves_shadow_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "case_id": "case-052",
        "case_version": 1,
        "plan": {"status": "completed", "steps": []},
        "source": {"tournament_id": "source-2026"},
        "draft": {"name": "Torneo 2027"},
        "validation": {"valid": True, "errors": [], "warnings": []},
        "diff": {"changes": []},
        "answer": "Borrador preparado.",
        "next_questions": [],
        "missing_information": [],
        "operational_writes": False,
    }
    received: Dict[str, Any] = {}

    async def fake_build_tournament_goal_shadow(
        session: object, **kwargs: Any
    ) -> Dict[str, Any]:
        received["session"] = session
        received["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(
        router,
        "build_tournament_goal_shadow",
        fake_build_tournament_goal_shadow,
    )
    session = object()
    arguments = {
        "goal": "Crear el torneo 2027 desde el torneo 2026",
        "source_tournament_id": "source-2026",
        "target_name": "Torneo 2027",
    }

    result = await router._run_read_tool(
        TOOL_NAME,
        arguments,
        gastos_session=session,
        tournament_key_default=None,
        current_role="admin",
        current_employee_id="employee-1",
        current_conversation_id="conversation-1",
    )

    assert received == {
        "session": session,
        "kwargs": {
            **arguments,
            "current_role": "admin",
            "current_employee_id": "employee-1",
            "current_conversation_id": "conversation-1",
        },
    }
    assert result == expected
    assert set(result) == {
        "case_id",
        "case_version",
        "plan",
        "source",
        "draft",
        "validation",
        "diff",
        "answer",
        "next_questions",
        "missing_information",
        "operational_writes",
    }
    assert result["operational_writes"] is False
    assert isinstance(result["plan"], dict)
    assert isinstance(result["draft"], dict)
    assert isinstance(result["validation"], dict)
    assert isinstance(result["diff"], dict)


@pytest.mark.parametrize(
    "message",
    [
        "Crea el torneo 2027 tomando como base el torneo 2026",
        "Crea un torneo 2027 usando Copa 2026 como plantilla",
        "Clona el torneo 2026 para hacer la edición 2027",
        "Haz el torneo 2027 a partir de Copa 2026",
    ],
)
def test_clone_goal_message_exposes_only_the_shadow_tool(message: str) -> None:
    route = {"route": "agentic_write", "domain": "tournament"}
    tools = router._assistant_tool_defs_for_message(route, message)

    assert [item["function"]["name"] for item in tools] == [TOOL_NAME]


@pytest.mark.asyncio
async def test_tournament_goal_shadow_rejects_non_admin_before_dispatch() -> None:
    with pytest.raises(router.HTTPException) as error:
        await router._run_read_tool(
            TOOL_NAME,
            {"goal": "Crear torneo", "source_tournament_name": "Base"},
            gastos_session=object(),
            tournament_key_default=None,
            current_role="user",
            current_employee_id="employee-1",
            current_conversation_id="conversation-1",
        )

    assert error.value.status_code == 403
