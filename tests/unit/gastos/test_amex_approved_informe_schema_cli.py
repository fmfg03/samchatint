"""Operator preflight requires an explicit source and never provisions schema."""

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def load_cli():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts/check_amex_approved_informe_schema.py"
    )
    spec = importlib.util.spec_from_file_location("amex_schema_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("ready,exit_code", [(True, 0), (False, 1)])
def test_cli_reports_metadata_without_connection_details(
    monkeypatch, capsys, ready, exit_code
):
    cli = load_cli()
    monkeypatch.setenv(
        "EXPLICIT_TEST_SOURCE",
        "postgresql+asyncpg://owner:must_not_appear@localhost/db",
    )
    monkeypatch.setattr(
        "sys.argv", ["preflight", "--database-url-env", "EXPLICIT_TEST_SOURCE"]
    )
    helper = AsyncMock(
        return_value={"ready": ready, "missing_columns": {}, "missing_triggers": []}
    )
    monkeypatch.setattr(cli, "inspect_schema", helper)
    assert cli.main() == exit_code
    helper.assert_awaited_once()
    assert "must_not_appear" not in capsys.readouterr().out


def test_cli_missing_explicit_source_fails_without_opening_connection(
    monkeypatch, capsys
):
    cli = load_cli()
    monkeypatch.delenv("AMEX_SCHEMA_DATABASE_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["preflight"])
    helper = AsyncMock()
    monkeypatch.setattr(cli, "inspect_schema", helper)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2
    helper.assert_not_called()
    assert "explicitly authorized" in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_inspector_sets_read_only_before_metadata_and_always_disposes(
    monkeypatch, failed
):
    cli = load_cli()
    queries = []

    @asynccontextmanager
    async def context():
        yield connection

    connection = SimpleNamespace(
        begin=context,
        exec_driver_sql=AsyncMock(
            side_effect=lambda statement: queries.append(statement)
        ),
    )
    engine = SimpleNamespace(connect=context, dispose=AsyncMock())
    monkeypatch.setattr(cli, "create_async_engine", lambda _: engine)

    async def metadata(actual_connection):
        assert actual_connection is connection
        assert queries == ["SET TRANSACTION READ ONLY"]
        if failed:
            raise RuntimeError("catalog unavailable")
        return {"ready": True}

    monkeypatch.setattr(cli, "inspect_approved_informe_schema", metadata)
    if failed:
        with pytest.raises(RuntimeError, match="catalog unavailable"):
            await cli.inspect_schema("explicit-source")
    else:
        assert await cli.inspect_schema("explicit-source") == {"ready": True}
    engine.dispose.assert_awaited_once()


def test_changed_approval_source_has_current_finance_attestation_message():
    from devnous.gastos.routes.admin_amex_accounting_routes import _exception_text

    explanation = _exception_text("stale_amex_operational_source")
    assert "cambió desde la aprobación" in explanation
    assert "atestación actual de Finanzas" in explanation
