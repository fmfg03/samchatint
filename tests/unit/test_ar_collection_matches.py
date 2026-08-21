from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from samchat.ar.collection_matches import (
    ACCEPTED_STATUS,
    REVERSED_STATUS,
    ARCollectionMatchError,
    accept_ar_collection_match,
    ensure_ar_collection_match_schema,
    reverse_ar_collection_match,
)


class _FakeSession:
    def __init__(self):
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return None


def _ar_item(**overrides):
    item = {
        "ar_item_id": "linked:1",
        "budget_version_id": "11111111-1111-1111-1111-111111111111",
        "budget_line_id": "22222222-2222-2222-2222-222222222222",
        "cfdi_report_id": "33333333-3333-3333-3333-333333333333",
        "amount": 100,
        "payer_rfc": "CLI010101AAA",
        "payer_name": "Cliente Uno",
    }
    item.update(overrides)
    return item


def _bank_movement(**overrides):
    movement = {
        "id": "44444444-4444-4444-4444-444444444444",
        "signo": "+",
        "importe": 100,
        "fecha": datetime(2026, 1, 16),
        "rfc_ordenante": "CLI010101AAA",
        "nombre_ordenante": "Cliente Uno",
        "descripcion": "Transferencia Cliente Uno",
        "conciliacion_estado": "unmatched",
    }
    movement.update(overrides)
    return movement


@pytest.mark.asyncio
async def test_ensure_ar_collection_match_schema_creates_expected_tables():
    session = _FakeSession()

    await ensure_ar_collection_match_schema(session)

    source = "\n".join(session.statements)
    assert "CREATE TABLE IF NOT EXISTS ar_collection_matches" in source
    assert "CREATE TABLE IF NOT EXISTS ar_collection_match_audit_log" in source
    assert "ux_ar_collection_matches_active_ar_item" in source
    assert "ux_ar_collection_matches_active_bank" in source


@pytest.mark.asyncio
async def test_accept_ar_collection_match_accepts_valid_match():
    inserted = {"id": "match-1", "status": ACCEPTED_STATUS}
    with (
        patch(
            "samchat.ar.collection_matches.ensure_ar_collection_match_schema",
            new=AsyncMock(),
        ),
        patch(
            "samchat.ar.collection_matches._load_bank_movement",
            new=AsyncMock(return_value=_bank_movement()),
        ),
        patch(
            "samchat.ar.collection_matches._find_active_match",
            new=AsyncMock(side_effect=[{}, {}]),
        ),
        patch(
            "samchat.ar.collection_matches._insert_match",
            new=AsyncMock(return_value=inserted),
        ) as insert_match,
        patch(
            "samchat.ar.collection_matches._audit_match_event",
            new=AsyncMock(),
        ) as audit_event,
    ):
        result = await accept_ar_collection_match(
            object(),
            ar_item=_ar_item(),
            bank_movement_id="bank-1",
            actor_empleado_id="employee-1",
            acceptance_reason="RFC y monto coinciden",
        )

    assert result == inserted
    assert insert_match.await_count == 1
    audit_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_accept_rejects_non_inflow_bank_movement():
    with (
        patch(
            "samchat.ar.collection_matches.ensure_ar_collection_match_schema",
            new=AsyncMock(),
        ),
        patch(
            "samchat.ar.collection_matches._load_bank_movement",
            new=AsyncMock(return_value=_bank_movement(signo="-")),
        ),
    ):
        with pytest.raises(ARCollectionMatchError, match="bank_movement_not_inflow"):
            await accept_ar_collection_match(
                object(),
                ar_item=_ar_item(),
                bank_movement_id="bank-1",
                actor_empleado_id="employee-1",
                acceptance_reason="manual",
            )


@pytest.mark.asyncio
async def test_accept_rejects_incompatible_amount():
    with (
        patch(
            "samchat.ar.collection_matches.ensure_ar_collection_match_schema",
            new=AsyncMock(),
        ),
        patch(
            "samchat.ar.collection_matches._load_bank_movement",
            new=AsyncMock(return_value=_bank_movement(importe=80)),
        ),
    ):
        with pytest.raises(ARCollectionMatchError, match="amount_incompatible"):
            await accept_ar_collection_match(
                object(),
                ar_item=_ar_item(),
                bank_movement_id="bank-1",
                actor_empleado_id="employee-1",
                acceptance_reason="manual",
            )


@pytest.mark.asyncio
async def test_accept_rejects_missing_identity_without_manual_reason():
    with (
        patch(
            "samchat.ar.collection_matches.ensure_ar_collection_match_schema",
            new=AsyncMock(),
        ),
        patch(
            "samchat.ar.collection_matches._load_bank_movement",
            new=AsyncMock(
                return_value=_bank_movement(
                    rfc_ordenante="OTR010101AAA",
                    nombre_ordenante="Otra Persona",
                    descripcion="Transferencia otra",
                )
            ),
        ),
    ):
        with pytest.raises(ARCollectionMatchError, match="manual_reason_required"):
            await accept_ar_collection_match(
                object(),
                ar_item=_ar_item(),
                bank_movement_id="bank-1",
                actor_empleado_id="employee-1",
                acceptance_reason="",
            )


@pytest.mark.asyncio
async def test_accept_rejects_active_duplicate_for_ar_item():
    with (
        patch(
            "samchat.ar.collection_matches.ensure_ar_collection_match_schema",
            new=AsyncMock(),
        ),
        patch(
            "samchat.ar.collection_matches._load_bank_movement",
            new=AsyncMock(return_value=_bank_movement()),
        ),
        patch(
            "samchat.ar.collection_matches._find_active_match",
            new=AsyncMock(side_effect=[{"id": "existing"}, {}]),
        ),
    ):
        with pytest.raises(
            ARCollectionMatchError,
            match="active_match_exists_for_ar_item",
        ):
            await accept_ar_collection_match(
                object(),
                ar_item=_ar_item(),
                bank_movement_id="bank-1",
                actor_empleado_id="employee-1",
                acceptance_reason="manual",
            )


@pytest.mark.asyncio
async def test_accept_rejects_active_duplicate_for_bank_movement():
    with (
        patch(
            "samchat.ar.collection_matches.ensure_ar_collection_match_schema",
            new=AsyncMock(),
        ),
        patch(
            "samchat.ar.collection_matches._load_bank_movement",
            new=AsyncMock(return_value=_bank_movement()),
        ),
        patch(
            "samchat.ar.collection_matches._find_active_match",
            new=AsyncMock(side_effect=[{}, {"id": "existing"}]),
        ),
    ):
        with pytest.raises(
            ARCollectionMatchError,
            match="active_match_exists_for_bank_movement",
        ):
            await accept_ar_collection_match(
                object(),
                ar_item=_ar_item(),
                bank_movement_id="bank-1",
                actor_empleado_id="employee-1",
                acceptance_reason="manual",
            )


@pytest.mark.asyncio
async def test_reverse_marks_match_reversed_without_deleting():
    before = {"id": "match-1", "status": ACCEPTED_STATUS}
    after = {"id": "match-1", "status": REVERSED_STATUS}
    with (
        patch(
            "samchat.ar.collection_matches.ensure_ar_collection_match_schema",
            new=AsyncMock(),
        ),
        patch(
            "samchat.ar.collection_matches._load_match",
            new=AsyncMock(return_value=before),
        ),
        patch(
            "samchat.ar.collection_matches._update_match_reversed",
            new=AsyncMock(return_value=after),
        ) as update_match,
        patch(
            "samchat.ar.collection_matches._audit_match_event",
            new=AsyncMock(),
        ) as audit_event,
    ):
        result = await reverse_ar_collection_match(
            object(),
            match_id="match-1",
            actor_empleado_id="employee-1",
            reversal_reason="error de captura",
        )

    assert result["status"] == REVERSED_STATUS
    update_match.assert_awaited_once()
    audit_event.assert_awaited_once()


def test_collection_match_service_does_not_update_bank_reconciliation():
    source = Path("src/samchat/ar/collection_matches.py").read_text()

    assert "UPDATE bank_movements" not in source
