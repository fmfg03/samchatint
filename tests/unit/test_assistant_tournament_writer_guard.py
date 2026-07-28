from __future__ import annotations

import pytest
from fastapi import HTTPException

from samchat.assistant import router


@pytest.mark.parametrize(
    "table",
    sorted(
        router.GASTOS_PROJECT_AUTHORITY_TABLES
        | router.OPERATIONS_LEGACY_LOCAL_AUTHORITY_TABLES
    ),
)
def test_universal_writer_blocks_local_tournament_authority_tables(table: str) -> None:
    with pytest.raises(HTTPException) as exc:
        router._validate_db_write_target(data_source="gastos", table=table)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("table", sorted(router.DEFAULT_SUPABASE_DB_TABLES))
def test_universal_writer_blocks_every_known_operations_tournament_table(
    table: str,
) -> None:
    with pytest.raises(HTTPException) as exc:
        router._validate_db_write_target(data_source="supabase", table=table)
    assert exc.value.status_code == 403


def test_universal_supabase_writer_stays_disabled_for_future_allowlisted_table() -> (
    None
):
    with pytest.raises(HTTPException) as exc:
        router._validate_db_write_target(
            data_source="supabase", table="future_operations_table"
        )
    assert exc.value.status_code == 403


def test_environment_can_only_strengthen_hard_denies(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_DB_WRITE_DENYLIST_GASTOS", "expense_reports")
    deny = router._db_write_denylist(
        router.DEFAULT_DB_WRITE_DENYLIST_GASTOS,
        "ASSISTANT_DB_WRITE_DENYLIST_GASTOS",
    )
    assert router.DEFAULT_DB_WRITE_DENYLIST_GASTOS <= deny
    assert "expense_reports" in deny
    assert "tournaments" in deny


def test_universal_writer_is_not_a_finance_or_tournament_domain_tool() -> None:
    assert "db_write_universal" not in router.FINANCE_WRITE_TOOLS
    assert "db_write_universal" not in router.TOURNAMENT_WRITE_TOOLS


def test_domain_table_classes_are_covered_by_hard_denies() -> None:
    assert (
        router.GASTOS_PROJECT_AUTHORITY_TABLES
        <= router.DEFAULT_DB_WRITE_DENYLIST_GASTOS
    )
    assert (
        router.OPERATIONS_LEGACY_LOCAL_AUTHORITY_TABLES
        <= router.DEFAULT_DB_WRITE_DENYLIST_GASTOS
    )
    assert (
        router.OPERATIONS_TOURNAMENT_AUTHORITY_TABLES
        <= router.DEFAULT_DB_WRITE_DENYLIST_SUPABASE
    )
