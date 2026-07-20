from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

os.environ.setdefault(
    "SESSION_SECRET_KEY",
    "test-only-session-secret-key-0123456789abcdef",
)

import copa_telmex_dashboard as dashboard  # noqa: E402


class _PromotionForm(dict):
    def getlist(self, key: str):
        value = self.get(key, [])
        return list(value) if isinstance(value, (list, tuple)) else [value]


class _PromotionRequest:
    def __init__(self, form_data) -> None:
        self.form_data = form_data

    async def form(self):
        return self.form_data


class _PromotionResult:
    def __init__(self, review_session) -> None:
        self.review_session = review_session

    def scalar_one_or_none(self):
        return self.review_session


class _PromotionSession:
    def __init__(self, review_session) -> None:
        self.review_session = review_session
        self.statements = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement):
        self.statements.append(statement)
        return _PromotionResult(self.review_session)

    async def commit(self) -> None:
        self.commits += 1


def _canonical_sidecar():
    return {
        "canonical_shadow": {
            "schema_version": "ctt.canonical_review.v1",
            "accepted": True,
            "authoritative": False,
            "canonical_hash": "canonical-123",
            "document_sha256": "document-456",
            "team": {
                "name": "Deportivo Estrellas",
                "category": "Libre",
                "gender": "Femenil",
                "league": None,
                "municipality": None,
                "state": None,
                "field_evidence": {
                    "team_name": {"page": 1, "crop_id": "p1:header:team_name"}
                },
            },
            "manager": None,
            "players": [
                {
                    "slot": 1,
                    "name": "María López",
                    "birth_date": "01/01/2000",
                    "curp": None,
                    "field_evidence": {
                        "given_names": {"page": 1, "slot": 1},
                        "paternal_surname": {"page": 1, "slot": 1},
                    },
                }
            ],
        }
    }


def _review_session(status: str = "review"):
    extraction = {
        "team": {
            "name": "Deportivo Estellas",
            "category": "Libre",
            "gender": "Femenil",
        },
        "manager": None,
        "players": [
            {
                "name": "Maria Lopes",
                "birth_date": "01/01/2000",
                "curp": None,
                "confidence": 0.8,
            }
        ],
        "overall_confidence": 0.8,
    }
    committed = status == "committed"
    return SimpleNamespace(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        status=status,
        committed_at=None if not committed else dashboard.datetime(2026, 1, 1),
        committed_team_id=None if not committed else "team-id",
        tournament_slug="copa_telmex",
        provider="openai",
        assets=[],
        draft=SimpleNamespace(
            extraction=extraction,
            review_edits=None,
            validation={"audit": {"existing": True}},
            ocr_raw=_canonical_sidecar(),
            needs_review=True,
            overall_confidence=0.8,
        ),
    )


def _configure_endpoint(monkeypatch, review_session):
    session = _PromotionSession(review_session)
    session.appended_drafts = []

    async def append_version(_db, target_session, **values):
        predecessor = values.pop("expected_draft")
        successor = SimpleNamespace(**{**vars(predecessor), **values})
        session.appended_drafts.append(successor)
        target_session.draft = successor
        return successor

    monkeypatch.setattr(
        dashboard, "_ensure_registration_review_access", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(dashboard, "_canonical_promotion_enabled", lambda: True)
    monkeypatch.setattr(
        dashboard,
        "_review_session_actor",
        lambda _request: {
            "user_id": "operator-id",
            "role": "admin",
            "display_name": "Operador",
        },
    )
    monkeypatch.setattr(dashboard, "async_session_maker", lambda: session)
    monkeypatch.setattr(dashboard, "append_draft_version", append_version)
    monkeypatch.setattr(
        dashboard, "_log_registration_review_event", lambda *_args, **_kwargs: None
    )
    return session


def _form_data(*, canonical_hash: str = "canonical-123"):
    return _PromotionForm(
        {
            "canonical_hash": canonical_hash,
            "canonical_fields": ["team.name", "player.1.name"],
            "confirm_canonical_adoption": "yes",
            "canonical_value": "VALOR CONTROLADO POR EL CLIENTE",
        }
    )


def test_canonical_promotion_flag_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("CTT_CANONICAL_PROMOTION", raising=False)
    assert dashboard._canonical_promotion_enabled() is False

    for enabled_value in ("on", "true", "1", "YES"):
        monkeypatch.setenv("CTT_CANONICAL_PROMOTION", enabled_value)
        assert dashboard._canonical_promotion_enabled() is True


@pytest.mark.asyncio
async def test_retired_endpoint_cannot_bypass_reg_s05(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard, "_ensure_registration_review_access", lambda *_args, **_kwargs: None
    )

    with pytest.raises(HTTPException) as exc_info:
        await dashboard.adopt_canonical_review_fields(
            "11111111-1111-1111-1111-111111111111",
            _PromotionRequest(_form_data()),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == (
        "canonical_promotion_requires_regs05_field_resolution"
    )
