"""Attack the explicit account-treatment owner contract in disposable PostgreSQL."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from devnous.gastos.services.amex_direct_schema_service import (
    inspect_amex_direct_schema,
)

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "account_schema_owner_fixture",
    ROOT / "tests/acceptance/test_amex_sat_budget_direct_postgres.py",
)
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
direct_pg = owner.direct_pg
install_account_treatment_policy = owner.install_account_treatment_policy
FORWARD = ROOT / "database/migrations/20261023_amex_direct_account_treatments.sql"
ROLLBACK = (
    ROOT / "database/migrations/20261023_amex_direct_account_treatments_rollback.sql"
)


@pytest.mark.asyncio
async def test_complete_owner_admission_and_empty_rollback(direct_pg):
    pg = direct_pg
    async with pg.factory() as session:
        assert (await inspect_amex_direct_schema(session))["ready"]
    await pg.conn.execute(ROLLBACK.read_text())
    assert not await pg.conn.fetchval(
        "SELECT to_regclass('amex_direct_account_treatment_decisions')"
    )
    assert not await pg.conn.fetchval(
        "SELECT to_regclass('amex_direct_account_treatment_policies')"
    )
    await pg.conn.execute(FORWARD.read_text())
    async with pg.factory() as session:
        assert (await inspect_amex_direct_schema(session))["ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["insert", "update", "delete", "truncate"])
async def test_application_dml_cannot_self_certify_policy(direct_pg, operation):
    pg = direct_pg
    policy_id = await install_account_treatment_policy(pg)
    with pytest.raises(asyncpg.CheckViolationError, match="immutable"):
        async with pg.conn.transaction():
            if operation == "insert":
                await pg.conn.execute(
                    "INSERT INTO amex_direct_account_treatment_policies(id) VALUES($1)",
                    uuid4(),
                )
            elif operation == "update":
                await pg.conn.execute(
                    "UPDATE amex_direct_account_treatment_policies "
                    "SET config_hash=$1 WHERE id=$2",
                    "f" * 64,
                    policy_id,
                )
            elif operation == "delete":
                await pg.conn.execute(
                    "DELETE FROM amex_direct_account_treatment_policies WHERE id=$1",
                    policy_id,
                )
            else:
                await pg.conn.execute(
                    "TRUNCATE amex_direct_account_treatment_policies CASCADE"
                )
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_account_treatment_policies"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_policy_evidence_prevents_empty_layer_rollback(direct_pg):
    pg = direct_pg
    await install_account_treatment_policy(pg)
    with pytest.raises(asyncpg.RaiseError, match="evidence prevents rollback"):
        await pg.conn.execute(ROLLBACK.read_text())
    await pg.conn.execute("ROLLBACK")
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_account_treatment_policies"
        )
        == 1
    )


async def partner_source(pg):
    import json
    import os

    from devnous.gastos import models
    from devnous.gastos.services import amex_direct_account_treatment_service as service

    debtor = models.CuentaContable(
        id=uuid4(),
        codigo="1170-002-123",
        nombre="Authorized synthetic partner",
        tipo="deudor",
        activo=True,
    )
    rogue = models.CuentaContable(
        id=uuid4(),
        codigo="1170-002-124",
        nombre="Unrelated synthetic partner",
        tipo="deudor",
        activo=True,
    )
    async with pg.factory.begin() as session:
        session.add_all([debtor, rogue])
    configs = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
    cfg = configs[0]
    cfg["partner_debtor_account_ids"] = [str(debtor.id)]
    cfg["benefit_budget_line_ids"] = [str(pg.line_id)]
    for scope in cfg["actor_scopes"]:
        scope["partner_debtor_account_ids"] = [str(debtor.id)]
        scope["benefit_budget_line_ids"] = [str(pg.line_id)]
    pg.monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(configs))
    await install_account_treatment_policy(pg, cfg)
    imported = await owner._direct_import(
        pg, b"Date,Description,Amount\n2026-10-02,PARTNER PERSONAL,-939.00\n"
    )
    view = await owner._direct_current(pg, imported.statement.id)
    item = view.items[0]
    request = dict(
        actor_id=pg.actor.id,
        import_id=view.statement.id,
        item_ids=[item.id],
        treatment="partner_receivable",
        debtor_account_id=debtor.id,
        reason="Explicit synthetic partner decision",
        expected_versions={str(item.id): item.source_version},
    )
    async with pg.factory() as session:
        preview = await service.preview_direct_account_treatment(session, **request)
    assert preview.status == "preview", preview.reason
    async with pg.factory.begin() as session:
        confirmed = await service.confirm_direct_account_treatment(
            session,
            **request,
            expected_preview_hash=preview.progress["preview_hash"],
            idempotency_key="schema-partner",
            confirmed=True,
        )
    assert confirmed.status == "ready", confirmed.reason
    row = await pg.conn.fetchrow("SELECT * FROM amex_direct_items WHERE id=$1", item.id)
    return row, debtor, rogue


async def append_direct_decision(pg, item, *, forged_account=None, audit=True):
    import hashlib
    import json

    prior = await pg.conn.fetchrow(
        "SELECT * FROM amex_direct_account_treatment_decisions WHERE id=$1",
        item["account_treatment_decision_id"],
    )
    data = dict(prior)
    data.update(
        id=uuid4(),
        revision=prior["revision"] + 1,
        previous_decision_id=prior["id"],
        source_version=item["source_version"],
        source_hash=item["source_hash"],
        request_id="sql-probe-" + uuid4().hex,
        idempotency_key=uuid4().hex * 2,
        payload_hash="f" * 64,
        versions_json=json.dumps(
            {
                "confirmed": True,
                "expected_versions": {str(item["id"]): item["source_version"]},
            }
        ),
    )
    if forged_account is not None:
        auth = json.loads(data["authorization_json"])
        config = json.loads(auth["config_text"])
        config["partner_debtor_account_ids"].append(str(forged_account.id))
        for scope in config["actor_scopes"]:
            scope["partner_debtor_account_ids"].append(str(forged_account.id))
        auth["config_text"] = json.dumps(config, sort_keys=True, separators=(",", ":"))
        auth["config_hash"] = hashlib.sha256(auth["config_text"].encode()).hexdigest()
        data["authorization_json"] = json.dumps(auth)
        data["debtor_account_id"] = forged_account.id
        accounts = json.loads(data["account_snapshot_json"])
        accounts["debtor"] = {
            "id": str(forged_account.id),
            "code": forged_account.codigo,
            "name": forged_account.nombre,
            "active": True,
            "type": forged_account.tipo,
        }
        data["account_snapshot_json"] = json.dumps(accounts)
    columns = list(data)
    await pg.conn.execute(
        "INSERT INTO amex_direct_account_treatment_decisions("
        + ",".join(columns)
        + ") VALUES("
        + ",".join(f"${i}" for i in range(1, len(columns) + 1))
        + ")",
        *data.values(),
    )
    await pg.conn.execute(
        "UPDATE amex_direct_items SET account_treatment_decision_id=$1,"
        "source_version=source_version+1 WHERE id=$2",
        data["id"],
        item["id"],
    )
    if forged_account is not None:
        await pg.conn.execute(
            "UPDATE expense_reports SET cuenta_contable_id=$1 WHERE id=$2",
            forged_account.id,
            item["imported_expense_id"],
        )
    if audit:
        payload = {
            "account_treatment_decision_id": str(data["id"]),
            "confirmed": True,
            "request_payload_hash": data["payload_hash"],
            "reason": data["reason"],
        }
        await pg.conn.execute(
            "INSERT INTO amex_direct_events(id,item_id,action,actor_id,source_version,"
            "source_hash,payload_hash,payload_json) SELECT $1,id,'account_treatment_set',"
            "$2,source_version,source_hash,$3,$4 FROM amex_direct_items WHERE id=$5",
            uuid4(),
            pg.actor.id,
            "e" * 64,
            json.dumps(payload),
            item["id"],
        )
    await pg.conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return data["id"]


@pytest.mark.asyncio
async def test_forged_config_hash_cannot_expand_installed_grants(direct_pg):
    pg = direct_pg
    item, debtor, rogue = await partner_source(pg)
    with pytest.raises(asyncpg.CheckViolationError, match="bank/config/actor evidence"):
        async with pg.conn.transaction():
            await append_direct_decision(pg, item, forged_account=rogue)
    assert (
        await pg.conn.fetchval(
            "SELECT account_treatment_decision_id FROM amex_direct_items WHERE id=$1",
            item["id"],
        )
        == item["account_treatment_decision_id"]
    )
    assert (
        await pg.conn.fetchval(
            "SELECT cuenta_contable_id FROM expense_reports WHERE id=$1",
            item["imported_expense_id"],
        )
        == debtor.id
    )
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_account_treatment_decisions"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_valid_destination_still_requires_confirmed_audit_event(direct_pg):
    pg = direct_pg
    item, *_ = await partner_source(pg)
    with pytest.raises(asyncpg.CheckViolationError, match="confirmed audit event"):
        async with pg.conn.transaction():
            await append_direct_decision(pg, item, audit=False)
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_account_treatment_decisions"
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["mutate", "clear_head", "change_anchor"])
async def test_decision_and_bank_account_destination_are_guarded(direct_pg, operation):
    pg = direct_pg
    item, _, rogue = await partner_source(pg)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pg.conn.transaction():
            if operation == "mutate":
                await pg.conn.execute(
                    "UPDATE amex_direct_account_treatment_decisions SET reason='overwrite' WHERE id=$1",
                    item["account_treatment_decision_id"],
                )
            elif operation == "clear_head":
                await pg.conn.execute(
                    "UPDATE amex_direct_items SET account_treatment_decision_id=NULL WHERE id=$1",
                    item["id"],
                )
            else:
                await pg.conn.execute(
                    "UPDATE expense_reports SET cuenta_contable_id=$1 WHERE id=$2",
                    rogue.id,
                    item["imported_expense_id"],
                )
                await pg.conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["source", "project", "classification", "new_set"]
)
async def test_stale_policy_head_does_not_authorize_unrelated_writes(
    direct_pg, operation
):
    """Withdrawn grants permit exact owner cleanup, never fresh financial writes."""
    import json
    import os

    pg = direct_pg
    item, *_ = await partner_source(pg)
    configs = json.loads(os.environ["AMEX_DIRECT_AUTHORIZED_CONTEXTS"])
    cfg = configs[0]
    cfg["partner_debtor_account_ids"] = []
    for scope in cfg["actor_scopes"]:
        scope["partner_debtor_account_ids"] = []
    pg.monkeypatch.setenv("AMEX_DIRECT_AUTHORIZED_CONTEXTS", json.dumps(configs))
    await install_account_treatment_policy(pg, cfg)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pg.conn.transaction():
            if operation == "new_set":
                await append_direct_decision(pg, item)
            else:
                changes = {
                    "source": "source_hash=repeat('f',64)",
                    "project": "project_code='FORGED'",
                    "classification": "budget_status='sent'",
                }
                await pg.conn.execute(
                    "UPDATE amex_direct_items SET "
                    + changes[operation]
                    + " WHERE id=$1",
                    item["id"],
                )
                await pg.conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_account_treatment_decisions"
        )
        == 1
    )
    current = await pg.conn.fetchrow(
        "SELECT * FROM amex_direct_items WHERE id=$1", item["id"]
    )
    assert dict(current) == dict(item)


@pytest.mark.asyncio
async def test_unconfigured_finance_actor_cannot_revoke_original_decision(direct_pg):
    """An active Finance role alone cannot revoke another scope's evidence."""
    import json

    from devnous.gastos import models

    pg = direct_pg
    item, *_ = await partner_source(pg)
    outsider = models.Empleado(
        id=uuid4(), nombre="Unconfigured synthetic Finance", rol="finanzas", activo=True
    )
    async with pg.factory.begin() as session:
        session.add(outsider)
    prior = await pg.conn.fetchrow(
        "SELECT * FROM amex_direct_account_treatment_decisions WHERE id=$1",
        item["account_treatment_decision_id"],
    )
    data = dict(prior)
    data.update(
        id=uuid4(),
        action="revoke",
        actor_id=outsider.id,
        revision=prior["revision"] + 1,
        previous_decision_id=prior["id"],
        source_version=item["source_version"],
        source_hash=item["source_hash"],
        request_id="unconfigured-revoke-" + uuid4().hex,
        idempotency_key=uuid4().hex * 2,
        versions_json=json.dumps(
            {
                "confirmed": True,
                "expected_versions": {str(item["id"]): item["source_version"]},
            }
        ),
    )
    columns = list(data)
    with pytest.raises(asyncpg.CheckViolationError, match="configured Finance"):
        async with pg.conn.transaction():
            await pg.conn.execute(
                "INSERT INTO amex_direct_account_treatment_decisions("
                + ",".join(columns)
                + ") VALUES("
                + ",".join(f"${i}" for i in range(1, len(columns) + 1))
                + ")",
                *data.values(),
            )
    assert (
        await pg.conn.fetchval(
            "SELECT count(*) FROM amex_direct_account_treatment_decisions"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_empty_rollback_waits_then_refuses_concurrent_owner_policy(direct_pg):
    """The empty check must execute after the serialized owner install commits."""
    import asyncio

    pg = direct_pg
    url = pg.factory.kw["bind"].url
    peer = await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database=url.database,
    )
    pending = None
    try:
        await peer.execute(f'SET search_path TO "{pg.schema}"')
        peer_pid = await peer.fetchval("SELECT pg_backend_pid()")
        async with pg.conn.transaction():
            policy_id = await install_account_treatment_policy(pg)
            pending = asyncio.create_task(peer.execute(ROLLBACK.read_text()))
            for _ in range(100):
                blocked = await pg.conn.fetchval(
                    "SELECT cardinality(pg_blocking_pids($1))>0", peer_pid
                )
                if blocked:
                    break
                await asyncio.sleep(0.01)
            assert blocked, "Rollback must wait for owner policy transaction"
            assert not pending.done()
        with pytest.raises(asyncpg.RaiseError, match="evidence prevents rollback"):
            await pending
        await peer.execute("ROLLBACK")
        assert (
            await pg.conn.fetchval(
                "SELECT id FROM amex_direct_account_treatment_policies WHERE id=$1",
                policy_id,
            )
            == policy_id
        )
        async with pg.factory() as session:
            assert (await inspect_amex_direct_schema(session))["ready"]
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await peer.close()
