import pytest
from backend_native_fixtures import app as app_fixture
from backend_native_fixtures import graph as graph_fixture
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from samchat.batisenal_policy_fence import PolicyFenceUnavailable
from samchat.batisenal_synthetic_protocol import SyntheticProtocol

app, graph = app_fixture, graph_fixture


async def test_hook_precedes_owner_sql_and_marks_current_transaction(graph):
    with graph.engine.begin() as c:
        assert (
            c.execute(
                text(
                    "SELECT transaction_id=pg_current_xact_id() FROM"
                    " support_batisenal_v01_transactions WHERE"
                    " transaction_id=pg_current_xact_id()"
                )
            ).scalar_one()
            is True
        )
        c.execute(
            text("UPDATE documentos SET estado='enviado' WHERE id=:id"),
            {"id": graph.report},
        )
        assert (
            c.execute(
                text("SELECT revision FROM support_batisenal_v01_case_binding")
            ).scalar_one()
            == 1
        )
    with graph.engine.begin() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_v01_transactions")
            ).scalar_one()
            == 2
        )
        assert (
            c.execute(
                text("SELECT revision FROM support_batisenal_v01_case_binding")
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE documentos SET estado='enviado'",
        "DELETE FROM adjuntos",
        "TRUNCATE adjuntos",
        "UPDATE access_profiles SET active=false",
    ],
)
async def test_uncomposed_writer_rejected_without_late_acquisition(graph, statement):
    with pytest.raises(DBAPIError, match="V01_WRITER_PROTOCOL_REQUIRED"):
        with graph.raw.begin() as c:
            c.execute(text(statement))
    with graph.engine.begin() as c:
        assert (
            c.execute(
                text("SELECT revision FROM support_batisenal_v01_case_binding")
            ).scalar_one()
            == 0
        )


async def test_async_session_and_savepoint_rollback_keep_outer_guards(graph):
    async with graph.app.state.maker() as session:
        await session.execute(text("SELECT 1"))
        nested = await session.begin_nested()
        await session.execute(text("UPDATE documentos SET estado='enviado'"))
        await nested.rollback()
        assert (
            await session.execute(
                text("SELECT revision FROM support_batisenal_v01_case_binding")
            )
        ).scalar_one() == 0
        await session.execute(text("UPDATE documentos SET estado='borrador'"))
        await session.commit()
    with graph.engine.begin() as c:
        assert (
            c.execute(
                text("SELECT revision FROM support_batisenal_v01_case_binding")
            ).scalar_one()
            == 1
        )


async def test_feature_disabled_and_other_schema_rejected(graph):
    with pytest.raises(PolicyFenceUnavailable, match="ACTIVATION_REQUIRED"):
        SyntheticProtocol(graph.schema, graph.case_id).install(
            graph.raw, synchronous=True
        )
    with pytest.raises(PolicyFenceUnavailable, match="SCHEMA_REQUIRED"):
        SyntheticProtocol("public", graph.case_id, enabled=True).install(
            graph.raw, synchronous=True
        )


async def test_native_fence_missing_and_contention_are_not_authority(app, graph):
    # Separate fixture schema without a fence: optional legacy paths stay inert.
    from sqlalchemy import create_engine

    from samchat.batisenal_policy_fence import PolicyFenceBusy, acquire_policy_fence

    absent = create_engine(
        app.state.native_sync_url, connect_args={"options": "-c search_path=pg_catalog"}
    )
    try:
        with absent.begin() as c:
            assert acquire_policy_fence(c) is None
            with pytest.raises(PolicyFenceUnavailable, match="FENCE_UNAVAILABLE"):
                acquire_policy_fence(c, required=True)
        with graph.raw.begin() as held:
            held.execute(
                text(
                    "SELECT epoch FROM support_batisenal_v01_policy_guard WHERE"
                    " singleton FOR UPDATE"
                )
            )
            with pytest.raises(PolicyFenceBusy, match="POLICY_BUSY"):
                with graph.raw.begin() as other:
                    acquire_policy_fence(other, required=True, nowait=True)
        with graph.raw.execution_options(isolation_level="AUTOCOMMIT").connect() as c:
            with pytest.raises(PolicyFenceUnavailable, match="TRANSACTION_REQUIRED"):
                acquire_policy_fence(c, required=True)
        with graph.raw.execution_options(
            isolation_level="REPEATABLE READ"
        ).begin() as c:
            with pytest.raises(PolicyFenceUnavailable, match="READ_COMMITTED_REQUIRED"):
                acquire_policy_fence(c, required=True)
    finally:
        absent.dispose()


async def test_protocol_rejects_wrong_runtime_schema_and_autocommit(graph):
    from sqlalchemy import create_engine

    wrong = create_engine(
        graph.app.state.native_sync_url,
        connect_args={"options": "-c search_path=pg_catalog"},
    )
    graph.protocol.install(wrong, synchronous=True)
    try:
        with pytest.raises(PolicyFenceUnavailable, match="SCHEMA_REQUIRED"):
            with wrong.begin():
                pass
    finally:
        wrong.dispose()
    with pytest.raises(PolicyFenceUnavailable, match="TRANSACTION_REQUIRED"):
        with graph.engine.execution_options(
            isolation_level="AUTOCOMMIT"
        ).connect() as c:
            c.execute(text("SELECT 1"))


def test_non_postgres_and_invalid_binding_never_install_protocol():
    from sqlalchemy import create_engine

    from samchat.batisenal_policy_fence import acquire_policy_fence

    engine = create_engine("sqlite://")
    try:
        with engine.begin() as c:
            assert acquire_policy_fence(c) is None
            with pytest.raises(PolicyFenceUnavailable, match="POSTGRESQL_REQUIRED"):
                acquire_policy_fence(c, required=True)
        with pytest.raises(PolicyFenceUnavailable, match="POSTGRESQL_REQUIRED"):
            SyntheticProtocol(
                "bs_test_v01_" + "a" * 32,
                "11111111-1111-1111-1111-111111111111",
                enabled=True,
            ).install(engine, synchronous=True)
        with pytest.raises(ValueError):
            SyntheticProtocol(
                "bs_test_v01_" + "a" * 32, "invalid", enabled=True
            ).install(engine, synchronous=True)
    finally:
        engine.dispose()
