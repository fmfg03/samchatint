"""Canonical imports preserve only explicitly supplied accounting scopes."""

from unittest.mock import AsyncMock

import pytest

from samchat.accounting_historical import service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        {"project_raw": "explicit-project", "cost_center_raw": "explicit-center"},
        {"project_raw": "explicit-project"},
        {},
    ],
)
async def test_canonical_import_binds_explicit_scopes_and_leaves_missing_null(
    monkeypatch, scope
):
    row = {
        "month_num": "01",
        "policy_id": "p",
        "account_code": "5300-001",
        "account_name": "Torneo",
        "concept": "Tournament project",
        "debe": "10",
        "haber": "0",
        **scope,
    }
    source = {
        "source_family": "poliza_line",
        "source_format": "csv",
        "source_filename": "lines.csv",
        "source_path": "lines.csv",
        "source_sha256": "hash",
        "validation_status": "OK",
        "source_scope": "canonical",
        "metadata": {},
        "company_code": "01",
    }
    monkeypatch.setattr(service, "ensure_historical_accounting_schema", AsyncMock())
    monkeypatch.setattr(
        service, "_find_existing_import_run_id", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        service, "load_historical_accounting_snapshot", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        service,
        "_load_historical_source_dataset",
        lambda **kwargs: ({}, [], [], [row], [source]),
    )
    session = AsyncMock()
    result = await service.import_historical_accounting_pilot(
        session, fiscal_year=2026, apply_changes=True
    )
    assert result["created"] is True
    inserts = [
        (str(call.args[0]), call.args[1])
        for call in session.execute.await_args_list
        if "INSERT INTO historical_policy_lines" in str(call.args[0])
    ]
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert ":cost_center_raw, :project_raw" in sql
    assert params["project_raw"] == scope.get("project_raw")
    assert params["cost_center_raw"] == scope.get("cost_center_raw")
    session.commit.assert_awaited_once()
