import os

os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret")

import copa_telmex_dashboard as dashboard


def _player(idx: int, **overrides):
    payload = {
        "name": f"Jugador {idx} Perez",
        "birth_date": "01/01/2010",
        "curp": "",
        "confidence": 0.91,
        "needs_review": False,
    }
    payload.update(overrides)
    return payload


def _extraction(players):
    return {
        "team": {
            "name": "Club Demo",
            "category": "Juvenil",
            "gender": "varonil",
        },
        "manager": {"name": "Ana"},
        "players": players,
    }


def test_incident_policy_counts_nonblocking_pending_players_as_eligible():
    players = [_player(idx) for idx in range(1, 16)]
    players.extend(
        _player(idx, confidence=0.5, needs_review=True)
        for idx in range(16, 19)
    )
    players.extend(
        _player(idx, birth_date="02/07/08") for idx in range(19, 21)
    )

    policy = dashboard._build_registration_incident_policy(
        _extraction(players)
    )

    assert policy["schema_version"] == "registration_incidents.v1"
    assert policy["team_decision"] == "REGISTERED_WITH_INCIDENTS"
    assert policy["summary"] == {
        "total_players": 20,
        "cleared_players": 15,
        "pending_nonblocking_players": 3,
        "pending_blocking_players": 2,
        "rejected_players": 0,
        "eligible_players": 18,
        "incident_count": 5,
    }


def test_incident_policy_blocks_team_only_below_minimum():
    players = [_player(idx) for idx in range(1, 16)]
    players.extend(
        _player(idx, birth_date="02/07/08") for idx in range(16, 18)
    )

    policy = dashboard._build_registration_incident_policy(
        _extraction(players)
    )

    assert policy["team_decision"] == "PENDING_MINIMUM_ROSTER"
    assert policy["summary"]["eligible_players"] == 15
    assert all(
        incident["blocks_team_registration"] is False
        for result in policy["player_results"]
        for incident in result["incidents"]
    )


def test_sixteen_eligible_players_allow_registration_with_incidents():
    players = [_player(idx) for idx in range(1, 17)]
    players.extend(
        _player(idx, birth_date="02/07/08") for idx in range(17, 19)
    )

    policy = dashboard._build_registration_incident_policy(
        _extraction(players)
    )

    assert policy["team_decision"] == "REGISTERED_WITH_INCIDENTS"
    assert policy["summary"]["eligible_players"] == 16
    assert dashboard._players_blocked_by_incident_policy(policy) == {17, 18}


def test_incident_policy_is_deterministic_for_same_inputs():
    players = [_player(idx) for idx in range(1, 16)]
    players.extend(
        _player(idx, birth_date="02/07/08") for idx in range(16, 18)
    )

    first = dashboard._build_registration_incident_policy(
        _extraction(players)
    )
    second = dashboard._build_registration_incident_policy(
        _extraction(players)
    )

    assert first == second


def test_photo_duplicate_inside_draft_blocks_both_players():
    players = [_player(idx) for idx in range(1, 17)]
    photo_artifacts = {
        1: {
            "photo_sha256": "same-sha",
            "photo_ahash": "0000000000000000",
        },
        2: {
            "photo_sha256": "same-sha",
            "photo_ahash": "0000000000000000",
        },
    }

    policy = dashboard._build_registration_incident_policy(
        _extraction(players),
        photo_artifacts=photo_artifacts,
    )

    assert dashboard._players_blocked_by_incident_policy(policy) == {1, 2}
    assert {
        policy["player_results"][idx]["incidents"][0]["incident_type"]
        for idx in (0, 1)
    } == {"PHOTO_EXACT_DUPLICATE"}


def test_single_photo_artifact_does_not_match_itself():
    players = [_player(idx) for idx in range(1, 17)]

    policy = dashboard._build_registration_incident_policy(
        _extraction(players),
        photo_artifacts={
            1: {
                "photo_sha256": "same-sha",
                "photo_ahash": "0000000000000000",
            }
        },
    )

    assert policy["summary"]["incident_count"] == 0
    assert policy["team_decision"] == "REGISTERED"


def test_tournament_perceptual_duplicate_creates_blocking_incident():
    players = [_player(idx) for idx in range(1, 17)]
    existing = [
        {
            "player_ref": "player-981",
            "player_name": "Betzabe Ruiz Cruz",
            "team_ref": "team-47",
            "team_name": "C. F. Morritos",
            "tournament_slug": "copa_telmex",
            "photo_sha256": "old-sha",
            "photo_ahash": "0000000000000003",
        }
    ]

    policy = dashboard._build_registration_incident_policy(
        _extraction(players),
        photo_artifacts={
            7: {
                "photo_sha256": "new-sha",
                "photo_ahash": "0000000000000000",
            }
        },
        existing_photo_records=existing,
    )

    incident = policy["player_results"][6]["incidents"][0]
    assert incident["incident_type"] == "PHOTO_PERCEPTUAL_DUPLICATE"
    assert incident["blocks_player_eligibility"] is True
    assert incident["evidence"]["matching_player_ref"] == "player-981"
    assert incident["evidence"]["hash_distance"] <= 4


def test_commit_validation_binds_policy_and_enforces_minimum():
    players = [_player(idx) for idx in range(1, 16)]
    extraction = _extraction(players)
    policy = dashboard._build_registration_incident_policy(extraction)

    validation = dashboard._build_review_commit_validation(
        extraction,
        {},
        incident_policy=policy,
    )

    assert validation["incident_policy"] == policy
    assert validation["ready_to_commit"] is False
    assert {item["code"] for item in validation["blockers"]} == {
        "PENDING_MINIMUM_ROSTER"
    }


def test_commit_validation_permits_sixteen_eligible_players():
    extraction = _extraction([_player(idx) for idx in range(1, 17)])
    policy = dashboard._build_registration_incident_policy(extraction)

    validation = dashboard._build_review_commit_validation(
        extraction,
        {},
        incident_policy=policy,
    )

    assert validation["incident_policy"] == policy
    assert validation["blockers"] == []
    assert validation["ready_to_commit"] is True
