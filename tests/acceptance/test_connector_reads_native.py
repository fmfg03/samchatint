"""Candidate reads through original imports/ORM and disposable native PostgreSQL.

Reuses the AMEX owner's complete migration fixture and disposable Unix socket
admission. Test identities, bindings and MemoryAudit are synthetic; no transport,
OAuth credentials, durable audit or Batiseñal E2E is claimed. Read sessions use a
separate temporary LOGIN role with no password, writes, DDL or elevated flags.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from devnous.gastos import models
from devnous.gastos.routes.admin_routes import _ensure_access_profiles_schema
from devnous.gastos.routes.dependencies import _load_effective_permissions
from devnous.gastos.services.payment_run_service import list_payment_run_items
from samchat.readonly_gateway.canonical_authority import (
    ServerBinding,
    compose_candidate,
)
from samchat.readonly_gateway.contracts import MemoryAudit, ReadError
from samchat.readonly_gateway.expanded import DomainScope
from samchat.readonly_gateway.expanded_adapters import postgres_read_session

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "connector_native_owner_fixture",
    ROOT / "tests/acceptance/test_amex_sat_budget_direct_postgres.py",
)
owner_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner_fixture)
direct_pg = owner_fixture.direct_pg


@pytest_asyncio.fixture
async def candidate(direct_pg):
    assert os.getenv(
        "TEST_AMEX_NATIVE_SOCKET"
    ), "Native candidate acceptance requires the disposable socket"
    pg = direct_pg
    # Setup only: canonical schema owners, not the candidate's read transaction.
    async with pg.factory() as session:
        await _ensure_access_profiles_schema(session)
    from devnous.gastos.services.cfdi_income_bridge_service import (
        ensure_cfdi_income_tournament_assignment_schema,
    )
    from samchat.ar.collection_matches import ensure_ar_collection_match_schema

    async with pg.factory.begin() as session:
        await ensure_ar_collection_match_schema(session)
        await ensure_cfdi_income_tournament_assignment_schema(session)
        await session.execute(
            text(
                "INSERT INTO budget_lines(id,budget_version_id,tournament_id,"
                "tournament_name,concept_name,line_direction,budget_amount) "
                "VALUES(:id,:version,:tournament,'Acceptance direct','Income','income',2000)"
            ),
            {"id": uuid4(), "version": pg.version_id, "tournament": pg.tournament.id},
        )
    documents = [uuid4() for _ in range(4)]
    async with pg.factory.begin() as session:
        for index, identifier in enumerate(documents):
            session.add(
                models.Documento(
                    id=identifier,
                    empleado_id=pg.actor.id,
                    tipo="SOLICITUD",
                    numero_referencia=f"isolated-{index}",
                    estado="pagado" if index == 2 else "aprobado",
                    creado_en=datetime(2026, 10, 2, tzinfo=timezone.utc),
                    fecha_pago=date(2026, 10, 15),
                    fecha_pago_efectiva=date(2026, 11, 1) if index == 2 else None,
                    torneo_id=pg.tournament.id,
                    monto_total=None if index == 1 else 125,
                    currency="MXN",
                )
            )
    role = "connector_fixture_" + uuid4().hex
    # No password or persistent account: this role exists only for this fixture.
    await pg.conn.execute(
        f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
        "NOINHERIT NOREPLICATION NOBYPASSRLS"
    )
    engine = None
    try:
        await pg.conn.execute(f'GRANT USAGE ON SCHEMA "{pg.schema}" TO "{role}"')
        tables = (
            "documentos,access_profiles,empleado_access_profiles,access_control_rules,"
            "proveedores_clientes,payment_run_closures,payment_run_closure_items,"
            "amex_statement_imports,amex_statement_direct_state,"
            "amex_statement_direct_contexts,budget_versions,budget_lines,budget_concepts,"
            "budget_approvals,budget_version_audit_log,budget_line_monthly_plan,"
            "budget_cfdi_income_links,cfdi_income_tournament_assignments,ar_collection_matches,"
            "expense_reports,cfdi_reports,tournaments,adjuntos,accounting_polizas,"
            "accounting_poliza_lines,amex_recognition_consumptions,rfc_configs"
        )
        await pg.conn.execute(f'GRANT SELECT ON {tables} TO "{role}"')
        await pg.conn.execute(
            "GRANT SELECT(id,nombre,activo,rol,departamento,correo) "
            f'ON empleados TO "{role}"'
        )
        url = pg.factory.kw["bind"].url.set(username=role)
        engine = create_async_engine(
            url,
            connect_args={"server_settings": {"search_path": pg.schema}},
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        statements = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def record_sql(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        @asynccontextmanager
        async def scope(*, authority=False):
            async with postgres_read_session(
                factory, expected_role=role, authority=authority
            ) as session:
                yield session

        binding = ServerBinding(
            "synthetic-native-binding",
            pg.actor.id,
            "isolated-native-test",
            "test-generation",
            "test-policy",
            datetime.now(timezone.utc) + timedelta(hours=1),
            frozenset(),
        )
        state = SimpleNamespace(
            pg=pg,
            role=role,
            factory=factory,
            scope=scope,
            statements=statements,
            binding=binding,
            documents=documents,
            audit=MemoryAudit(),
            data_opens=0,
        )

        @asynccontextmanager
        async def data_scope():
            state.data_opens += 1
            async with scope() as session:
                yield session

        def compose(data_session_scope=data_scope):
            return compose_candidate(
                binding_provider=lambda: state.binding,
                authority_session_scope=lambda: scope(authority=True),
                data_session_scope=data_session_scope,
                audit=state.audit,
                source_commit="a" * 40,
            )

        state.compose = compose
        yield state
    finally:
        if engine is not None:
            await engine.dispose()
        await pg.conn.execute(f'DROP OWNED BY "{role}"')
        await pg.conn.execute(f'DROP ROLE "{role}"')


def admit(state, dataset, identifiers, *, version=""):
    state.binding = replace(
        state.binding,
        ceilings=frozenset(
            {
                DomainScope(
                    dataset,
                    str(state.pg.tournament.id),
                    frozenset(map(str, identifiers)),
                    version,
                )
            }
        ),
    )
    return {
        "dataset": dataset,
        "object_ids": list(map(str, identifiers)),
        "tournament_id": str(state.pg.tournament.id),
        "date_from": "2026-10-01",
        "date_to": "2026-11-01",
        **({"version_id": version} if version else {}),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("dataset", ["solicitud", "payment_run"])
async def test_native_exact_scope_missing_amount_and_zero_writes(candidate, dataset):
    state = candidate
    args = admit(state, dataset, state.documents[:3])
    gateway = state.compose()
    result = await gateway.query(args)
    rows = {row["object_id"]: row for row in result["rows"]}
    assert set(rows) == set(map(str, state.documents[:3]))
    assert Decimal(rows[str(state.documents[0])]["amount"]) == Decimal("125")
    assert rows[str(state.documents[1])]["amount"] is None
    if dataset == "payment_run":
        assert rows[str(state.documents[1])]["amount_missing"] is True
        # The candidate's "todas" contract is by scheduled date, even for a paid
        # row. Only the owner's separate "pagadas" selector uses effective date.
        assert rows[str(state.documents[2])]["effective_payment_date"] == "2026-11-01"
        assert result["period"]["basis"] == "scheduled_payment_date_inclusive_owner"
    assert state.audit.events[-1]["decision"] == "allow"
    assert not any(
        token in sql.upper()
        for sql in state.statements
        for token in (
            "FOR UPDATE",
            "PASSWORD_HASH",
            "INSERT ",
            "UPDATE ",
            "DELETE ",
            "CREATE ",
            "ALTER ",
        )
    )
    opens = state.data_opens
    with pytest.raises(ReadError, match="NOT_FOUND_OR_NOT_VISIBLE"):
        await gateway.query({**args, "object_ids": [str(state.documents[3])]})
    assert state.data_opens == opens
    assert state.audit.events[-1]["decision"] == "deny"


@pytest.mark.asyncio
async def test_native_payment_empty_scope_and_legacy_schema_default(candidate):
    state = candidate
    async with state.scope() as session:
        before = len(state.statements)
        assert (
            await list_payment_run_items(session, ensure_schema=False, document_ids=[])
            == []
        )
        assert len(state.statements) == before
        assert (
            await list_payment_run_items(
                session,
                status_filter="pagadas",
                ensure_schema=False,
                document_ids=[str(state.documents[2])],
                tournament_id=str(state.pg.tournament.id),
                date_from=date(2026, 10, 1),
                date_to=date(2026, 10, 31),
            )
            == []
        )
    # The default still attempts its schema owner; the read role must reject it.
    with pytest.raises(DBAPIError):
        async with state.scope() as session:
            await list_payment_run_items(session)


@pytest.mark.asyncio
async def test_native_profile_lookup_failure_is_closed(candidate):
    state = candidate
    args = admit(state, "solicitud", state.documents[:1])
    await state.pg.conn.execute(f'REVOKE SELECT ON access_profiles FROM "{state.role}"')
    with pytest.raises(DBAPIError):
        async with state.scope(authority=True) as session:
            await _load_effective_permissions(
                session, state.pg.actor.id, fail_closed=True
            )
    with pytest.raises(ReadError, match="AUTH_REQUIRED"):
        await state.compose().query(args)
    assert state.data_opens == 0
    assert state.audit.events[-1]["decision"] == "deny"


@pytest.mark.asyncio
async def test_native_revocation_outside_data_snapshot_prevents_delivery(candidate):
    state = candidate
    args = admit(state, "solicitud", state.documents[:1])
    entered, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def paused_data():
        async with state.scope() as session:
            yield session
            entered.set()
            await release.wait()
            # The data snapshot still sees the pre-revocation row. Authority must
            # use another transaction, not accidentally revalidate this snapshot.
            assert (
                await session.scalar(
                    text("SELECT activo FROM empleados WHERE id=:id"),
                    {"id": state.pg.actor.id},
                )
                is True
            )

    task = asyncio.create_task(state.compose(paused_data).query(args))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await state.pg.conn.execute(
            "UPDATE empleados SET activo=false WHERE id=$1", state.pg.actor.id
        )
        release.set()
        with pytest.raises(ReadError, match="AUTH_REQUIRED"):
            await asyncio.wait_for(task, timeout=5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert state.audit.events[-1]["decision"] == "deny"


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["cancel", "timeout"])
async def test_native_cancellation_closes_transaction_and_role_cannot_write(
    candidate, termination
):
    state = candidate
    started = asyncio.Event()

    async def long_read():
        async with state.scope() as session:
            started.set()
            await session.execute(text("SELECT pg_sleep(10)"))

    task = asyncio.create_task(long_read())
    await started.wait()
    async with asyncio.timeout(3):
        while not await state.pg.conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE usename=$1 "
            "AND state='active' AND query LIKE 'SELECT pg_sleep%'",
            state.role,
        ):
            await asyncio.sleep(0.01)
    if termination == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        # The real PostgreSQL statement_timeout must abort the running query.
        with pytest.raises(DBAPIError, match="statement timeout"):
            await asyncio.wait_for(task, timeout=4)
    assert (
        await state.pg.conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE usename=$1 AND xact_start IS NOT NULL",
            state.role,
        )
        == 0
    )
    with pytest.raises(DBAPIError):
        async with state.scope() as session:
            await session.execute(text("UPDATE documentos SET monto_total=0"))
    with pytest.raises(DBAPIError):
        async with state.scope() as session:
            await session.execute(text("SELECT password_hash FROM empleados"))
    with pytest.raises(ReadError, match="READONLY_ROLE_UNPROVEN"):
        async with postgres_read_session(
            state.pg.factory, expected_role=state.pg.factory.kw["bind"].url.username
        ):
            pytest.fail("Superuser cannot be admitted as the read role")
    async with state.scope() as session:
        assert await session.scalar(text("SELECT 1")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("dataset", ["budget.snapshot", "ar.summary"])
async def test_native_budget_ar_canonical_version_and_scope(candidate, dataset):
    state = candidate
    version = str(state.pg.version_id)
    args = admit(state, dataset, [version], version=version)
    # Avoid the October document cohort: January has a real budget but no
    # documentary movements in this fixture. Absence is not an invented payment.
    args.update(date_from="2026-01-01", date_to="2026-02-01")
    gateway = state.compose()
    if dataset == "budget.snapshot":
        # A Finance role alone does not pass the existing budget-view guard.
        with pytest.raises(ReadError, match="NOT_FOUND_OR_NOT_VISIBLE"):
            await gateway.query(args)
        assert state.data_opens == 0
        await state.pg.conn.execute(
            "UPDATE empleados SET departamento='Dirección synthetic' WHERE id=$1",
            state.pg.actor.id,
        )
    result = await gateway.query(args)
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["object_id"] == version
    if dataset == "budget.snapshot":
        assert Decimal(row["budget_total"]) == Decimal("1000")
        assert Decimal(row["actual_total"]) == Decimal("0")
    else:
        assert Decimal(row["expected_income_total"]) == Decimal("2000")
        assert Decimal(row["collected_total"]) == Decimal("0")
    opens = state.data_opens
    with pytest.raises(ReadError, match="NOT_FOUND_OR_NOT_VISIBLE"):
        await gateway.query({**args, "tournament_id": str(uuid4())})
    assert state.data_opens == opens
    await state.pg.conn.execute(
        "UPDATE budget_versions SET status='draft' WHERE id=$1", state.pg.version_id
    )
    with pytest.raises(ReadError, match="NOT_FOUND_OR_NOT_VISIBLE"):
        await gateway.query(args)


async def import_statement(state, mode):
    pg = state.pg
    if mode == "direct":
        result = await owner_fixture._direct_import(
            pg, b"Date,Description,Amount\n2026-10-02,SYNTHETIC MERCHANT,-116.00\n"
        )
        assert result.status in {"created", "imported"}, result.reason
        return result.statement.id
    from devnous.gastos.services.amex_statement_import_service import (
        import_amex_statement,
    )

    async with pg.factory.begin() as session:
        result = await import_amex_statement(
            session,
            pg.actor,
            pg.card.id,
            pg.owner.id,
            date(2026, 10, 1),
            date(2026, 10, 31),
            "synthetic.csv",
            b"Date,Description,Amount\n2026-10-02,SYNTHETIC MERCHANT,116.00\n",
        )
        assert result["status"] == "imported", result
        return result["import"].id


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "direct"])
async def test_native_amex_original_schema_orm_and_explicit_denial(candidate, mode):
    state = candidate
    identifier = await import_statement(state, mode)
    args = admit(state, "amex.statement", [identifier])
    gateway = state.compose()
    result = await gateway.query(args)
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["object_id"] == str(identifier) and row["workflow_mode"] == mode
    assert row["total_rows"] == 1 and row["imported_count"] == 1
    assert not ({"card_account_id", "filename", "employee_id", "content"} & set(row))
    assert all("FOR UPDATE" not in sql.upper() for sql in state.statements)
    async with state.pg.factory.begin() as session:
        session.add(
            models.AccessControlRule(
                tool_key="amex_direct" if mode == "direct" else "admin.contabilidad",
                action_key="ver",
                role_key="finanzas",
                area_key="",
                allowed=False,
                active=True,
            )
        )
    with pytest.raises(ReadError):
        await gateway.query(args)
    assert state.audit.events[-1]["decision"] == "deny"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["context", "schema", "company", "card", "fiscal", "actor"]
)
async def test_native_amex_missing_context_or_schema_never_delivers(candidate, failure):
    state = candidate
    identifier = await import_statement(state, "direct")
    args = admit(state, "amex.statement", [identifier])
    if failure == "context":
        state.pg.monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps([]))
    elif failure == "schema":
        # Break only the disposable fixture, so the real schema guard must deny.
        await state.pg.conn.execute(
            "ALTER TABLE amex_statement_source_files RENAME COLUMN mime_type TO missing_mime_type"
        )
    else:
        contexts = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
        replacement = {
            "company": ("company_code", "OTHER_SYNTHETIC_COMPANY"),
            "card": ("card_account_ids", [str(uuid4())]),
            "fiscal": ("receiver_rfc", "AAA010101AAA"),
            "actor": ("actor_scopes", []),
        }
        key, value = replacement[failure]
        contexts[0][key] = value
        state.pg.monkeypatch.setenv(
            "AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(contexts)
        )
    with pytest.raises(ReadError, match="AUTH_REQUIRED"):
        await state.compose().query(args)
    assert state.data_opens == 0
    assert state.audit.events[-1]["decision"] == "deny"
