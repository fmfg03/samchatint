"""Real PostgreSQL proof of the CLI's outer transaction and SQL write boundary.

Canonical source/actor/eligibility tests live with the reconciliation owner. Here
that owner is controlled so failures can be injected precisely after real INSERTs;
no production database or private financial fixture is used.
"""

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import devnous.gastos  # noqa: F401
from samchat.budgets import service

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = load("approved_cli_pg", ROOT / "scripts/reconcile_approved_budget_movements.py")
fixture = load(
    "approved_cli_fixture",
    Path(__file__).with_name("test_tournament_weekly_overlay_postgres.py"),
)


@pytest_asyncio.fixture
async def controlled_database():
    server = fixture._test_server()
    name = "approved_cli_test_" + uuid4().hex
    admin = await asyncpg.connect(server.replace("+asyncpg", ""))
    await admin.execute(
        f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8'"
    )
    target = server.rsplit("/", 1)[0] + "/" + name
    engine = create_async_engine(target)
    try:
        async with engine.begin() as conn:
            for table in cli.PROTECTED:
                await conn.execute(
                    text("CREATE TABLE " + table + "(id uuid PRIMARY KEY, value jsonb)")
                )
                await conn.execute(
                    text(
                        "INSERT INTO "
                        + table
                        + "(id,value) VALUES(CAST(:id AS uuid),CAST(:v AS jsonb))"
                    ),
                    {"id": str(uuid4()), "v": '{"protected":true}'},
                )
            await conn.execute(
                text(
                    """CREATE TABLE budget_movement_assignments(
                id uuid PRIMARY KEY,budget_version_id uuid,budget_concept_id uuid,
                budget_line_id uuid,movement_key text,assigned_by_empleado_id uuid,
                created_at timestamptz DEFAULT now(),
                updated_at timestamptz DEFAULT now(),
                UNIQUE(budget_version_id,movement_key))"""
                )
            )
            await conn.execute(
                text(
                    """CREATE TABLE budget_version_audit_log(
                id uuid PRIMARY KEY,budget_version_id uuid,event_type text,
                actor_empleado_id uuid,from_status text,to_status text,note text,
                payload jsonb,created_at timestamptz DEFAULT now())"""
                )
            )
        yield engine, target, name
    finally:
        await engine.dispose()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, 1, 17])
async def test_cli_real_pg_atomicity_and_no_duplicate_history(
    controlled_database, monkeypatch, tmp_path, failure
):
    engine, target, name = controlled_database
    data = copy.deepcopy(cli._FIXED_CONFIG)
    source = {
        "commit": "a" * 40,
        "tree": "b" * 40,
        "files": {"controlled-owner": "c" * 64},
    }
    monkeypatch.setattr(cli, "inputs", lambda: copy.deepcopy(data))
    monkeypatch.setattr(cli, "source_identity", lambda: copy.deepcopy(source))
    monkeypatch.setattr(cli, "RESTORE_DB", name)
    monkeypatch.setattr(cli, "RESTORE_URL", target)
    actual_factory = create_async_engine
    monkeypatch.setattr(cli, "create_async_engine", actual_factory)

    async def actor(session, actor_id, *, lock=False):
        assert actor_id == cli.ACTOR
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"

    async def preview(session, **kwargs):
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        return {
            "plan_sha256": "c" * 64,
            "assignments": copy.deepcopy(data["assignments"]),
        }

    async def recover(session, **kwargs):
        return {"status": "ALREADY_APPLIED", "plan_sha256": "c" * 64}

    async def apply(session, **kwargs):
        assert await session.scalar(text("SHOW transaction_read_only")) == "off"
        if await session.scalar(
            text("SELECT count(*) FROM budget_movement_assignments")
        ):
            return {"status": "ALREADY_APPLIED", "plan_sha256": "c" * 64}
        records = []
        for row in data["assignments"]:
            ident = str(uuid5(NAMESPACE_URL, row["movement_key"]))
            params = {
                "id": ident,
                "budget_version_id": cli.VERSION,
                "budget_concept_id": row["budget_concept_id"],
                "budget_line_id": row["budget_line_id"],
                "movement_key": row["movement_key"],
                "assigned_by_empleado_id": cli.ACTOR,
            }
            await session.execute(
                text(
                    """INSERT INTO budget_movement_assignments(
                id,budget_version_id,budget_concept_id,budget_line_id,movement_key,
                assigned_by_empleado_id) VALUES(CAST(:id AS uuid),
                CAST(:budget_version_id AS uuid),
                CAST(:budget_concept_id AS uuid),
                CAST(:budget_line_id AS uuid),:movement_key,
                CAST(:assigned_by_empleado_id AS uuid)) ON CONFLICT DO NOTHING"""
                ),
                params,
            )
            records.append({**row, "assignment_id": ident})
        await service._audit_budget_event(
            session,
            budget_version_id=cli.VERSION,
            actor_empleado_id=cli.ACTOR,
            event_type=cli.EVENT,
            from_status="approved",
            to_status="approved",
            note=data["reason"],
            payload={
                "plan_sha256": "c" * 64,
                "actor_empleado_id": cli.ACTOR,
                "reason": data["reason"],
                "assignments": records,
            },
        )
        return {"status": "APPLIED", "plan_sha256": "c" * 64}

    monkeypatch.setattr(
        cli,
        "core_module",
        lambda: SimpleNamespace(
            require_reconciliation_actor=actor,
            preview_approved_movement_reconciliation=preview,
            recover_approved_movement_reconciliation=recover,
            apply_approved_movement_reconciliation=apply,
        ),
    )

    async def capture():
        async with AsyncSession(engine) as session:
            return await cli.snapshot(session)

    before = await capture()

    def args(*extra):
        return cli.parser().parse_args(
            ["--output-dir", str(tmp_path), "--restore", *extra]
        )

    proposal = await cli.run(args())
    action = [
        "--apply",
        "--expected-hash",
        proposal["hash"],
        "--expected-commit",
        source["commit"],
    ]
    if failure:
        with pytest.raises(RuntimeError, match="Injected disposable"):
            await cli.run(args(*action, "--fail-after-insert", str(failure)))
        assert await capture() == before
        assert not list(tmp_path.glob("committed-*.json"))
    else:
        result = await cli.run(args(*action))
        assert result["status"] == "COMMITTED_VERIFIED"
        after = await capture()
        assert len(after["budget_movement_assignments"]) == 16
        assert len(after["budget_version_audit_log"]) == 1
        assert cli.verify_preservation(before, after, data, completed=False)[
            "protected_preserved"
        ]
        again = await cli.run(args())
        repeat = await cli.run(
            args(
                "--apply",
                "--expected-hash",
                again["hash"],
                "--expected-commit",
                source["commit"],
            )
        )
        assert repeat["core"]["status"] == "ALREADY_APPLIED"
        assert await capture() == after
