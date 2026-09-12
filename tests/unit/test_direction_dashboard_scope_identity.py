from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from samchat.client_executive import service


class _RowsResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_RowsResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _Session:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def execute(self, _statement: Any, _params: Any = None) -> _RowsResult:
        return _RowsResult(self._rows)


def _statement():
    return text("""
        SELECT l.tournament_id, l.tournament_name, l.budget_amount
        FROM budget_lines l
        WHERE l.budget_version_id = :version_id
        """)


def _guard(rows: list[dict[str, Any]]) -> service._DirectionBudgetReadSession:
    return service._DirectionBudgetReadSession(
        _Session(rows),
        tournament={
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Copa Telmex Telcel de Fútbol",
            "slug": "",
        },
        aliases=["CTT"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row_name", "row_id"),
    [
        ("Copa Telmex Telcel de Futbol", None),
        ("Copa  Telmex Telcel de Fútbol", None),
        ("Copa Telmex Telcel de Fútbol", ""),
    ],
)
async def test_consuming_guard_rejects_identity_variants_outside_preflight_scope(
    row_name: str, row_id: str | None
) -> None:
    """Consumer validation must match SQL preflight semantics exactly."""
    guarded = _guard(
        [
            {
                "tournament_id": row_id,
                "tournament_name": row_name,
                "budget_amount": 999_999,
            }
        ]
    )

    with pytest.raises(service._DirectionBudgetScopeViolation):
        await guarded.execute(
            _statement(),
            {
                "version_id": "22222222-2222-2222-2222-222222222222",
                "aliases": ["CTT"],
                "tournament_id": "11111111-1111-1111-1111-111111111111",
            },
        )


@pytest.mark.asyncio
async def test_consuming_guard_allows_trimmed_case_variant_with_null_legacy_id() -> (
    None
):
    """Case and edge whitespace may differ because SQL uses UPPER(TRIM())."""
    guarded = _guard(
        [
            {
                "tournament_id": None,
                "tournament_name": "  copa telmex telcel de fútbol  ",
                "budget_amount": 100,
            }
        ]
    )

    result = await guarded.execute(
        _statement(),
        {
            "version_id": "22222222-2222-2222-2222-222222222222",
            "aliases": ["CTT"],
            "tournament_id": "11111111-1111-1111-1111-111111111111",
        },
    )

    assert result.mappings().all()[0]["budget_amount"] == 100
