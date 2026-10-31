"""Synthetic v2 events; no personal identity values."""

from durable_event_store import _operation_id

SHA = "sha256:" + "a" * 64
HMAC = "hmac-sha256:" + "b" * 64
UUID = "11111111-1111-4111-8111-111111111111"


def roster_event():
    slots = [
        {
            "slot": slot,
            "role": role,
            "staff_entry_binding": HMAC,
            "presence": "PRESENT",
            "source_page_hash": SHA,
            "evidence_binding": HMAC,
            "review_decision_hash": SHA,
            "field_decision_hashes": [SHA],
        }
        for slot, role in [(1, "director_tecnico"), (2, "auxiliar")]
    ]
    event = {
        "event_type": "samchat_registration_roster_decision_v2",
        "tenant_id": "synthetic",
        "tournament_edition_id": UUID,
        "tournament_slug": "edition",
        "team_id": UUID,
        "session_id": UUID,
        "draft_id": UUID,
        "draft_version": 1,
        "timestamp": "2026-10-28T00:00:00Z",
        "policy_hash": SHA,
        "workflow_id": "samchat_registration_identity_v2",
        "effective_sal": "SAL-3",
        "policy_id": "samchat_registration_identity_policy_v2",
        "policy_version": "2.0.0",
        "attempt_id": SHA,
        "decision_id": SHA,
        "ordered_player_slots": [1],
        "ordered_player_field_decision_hashes": [SHA],
        "ordered_staff_slots": slots,
        "source_page_bindings": [SHA],
        "incident_state_hash": SHA,
        "roster_draft_binding": HMAC,
        "decision": "AUTHORIZE_PENDING_MATERIALIZATION",
        "reason_codes": ["CANDIDATE_POLICY_SATISFIED"],
        "decision_hash": SHA,
    }
    event["operation_id"] = _operation_id(event)
    return event


def finality_event():
    event = roster_event()
    for key in (
        "decision_id",
        "ordered_player_slots",
        "ordered_player_field_decision_hashes",
        "ordered_staff_slots",
        "source_page_bindings",
        "incident_state_hash",
        "decision_hash",
    ):
        event.pop(key)
    event.update(
        event_type="samchat_registration_staff_finality_attestation_v2",
        attestation_id=SHA,
        staff_id=UUID,
        staff_entry_binding=HMAC,
        staff_slot=1,
        staff_role="director_tecnico",
        preauthorization_receipt_id=SHA,
        database_transaction_id="samchat-tx-" + "c" * 24,
        cas_succeeded=True,
        provisional_row_binding=HMAC,
        post_execution_row_binding=HMAC,
        blocking_incident_count=0,
        decision="ACTIVATE_STAFF",
        attestation_hash=SHA,
    )
    event["operation_id"] = _operation_id(event)
    return event
