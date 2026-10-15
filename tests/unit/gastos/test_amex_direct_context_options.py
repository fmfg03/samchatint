"""Context choices use their own Finance action and actual statement scope."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_direct_context_service as context
from devnous.gastos.services.amex_direct_common import DirectConflict


@pytest.mark.asyncio
async def test_context_choices_use_correction_owner_without_classification(monkeypatch):
    actor, source, card, other = uuid4(), uuid4(), uuid4(), uuid4()
    configs = [
        {
            "company_code": "OTHER",
            "receiver_rfc": "OTHER",
            "card_account_ids": [str(other)],
        },
        {
            "company_code": "LIVE",
            "receiver_rfc": "AAA010101AAA",
            "card_account_ids": [str(card)],
        },
        {
            "company_code": "DENIED",
            "receiver_rfc": "DENIED",
            "card_account_ids": [str(card)],
        },
        {
            "company_code": "OTHER2",
            "receiver_rfc": "OTHER2",
            "card_account_ids": [str(other)],
        },
    ]
    authorize = AsyncMock()
    load = AsyncMock(
        side_effect=[
            DirectConflict("contexto_fiscal_pendiente"),
            (SimpleNamespace(card_account_id=card), None, None, configs[1]),
        ]
    )
    monkeypatch.setattr(context, "authorize", authorize)
    monkeypatch.setattr(context, "configured_scopes", lambda: configs)
    monkeypatch.setattr(
        context,
        "actor_scope",
        lambda row, who: {} if row["company_code"] != "DENIED" else None,
    )
    monkeypatch.setattr(context, "load_statement", load)
    choices = await context.list_direct_context_options(
        SimpleNamespace(no_autoflush=nullcontext()), actor_id=actor, import_id=source
    )
    assert choices == {
        "contexts": [
            {"company_code": "LIVE", "receiver_rfc": "AAA010101AAA", "currency": "MXN"}
        ]
    }
    authorize.assert_awaited_once()
    assert authorize.await_args.args[1:] == (actor, "contexto")
    assert authorize.await_args.kwargs == {"finance": True}
    assert load.await_count == 2
    for call in load.await_args_list:
        assert call.args[1:] == (actor, source, "contexto")
        assert call.kwargs["finance"] is True
        assert call.kwargs["requested_company_code"] in {"LIVE", "OTHER"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [PermissionError("denied"), DirectConflict("direct_statement_required")]
)
async def test_context_options_do_not_swallow_authority_or_source_errors(
    monkeypatch, error
):
    monkeypatch.setattr(context, "authorize", AsyncMock())
    monkeypatch.setattr(
        context,
        "configured_scopes",
        lambda: [
            {"company_code": "TEST", "receiver_rfc": "AAA", "card_account_ids": []}
        ],
    )
    monkeypatch.setattr(context, "actor_scope", lambda row, actor: {})
    monkeypatch.setattr(context, "load_statement", AsyncMock(side_effect=error))
    with pytest.raises(type(error), match=str(error)):
        await context.list_direct_context_options(
            SimpleNamespace(no_autoflush=nullcontext()),
            actor_id=uuid4(),
            import_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_context_options_empty_scope_denies_without_source_read(monkeypatch):
    monkeypatch.setattr(context, "authorize", AsyncMock())
    monkeypatch.setattr(context, "configured_scopes", lambda: [])
    load = AsyncMock()
    monkeypatch.setattr(context, "load_statement", load)
    with pytest.raises(PermissionError, match="actor_scope_denied"):
        await context.list_direct_context_options(
            SimpleNamespace(no_autoflush=nullcontext()),
            actor_id=uuid4(),
            import_id=uuid4(),
        )
    load.assert_not_awaited()
