"""Incident recounting never promotes a supplied zero into authority."""

from copy import deepcopy
import pytest

from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_finalizing_writer import (
    _blocking_incident_count,
    _private_json,
)


def validation():
    return {
        "incident_policy": {
            "player_results": [
                {"player_index": 1, "eligibility_status": "CLEARED", "incidents": []},
                {
                    "player_index": 3,
                    "eligibility_status": "PENDING_NONBLOCKING",
                    "incidents": [],
                },
            ]
        }
    }


def test_recounts_exact_inventory_preserving_printed_gaps():
    assert _blocking_incident_count(validation(), [1, 3]) == 0
    data = validation()
    data["incident_policy"]["player_results"][1]["incidents"] = [
        {"blocks_player_eligibility": True, "blocks_team_registration": False},
        {"blocks_player_eligibility": False, "blocks_team_registration": True},
    ]
    data["blocking_incident_count"] = 0
    assert _blocking_incident_count(data, [1, 3]) == 2
    data["blockers"] = [{"code": "EXPLICIT_BLOCKER"}]
    assert _blocking_incident_count(data, [1, 3]) == 3


@pytest.mark.parametrize("status", ["REJECTED", "PENDING_BLOCKING"])
def test_blocking_status_is_not_ignored_when_incident_list_is_empty(status):
    data = validation()
    data["incident_policy"]["player_results"][0]["eligibility_status"] = status
    assert _blocking_incident_count(data, [1, 3]) == 1


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "count_only",
        "duplicate",
        "other",
        "unknown_status",
        "missing_flags",
        "false_string",
        "missing_row",
        "bool_index",
    ],
)
def test_missing_or_malformed_state_is_not_zero(change):
    data = deepcopy(validation())
    rows = data["incident_policy"]["player_results"]
    if change == "missing":
        data = {}
    elif change == "count_only":
        data = {"blocking_incident_count": 0}
    elif change == "duplicate":
        rows[1]["player_index"] = 1
    elif change == "other":
        rows[1]["player_index"] = 2
    elif change == "unknown_status":
        rows[0]["eligibility_status"] = "unknown"
    elif change == "missing_flags":
        rows[0]["incidents"] = [{}]
    elif change == "false_string":
        rows[0]["incidents"] = [
            {"blocks_player_eligibility": "false", "blocks_team_registration": False}
        ]
    elif change == "missing_row":
        rows.pop()
    else:
        rows[0]["player_index"] = True
    with pytest.raises(CaptureDenied):
        _blocking_incident_count(data, [1, 3])


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b"[]", b'{ "a":1}'])
def test_private_inputs_reject_ambiguous_or_noncanonical_bytes(raw):
    with pytest.raises(CaptureDenied):
        _private_json(raw)


def test_private_inputs_parse_only_exact_canonical_object():
    assert _private_json(b'{"a":1,"b":null}') == {"a": 1, "b": None}
