from __future__ import annotations

from devnous.tournaments.instances.copa_telmex.ctt_review_ui import (
    build_canonical_review_view,
)


def _sidecar(
    *, relative_path: str = "review_sessions/session/canonical_shadow/player_01.jpg"
):
    return {
        "canonical_shadow": {
            "schema_version": "ctt.canonical_review.v1",
            "accepted": True,
            "authoritative": False,
            "canonical_hash": "abc123",
            "team": {
                "name": "Deportivo Estrellas",
                "category": "Libre",
                "gender": "Femenil",
            },
            "manager": {"name": "Ana", "email": "ana@example.test"},
            "players": [
                {
                    "slot": 1,
                    "source_page": 1,
                    "source_slot": 1,
                    "name": "María López",
                    "birth_date": "01/01/2000",
                    "curp": "",
                    "confidence": 0.943,
                    "requires_review": True,
                    "validation_codes": ["birth_date_ambiguous"],
                    "photo_preview": {"relative_path": relative_path},
                }
            ],
            "report": {"review_count": 1},
        }
    }


def test_returns_none_without_an_accepted_non_authoritative_sidecar() -> None:
    assert build_canonical_review_view({}) is None

    payload = _sidecar()
    payload["canonical_shadow"]["authoritative"] = True
    assert build_canonical_review_view(payload) is None


def test_builds_safe_comparison_view_with_private_preview_url() -> None:
    legacy = {
        "team": {
            "name": "Deportivo Estellas",
            "category": "Libre",
            "gender": "Femenil",
        },
        "players": [
            {
                "name": "Maria Lopes",
                "birth_date": "01/01/2000",
                "curp": "",
            }
        ],
    }

    view = build_canonical_review_view(_sidecar(), legacy)

    assert view is not None
    assert view["authoritative"] is False
    assert view["player_count"] == 1
    assert view["review_count"] == 1
    assert view["matches_legacy"] is False
    assert view["team_difference_fields"] == ["name"]
    assert view["players"][0]["difference_fields"] == ["name"]
    assert view["players"][0]["confidence_pct"] == "94%"
    assert view["players"][0]["photo_url"] == (
        "/photos/review_sessions/session/canonical_shadow/player_01.jpg"
    )


def test_rejects_preview_paths_outside_private_review_session() -> None:
    view = build_canonical_review_view(
        _sidecar(relative_path="../../etc/passwd"),
        {"players": []},
    )

    assert view is not None
    assert view["players"][0]["photo_url"] is None


def test_marks_equal_normalized_values_as_matching() -> None:
    legacy = {
        "team": {
            "name": " deportivo  estrellas ",
            "category": "libre",
            "gender": "femenil",
        },
        "players": [
            {
                "name": "MARÍA LÓPEZ",
                "birth_date": "01/01/2000",
                "curp": "",
            }
        ],
    }

    view = build_canonical_review_view(_sidecar(), legacy)

    assert view is not None
    assert view["matches_legacy"] is True
    assert view["difference_count"] == 0
