"""Source admission preserves old owners and rejects partial recovery schemas."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from devnous.gastos.services import amex_accounting_identity_service as identity
from devnous.gastos.services import amex_direct_schema_service as schema


@pytest.mark.asyncio
@pytest.mark.parametrize("view,recovery", [(False, False), (True, True), (True, False)])
async def test_effective_source_presence_requires_owner_admission(
    monkeypatch, view, recovery
):
    result = Mock()
    result.mappings.return_value.one.return_value = {
        "view_present": view,
        "recovery_present": recovery,
    }
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    admission = AsyncMock()
    monkeypatch.setattr(schema, "require_amex_direct_schema", admission)
    assert await identity.effective_direct_sources_available(session) is view
    assert admission.await_count == int(view)


@pytest.mark.asyncio
async def test_partial_or_drifted_effective_source_never_falls_back(monkeypatch):
    result = Mock()
    result.mappings.return_value.one.return_value = {
        "view_present": False,
        "recovery_present": True,
    }
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    with pytest.raises(schema.DirectSchemaUnavailable):
        await identity.effective_direct_sources_available(session)
    result.mappings.return_value.one.return_value["view_present"] = True
    monkeypatch.setattr(
        schema,
        "require_amex_direct_schema",
        AsyncMock(side_effect=schema.DirectSchemaUnavailable("drifted")),
    )
    with pytest.raises(schema.DirectSchemaUnavailable, match="drifted"):
        await identity.effective_direct_sources_available(session)
