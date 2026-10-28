"""Boundary checks for the non-destructive historical import operator."""

from pathlib import Path
import importlib.util
import json

import pytest

SPEC = importlib.util.spec_from_file_location(
    "historical_operator",
    Path(__file__).parents[2] / "scripts/import_historical_explicit.py",
)
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE test(id int)",
        "ALTER TABLE historical_policy_lines ADD COLUMN x int",
        "DELETE FROM historical_policy_lines",
        "UPDATE historical_policy_lines SET debit=1",
        "INSERT INTO budget_lines(id) VALUES (1)",
    ],
)
def test_guard_rejects_ddl_mutation_and_other_business_tables(sql):
    with pytest.raises(ValueError):
        operator.sql_guard(sql, True)


@pytest.mark.parametrize("table", operator.HIST + ("accounting_import_runs",))
def test_guard_allows_only_canonical_insert_tables(table):
    operator.sql_guard(f"INSERT INTO {table} (id) VALUES (:id)", True)
    with pytest.raises(ValueError):
        operator.sql_guard(f"INSERT INTO {table} (id) VALUES (:id)", False)


def test_preview_cannot_lock_rows():
    with pytest.raises(ValueError):
        operator.sql_guard("SELECT id FROM accounting_import_runs FOR UPDATE", False)


def test_evidence_cannot_be_overwritten(tmp_path):
    first = operator.save(tmp_path, "before.json", {"value": 1})
    assert first == operator.save(tmp_path, "before.json", {"value": 1})
    with pytest.raises(ValueError, match="different content"):
        operator.save(tmp_path, "before.json", {"value": 2})
    assert json.loads((tmp_path / "before.json").read_text()) == {"value": 1}
    assert (tmp_path / "before.json").stat().st_mode & 0o777 == 0o600


def test_duplicate_company_year_manifest_selection_rejected(tmp_path):
    paths = []
    for n in range(2):
        path = tmp_path / f"{n}.json"
        path.write_text(
            json.dumps(
                {
                    "fiscal_year": 2026,
                    "company_code": "01",
                    "company_label": "PSP1705058S4",
                }
            )
        )
        paths.append(path)
    with pytest.raises(ValueError, match="exactly once"):
        operator.manifest_configs(paths)


def test_manifest_and_referenced_source_drift_rejected(tmp_path):
    import hashlib

    source = tmp_path / "source.csv"
    source.write_text("original")
    item = {
        "path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"originals": [item], "derived": {"trial_balance": item}})
    )
    config = {
        "path": str(manifest),
        "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }
    operator.recheck_inputs([config])
    source.write_text("changed")
    with pytest.raises(ValueError, match="Source changed"):
        operator.recheck_inputs([config])
    manifest.write_text("{}")
    with pytest.raises(ValueError, match="Manifest changed"):
        operator.recheck_inputs([config])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [None, {"activo": False, "rol": "superadmin"}, {"activo": True, "rol": "empleado"}],
)
async def test_actor_must_be_active_superadmin(row):
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    session.execute.return_value = result
    with pytest.raises(PermissionError):
        await operator.authorize(session, operator.ACTOR)


@pytest.mark.asyncio
async def test_named_actor_with_existing_superadmin_role_is_accepted():
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = {
        "activo": True,
        "rol": "superadmin",
    }
    session.execute.return_value = result
    await operator.authorize(session, operator.ACTOR)


@pytest.mark.parametrize(
    "code,label,months",
    [
        ("01", "PSP1705058S4", [3, 4, 6, 7]),
        ("GOAT", "GDM210323RIA", [8]),
        ("02", "PMD0608162M2", []),
    ],
)
def test_apply_admission_only_accepts_approved_quarantine(
    tmp_path, code, label, months
):
    path = tmp_path / "manifest.json"
    config = {
        "path": str(path),
        "fiscal_year": 2026,
        "company_code": code,
        "company_label": label,
    }
    path.write_text(json.dumps({"withheld_months": [{"month_num": n} for n in months]}))
    operator.require_approved_admission([config])
    path.write_text(
        json.dumps(
            {"withheld_months": [{"month_num": n} for n in sorted(set(months) | {1})]}
        )
    )
    with pytest.raises(ValueError, match="outside the approved admission"):
        operator.require_approved_admission([config])


@pytest.mark.parametrize(
    "reference_kind", ["full_validation_report", "withheld_evidence"]
)
def test_full_report_and_withheld_evidence_pinned_through_commit(
    tmp_path, reference_kind
):
    import hashlib

    source = tmp_path / "evidence.json"
    source.write_text("{}")
    item = {
        "path": "evidence.json",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    body = {"originals": [], "derived": {}}
    if reference_kind == "full_validation_report":
        body["full_validation_report"] = item
    else:
        body["withheld_months"] = [{"month_num": 3, "evidence": item}]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(body))
    config = {
        "path": str(manifest),
        "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }
    operator.recheck_inputs([config])
    source.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="Source changed"):
        operator.recheck_inputs([config])
