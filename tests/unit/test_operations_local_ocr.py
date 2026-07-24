import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from devnous.copa_telmex.models import (
    RegistrationReviewAsset,
    RegistrationReviewDraft,
    RegistrationReviewSession,
)
from devnous.agents.ocr_schemas import RegistrationFormExtraction
import devnous.tournaments.core.operations_module as operations_module
from devnous.tournaments.core.operations_module import OperationsModule


def _image_bytes() -> bytes:
    image = Image.new("RGB", (1200, 800), color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _extraction_dict(*, confidence: float = 0.82, team_name: str = "Club Deportivo Norte"):
    return {
        "team": {
            "name": team_name,
            "category": "Sub-15",
            "gender": "varonil",
            "league": "Liga Norte",
            "municipality": "Guadalajara",
            "state": "Jalisco",
            "confidence": confidence,
        },
        "manager": {
            "name": "Luis Garcia",
            "role": "delegado",
            "phone": "3312345678",
            "email": "luis@example.com",
            "confidence": confidence,
        },
        "players": [
            {
                "name": "Juan Perez Lopez",
                "first_name": "Juan",
                "paternal_surname": "Perez",
                "maternal_surname": "Lopez",
                "birth_date": "01/02/2011",
                "curp": "PELJ110201HJCRPN09",
                "jersey_number": 9,
                "position": "delantero",
                "photo_region": {"x": 10, "y": 120, "width": 80, "height": 100, "confidence": confidence},
                "confidence": confidence,
                "needs_review": False,
            },
            {
                "name": "Carlos Hernandez Ruiz",
                "first_name": "Carlos",
                "paternal_surname": "Hernandez",
                "maternal_surname": "Ruiz",
                "birth_date": "05/08/2011",
                "curp": "HERC110805HJCRRLA1",
                "jersey_number": 10,
                "position": "defensa",
                "photo_region": {"x": 10, "y": 240, "width": 80, "height": 100, "confidence": confidence},
                "confidence": confidence,
                "needs_review": False,
            },
        ],
        "overall_confidence": confidence,
        "notes": "local test",
    }


async def _fake_tournament_slug(*, optimized_bytes: bytes, image_b64: str):
    return "copa-telmex-2026", 0.95, "test"


class _FakeReviewSession:
    def __init__(self):
        self.added = []
        self.commit_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def commit(self):
        self.commit_calls += 1

    def by_type(self, model_type):
        return [obj for obj in self.added if isinstance(obj, model_type)]


@pytest.mark.asyncio
async def test_local_only_provider_uses_local_runner(monkeypatch) -> None:
    module = OperationsModule(
        tournament_id="copa_telmex",
        config={
            "operations": {"ocr_enabled": True, "ocr_provider": "local_only"},
            "telegram": {"admin_chat_ids": [1]},
        },
        db=None,
    )

    async def fake_local_runner(_: bytes):
        return _extraction_dict(), {"backend": "local-test"}

    monkeypatch.setattr(
        module.local_ocr_runner,
        "extract_registration_form_from_bytes_async",
        fake_local_runner,
    )
    monkeypatch.setattr(module, "_infer_tournament_slug", _fake_tournament_slug)

    response = await module.process_ocr_registration(
        SimpleNamespace(chat_id=1, user_id=99, photo=_image_bytes())
    )

    assert isinstance(response, dict)
    assert "Resultado (local)" in response["text"]
    assert response["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "stage_ocr:local"
    assert module.pending_saves[1]["local_extraction"]["team"]["name"] == "Club Deportivo Norte"


@pytest.mark.asyncio
async def test_telegram_auto_web_review_groups_up_to_three_pages(monkeypatch, tmp_path) -> None:
    fake_session = _FakeReviewSession()
    module = OperationsModule(
        tournament_id="copa_telmex",
        config={
            "operations": {
                "ocr_enabled": True,
                "ocr_provider": "openai",
                "telegram_review_max_pages": 3,
            },
            "telegram": {"admin_chat_ids": [1]},
        },
        db=lambda: fake_session,
    )
    module.photos_base_dir = tmp_path

    extraction = RegistrationFormExtraction.model_validate(_extraction_dict(confidence=0.91))

    async def fake_extract(provider: str, optimized_bytes: bytes, image_b64: str):
        assert provider == "openai"
        return extraction, {"provider": "openai-test"}

    monkeypatch.setattr(module, "_extract_registration_form", fake_extract)
    monkeypatch.setattr(module, "_infer_tournament_slug", _fake_tournament_slug)
    appended_pages = []

    async def fake_append_draft_version(session, review_session, **kwargs):
        draft = RegistrationReviewDraft(
            session_id=review_session.id,
            content_hash="sha256:test-draft",
            mutation_type=str(kwargs.get("mutation_type") or "test"),
            mutation_operation_id="test-operation",
            mutation_decision_id="sha256:test-decision",
            mutation_receipt_id="test-receipt",
        )
        session.add(draft)
        await session.flush()
        return draft

    monkeypatch.setattr(
        operations_module,
        "append_draft_version",
        fake_append_draft_version,
    )

    async def fake_append_back_photo(**kwargs):
        appended_pages.append(kwargs)
        return True, "https://sam.chat/registration-review/session-1"

    monkeypatch.setattr(module, "_append_back_photo_to_review_session", fake_append_back_photo)

    first = await module.process_ocr_registration(
        SimpleNamespace(chat_id=1, user_id=99, photo=_image_bytes())
    )
    second = await module.process_ocr_registration(
        SimpleNamespace(chat_id=1, user_id=99, photo=_image_bytes())
    )
    third = await module.process_ocr_registration(
        SimpleNamespace(chat_id=1, user_id=99, photo=_image_bytes())
    )

    assert isinstance(first, dict)
    assert "enviada a precaptura web" in first["text"]
    assert "Página agregada" in second
    assert "Página agregada" in third
    assert len(appended_pages) == 2
    assert len(fake_session.by_type(RegistrationReviewSession)) == 1
    assert len(fake_session.by_type(RegistrationReviewAsset)) == 1
    assert len(fake_session.by_type(RegistrationReviewDraft)) == 1
    assert module.pending_back_photos == {}
    assert module.pending_saves == {}


def test_openai_payload_normalization_allows_headerless_later_page() -> None:
    module = OperationsModule(
        tournament_id="copa_telmex",
        config={"operations": {"ocr_enabled": True}},
        db=None,
    )

    normalized = module._normalize_openai_registration_payload(
        {
            "team": {"name": None, "state": None, "municipality": None},
            "manager": {"name": None, "phone": None},
            "players": [
                {
                    "name": "Luis Perez Lopez",
                    "birth_date": "01/01/2012",
                    "confidence": 0.8,
                    "needs_review": False,
                },
                {"name": "", "birth_date": "", "curp": ""},
            ],
            "overall_confidence": 0.7,
        }
    )

    extraction = RegistrationFormExtraction.model_validate(normalized)

    assert extraction.team.name == "Unknown Team"
    assert extraction.manager is None
    assert len(extraction.players) == 1
    assert extraction.players[0].name == "Luis Perez Lopez"


def test_openai_ctt_prompt_is_template_aware_for_multiple_pages() -> None:
    module = OperationsModule(
        tournament_id="copa_telmex",
        config={"operations": {"ocr_enabled": True}},
        db=None,
    )

    prompt = module._openai_ctt_prompt(page_count=2)

    assert "CEDULA DE INSCRIPCION Copa Telmex Telcel 2026" in prompt
    assert "Son 2 paginas del mismo expediente" in prompt
    assert "Extrae por casilla del formulario" in prompt
    assert "photo_region debe ser null" in prompt
    assert "Tacambaro/Tacámbaro" in prompt


def test_operations_openai_montage_is_built_from_template_cells() -> None:
    module = OperationsModule(
        tournament_id="copa_telmex",
        config={"operations": {"ocr_enabled": True}},
        db=None,
    )
    image_b64 = __import__("base64").b64encode(_image_bytes()).decode("utf-8")

    montage_url = module._build_ctt_openai_montage_url([image_b64])

    assert montage_url is not None
    assert montage_url.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_local_first_falls_back_to_remote_when_local_quality_is_low(monkeypatch) -> None:
    module = OperationsModule(
        tournament_id="copa_telmex",
        config={
            "operations": {"ocr_enabled": True, "ocr_provider": "local_first"},
            "telegram": {"admin_chat_ids": [1]},
        },
        db=None,
    )
    module.anthropic_key = "test-key"
    module.ocr_agent = object()

    low_local = RegistrationFormExtraction.model_validate(_extraction_dict(confidence=0.2))
    remote_good = RegistrationFormExtraction.model_validate(_extraction_dict(confidence=0.91))

    async def fake_extract(provider: str, optimized_bytes: bytes, image_b64: str):
        if provider == "local":
            return low_local, {"backend": "local-low"}
        if provider == "anthropic":
            return remote_good, {"backend": "anthropic-good"}
        raise AssertionError(f"unexpected provider {provider}")

    monkeypatch.setattr(module, "_extract_registration_form", fake_extract)
    monkeypatch.setattr(module, "_infer_tournament_slug", _fake_tournament_slug)

    response = await module.process_ocr_registration(
        SimpleNamespace(chat_id=1, user_id=99, photo=_image_bytes())
    )

    assert isinstance(response, dict)
    assert "fallback remoto" in response["text"]
    assert response["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "stage_ocr:anthropic"
