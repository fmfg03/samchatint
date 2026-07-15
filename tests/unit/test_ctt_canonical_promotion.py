from __future__ import annotations

import copy

import pytest

from devnous.tournaments.instances.copa_telmex.ctt_canonical_promotion import (
    CanonicalPromotionError,
    promote_canonical_fields,
)


def _raw_payload():
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
                "league": "Liga Centro",
                "municipality": "Guadalajara",
                "state": "Jalisco",
                "field_evidence": {
                    "team_name": {"page": 1, "crop_id": "p1:header:team_name"}
                },
            },
            "manager": {
                "name": "Ana Pérez",
                "email": "ana@example.test",
                "field_evidence": {"email": {"page": 1, "crop_id": "p1:header:email"}},
            },
            "players": [
                {
                    "slot": 1,
                    "name": "María López",
                    "birth_date": "01/01/2000",
                    "curp": "LOPM000101MJCPRR01",
                    "field_evidence": {
                        "given_names": {"page": 1, "slot": 1},
                        "paternal_surname": {"page": 1, "slot": 1},
                        "birth_date": {"page": 1, "slot": 1},
                        "curp": {"page": 1, "slot": 1},
                    },
                }
            ],
        }
    }


def _legacy_extraction():
    return {
        "team": {
            "name": "Deportivo Estellas",
            "category": "Libre",
            "gender": "Femenil",
        },
        "manager": {"name": "Ana", "email": "a@legacy.test"},
        "players": [
            {
                "name": "Maria Lopes",
                "birth_date": "01/01/2000",
                "curp": None,
            }
        ],
        "notes": "Conservar",
    }


def _promote(selections):
    return promote_canonical_fields(
        _raw_payload(),
        _legacy_extraction(),
        selections,
        expected_hash="canonical-123",
        actor={"user_id": "operator-id", "role": "admin"},
        promoted_at="2026-07-15T12:00:00Z",
    )


def test_promotes_only_selected_fields_and_preserves_evidence() -> None:
    original = _legacy_extraction()
    snapshot = copy.deepcopy(original)

    result = promote_canonical_fields(
        _raw_payload(),
        original,
        ["team.name", "manager.email", "player.1.name", "player.1.curp"],
        expected_hash="canonical-123",
        actor={"user_id": "operator-id", "role": "admin"},
        promoted_at="2026-07-15T12:00:00Z",
    )

    assert original == snapshot
    assert result.extraction["team"]["name"] == "Deportivo Estrellas"
    assert result.extraction["team"]["category"] == "Libre"
    assert result.extraction["manager"]["name"] == "Ana"
    assert result.extraction["manager"]["email"] == "ana@example.test"
    assert result.extraction["players"][0]["name"] == "María López"
    assert result.extraction["players"][0]["birth_date"] == "01/01/2000"
    assert result.extraction["players"][0]["curp"] == "LOPM000101MJCPRR01"
    assert result.extraction["notes"] == "Conservar"
    assert [event["path"] for event in result.field_events] == [
        "team.name",
        "manager.email",
        "players[0].name",
        "players[0].curp",
    ]
    team_event = result.field_events[0]
    assert team_event["before"] == "Deportivo Estellas"
    assert team_event["after"] == "Deportivo Estrellas"
    assert team_event["source"] == "canonical_promotion"
    assert team_event["canonical_hash"] == "canonical-123"
    assert team_event["document_sha256"] == "document-456"
    assert team_event["evidence"]["team_name"]["page"] == 1
    player_event = result.field_events[2]
    assert set(player_event["evidence"]) == {
        "given_names",
        "paternal_surname",
    }


def test_rejects_stale_sidecar_hash() -> None:
    with pytest.raises(CanonicalPromotionError) as exc_info:
        promote_canonical_fields(
            _raw_payload(),
            _legacy_extraction(),
            ["team.name"],
            expected_hash="stale-hash",
            actor={"user_id": "operator-id", "role": "admin"},
            promoted_at="2026-07-15T12:00:00Z",
        )

    assert exc_info.value.code == "canonical_sidecar_changed"


def test_uses_canonical_draft_evidence_for_existing_sidecars() -> None:
    payload = _raw_payload()
    payload["canonical_shadow"]["team"].pop("field_evidence")
    payload["canonical_shadow"]["canonical_draft"] = {
        "team": {
            "fields": {
                "name": {"evidence": {"page": 1, "crop_id": "p1:header:team_name"}}
            }
        }
    }

    result = promote_canonical_fields(
        payload,
        _legacy_extraction(),
        ["team.name"],
        expected_hash="canonical-123",
        actor={"user_id": "operator-id", "role": "admin"},
        promoted_at="2026-07-15T12:00:00Z",
    )

    assert result.field_events[0]["evidence"]["team_name"]["crop_id"] == (
        "p1:header:team_name"
    )


def test_rejects_field_without_verifiable_evidence() -> None:
    payload = _raw_payload()
    payload["canonical_shadow"]["team"].pop("field_evidence")

    with pytest.raises(CanonicalPromotionError) as exc_info:
        promote_canonical_fields(
            payload,
            _legacy_extraction(),
            ["team.name"],
            expected_hash="canonical-123",
            actor={"user_id": "operator-id", "role": "admin"},
            promoted_at="2026-07-15T12:00:00Z",
        )

    assert exc_info.value.code == "canonical_evidence_missing"


@pytest.mark.parametrize(
    "selection",
    [
        "team.__class__",
        "manager.phone",
        "player.1.confidence",
        "player.one.name",
        "players.1.name",
        "team.name.extra",
    ],
)
def test_rejects_non_allowlisted_field_paths(selection: str) -> None:
    with pytest.raises(CanonicalPromotionError) as exc_info:
        _promote([selection])

    assert exc_info.value.code == "canonical_field_invalid"


def test_rejects_canonical_player_without_existing_legacy_slot() -> None:
    with pytest.raises(CanonicalPromotionError) as exc_info:
        _promote(["player.2.name"])

    assert exc_info.value.code == "canonical_player_slot_missing"


def test_rejects_noop_selection_without_adding_audit_noise() -> None:
    with pytest.raises(CanonicalPromotionError) as exc_info:
        _promote(["team.category", "player.1.birth_date"])

    assert exc_info.value.code == "canonical_no_changes"


def test_rejects_unaccepted_or_authoritative_sidecar() -> None:
    for key, value in (("accepted", False), ("authoritative", True)):
        payload = _raw_payload()
        payload["canonical_shadow"][key] = value
        with pytest.raises(CanonicalPromotionError) as exc_info:
            promote_canonical_fields(
                payload,
                _legacy_extraction(),
                ["team.name"],
                expected_hash="canonical-123",
                actor={"user_id": "operator-id", "role": "admin"},
                promoted_at="2026-07-15T12:00:00Z",
            )

        assert exc_info.value.code == "canonical_sidecar_unavailable"
