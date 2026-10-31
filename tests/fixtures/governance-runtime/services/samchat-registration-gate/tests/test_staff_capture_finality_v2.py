"""Real signed Bus receipts exercise separate provisional/finality capabilities."""

import copy
import base64
from dataclasses import replace
from datetime import datetime, timezone
import json
import time
from uuid import uuid4

import jsonschema
import pytest

from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_finality_authorization import (
    DOMAIN,
    verify_pre_authorization,
    verify_capture_finality_authorization,
)
from devnous.copa_telmex.staff_capture_finality_effect import (
    FinalityContext,
    VerifiedFinalityBundle,
    bind_finality_effect,
    canonical,
    freeze_finality_effect,
    pending_rows,
    verify_finality_bundle,
)
from durable_event_store import _operation_id
from evidence_receipt import issue_evidence_receipt
from staff_v2_event_validation import validate_staff_v2_event
from test_staff_capture_gate_v2 import ctx as _ctx, pub

ctx = _ctx


@pytest.fixture
def final(ctx):
    preauth = ctx.preauthorize()
    pending = ctx.final_effect(preauth)
    context = FinalityContext(
        "isolated-database",
        16384,
        497,
        ctx.attempt,
        int(time.time()),
        ctx.expiry,
        uuid4(),
        ctx.snapshot,
        0,
        True,
    )
    args = dict(
        protected_context=context,
        scope=ctx.scope,
        review_attempt_id=ctx.attempt,
        review_bytes=ctx.review,
        pending_effect_bytes=pending,
        signed_review=ctx.signed,
        preauthorization=preauth,
    )
    observed = pending_rows(json.loads(pending))
    bundle = ctx.gate.finalize_rows(**args, observed_pending_rows=observed)
    active = copy.deepcopy(observed)
    for item in bundle.records:
        row = (
            active["players"][0]
            if item["kind"] == "PLAYER"
            else active["staff"][item["slot"] - 1]["row"]
        )
        row.update(
            governance_state="ACTIVE", finality_receipt_id=item["receipt"]["receipt_id"]
        )
        if "updated_at" in row:
            updated = datetime.fromtimestamp(context.issued_at, timezone.utc)
            row["updated_at"] = (
                updated.replace(tzinfo=None) if item["kind"] == "PLAYER" else updated
            ).isoformat()
        if item["kind"] == "PLAYER":
            row.update(
                postcommit_revision=row["postcommit_revision"] + 1,
                postcommit_snapshot_hash="sha256:" + "f" * 64,
            )
    freeze = dict(
        scope=ctx.scope,
        protected_context=context,
        pending_effect_bytes=pending,
        observed_pending_rows=observed,
        verified_finalities=bundle,
        active_projection_db_derived=active,
        effect_key=b"e" * 32,
        preauthorization=preauth,
        receipt_public_keys={"bus": pub(ctx.bus)},
    )
    effect = freeze_finality_effect(**freeze)
    signing = dict(args, finality_effect_bytes=effect, verified_finalities=bundle)
    envelope = ctx.gate.authorize_finality_effect(**signing)
    verify = dict(
        envelope=envelope,
        public_keys={"gate": pub(ctx.signer)},
        scope=ctx.scope,
        protected_context=context,
        attempt_id=ctx.attempt,
        nonce=context.nonce,
        review_bytes=ctx.review,
        pending_effect_bytes=pending,
        finality_effect_bytes=effect,
        signed_review=ctx.signed,
        preauthorization=preauth,
        verified_finalities=bundle,
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
        receipt_public_keys={"bus": pub(ctx.bus)},
        assertion_public_keys={"issuer": pub(ctx.issuer)},
        now_seconds=int(time.time()),
    )
    return dict(
        ctx=ctx,
        args=args,
        observed=observed,
        context=context,
        bundle=bundle,
        active=active,
        freeze=freeze,
        effect=effect,
        signing=signing,
        envelope=envelope,
        verify=verify,
    )


def test_real_receipts_capability_and_exact_schema(final):
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "schemas/evidence/samchat_registration_staff_contract_v2.schema.json"
    )
    schema = json.loads(path.read_text())
    assert verify_capture_finality_authorization(**final["verify"]).startswith(
        "sha256:"
    )
    assert len(final["bundle"].records) == 2
    for record in final["bundle"].records:
        event = record["event"]
        assert validate_staff_v2_event(event)
        jsonschema.validate(event, schema)
        assert _operation_id(event) == event["operation_id"]
        assert (
            event["database_transaction_id"] == final["context"].database_transaction_id
        )
        assert event["attempt_id"] == final["context"].event_attempt_id
        assert "PrivateFirstName" not in json.dumps(event)
        assert "AAAA000101HDFXXX01" not in json.dumps(event)
    assert json.loads(final["effect"])["staff"][1]["receipt"] is None
    assert bind_finality_effect(final["effect"], b"e" * 32) != bind_finality_effect(
        final["effect"], b"x" * 32
    )


@pytest.mark.parametrize("slot,valid", [(35, True), (36, False), (True, False)])
def test_player_event_schema_and_runtime_agree_on_structural_limits(final, slot, valid):
    from pathlib import Path

    schema = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "schemas/evidence/samchat_registration_staff_contract_v2.schema.json"
        ).read_text()
    )
    event = copy.deepcopy(final["bundle"].records[0]["event"])
    event["player_slot"] = slot
    assert validate_staff_v2_event(event) is valid
    assert jsonschema.Draft202012Validator(schema).is_valid(event) is valid


@pytest.mark.parametrize(
    "field,value",
    [
        ("cas_succeeded", False),
        ("blocking_incident_count", 1),
        ("staff_id", str(uuid4())),
    ],
)
def test_player_event_never_accepts_missing_activation_conditions_or_staff_subject(
    final, field, value
):
    from pathlib import Path

    schema = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "schemas/evidence/samchat_registration_staff_contract_v2.schema.json"
        ).read_text()
    )
    event = copy.deepcopy(final["bundle"].records[0]["event"])
    event[field] = value
    assert not validate_staff_v2_event(event)
    assert not jsonschema.Draft202012Validator(schema).is_valid(event)


@pytest.mark.parametrize(
    "field,value",
    [
        ("first_name", "substituted"),
        ("photo_path", "ctt-photo:" + "1" * 64),
        ("team_id", str(uuid4())),
        ("governance_state", "ACTIVE"),
    ],
)
def test_pending_observation_mutation_publishes_nothing(final, field, value):
    observed = copy.deepcopy(final["observed"])
    observed["players"][0][field] = value
    before = final["ctx"].published
    with pytest.raises(CaptureDenied):
        final["ctx"].gate.finalize_rows(**final["args"], observed_pending_rows=observed)
    assert final["ctx"].published == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("first_name", "changed"),
        ("roster_index", 2),
        ("postcommit_revision", 4),
        ("postcommit_revision", True),
        ("postcommit_revision", 1.0),
        ("postcommit_snapshot_hash", "not-a-hash"),
        ("governance_state", "PENDING_FINALITY"),
        ("finality_receipt_id", "sha256:" + "c" * 64),
        ("updated_at", "2020-01-01T00:00:00"),
    ],
)
def test_active_projection_cannot_change_material_or_unverified_fields(
    final, field, value
):
    active = copy.deepcopy(final["active"])
    active["players"][0][field] = value
    with pytest.raises(CaptureDenied):
        freeze_finality_effect(
            **(final["freeze"] | {"active_projection_db_derived": active})
        )


@pytest.mark.parametrize(
    "mutation", ["signature", "event", "missing", "reorder", "extra", "another-subject"]
)
def test_bundle_flags_or_signed_receipt_substitution_are_rejected(final, mutation):
    obj = json.loads(final["bundle"].payload_bytes)
    if mutation == "signature":
        obj["records"][0]["receipt"]["signature"] = "AAAA"
    elif mutation == "event":
        obj["records"][0]["event"]["cas_succeeded"] = False
    elif mutation == "missing":
        obj["records"].pop()
    elif mutation == "reorder":
        obj["records"].reverse()
    elif mutation == "extra":
        obj["extra"] = True
    else:
        obj["records"][0]["row_id"] = str(uuid4())
    with pytest.raises(CaptureDenied):
        final["ctx"].gate.authorize_finality_effect(
            **(
                final["signing"]
                | {"verified_finalities": VerifiedFinalityBundle(canonical(obj))}
            )
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("pg_top_xid", 498),
        ("database_identity", "different-database"),
        ("database_oid", 16385),
        ("review_attempt_id", uuid4()),
        ("nonce", uuid4()),
        ("blocking_incident_count", 1),
        ("cas_succeeded", False),
        ("expires_at", 1),
        ("issued_at", 1),
    ],
)
def test_context_changes_cannot_reuse_capability_or_receipts(final, field, value):
    altered = replace(final["context"], **{field: value})
    with pytest.raises(CaptureDenied):
        verify_capture_finality_authorization(
            **(final["verify"] | {"protected_context": altered})
        )


@pytest.mark.parametrize(
    "mutation",
    ["false-flag", "signature", "tenant", "attempt", "row-receipt", "review-signature"],
)
def test_original_preauthorization_and_review_are_reverified(final, mutation):
    verify = dict(final["verify"])
    preauth = copy.deepcopy(verify["preauthorization"])
    if mutation == "false-flag":
        preauth["preauthorization_receipt"] = {"verified": True}
    elif mutation == "signature":
        preauth["preauthorization_receipt"]["signature"] = "AAAA"
    elif mutation == "tenant":
        preauth["roster_decision"]["tenant_id"] = "other"
    elif mutation == "attempt":
        preauth["roster_decision"]["attempt_id"] = "sha256:" + "a" * 64
    elif mutation == "review-signature":
        verify["signed_review"] = b"{}"
    else:
        pending = json.loads(verify["pending_effect_bytes"])
        pending["players"][0]["preauthorization_receipt_id"] = "sha256:" + "b" * 64
        verify["pending_effect_bytes"] = canonical(pending)
    verify["preauthorization"] = preauth
    with pytest.raises(CaptureDenied):
        verify_capture_finality_authorization(**verify)


def test_lost_bus_response_retries_identical_event_and_recovers_receipts(
    final, monkeypatch
):
    ctx = final["ctx"]
    publish = ctx.gate._publish
    lost = set()

    def once(event):
        receipt = publish(event)
        if event["operation_id"] not in lost:
            lost.add(event["operation_id"])
            raise ConnectionError("synthetic lost reply after durable append")
        return receipt

    monkeypatch.setattr(ctx.gate, "_publish", once)
    recovered = ctx.gate.finalize_rows(
        **final["args"], observed_pending_rows=final["observed"]
    )
    assert recovered.payload_bytes == final["bundle"].payload_bytes
    ctx.journal.close()
    ctx.open_journal()
    recovered = ctx.gate.finalize_rows(
        **final["args"], observed_pending_rows=final["observed"]
    )
    assert recovered.payload_bytes == final["bundle"].payload_bytes


@pytest.mark.parametrize("mode", ["false", "signature"])
def test_bad_bus_response_never_yields_finality(final, mode):
    final["ctx"].mode = mode
    with pytest.raises(CaptureDenied):
        final["ctx"].gate.finalize_rows(
            **final["args"], observed_pending_rows=final["observed"]
        )


@pytest.mark.parametrize("name", ["nonce", "issued_at", "expires_at"])
def test_gate_never_accepts_a_caller_replacement_for_protected_time_or_nonce(
    final, name
):
    value = uuid4() if name == "nonce" else 1
    with pytest.raises(CaptureDenied):
        final["ctx"].gate.authorize_finality_effect(
            **(final["signing"] | {name: value})
        )


@pytest.mark.parametrize(
    "name",
    [
        "scope_hash",
        "preauthorization_event_hash",
        "signed_review_hash",
        "pending_effect_binding",
        "ordered_finality_receipts",
    ],
)
def test_consumer_recomputes_even_correctly_signed_body_references(final, name):
    envelope = json.loads(final["envelope"])
    envelope["body"][name] = (
        [] if name == "ordered_finality_receipts" else "sha256:" + "1" * 64
    )
    envelope["signature"] = base64.b64encode(
        final["ctx"].signer.sign(DOMAIN + canonical(envelope["body"]))
    ).decode()
    with pytest.raises(CaptureDenied):
        verify_capture_finality_authorization(
            **(final["verify"] | {"envelope": canonical(envelope)})
        )


@pytest.mark.parametrize("mutation", ["slot-bool", "issued-at-float"])
def test_correctly_signed_body_cannot_coerce_integer_types(final, mutation):
    envelope = json.loads(final["envelope"])
    if mutation == "slot-bool":
        envelope["body"]["ordered_finality_receipts"][0]["slot"] = True
    else:
        envelope["body"]["issued_at"] = float(envelope["body"]["issued_at"])
    envelope["signature"] = base64.b64encode(
        final["ctx"].signer.sign(DOMAIN + canonical(envelope["body"]))
    ).decode()
    with pytest.raises(CaptureDenied):
        verify_capture_finality_authorization(
            **(final["verify"] | {"envelope": canonical(envelope)})
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrapper-slot-bool",
        "wrapper-slot-float",
        "event-slot-bool",
        "event-slot-float",
        "event-draft-bool",
        "event-draft-float",
        "resigned-event-slot-bool",
        "resigned-event-draft-bool",
    ],
)
def test_bundle_wire_never_coerces_numeric_subject_or_event_types(final, mutation):
    obj = json.loads(final["bundle"].payload_bytes)
    record = obj["records"][0]
    value = 1.0 if mutation.endswith("float") else True
    if mutation.startswith("wrapper"):
        record["slot"] = value
    else:
        name = "draft_version" if "draft" in mutation else "player_slot"
        record["event"][name] = value
        if mutation.startswith("resigned"):
            # These already fail via exact receipt/event hashing. Keep them
            # alongside unchanged valid receipts, which isolate JSON equality.
            record["receipt"] = issue_evidence_receipt(
                record["event"],
                None,
                kid="bus",
                alg="Ed25519",
                ed25519_private_key=final["ctx"].bus,
            )
    with pytest.raises(CaptureDenied):
        verify_finality_bundle(
            bundle=VerifiedFinalityBundle(canonical(obj)),
            scope=final["ctx"].scope,
            context=final["context"],
            pending_effect_bytes=final["args"]["pending_effect_bytes"],
            preauthorization=final["args"]["preauthorization"],
            effect_key=b"e" * 32,
            receipt_public_keys={"bus": pub(final["ctx"].bus)},
            now_seconds=int(time.time()),
        )


@pytest.mark.parametrize("field", ["draft-version", "player-slot", "staff-slot"])
@pytest.mark.parametrize("value", [True, 1.0])
def test_signed_preauthorization_preserves_exact_numeric_wire_types(
    final, field, value
):
    preauth = copy.deepcopy(final["args"]["preauthorization"])
    event = preauth["roster_decision"]
    if field == "draft-version":
        event["draft_version"] = value
    elif field == "player-slot":
        event["ordered_player_slots"][0] = value
    else:
        event["ordered_staff_slots"][0]["slot"] = value
    preauth["preauthorization_receipt"] = issue_evidence_receipt(
        event,
        None,
        kid="bus",
        alg="Ed25519",
        ed25519_private_key=final["ctx"].bus,
    )
    receipt_id = preauth["preauthorization_receipt"]["receipt_id"]
    pending = json.loads(final["args"]["pending_effect_bytes"])
    for row in pending["players"] + [
        s["row"] for s in pending["staff"] if s["row"] is not None
    ]:
        row["preauthorization_receipt_id"] = receipt_id
        preauth["authority_metadata"][row["id"]][
            "preauthorization_receipt_id"
        ] = receipt_id
    with pytest.raises(CaptureDenied):
        verify_pre_authorization(
            preauthorization=preauth,
            scope=final["ctx"].scope,
            attempt_id=final["ctx"].attempt,
            pending_effect_bytes=canonical(pending),
            receipt_public_keys={"bus": pub(final["ctx"].bus)},
            signed_review=final["ctx"].signed,
            assertion_public_keys={"issuer": pub(final["ctx"].issuer)},
            now_seconds=int(time.time()),
        )


@pytest.mark.parametrize("name", ["issued_at", "expires_at"])
def test_gate_rejects_float_replacements_for_protected_timestamps(final, name):
    value = float(getattr(final["context"], name))
    with pytest.raises(CaptureDenied):
        final["ctx"].gate.authorize_finality_effect(
            **(final["signing"] | {name: value})
        )


def test_two_present_staff_receive_distinct_individual_finalities(ctx):
    from devnous.copa_telmex.staff_capture_effect import freeze_capture_effect

    row = copy.deepcopy(ctx.args["staff"][0]["row"])
    row.update(id=str(uuid4()), staff_slot=2, role="auxiliar")
    ctx.args["staff"][1].update(presence="PRESENT", row=row)
    ctx.evidence["members"][2] = copy.deepcopy(ctx.evidence["members"][1])
    ctx.evidence["members"][2].update(row_id=row["id"], slot=2)
    ctx.policy["required_staff_slots"] = [1, 2]
    ctx.policy["policy_hash"] = ctx.policy_hash()
    ctx.scope = replace(ctx.scope, policy_hash=ctx.policy["policy_hash"])
    ctx.rebind()
    ctx.gate = ctx.make_gate()
    preauth = ctx.preauthorize()
    args = copy.deepcopy(ctx.args)
    for item in [args["team"]] + args["players"]:
        item.update(ctx.generated[item["id"]])
    for item in args["players"] + [slot["row"] for slot in args["staff"]]:
        item.update(preauth["authority_metadata"][item["id"]])
    pending = freeze_capture_effect(**args)
    context = FinalityContext(
        "isolated",
        16384,
        501,
        ctx.attempt,
        int(time.time()),
        ctx.expiry,
        uuid4(),
        ctx.snapshot,
        0,
        True,
    )
    bundle = ctx.gate.finalize_rows(
        protected_context=context,
        scope=ctx.scope,
        review_attempt_id=ctx.attempt,
        review_bytes=ctx.review,
        pending_effect_bytes=pending,
        signed_review=ctx.signed,
        preauthorization=preauth,
        observed_pending_rows=pending_rows(json.loads(pending)),
    )
    assert [r["kind"] for r in bundle.records] == ["PLAYER", "STAFF", "STAFF"]
    assert [r["slot"] for r in bundle.records] == [1, 1, 2]
    assert len({r["receipt"]["receipt_id"] for r in bundle.records}) == 3


@pytest.mark.parametrize(
    "mutation", ["extra", "signature", "nonce", "effect", "scope", "time"]
)
def test_envelope_or_effect_tampering_fails_closed(final, mutation):
    verify = dict(final["verify"])
    envelope = json.loads(verify["envelope"])
    if mutation == "extra":
        envelope["body"]["extra"] = True
    elif mutation == "signature":
        envelope["signature"] = "AAAA"
    elif mutation == "nonce":
        envelope["body"]["nonce"] = str(uuid4())
    elif mutation == "scope":
        envelope["body"]["scope_hash"] = "sha256:" + "a" * 64
    elif mutation == "time":
        verify["now_seconds"] = final["context"].expires_at
    else:
        effect = json.loads(verify["finality_effect_bytes"])
        effect["team"]["name"] = "changed"
        verify["finality_effect_bytes"] = canonical(effect)
    verify["envelope"] = canonical(envelope)
    with pytest.raises(CaptureDenied):
        verify_capture_finality_authorization(**verify)
