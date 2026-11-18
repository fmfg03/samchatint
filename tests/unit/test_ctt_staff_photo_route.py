"""Private photo delivery requires access, a committed binding and intact bytes."""

import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

os.environ.setdefault(
    "SESSION_SECRET_KEY", "isolated-photo-route-test-key-0123456789abcdef"
)

import copa_telmex_dashboard as dashboard  # noqa: E402

from devnous.copa_telmex.staff_photo_store import (  # noqa: E402
    PrivateReviewPhotoStore,
    encode_review_photo,
)


@pytest.fixture
def delivery(monkeypatch, tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    photo = encode_review_photo(Image.new("RGB", (80, 80), "blue"))
    PrivateReviewPhotoStore(root).stage(photo)
    state = SimpleNamespace(committed=True, queries=0, failure=None, visible=True)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement, parameters):
            state.queries += 1
            assert str(statement) == (
                "SELECT public.ctt_photo_is_committed(:photo_sha256)"
            )
            assert parameters == {"photo_sha256": photo.sha256}
            if state.failure:
                raise state.failure
            return SimpleNamespace(scalar_one=lambda: state.committed)

        async def scalars(self, statement):
            assert "copa_telmex_players.photo_path" in str(statement)
            assert "copa_telmex_team_staff.photo_path" in str(statement)
            return [SimpleNamespace(tournament_slug="copa-telmex-2026")]

    async def scoped_owners(_session, _request, owners):
        return owners if state.visible else []

    monkeypatch.setenv("CTT_REVIEW_PHOTO_ROOT", str(root))
    monkeypatch.setattr(dashboard, "async_session_maker", Session)
    monkeypatch.setattr(dashboard, "_visible_ctt_roster_teams", scoped_owners)
    monkeypatch.setattr(
        dashboard, "_ensure_legacy_copa_dashboard_access", lambda _: None
    )
    return root, photo, state


@pytest.mark.asyncio
async def test_committed_photo_is_served_with_private_headers(delivery):
    _, photo, state = delivery
    response = await dashboard.serve_committed_registration_photo(
        photo.sha256, object()
    )
    assert response.body == photo.data
    assert response.media_type == "image/jpeg"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert state.queries == 1
    assert dashboard._public_storage_path(photo.photo_path) == (
        "/api/registration-photos/" + photo.sha256
    )


@pytest.mark.asyncio
async def test_committed_photo_outside_roster_scope_is_hidden(delivery):
    _, photo, state = delivery
    state.visible = False
    with pytest.raises(HTTPException) as denied:
        await dashboard.serve_committed_registration_photo(photo.sha256, object())
    assert denied.value.status_code == 404
    assert state.queries == 1


@pytest.mark.parametrize("committed", [False, None, 1, "true"])
@pytest.mark.asyncio
async def test_staged_file_and_truthy_values_do_not_grant_visibility(
    delivery, committed
):
    _, photo, state = delivery
    state.committed = committed
    with pytest.raises(HTTPException) as denied:
        await dashboard.serve_committed_registration_photo(photo.sha256, object())
    assert denied.value.status_code == 404
    assert denied.value.detail == "Photo unavailable"


@pytest.mark.asyncio
async def test_access_denial_precedes_storage_or_database(delivery, monkeypatch):
    _, photo, state = delivery

    def deny(_):
        raise HTTPException(status_code=403, detail="denied")

    monkeypatch.setattr(dashboard, "_ensure_legacy_copa_dashboard_access", deny)
    with pytest.raises(HTTPException) as denied:
        await dashboard.serve_committed_registration_photo(photo.sha256, object())
    assert denied.value.status_code == 403
    assert state.queries == 0


@pytest.mark.parametrize("identifier", ["../private.jpg", "A" * 64, "a" * 63])
@pytest.mark.asyncio
async def test_invalid_identifier_never_queries_database(delivery, identifier):
    _, _, state = delivery
    with pytest.raises(HTTPException) as denied:
        await dashboard.serve_committed_registration_photo(identifier, object())
    assert denied.value.status_code == 404
    assert state.queries == 0


@pytest.mark.asyncio
async def test_sql_failure_does_not_expose_diagnostics(delivery):
    _, photo, state = delivery
    state.failure = RuntimeError("PRIVATE_DATABASE_DIAGNOSTIC")
    with pytest.raises(HTTPException) as denied:
        await dashboard.serve_committed_registration_photo(photo.sha256, object())
    assert denied.value.status_code == 404
    assert denied.value.detail == "Photo unavailable"
    assert denied.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_corrupt_file_remains_hidden_even_with_committed_binding(delivery):
    root, photo, _ = delivery
    path = root / (photo.sha256 + ".jpg")
    path.chmod(0o600)
    path.write_bytes(b"corrupt")
    path.chmod(0o400)
    with pytest.raises(HTTPException) as denied:
        await dashboard.serve_committed_registration_photo(photo.sha256, object())
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_missing_configuration_does_not_query_or_fallback(delivery, monkeypatch):
    _, photo, state = delivery
    monkeypatch.delenv("CTT_REVIEW_PHOTO_ROOT")
    with pytest.raises(HTTPException) as denied:
        await dashboard.serve_committed_registration_photo(photo.sha256, object())
    assert denied.value.status_code == 404
    assert state.queries == 0
