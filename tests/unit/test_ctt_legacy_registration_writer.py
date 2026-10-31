"""Explicit legacy adapter defaults and privacy; no v2 authority is issued."""

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
import traceback
from uuid import UUID

import pytest
from sqlalchemy.exc import StatementError

from devnous.copa_telmex.legacy_registration_writer import (
    LegacyRegistrationWriter,
    materialized_insert_values,
    uses_legacy_sql_boundary,
)
from devnous.copa_telmex.models import Player, Team
from devnous.copa_telmex.persistence_authority import PersistenceAuthorityDenied


def test_materializes_existing_python_defaults_without_new_business_defaults():
    team = Team(name="Synthetic", telegram_chat_id=0)
    values = materialized_insert_values(team)
    assert set(values) == set(Team.__table__.columns.keys())
    assert UUID(values["id"]) == team.id
    assert datetime.fromisoformat(values["created_at"]).tzinfo is None
    assert (
        values["postcommit_revision"]
        == Team.__table__.columns.postcommit_revision.default.arg
    )
    assert values["contact_email"] is None
    assert values["postcommit_snapshot_hash"] == "sha256:" + "0" * 64
    player = Player(team_id=team.id, first_name="Synthetic", last_name="Example")
    player_values = materialized_insert_values(player)
    assert player_values["curp_valid"] is False
    assert player_values["governance_state"] == "LEGACY_ACTIVE"


def test_never_falls_back_to_raw_postgres_writes_or_unknown_models():
    for session in (
        object(),
        SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))),
    ):
        assert not uses_legacy_sql_boundary(session)
        with pytest.raises(PersistenceAuthorityDenied):
            LegacyRegistrationWriter(session)
    with pytest.raises(PersistenceAuthorityDenied):
        materialized_insert_values(object())


@pytest.mark.asyncio
async def test_personal_statement_parameters_are_not_propagated_or_logged():
    sentinel = "PRIVATE_SYNTHETIC_PERSON_NOT_FOR_LOGS"

    class Session:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        no_autoflush = nullcontext()

        async def execute(self, statement, parameters=None):
            raise StatementError(
                sentinel, str(statement), parameters, RuntimeError(sentinel)
            )

    with pytest.raises(PersistenceAuthorityDenied) as denied:
        await LegacyRegistrationWriter(Session()).insert(Team(name=sentinel))
    rendered = "".join(traceback.format_exception(denied.value))
    assert sentinel not in rendered
    assert "legacy registration write denied" in rendered
