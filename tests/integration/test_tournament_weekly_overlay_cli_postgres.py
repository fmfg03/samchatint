"""End-to-end operator CLI checks against a disposable PostgreSQL database.

These tests reuse the local-only disposable fixture. Production runtime/source
identity and exact selectors are replaced only inside each test using pytest's
scoped monkeypatch; the CLI's actual production guard remains unchanged.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture_module = _load(
    "weekly_fixture_cli",
    Path(__file__).with_name("test_tournament_weekly_overlay_postgres.py"),
)
database = fixture_module.database
cli = _load(
    "weekly_cli_under_test", ROOT / "scripts/apply_tournament_weekly_overlay.py"
)


@pytest.fixture
def cli_setup(monkeypatch, database, tmp_path):
    url = database.url
    assert url.host == "127.0.0.1"
    assert url.port in {55471, 55479}
    assert url.database.startswith("weekly_overlay_test_")
    source = {"commit": "a" * 40, "code_sha256": "b" * 64, "files": {}}
    targets = (
        {
            "name": "Liga",
            "id": fixture_module.LIGA,
            "week": 40,
            "line_count": 2,
            "total": "584640.00",
        },
        {
            "name": "Homeless",
            "id": fixture_module.HOMELESS,
            "week": 52,
            "line_count": 1,
            "total": "99.91",
        },
    )
    monkeypatch.setattr(cli, "VERSION", fixture_module.VERSION)
    monkeypatch.setattr(cli, "ACTOR", fixture_module.ACTOR)
    monkeypatch.setattr(cli, "TARGETS", targets)
    monkeypatch.setattr(cli, "source_identity", lambda: source)

    # The real guard requires systemd/proc to match the real live release;
    # emulate only that identity check locally, with a pinned disposable DSN.
    def disposable_guard(expected_commit):
        assert expected_commit == source["commit"]
        assert database.url.database.startswith("weekly_overlay_test_")

    monkeypatch.setattr(cli, "require_serving_source", disposable_guard)
    monkeypatch.setattr(
        cli, "database_url", lambda args: url.render_as_string(hide_password=False)
    )

    def args(**overrides):
        values = dict(
            env_file=None,
            output_dir=tmp_path / "evidence",
            apply=False,
            expected_plan_sha256=None,
            expected_commit=None,
            deactivate=False,
            receipt_file=None,
            expected_receipt_sha256=None,
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    return SimpleNamespace(args=args, source=source, targets=targets)


async def counts(database):
    async with AsyncSession(database) as session:
        return {
            name: (
                await session.execute(text(f"SELECT count(*) FROM {name}"))
            ).scalar_one()
            for name in (
                "budget_tournament_weekly_revisions",
                "budget_tournament_weekly_lines",
                "budget_version_audit_log",
            )
        }


@pytest.mark.asyncio
async def test_apply_and_deactivation_are_scoped_audited_and_preserve_financial_history(
    database, cli_setup
):
    before = await counts(database)
    preview = await cli.run(cli_setup.args())
    assert preview["status"] == "PREVIEW"
    assert await counts(database) == before
    result = await cli.run(
        cli_setup.args(
            apply=True,
            expected_plan_sha256=preview["plan_sha256"],
            expected_commit=cli_setup.source["commit"],
        )
    )
    assert result["status"] == "COMMITTED_VERIFIED"
    assert result["verification"]["target_line_count"] == 3
    assert result["verification"]["other_lines_preserved"]
    assert result["verification"]["expected_income_preserved"]
    assert result["before_sha256"] == result["after_sha256"]
    assert await counts(database) == {
        "budget_tournament_weekly_revisions": 2,
        "budget_tournament_weekly_lines": 3,
        "budget_version_audit_log": 2,
    }
    receipt_path = cli_setup.args().output_dir / "receipt.json"
    receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    rollback_args = dict(
        deactivate=True,
        receipt_file=receipt_path,
        expected_receipt_sha256=receipt_hash,
        output_dir=cli_setup.args().output_dir / "rollback",
    )
    rollback_preview = await cli.deactivate_run(cli_setup.args(**rollback_args))
    assert rollback_preview["status"] == "DEACTIVATION_PREVIEW"
    rollback = await cli.deactivate_run(
        cli_setup.args(
            **rollback_args,
            apply=True,
            expected_plan_sha256=rollback_preview["plan_sha256"],
            expected_commit=cli_setup.source["commit"],
        )
    )
    assert rollback["status"] == "DEACTIVATED_VERIFIED"
    assert rollback["verification"]["previous_revisions_restored"]
    assert rollback["before_sha256"] == rollback["after_sha256"]
    assert await counts(database) == {
        "budget_tournament_weekly_revisions": 2,
        "budget_tournament_weekly_lines": 3,
        "budget_version_audit_log": 4,
    }
    async with AsyncSession(database) as session:
        active = (
            await session.execute(
                text(
                    "SELECT count(*) FROM budget_tournament_weekly_revisions WHERE is_current"
                )
            )
        ).scalar_one()
        assert active == 0


@pytest.mark.asyncio
async def test_stale_preview_rejected_before_any_effect(database, cli_setup):
    preview = await cli.run(cli_setup.args())
    with pytest.raises(ValueError, match="Plan drifted"):
        await cli.run(
            cli_setup.args(
                apply=True,
                expected_plan_sha256="f" * 64,
                expected_commit=cli_setup.source["commit"],
            )
        )
    assert preview["plan_sha256"] != "f" * 64
    assert all(v == 0 for v in (await counts(database)).values())


@pytest.mark.asyncio
async def test_failure_on_second_tournament_rolls_back_both(
    database, cli_setup, monkeypatch
):
    preview = await cli.run(cli_setup.args())
    original = cli.apply_tournament_overlay
    calls = []

    async def fail_second(*args, **kwargs):
        calls.append(kwargs["tournament_id"])
        if len(calls) == 2:
            raise ValueError("injected failure on second tournament")
        return await original(*args, **kwargs)

    monkeypatch.setattr(cli, "apply_tournament_overlay", fail_second)
    with pytest.raises(ValueError, match="injected failure"):
        await cli.run(
            cli_setup.args(
                apply=True,
                expected_plan_sha256=preview["plan_sha256"],
                expected_commit=cli_setup.source["commit"],
            )
        )
    assert len(calls) == 2
    assert all(v == 0 for v in (await counts(database)).values())
    assert not (cli_setup.args().output_dir / "committed-unverified.json").exists()


@pytest.mark.asyncio
async def test_receipt_hash_and_actor_guard_fail_closed(database, cli_setup):
    preview = await cli.run(cli_setup.args())
    await cli.run(
        cli_setup.args(
            apply=True,
            expected_plan_sha256=preview["plan_sha256"],
            expected_commit=cli_setup.source["commit"],
        )
    )
    receipt = cli_setup.args().output_dir / "receipt.json"
    with pytest.raises(ValueError, match="receipt does not match"):
        await cli.deactivate_run(
            cli_setup.args(
                deactivate=True, receipt_file=receipt, expected_receipt_sha256="0" * 64
            )
        )
    async with database.begin() as connection:
        await connection.execute(
            text("UPDATE empleados SET rol='empleado' WHERE id=CAST(:id AS uuid)"),
            {"id": fixture_module.ACTOR},
        )
    with pytest.raises(PermissionError, match="lacks required"):
        await cli.run(cli_setup.args())
    assert (await counts(database))["budget_tournament_weekly_revisions"] == 2


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_does_not_deactivate_preexisting_plans(
    database, cli_setup
):
    preview = await cli.run(cli_setup.args())
    await cli.run(
        cli_setup.args(
            apply=True,
            expected_plan_sha256=preview["plan_sha256"],
            expected_commit=cli_setup.source["commit"],
        )
    )
    replay_preview = await cli.run(cli_setup.args())
    replay = await cli.run(
        cli_setup.args(
            apply=True,
            expected_plan_sha256=replay_preview["plan_sha256"],
            expected_commit=cli_setup.source["commit"],
        )
    )
    assert all(item["idempotent"] for item in replay["results"])
    assert (await counts(database))["budget_version_audit_log"] == 2
    receipt_path = cli_setup.args().output_dir / "receipt.json"
    rollback_args = dict(
        deactivate=True,
        receipt_file=receipt_path,
        expected_receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        output_dir=cli_setup.args().output_dir / "rollback",
    )
    rollback_preview = await cli.deactivate_run(cli_setup.args(**rollback_args))
    rollback = await cli.deactivate_run(
        cli_setup.args(
            **rollback_args,
            apply=True,
            expected_plan_sha256=rollback_preview["plan_sha256"],
            expected_commit=cli_setup.source["commit"],
        )
    )
    assert rollback["status"] == "DEACTIVATED_VERIFIED"
    assert rollback["results"] == []
    assert (await counts(database))["budget_version_audit_log"] == 2
    async with AsyncSession(database) as session:
        assert (
            await session.execute(
                text(
                    "SELECT count(*) FROM budget_tournament_weekly_revisions WHERE is_current"
                )
            )
        ).scalar_one() == 2


@pytest.mark.asyncio
async def test_failure_on_second_deactivation_rolls_back_both(
    database, cli_setup, monkeypatch
):
    preview = await cli.run(cli_setup.args())
    await cli.run(
        cli_setup.args(
            apply=True,
            expected_plan_sha256=preview["plan_sha256"],
            expected_commit=cli_setup.source["commit"],
        )
    )
    receipt_path = cli_setup.args().output_dir / "receipt.json"
    rollback_args = dict(
        deactivate=True,
        receipt_file=receipt_path,
        expected_receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        output_dir=cli_setup.args().output_dir / "rollback",
    )
    rollback_preview = await cli.deactivate_run(cli_setup.args(**rollback_args))
    from samchat.budgets import tournament_weekly_overlay as overlay

    original = overlay.deactivate_tournament_overlay
    calls = []

    async def fail_second(*args, **kwargs):
        calls.append(kwargs["tournament_id"])
        if len(calls) == 2:
            raise ValueError("injected failure on second deactivation")
        return await original(*args, **kwargs)

    monkeypatch.setattr(overlay, "deactivate_tournament_overlay", fail_second)
    with pytest.raises(ValueError, match="injected failure"):
        await cli.deactivate_run(
            cli_setup.args(
                **rollback_args,
                apply=True,
                expected_plan_sha256=rollback_preview["plan_sha256"],
                expected_commit=cli_setup.source["commit"],
            )
        )
    assert len(calls) == 2
    assert (await counts(database))["budget_version_audit_log"] == 2
    async with AsyncSession(database) as session:
        assert (
            await session.execute(
                text(
                    "SELECT count(*) FROM budget_tournament_weekly_revisions WHERE is_current"
                )
            )
        ).scalar_one() == 2
    assert not (
        rollback_args["output_dir"] / "deactivation-committed-unverified.json"
    ).exists()


def test_real_serving_source_guard_rejects_invalid_commit():
    # This tests the unmodified real guard and exits before contacting systemd.
    with pytest.raises(ValueError, match="full reviewed commit"):
        cli.require_serving_source("invalid")
