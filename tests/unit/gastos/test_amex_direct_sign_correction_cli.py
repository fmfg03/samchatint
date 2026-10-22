"""The maintenance entrypoint cannot silently select an actor or another file."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from devnous.gastos.services.amex_direct_common import DirectConflict, digest

SPEC = importlib.util.spec_from_file_location(
    "sign_cli",
    Path(__file__).parents[3] / "scripts/correct_amex_direct_sign_profile.py",
)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def arguments():
    return NS(
        command="apply",
        import_id=cli.TARGET_IMPORT_ID,
        actor_id=uuid4(),
        profile="debit_positive",
        reason=" Reviewed correction ",
        source_version=7,
        preview_hash="a" * 64,
        idempotency_key="repair",
        confirmed=True,
        manifest="unused",
        database_url_env="TEST_SIGN_URL",
    )


def manifest(args):
    return dict(
        import_id=str(args.import_id),
        actor_id=str(args.actor_id),
        profile=args.profile,
        original_profile="debit_negative",
        reason=args.reason.strip(),
        expected_source_version=7,
        expected_count=16,
        effective_counts=cli.TARGET_COUNTS,
        effective_totals=cli.TARGET_TOTALS,
        preview_hash=args.preview_hash,
        rows=[{}] * 16,
    )


def test_manifest_exact_target_actor_profile_version_and_totals():
    args = arguments()
    value = manifest(args)
    value.pop("preview_hash")
    args.preview_hash = digest(value)
    value["preview_hash"] = args.preview_hash
    cli.validate_manifest(value, args)
    for key in value:
        altered = {**value, key: None}
        with pytest.raises(DirectConflict, match="sign_manifest_mismatch"):
            cli.validate_manifest(altered, args)


def test_parser_preview_default_and_actor_is_mandatory():
    args = cli.parse_args(
        [
            "--import-id",
            str(cli.TARGET_IMPORT_ID),
            "--actor-id",
            str(uuid4()),
            "--profile",
            "debit_positive",
            "--reason",
            "Reviewed",
            "--source-version",
            "7",
        ]
    )
    assert args.command == "preview" and args.confirmed is False
    with pytest.raises(SystemExit):
        cli.parse_args(["--import-id", str(cli.TARGET_IMPORT_ID)])


@pytest.mark.asyncio
async def test_wrong_target_never_opens_database(monkeypatch):
    args = arguments()
    args.import_id = uuid4()
    engine = Mock()
    monkeypatch.setattr(cli, "create_async_engine", engine)
    with pytest.raises(DirectConflict, match="sign_maintenance_target_denied"):
        await cli.run(args, "never-connect")
    engine.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["confirmed", "preview_hash", "idempotency_key", "manifest"]
)
async def test_apply_requires_review_artifacts_before_connection(monkeypatch, field):
    args = arguments()
    setattr(args, field, None)
    engine = Mock()
    monkeypatch.setattr(cli, "create_async_engine", engine)
    with pytest.raises(DirectConflict, match="sign_confirmation_required"):
        await cli.run(args, "never-connect")
    engine.assert_not_called()


def test_main_no_credentials(monkeypatch, capsys):
    args = arguments()
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    monkeypatch.delenv("TEST_SIGN_URL", raising=False)
    assert cli.main() == 1
    assert (
        json.loads(capsys.readouterr().out)["reason"]
        == "explicit_database_url_required"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,status",
    [
        ("preview", "preview"),
        ("apply", "corrected"),
        ("apply", "exists"),
        ("apply", "pending"),
    ],
)
async def test_transaction_readonly_preview_apply_and_blocked_rollback(
    monkeypatch, tmp_path, command, status
):
    from contextlib import asynccontextmanager

    args = arguments()
    args.command = command
    value = manifest(args)
    value.pop("preview_hash")
    args.preview_hash = digest(value)
    value["preview_hash"] = args.preview_hash
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value))
    args.manifest = str(path)
    engine = NS(dispose=AsyncMock())
    session = NS(execute=AsyncMock())

    @asynccontextmanager
    async def transaction():
        yield

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield session

    session.begin = transaction
    monkeypatch.setattr(cli, "create_async_engine", lambda url: engine)
    monkeypatch.setattr(cli, "AsyncSession", connection)
    operation = AsyncMock(
        return_value=NS(
            status=status, reason="blocked_for_test", source_version=8, progress=value
        )
    )
    monkeypatch.setattr(cli, "preview_direct_sign_correction", operation)
    monkeypatch.setattr(cli, "confirm_direct_sign_correction", operation)
    if status == "pending":
        with pytest.raises(DirectConflict, match="blocked_for_test"):
            await cli.run(args, "test")
    else:
        result = await cli.run(args, "test")
        assert result["status"] == status
    if command == "preview":
        assert str(session.execute.call_args.args[0]) == "SET TRANSACTION READ ONLY"
    else:
        session.execute.assert_not_called()
        assert operation.call_args.kwargs["confirmed"] is True
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    "failure", [None, DirectConflict("sign_preview_changed"), ValueError("bad_json")]
)
def test_main_safe_json_success_and_failure(monkeypatch, capsys, failure):
    args = arguments()
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    monkeypatch.setenv("TEST_SIGN_URL", "unused-secret")

    async def run(*args):
        if failure:
            raise failure
        return {"status": "corrected"}

    monkeypatch.setattr(cli, "run", run)
    assert cli.main() == (1 if failure else 0)
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == ("blocked" if failure else "corrected")


def test_database_error_never_discloses_sql_parameters(monkeypatch, capsys):
    from sqlalchemy.exc import DBAPIError

    args = arguments()
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    monkeypatch.setenv("TEST_SIGN_URL", "unused-secret")

    async def fail(*args):
        raise DBAPIError(
            "INSERT PRIVATE SQL", {"source_json": "PRIVATE CSV"}, ValueError("SECRET")
        )

    monkeypatch.setattr(cli, "run", fail)
    assert cli.main() == 1
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "sign_database_operation_failed"
    assert "PRIVATE" not in output and "SECRET" not in output
