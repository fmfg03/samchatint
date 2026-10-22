"""A corrected interpretation must reach UI, credit decisions and source hashes."""

import copy
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from devnous.gastos.services import amex_direct_matching_service as matching
from devnous.gastos.services.amex_direct_common import (
    DirectConflict,
    item_hash,
)
from devnous.gastos.services.amex_direct_no_cfdi_service import economic_source_hash
from tests.unit.gastos.test_amex_direct_no_cfdi_service import facts
from tests.unit.gastos.test_amex_direct_routes import (
    complete_direct_workspace,
    direct_workspace,
    workspace,
)


def test_revision_proof_changes_item_and_no_cfdi_hashes_without_changing_legacy():
    statement, _, item, source, _, _ = facts()
    source.profile = "debit_positive"
    item_before = item_hash(item, source)
    economic_before = economic_source_hash(statement, item, source)
    source.sign_revision_id = None
    source.sign_row_id = None
    source.sign_proof_hash = None
    assert item_hash(item, source) == item_before
    assert economic_source_hash(statement, item, source) == economic_before
    source.sign_revision_id = uuid4()
    source.sign_row_id = uuid4()
    source.sign_proof_hash = "a" * 64
    corrected_item = item_hash(item, source)
    corrected_economic = economic_source_hash(statement, item, source)
    assert corrected_item != item_before
    assert corrected_economic != economic_before
    source.sign_proof_hash = "b" * 64
    assert item_hash(item, source) != corrected_item
    assert economic_source_hash(statement, item, source) != corrected_economic


def test_fiscal_matching_proof_includes_sign_revision():
    _, context, item, source, expense, _ = facts()
    expense.ultimos_4_digitos = "5007"
    expense.origen = "amex_batch"
    baseline = matching.fiscal_source_hash(item, context, expense, source)
    source.sign_revision_id = uuid4()
    source.sign_row_id = uuid4()
    source.sign_proof_hash = "a" * 64
    corrected = matching.fiscal_source_hash(item, context, expense, source)
    assert corrected != baseline
    source.sign_proof_hash = "b" * 64
    assert matching.fiscal_source_hash(item, context, expense, source) != corrected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "physical,effective", [("debit", "credit"), ("credit", "debit")]
)
async def test_credit_exclusion_uses_effective_kind(monkeypatch, physical, effective):
    item = NS(
        id=uuid4(), import_id=uuid4(), movement_kind=physical, credit_excluded=False
    )
    session = NS(get=AsyncMock(return_value=item), flush=AsyncMock())
    monkeypatch.setattr(
        matching,
        "load_statement",
        AsyncMock(return_value=(NS(), NS(source_version=4), NS(), {})),
    )
    monkeypatch.setattr(matching, "require_unfrozen", AsyncMock())
    monkeypatch.setattr(
        matching,
        "effective_source_for_item",
        AsyncMock(return_value=NS(movement_kind=effective)),
    )
    event = AsyncMock()
    monkeypatch.setattr(matching, "record_event", event)
    args = dict(
        actor_id=uuid4(),
        import_id=item.import_id,
        item_id=item.id,
        expected_source_version=4,
        reason="Abono revisado contra archivo original",
        confirmed=True,
    )
    if effective == "credit":
        result = await matching.exclude_direct_credit(session, **args)
        assert result.status == "ready"
        assert item.credit_excluded
        event.assert_awaited_once()
    else:
        with pytest.raises(DirectConflict, match="known_credit_required"):
            await matching.exclude_direct_credit(session, **args)
        event.assert_not_awaited()
        assert not item.credit_excluded


@pytest.mark.asyncio
async def test_statement_shows_corrected_signs_and_original_profile(
    complete_direct_workspace,
):
    w = complete_direct_workspace
    google = w.item
    google.descriptor = "GOOGLE MAPXPLORER"
    google.raw_amount = "939.00"
    google.raw_signed_amount = Decimal("939.00")
    google.amount = Decimal("939.00")
    google.profile = "debit_positive"
    amazon = copy.copy(google)
    amazon.id = uuid4()
    amazon.movement_kind = "credit"
    amazon.descriptor = "AMAZON REFUND"
    amazon.raw_amount = "-46.53"
    amazon.raw_signed_amount = Decimal("-46.53")
    amazon.amount = None
    w.view.items = [google, amazon]
    w.view.progress.update(
        sign_revision_ids=[str(uuid4())], effective_profiles=["debit_positive"]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="http://test"
    ) as client:
        response = await client.get(f"{w.routes.BASE}/{w.statement.id}/directo")
    assert response.status_code == 200
    assert "Signos corregidos mediante revisión auditada" in response.text
    assert "El archivo y el perfil originales se conservan" in response.text
    assert f'value="{google.id}"' in response.text
    assert f'name="item_ids" value="{amazon.id}"' not in response.text
