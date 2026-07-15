import io
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from PIL import Image

from devnous.tournaments.core.telegram_security import TelegramActor
from devnous.tournaments.instances.copa_telmex import (
    registration_bot as registration_module,
)
from devnous.tournaments.instances.copa_telmex.registration_bot import (
    RegistrationBotAccessPolicy,
    RegistrationBotEmployee,
    RegistrationIntakeBot,
    RegistrationIntakeTelegramAdapter,
)


def _image_bytes() -> bytes:
    image = Image.new("RGB", (640, 480), color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


class _FakeRegistrationBot:
    def __init__(self):
        self.calls = []
        self.async_session_maker = None

    async def process_registration_image(
        self, *, chat_id: int, user_id: int, image_bytes: bytes
    ):
        self.calls.append(("image", chat_id, user_id, len(image_bytes)))
        return {
            "text": "revisión creada",
            "reply_markup": {
                "inline_keyboard": [[{"text": "Abrir", "url": "https://sam.chat"}]]
            },
        }

    async def process_registration_pdf(
        self, *, chat_id: int, user_id: int, pdf_bytes: bytes
    ):
        self.calls.append(("pdf", chat_id, user_id, len(pdf_bytes)))
        return "pdf procesado"

    async def _prepare_reupload_if_needed(self, chat_id: int):
        self.calls.append(("prepare_reupload", chat_id))

    async def close_idle_session_if_needed(self, chat_id: int):
        self.calls.append(("close_idle", chat_id))
        return None

    async def finish_current_session(self, chat_id: int) -> str:
        self.calls.append(("finish", chat_id))
        return "finalizado"

    async def reset_current_session(self, chat_id: int) -> str:
        self.calls.append(("reset", chat_id))
        return "reiniciado"


class _FakeShadowObserver:
    def __init__(self):
        self.calls = []

    async def capture_page(self, chat_id, payload, *, review_session_id=None):
        self.calls.append(("capture", chat_id, len(payload), review_session_id))
        return True

    async def finalize(self, chat_id, *, review_session_id=None):
        self.calls.append(("finalize", chat_id, review_session_id))
        return True

    async def discard(self, chat_id):
        self.calls.append(("discard", chat_id))

    async def close(self):
        self.calls.append(("close",))


class _TestAdapter(RegistrationIntakeTelegramAdapter):
    def __init__(self, *args, file_bytes: bytes = b"", **kwargs):
        super().__init__(*args, **kwargs)
        self.file_bytes = file_bytes
        self.sent = []
        self.callbacks = []

    async def send_message(self, chat_id, text, *, parse_mode=None, reply_markup=None):
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return {"ok": True}

    async def answer_callback_query(self, callback_query_id, text=None):
        self.callbacks.append({"id": callback_query_id, "text": text})
        return {"ok": True}

    async def download_file(self, file_id, *, max_bytes):
        return self.file_bytes, "photos/test.jpg"


def _photo_update(*, user_id: int = 42, chat_id: int = 99):
    return {
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id},
            "from": {"id": user_id},
            "photo": [
                {"file_id": "small", "file_size": 100},
                {"file_id": "large", "file_size": 200},
            ],
        }
    }


@pytest.mark.asyncio
async def test_registration_access_allows_operations_department(monkeypatch):
    policy = RegistrationBotAccessPolicy(
        mode="db",
        allowed_roles=["superadmin"],
        allowed_departments=["operaciones"],
    )

    async def fake_lookup(user_id):
        return RegistrationBotEmployee(
            telegram_user_id=user_id,
            nombre="Operador",
            rol="capturista",
            departamento="Operaciones",
        )

    monkeypatch.setattr(policy, "_lookup_employee", fake_lookup)

    assert await policy.is_allowed(TelegramActor(chat_id=10, user_id=42)) is True


@pytest.mark.asyncio
async def test_registration_access_denies_unlisted_external_user(monkeypatch):
    policy = RegistrationBotAccessPolicy(mode="allowlist", allowed_user_ids=[42])

    assert await policy.is_allowed(TelegramActor(chat_id=10, user_id=99)) is False


@pytest.mark.asyncio
async def test_unauthorized_photo_does_not_process_media():
    bot = _FakeRegistrationBot()
    policy = RegistrationBotAccessPolicy(mode="allowlist", allowed_user_ids=[7])
    adapter = _TestAdapter(
        bot, "registration-token", access_policy=policy, file_bytes=_image_bytes()
    )

    await adapter.handle_update(_photo_update(user_id=42))

    assert bot.calls == []
    assert "Acceso restringido" in adapter.sent[-1]["text"]


@pytest.mark.asyncio
async def test_authorized_photo_creates_web_review_flow():
    bot = _FakeRegistrationBot()
    policy = RegistrationBotAccessPolicy(mode="allowlist", allowed_user_ids=[42])
    adapter = _TestAdapter(
        bot, "registration-token", access_policy=policy, file_bytes=_image_bytes()
    )

    await adapter.handle_update(_photo_update(user_id=42))

    assert bot.calls[0] == ("close_idle", 99)
    assert bot.calls[1] == ("prepare_reupload", 99)
    assert bot.calls[2][0] == "image"
    assert adapter.sent[0]["text"] == "Procesando cédula para precaptura web..."
    assert adapter.sent[-1]["text"] == "revisión creada"
    assert adapter.sent[-1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Abrir"


@pytest.mark.asyncio
async def test_general_platform_command_is_not_available():
    bot = _FakeRegistrationBot()
    policy = RegistrationBotAccessPolicy(mode="allowlist", allowed_user_ids=[42])
    adapter = _TestAdapter(bot, "registration-token", access_policy=policy)

    await adapter.handle_update(
        {
            "message": {
                "message_id": 1,
                "chat": {"id": 99},
                "from": {"id": 42},
                "text": "/registrar_pago",
            }
        }
    )

    assert bot.calls == []
    assert adapter.sent[-1]["text"] == "Comando no disponible en este bot. Usa /help."


def test_registration_intake_bot_uses_dedicated_token_and_only_operations(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("REGISTRATION_BOT_TOKEN", "registration-token")
    config_path = tmp_path / "registration_bot.yaml"
    config_path.write_text(
        """
modules:
  operations:
    enabled: true
    ocr_enabled: true
    ocr_provider: openai
    telegram_auto_web_review: true
    telegram_review_max_pages: 3
telegram:
  bot_token: "${REGISTRATION_BOT_TOKEN}"
""",
        encoding="utf-8",
    )

    bot = RegistrationIntakeBot(config_path=str(config_path))

    assert bot.telegram_token == "registration-token"
    assert bot.finance is None
    assert bot.marketing is None
    assert bot.operations.ocr_enabled is True
    assert bot.operations._telegram_review_max_pages() == 3


@pytest.mark.asyncio
async def test_registration_session_reset_clears_pending_review_state():
    bot = SimpleNamespace()
    bot.operations = SimpleNamespace(
        pending_back_photos={99: {"review_session_id": "session-1"}},
        pending_saves={99: {"openai": {"raw": True}}},
    )
    intake = RegistrationIntakeBot.__new__(RegistrationIntakeBot)
    intake.operations = bot.operations
    intake.active_sessions_by_chat = {99: "session-1"}
    intake.active_session_touched_at = {}
    intake.reupload_sessions_by_chat = {}

    async def fake_set_metadata(**kwargs):
        return {"intake_folio": "REG-2026-SESSION1"}

    intake._set_intake_metadata = fake_set_metadata

    assert (
        await intake.reset_current_session(99)
        == "Listo. Empecemos con el siguiente equipo."
    )
    assert bot.operations.pending_back_photos == {}
    assert bot.operations.pending_saves == {}


@pytest.mark.asyncio
async def test_registration_image_marks_authorized_chat_for_web_review():
    seen_admin_chats = None

    class FakeOperations:
        def __init__(self):
            self.admin_chat_ids = set()

        async def process_ocr_registration(self, message):
            nonlocal seen_admin_chats
            seen_admin_chats = set(self.admin_chat_ids)
            return "ok"

    intake = RegistrationIntakeBot.__new__(RegistrationIntakeBot)
    intake.operations = FakeOperations()
    intake.active_sessions_by_chat = {}
    intake.active_session_touched_at = {}
    intake.reupload_sessions_by_chat = {}
    intake.shadow_observer = _FakeShadowObserver()

    async def fake_sync(**kwargs):
        return None

    async def fake_decorate(**kwargs):
        return kwargs["response"]

    intake._sync_intake_metadata_after_ocr = fake_sync
    intake._decorate_response_with_folio = fake_decorate

    response = await intake.process_registration_image(
        chat_id=99,
        user_id=42,
        image_bytes=_image_bytes(),
    )

    assert response == "ok"
    assert seen_admin_chats == {99}
    assert intake.shadow_observer.calls[0][0:2] == ("capture", 99)


@pytest.mark.asyncio
async def test_registration_image_passes_existing_session_to_shadow_capture():
    class FakeOperations:
        def __init__(self):
            self.admin_chat_ids = set()
            self.pending_back_photos = {99: {"review_session_id": "session-1"}}

        async def process_ocr_registration(self, _message):
            return "ok"

    intake = RegistrationIntakeBot.__new__(RegistrationIntakeBot)
    intake.operations = FakeOperations()
    intake.active_sessions_by_chat = {}
    intake.active_session_touched_at = {}
    intake.reupload_sessions_by_chat = {}
    intake.shadow_observer = _FakeShadowObserver()

    async def fake_sync(**_kwargs):
        return None

    async def fake_decorate(**kwargs):
        return kwargs["response"]

    intake._sync_intake_metadata_after_ocr = fake_sync
    intake._decorate_response_with_folio = fake_decorate

    await intake.process_registration_image(
        chat_id=99,
        user_id=42,
        image_bytes=_image_bytes(),
    )

    assert intake.shadow_observer.calls[0] == (
        "capture",
        99,
        len(_image_bytes()),
        "session-1",
    )


@pytest.mark.asyncio
async def test_registration_pdf_closes_session_after_rendered_pages(
    monkeypatch,
) -> None:
    intake = RegistrationIntakeBot.__new__(RegistrationIntakeBot)
    intake.operations = SimpleNamespace(_telegram_review_max_pages=lambda: 3)
    processed = []
    finished = []

    async def fake_process_image(*, chat_id, user_id, image_bytes):
        processed.append((chat_id, user_id, image_bytes))
        return f"page-{len(processed)}"

    async def fake_finish(chat_id, *, reason):
        finished.append((chat_id, reason))
        return "closed"

    intake.process_registration_image = fake_process_image
    intake.finish_current_session = fake_finish
    monkeypatch.setattr(
        registration_module,
        "_render_pdf_pages",
        lambda _payload, *, max_pages: [b"front", b"back"][:max_pages],
    )

    response = await intake.process_registration_pdf(
        chat_id=99,
        user_id=42,
        pdf_bytes=b"pdf",
    )

    assert response == "page-2"
    assert [item[2] for item in processed] == [b"front", b"back"]
    assert finished == [(99, "pdf_complete")]


@pytest.mark.asyncio
async def test_registration_reset_discards_shadow_document() -> None:
    intake = RegistrationIntakeBot.__new__(RegistrationIntakeBot)
    intake.operations = SimpleNamespace(pending_back_photos={}, pending_saves={})
    intake.active_sessions_by_chat = {}
    intake.active_session_touched_at = {}
    intake.reupload_sessions_by_chat = {}
    intake.shadow_observer = _FakeShadowObserver()

    assert (
        await intake.reset_current_session(99)
        == "Listo. Empecemos con el siguiente equipo."
    )
    assert intake.shadow_observer.calls == [("discard", 99)]


@pytest.mark.asyncio
async def test_finish_passes_review_session_to_canonical_handoff() -> None:
    intake = RegistrationIntakeBot.__new__(RegistrationIntakeBot)
    intake.operations = SimpleNamespace(
        pending_back_photos={99: {"review_session_id": "session-1"}}
    )
    intake.active_sessions_by_chat = {99: "session-1"}
    intake.active_session_touched_at = {}
    intake.shadow_observer = _FakeShadowObserver()

    async def fake_set_metadata(**_kwargs):
        return {"intake_folio": "REG-2026-SESSION1"}

    intake._set_intake_metadata = fake_set_metadata

    response = await intake.finish_current_session(99)

    assert "REG-2026-SESSION1" in response
    assert intake.shadow_observer.calls == [("finalize", 99, "session-1")]


@pytest.mark.asyncio
async def test_idle_timeout_closes_previous_session_before_next_upload():
    bot = SimpleNamespace()
    bot.operations = SimpleNamespace(
        pending_back_photos={99: {"review_session_id": "session-1"}},
        pending_saves={},
    )
    intake = RegistrationIntakeBot.__new__(RegistrationIntakeBot)
    intake.operations = bot.operations
    intake.active_sessions_by_chat = {99: "session-1"}
    intake.active_session_touched_at = {
        99: datetime.now(timezone.utc).replace(year=2000),
    }
    intake.reupload_sessions_by_chat = {}
    intake.session_idle_timeout_seconds = 180

    async def fake_set_metadata(**kwargs):
        assert kwargs["closed_reason"] == "idle_timeout"
        return {"intake_folio": "REG-2026-SESSION1"}

    intake._set_intake_metadata = fake_set_metadata

    closed = await intake.close_idle_session_if_needed(99)

    assert closed == "REG-2026-SESSION1"
    assert bot.operations.pending_back_photos == {}
    assert intake.active_sessions_by_chat == {}


@pytest.mark.asyncio
async def test_folio_is_added_to_registration_response():
    intake = RegistrationIntakeBot.__new__(RegistrationIntakeBot)
    intake.active_sessions_by_chat = {99: "12345678-1234-1234-1234-123456789abc"}

    async def fake_metadata(session_id):
        return {"intake_folio": "REG-2026-12345678"}

    intake._get_intake_metadata = fake_metadata

    response = await intake._decorate_response_with_folio(
        chat_id=99,
        response={"text": "revisión creada"},
    )

    assert response["text"].endswith("Folio: REG-2026-12345678")


@pytest.mark.asyncio
async def test_estado_command_returns_folio_status(monkeypatch):
    bot = _FakeRegistrationBot()
    policy = RegistrationBotAccessPolicy(mode="allowlist", allowed_user_ids=[42])
    adapter = _TestAdapter(bot, "registration-token", access_policy=policy)

    async def fake_status(folio):
        return f"Folio {folio}\nCalidad: QUALITY_PENDING"

    bot.status_for_folio = fake_status

    await adapter.handle_update(
        {
            "message": {
                "message_id": 1,
                "chat": {"id": 99},
                "from": {"id": 42},
                "text": "/estado REG-2026-12345678",
            }
        }
    )

    assert "QUALITY_PENDING" in adapter.sent[-1]["text"]


@pytest.mark.asyncio
async def test_reponer_command_arms_existing_folio_for_next_upload():
    bot = _FakeRegistrationBot()
    policy = RegistrationBotAccessPolicy(mode="allowlist", allowed_user_ids=[42])
    adapter = _TestAdapter(bot, "registration-token", access_policy=policy)

    async def fake_start_reupload(*, chat_id, folio):
        bot.calls.append(("reponer", chat_id, folio))
        return "envía reposición"

    bot.start_reupload = fake_start_reupload

    await adapter.handle_update(
        {
            "message": {
                "message_id": 1,
                "chat": {"id": 99},
                "from": {"id": 42},
                "text": "/reponer REG-2026-12345678",
            }
        }
    )

    assert bot.calls == [("reponer", 99, "REG-2026-12345678")]
    assert adapter.sent[-1]["text"] == "envía reposición"
