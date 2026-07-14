from types import SimpleNamespace

import pytest

from devnous.tournaments.core.operations_module import OperationsModule


@pytest.mark.asyncio
async def test_back_page_passes_selected_provider_to_review_reconciliation() -> None:
    operations = OperationsModule.__new__(OperationsModule)
    operations.pending_back_photos = {
        99: {
            "review_session_id": "session-1",
            "provider": "openai",
            "page_count": 1,
            "max_pages": 3,
        }
    }
    seen = {}

    async def fake_extract(provider, optimized_bytes, image_b64):
        return SimpleNamespace(players=[]), {"provider": provider}

    async def fake_append(**kwargs):
        seen.update(kwargs)
        return True, "https://sam.chat/registration-review/session-1"

    operations._extract_registration_form = fake_extract
    operations._append_back_photo_to_review_session = fake_append
    operations._telegram_review_max_pages = lambda: 3

    response = await operations._process_back_photo(
        chat_id=99,
        user_id=42,
        team_id=None,
        optimized_bytes=b"image",
        image_b64="aW1hZ2U=",
        provider="openai",
    )

    assert seen["provider"] == "openai"
    assert seen["review_session_id"] == "session-1"
    assert "Página agregada" in response
