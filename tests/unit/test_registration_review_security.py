import asyncio
import io
import os
from types import SimpleNamespace
from typing import Optional
from uuid import uuid4
from datetime import datetime

import pytest
from PIL import Image
from starlette.datastructures import FormData
from starlette.requests import Request

os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret")

import copa_telmex_dashboard as dashboard


def _request(path: str, *, method: str = "GET", session: Optional[dict] = None) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "session": session or {},
    }
    return Request(scope)


def _png_bytes(size=(240, 320), color="white") -> bytes:
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class _FakeUpload:
    def __init__(self, filename: str, payload: bytes, content_type: str):
        self.filename = filename
        self.content_type = content_type
        self._payload = payload
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._payload):
            return b""
        if size is None or size < 0:
            chunk = self._payload[self._offset :]
            self._offset = len(self._payload)
            return chunk
        end = min(self._offset + size, len(self._payload))
        chunk = self._payload[self._offset : end]
        self._offset = end
        return chunk


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, review_session):
        self.review_session = review_session
        self.commit_calls = 0

    async def execute(self, _statement):
        return _FakeResult(self.review_session)

    async def commit(self):
        self.commit_calls += 1


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_registration_review_requires_auth_for_html_routes():
    request = _request("/registration-review/new")
    response = asyncio.run(dashboard.new_registration_review_session(request))

    assert response.status_code == 307
    assert response.headers["location"].startswith("/login?next=")


def test_registration_review_requires_auth_for_api_routes():
    request = _request("/api/registration-review", method="POST")

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard.create_registration_review_session(request))

    assert exc_info.value.status_code == 401


def test_registration_review_forbidden_for_wrong_role():
    request = _request(
        "/registration-review/new",
        session={"empleado_id": str(uuid4()), "rol": "empleado"},
    )

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard.new_registration_review_session(request))

    assert exc_info.value.status_code == 403


def test_registration_review_allows_permitted_role():
    request = _request(
        "/registration-review/new",
        session={"empleado_id": str(uuid4()), "rol": "admin"},
    )

    response = asyncio.run(dashboard.new_registration_review_session(request))

    assert response.status_code == 200


def test_legacy_dashboard_requires_auth_for_players():
    request = _request("/players")

    response = asyncio.run(dashboard.list_players(request))

    assert response.status_code == 307
    assert response.headers["location"].startswith("/login?next=")


def test_legacy_dashboard_requires_auth_for_teams():
    request = _request("/teams")

    response = asyncio.run(dashboard.list_teams(request))

    assert response.status_code == 307
    assert response.headers["location"].startswith("/login?next=")


def test_legacy_dashboard_requires_auth_for_dashboard_home():
    request = _request("/dashboard")

    response = asyncio.run(dashboard.home(request))

    assert response.status_code == 307
    assert response.headers["location"].startswith("/login?next=")


def test_legacy_dashboard_stats_api_requires_auth():
    request = _request("/api/stats")

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard.get_stats(request))

    assert exc_info.value.status_code in {401, 403}


def test_legacy_dashboard_requires_auth_for_team_detail():
    team_id = str(uuid4())
    request = _request(f"/team/{team_id}")

    response = asyncio.run(dashboard.view_team(request, team_id))

    assert response.status_code == 307
    assert response.headers["location"].startswith("/login?next=")


def test_legacy_dashboard_forbids_generic_empleado_role_for_team_detail():
    team_id = str(uuid4())
    request = _request(
        f"/team/{team_id}",
        session={"empleado_id": str(uuid4()), "rol": "empleado"},
    )

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard.view_team(request, team_id))

    assert exc_info.value.status_code == 403


def test_sensitive_photo_assets_require_auth(tmp_path, monkeypatch):
    sensitive_asset = tmp_path / "review_sessions" / "session-1" / "page-01.png"
    sensitive_asset.parent.mkdir(parents=True)
    sensitive_asset.write_bytes(_png_bytes())
    monkeypatch.setattr(dashboard, "photos_dir", tmp_path)

    request = _request("/photos/review_sessions/session-1/page-01.png")

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard.serve_photo_asset("review_sessions/session-1/page-01.png", request))

    assert exc_info.value.status_code in {401, 403}


@pytest.mark.parametrize(
    "asset_path",
    [
        "../.secrets/foo",
        "%2e%2e/.secrets/foo",
        "review_sessions/../../.env",
    ],
)
def test_photo_assets_reject_path_escape(tmp_path, monkeypatch, asset_path):
    monkeypatch.setattr(dashboard, "photos_dir", tmp_path)
    request = _request(f"/photos/{asset_path}")

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard.serve_photo_asset(asset_path, request))

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    "asset_path",
    [
        "public/../review_sessions/session-1/page-01.png",
        "public/%2e%2e/review_sessions/session-1/page-01.png",
    ],
)
def test_public_photo_prefix_cannot_bypass_sensitive_auth(tmp_path, monkeypatch, asset_path):
    sensitive_asset = tmp_path / "review_sessions" / "session-1" / "page-01.png"
    sensitive_asset.parent.mkdir(parents=True)
    sensitive_asset.write_bytes(_png_bytes())
    monkeypatch.setattr(dashboard, "photos_dir", tmp_path)

    request = _request(f"/photos/{asset_path}")

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard.serve_photo_asset(asset_path, request))

    assert exc_info.value.status_code == 404


def test_public_photo_symlink_cannot_escape_public_root(tmp_path, monkeypatch):
    secret_file = tmp_path / "private.txt"
    secret_file.write_text("secret", encoding="utf-8")
    public_dir = tmp_path / "public"
    public_dir.mkdir()
    (public_dir / "leak.txt").symlink_to(secret_file)
    monkeypatch.setattr(dashboard, "photos_dir", tmp_path)

    request = _request("/photos/public/leak.txt")

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard.serve_photo_asset("public/leak.txt", request))

    assert exc_info.value.status_code == 404


def test_public_photo_assets_remain_public(tmp_path, monkeypatch):
    public_asset = tmp_path / "public" / "badge.png"
    public_asset.parent.mkdir(parents=True)
    public_asset.write_bytes(_png_bytes())
    monkeypatch.setattr(dashboard, "photos_dir", tmp_path)

    response = asyncio.run(dashboard.serve_photo_asset("public/badge.png", _request("/photos/public/badge.png")))

    assert response.status_code == 200


def test_authorized_role_can_read_sensitive_photo_assets(tmp_path, monkeypatch):
    sensitive_asset = tmp_path / "review_sessions" / "session-1" / "page-01.png"
    sensitive_asset.parent.mkdir(parents=True)
    sensitive_asset.write_bytes(_png_bytes())
    monkeypatch.setattr(dashboard, "photos_dir", tmp_path)
    request = _request(
        "/photos/review_sessions/session-1/page-01.png",
        session={"empleado_id": str(uuid4()), "rol": "admin"},
    )

    response = asyncio.run(dashboard.serve_photo_asset("review_sessions/session-1/page-01.png", request))

    assert response.status_code == 200


def test_authorized_role_can_view_team_detail(monkeypatch):
    team_id = uuid4()
    player_id = uuid4()
    now = datetime(2026, 1, 1, 12, 0)
    team = SimpleNamespace(
        id=team_id,
        name="Club Seguro",
        category="2010",
        gender="Mixto",
        league="Liga",
        league_phone="555",
        league_address="Calle 1",
        representative_name="Ana",
        contact_phone="555",
        state="CDMX",
        municipality="Benito Juarez",
        roster_image_path="/photos/review_sessions/session-1/roster.png",
        created_at=now,
    )
    player = SimpleNamespace(
        id=player_id,
        team_id=team_id,
        full_name="Jugador Seguro",
        birth_date=now.date(),
        curp="XXXX000000XXXXXX00",
        email="jugador@example.test",
        photo_path="/photos/review_sessions/session-1/player.png",
        ocr_confidence=0.95,
        needs_review=False,
        verified_by_human=True,
        created_at=now,
    )

    class _FakeCopaDB:
        def __init__(self, _session):
            pass

        async def get_team_by_id(self, requested_team_id):
            return team if requested_team_id == team_id else None

        async def get_players_by_team(self, requested_team_id):
            return [player] if requested_team_id == team_id else []

    monkeypatch.setattr(dashboard, "async_session_maker", lambda: _FakeSessionContext(object()))
    monkeypatch.setattr(dashboard, "CopaTelmexDB", _FakeCopaDB)
    request = _request(
        f"/team/{team_id}",
        session={"empleado_id": str(uuid4()), "rol": "admin"},
    )

    response = asyncio.run(dashboard.view_team(request, str(team_id)))

    assert response.status_code == 200


def test_session_secret_missing_fails_fast(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        dashboard._require_session_secret_key()

    assert "SESSION_SECRET_KEY" in str(exc_info.value)


def test_production_missing_database_url_fails_fast(monkeypatch):
    monkeypatch.setenv("SAMCHAT_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        dashboard._require_database_url_for_runtime()

    assert "DATABASE_URL" in str(exc_info.value)


def test_dev_missing_database_url_keeps_local_fallback(monkeypatch):
    for name in ("SAMCHAT_ENV", "ENVIRONMENT", "APP_ENV", "FASTAPI_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    db_url = dashboard._require_database_url_for_runtime()

    assert db_url.startswith("postgresql+asyncpg://")


def test_valid_upload_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "review_uploads_dir", tmp_path)
    upload = _FakeUpload("cedula.png", _png_bytes(), "image/png")

    stored = asyncio.run(dashboard._store_review_uploads(uuid4(), [upload]))

    assert len(stored) == 1
    assert stored[0]["width"] == 240
    assert stored[0]["height"] == 320
    assert stored[0]["image_path"].endswith(".png")


def test_invalid_extension_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "review_uploads_dir", tmp_path)
    upload = _FakeUpload("cedula.svg", b"<svg></svg>", "image/svg+xml")

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard._store_review_uploads(uuid4(), [upload]))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "invalid_file_type"


def test_invalid_mime_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "review_uploads_dir", tmp_path)
    upload = _FakeUpload("cedula.png", _png_bytes(), "text/plain")

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard._store_review_uploads(uuid4(), [upload]))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "invalid_mime_type"


def test_oversized_upload_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "review_uploads_dir", tmp_path)
    monkeypatch.setattr(dashboard, "MAX_REVIEW_UPLOAD_BYTES", 8)
    upload = _FakeUpload("cedula.png", b"123456789", "image/png")

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard._store_review_uploads(uuid4(), [upload]))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "file_too_large"


def test_corrupt_image_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "review_uploads_dir", tmp_path)
    upload = _FakeUpload("cedula.png", b"not-an-image", "image/png")

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard._store_review_uploads(uuid4(), [upload]))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "corrupt_image"


def test_no_save_before_commit():
    extraction = {
        "team": {"name": "Club Demo"},
        "manager": {"name": "Ana"},
        "players": [
            {
                "name": "Juan Perez",
                "birth_date": "01/01/2010",
                "curp": None,
                "confidence": 0.91,
                "needs_review": False,
            }
        ],
    }

    validation = dashboard._build_review_commit_validation(
        extraction,
        dashboard._build_review_validation(extraction),
    )

    assert validation["ready_to_commit"] is True
    assert all(item["code"] != "DRAFT_MISSING" for item in validation["blockers"])


def test_commit_blocked_when_no_team(monkeypatch):
    draft = SimpleNamespace(
        ocr_raw={},
        review_edits={
            "team": {"name": ""},
            "manager": {"name": "Ana"},
            "players": [{"name": "Juan Perez", "birth_date": "01/01/2010", "curp": None}],
        },
        extraction=None,
        validation={},
        needs_review=False,
        overall_confidence=0.8,
    )
    review_session = SimpleNamespace(
        id=uuid4(),
        draft=draft,
        tournament_slug="copa_telmex",
        status="ready",
        committed_at=None,
        committed_team_id=None,
        assets=[],
        telegram_chat_id=None,
        telegram_user_id=None,
        provider="local",
    )
    fake_session = _FakeSession(review_session)
    monkeypatch.setattr(dashboard, "async_session_maker", lambda: _FakeSessionContext(fake_session))
    monkeypatch.setattr(dashboard, "CopaTelmexDB", lambda session: pytest.fail("No debería persistir en commit bloqueado"))

    request = _request(
        f"/api/registration-review/{review_session.id}/commit",
        method="POST",
        session={"empleado_id": str(uuid4()), "rol": "admin"},
    )
    request._form = FormData({"tournament_slug": "copa_telmex", "player_count": "1"})

    async def _fake_form():
        return request._form

    request.form = _fake_form

    with pytest.raises(dashboard.HTTPException) as exc_info:
        asyncio.run(dashboard.commit_registration_review_session(str(review_session.id), request))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["status"] == "blocked"
    assert any(item["code"] == "NO_TEAM_NAME" for item in exc_info.value.detail["blockers"])
    assert fake_session.commit_calls == 1


def test_commit_blocked_when_no_valid_players():
    extraction = {
        "team": {"name": "Club Demo"},
        "manager": {"name": "Ana"},
        "players": [{"name": "", "birth_date": "", "curp": ""}],
    }

    validation = dashboard._build_review_commit_validation(
        extraction,
        dashboard._build_review_validation(extraction),
    )

    assert validation["ready_to_commit"] is False
    assert any(item["code"] == "NO_VALID_PLAYERS" for item in validation["blockers"])


def test_commit_blocked_when_player_missing_name():
    extraction = {
        "team": {"name": "Club Demo"},
        "manager": {"name": "Ana"},
        "players": [{"name": "", "birth_date": "01/01/2010", "curp": "PEJJ100101HDFRRN01"}],
    }

    validation = dashboard._build_review_commit_validation(
        extraction,
        dashboard._build_review_validation(extraction),
    )

    assert any(item["code"] == "PLAYER_MISSING_NAME" for item in validation["blockers"])


def test_commit_blocked_when_invalid_birthdate():
    extraction = {
        "team": {"name": "Club Demo"},
        "manager": {"name": "Ana"},
        "players": [{"name": "Juan Perez", "birth_date": "02/07/08", "curp": "PEJJ100101HDFRRN01"}],
    }

    validation = dashboard._build_review_commit_validation(
        extraction,
        dashboard._build_review_validation(extraction),
    )

    assert any(item["code"] == "PLAYER_INVALID_BIRTHDATE" for item in validation["blockers"])


def test_commit_allows_non_blocking_warnings():
    extraction = {
        "team": {"name": "Club Demo"},
        "manager": {"name": "Ana", "email": ""},
        "players": [{"name": "Juan Perez", "birth_date": "01/01/2010", "curp": "", "confidence": 0.91}],
    }

    validation = dashboard._build_review_commit_validation(
        extraction,
        dashboard._build_review_validation(extraction),
    )

    assert validation["ready_to_commit"] is True
    assert validation["warnings"]
    assert any(item["code"] == "MISSING_OPTIONAL_CURP" for item in validation["warnings"])


def test_already_committed_session_does_not_duplicate():
    extraction = {
        "team": {"name": "Club Demo"},
        "manager": {"name": "Ana"},
        "players": [{"name": "Juan Perez", "birth_date": "01/01/2010", "curp": "PEJJ100101HDFRRN01"}],
    }
    review_session = SimpleNamespace(
        draft=SimpleNamespace(),
        status="committed",
        committed_at=object(),
        committed_team_id=uuid4(),
    )

    validation = dashboard._build_review_commit_validation(
        extraction,
        dashboard._build_review_validation(extraction),
        review_session=review_session,
    )

    assert validation["ready_to_commit"] is False
    assert any(item["code"] == "SESSION_ALREADY_COMMITTED" for item in validation["blockers"])
