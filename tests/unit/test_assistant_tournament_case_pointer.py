from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from samchat.assistant.tournament_case_pointer import (
    TournamentCasePointerError,
    get_active_tournament_case_pointer,
    set_active_tournament_case_pointer,
)


class ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class Session:
    def __init__(self, conversation: Any) -> None:
        self.conversation = conversation
        self.statements = []

    async def execute(self, statement: Any) -> ScalarResult:
        self.statements.append(statement)
        return ScalarResult(self.conversation)


@pytest.mark.asyncio
async def test_pointer_round_trip_preserves_other_conversation_metadata() -> None:
    conversation = SimpleNamespace(metadata_={"module_key": "tournament"})
    session = Session(conversation)
    case_id = "analyst_case_" + ("a" * 32)

    stored = await set_active_tournament_case_pointer(
        session,
        conversation_id=str(uuid.uuid4()),
        employee_id=str(uuid.uuid4()),
        case_id=case_id,
        case_version=3,
        status="draft",
    )
    recovered = await get_active_tournament_case_pointer(
        session,
        conversation_id=str(uuid.uuid4()),
        employee_id=str(uuid.uuid4()),
    )

    assert recovered == stored
    assert conversation.metadata_["module_key"] == "tournament"
    assert session.statements[0]._for_update_arg is not None
    assert session.statements[0].get_execution_options()["populate_existing"] is True
    assert session.statements[1]._for_update_arg is None
    assert "populate_existing" not in session.statements[1].get_execution_options()


@pytest.mark.asyncio
async def test_invalid_pointer_input_fails_closed() -> None:
    with pytest.raises(TournamentCasePointerError, match="case_id"):
        await set_active_tournament_case_pointer(
            Session(SimpleNamespace(metadata_=None)),
            conversation_id=str(uuid.uuid4()),
            employee_id=str(uuid.uuid4()),
            case_id="not-a-case",
            case_version=1,
            status="draft",
        )


@pytest.mark.asyncio
async def test_missing_owned_conversation_is_rejected() -> None:
    with pytest.raises(TournamentCasePointerError, match="not found"):
        await get_active_tournament_case_pointer(
            Session(None),
            conversation_id=str(uuid.uuid4()),
            employee_id=str(uuid.uuid4()),
        )
