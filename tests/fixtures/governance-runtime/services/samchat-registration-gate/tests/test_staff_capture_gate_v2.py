"""Synthetic protected inputs exercise the actual signed bus and final producer.

The complete HTTP WebAuthn/source-admission/PostgreSQL chain is covered by the
SamChat integration acceptance suite. These unit fixtures emulate its protected
issuer output, and never establish operational reviewer authority.
"""

import copy
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from devnous.copa_telmex import staff_capture_effect as effect_module
from devnous.copa_telmex.registration_governance import _derive_geometry
from devnous.copa_telmex.staff_capture_authorization import verify_capture_authorization
from devnous.copa_telmex.staff_capture_effect import (
    DATABASE_DERIVED_FIELDS,
    LATE_AUTHORITY_FIELDS,
    bind_review_evidence,
    bind_review_plan,
    freeze_capture_effect,
    freeze_review_plan,
)
from devnous.copa_telmex.staff_capture_source_adapter import SourceSnapshot
from devnous.copa_telmex.staff_review_challenge import canonical_scope_bytes, scope_hash
from devnous.copa_telmex.staff_signed_assertion import AssertionProof, sign_assertion
from durable_event_store import DurableJSONLEventStore
from evidence_receipt import issue_evidence_receipt
from staff_capture_gate_v2 import StaffCaptureGateV2, StaffPreauthorizationDenied

sys.path.insert(
    0, str(Path(effect_module.__file__).resolve().parents[3] / "tests/unit")
)
from test_ctt_staff_capture_effect import effect  # noqa: E402
from test_ctt_staff_capture_source_adapter import candidate  # noqa: E402


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def pub(key):
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


class Context:
    def __init__(self, root):
        self.root = root
        locked, self.scope, manifest, pdf, images = candidate()
        self.attempt, self.challenge = locked.attempt_id, locked.challenge_id
        self.manifest, self.pdf, self.images = manifest, pdf, images
        self.issued, self.expiry = int(time.time()), int(time.time()) + 180
        self.issuer, self.bus, self.signer = [
            Ed25519PrivateKey.generate() for _ in range(3)
        ]
        self.args = effect()
        self.args["team"].update(
            id=str(self.scope.team_id), tournament_slug=self.scope.tournament_slug
        )
        self.generated = {}
        for row in [self.args["team"]] + self.args["players"]:
            self.generated[row["id"]] = {
                key: row[key] for key in DATABASE_DERIVED_FIELDS
            }
            row.update({key: None for key in DATABASE_DERIVED_FIELDS})
        for row in self.args["players"] + [self.args["staff"][0]["row"]]:
            row.update(
                team_id=str(self.scope.team_id),
                governance_draft_id=str(self.scope.draft_id),
                governance_draft_version=self.scope.draft_version,
                first_name="PrivateFirstName",
                last_name="PrivateLastName",
                birth_date="2000-01-01",
                curp="AAAA000101HDFXXX01",
            )
            row.update({key: None for key in LATE_AUTHORITY_FIELDS})
        self.policy = dict(
            workflow_id="samchat_registration_identity_v2",
            policy_id="samchat_registration_identity_policy_v2",
            policy_version="2.0.0",
            effective_sal="SAL-3",
            field_order=["full_name", "birth_date", "curp"],
            minimum_effective_pixels=12000,
            allowed_roles=["coordinador"],
            minimum_roster_size=1,
            maximum_roster_size=25,
            required_staff_slots=[1],
        )
        self.policy["policy_hash"] = self.policy_hash()
        self.scope = replace(
            self.scope,
            decision_hash=None,
            review_receipt_id=None,
            field_evidence_binding="hmac-sha256:" + "0" * 64,
            policy_hash=self.policy["policy_hash"],
        )
        geometries = []
        for index, (page, image) in enumerate(zip(self.scope.source_pages, images)):
            path = root / f"{index}.png"
            path.write_bytes(image)
            geometries.append(
                _derive_geometry(
                    SimpleNamespace(image_path=path, sha256=page.image_sha256[7:]),
                    dict(x=10, y=10, width=150, height=100),
                )
            )
        self.evidence = dict(
            schema="ctt.capture.identity-evidence.v1",
            members=[
                dict(
                    kind=kind,
                    row_id=row["id"] if row else None,
                    slot=slot,
                    presence="PRESENT" if row else "ABSENT",
                    source_page_index=1 if row else 2,
                    fields=(
                        {
                            field: copy.deepcopy(geometries[0])
                            for field in self.policy["field_order"]
                        }
                        if row
                        else {}
                    ),
                    absence_geometry=None if row else geometries[1],
                )
                for kind, row, slot in [
                    ("PLAYER", self.args["players"][0], 1),
                    ("STAFF", self.args["staff"][0]["row"], 1),
                    ("STAFF", None, 2),
                ]
            ],
            incident_state={"blocking_incident_count": 0},
        )
        self.mode = None
        self.published = 0
        self.open_journal()
        self.rebind()
        self.gate = self.make_gate()

    def policy_hash(self):
        return (
            "sha256:"
            + hashlib.sha256(
                canonical({k: v for k, v in self.policy.items() if k != "policy_hash"})
            ).hexdigest()
        )

    def open_journal(self):
        self.journal = DurableJSONLEventStore(
            str(self.root / "events.jsonl"), v2_public_keys={"bus": pub(self.bus)}
        )

    def publish(self, event):
        self.published += 1
        wrapped = dict(
            event,
            type=event["event_type"],
            execution_receipt_ref="zaubern:registration-gate:precommit",
        )
        receipt = self.journal.append_idempotent_receipt(
            wrapped,
            lambda: issue_evidence_receipt(
                wrapped,
                None,
                kid="bus",
                alg="Ed25519",
                ed25519_private_key=self.bus,
            ),
        )
        receipt = copy.deepcopy(dict(receipt))
        if self.mode == "false":
            return {"verified": True}
        if self.mode == "signature":
            receipt["signature"] = "bad"
        return receipt

    def make_gate(self):
        return StaffCaptureGateV2(
            identity_key=b"i" * 32,
            review_key=b"r" * 32,
            effect_key=b"e" * 32,
            signing_kid="gate",
            signing_key=self.signer,
            assertion_public_keys={"issuer": pub(self.issuer)},
            receipt_public_keys={"bus": pub(self.bus)},
            policy_artifact=self.policy,
            expected_policy_hash=self.policy["policy_hash"],
            publisher=self.publish,
            max_page_bytes=1_000_000,
        )

    def rebind(self):
        self.evidence_bytes = canonical(self.evidence)
        self.scope = replace(
            self.scope,
            field_evidence_binding=bind_review_evidence(self.evidence_bytes, b"r" * 32),
        )
        self.args["scope"] = self.scope
        self.review = freeze_review_plan(**self.args)
        self.scope = replace(
            self.scope,
            staff_and_changes_binding=bind_review_plan(self.review, b"r" * 32),
        )
        self.args["scope"] = self.scope
        self.signed = sign_assertion(
            AssertionProof(
                self.attempt,
                self.challenge,
                self.scope.actor_id,
                b"synthetic-unit-credential",
                scope_hash(self.scope),
                4,
                5,
                self.expiry,
            ),
            kid="issuer",
            private_key=self.issuer,
        )
        self.snapshot = SourceSnapshot(
            canonical(self.manifest),
            BytesIO(self.pdf),
            {
                page.asset_id: image
                for page, image in zip(self.scope.source_pages, self.images)
            },
            canonical_scope_bytes(self.scope),
            1_000_000,
        )

    def preauthorize(self, **changes):
        args = dict(
            scope=self.scope,
            attempt_id=self.attempt,
            review_bytes=self.review,
            evidence_bytes=self.evidence_bytes,
            signed_review=self.signed,
            snapshot=self.snapshot,
            issued_at=self.issued,
            expires_at=self.expiry,
        )
        return self.gate.preauthorize(**(args | changes))

    def final_effect(self, result):
        args = copy.deepcopy(self.args)
        for row in [args["team"]] + args["players"]:
            row.update(self.generated[row["id"]])
        for row in args["players"] + [args["staff"][0]["row"]]:
            row.update(result["authority_metadata"][row["id"]])
        return freeze_capture_effect(**args)

    def authorize(self, result, **changes):
        args = dict(
            scope=self.scope,
            attempt_id=self.attempt,
            nonce=uuid4(),
            review_bytes=self.review,
            effect_bytes=self.final_effect(result),
            signed_review=self.signed,
            preauthorization=result,
            issued_at=self.issued,
            expires_at=self.expiry,
        )
        return self.gate.authorize_effect(**(args | changes))


@pytest.fixture
def ctx(tmp_path):
    value = Context(tmp_path)
    yield value
    value.journal.close()
    value.snapshot.pdf.close()


def test_real_receipt_and_final_signature_and_restart_retry(ctx):
    result = ctx.preauthorize()
    assert result["authorized"] is True
    public = json.dumps(result)
    for private in (
        "PrivateFirstName",
        "PrivateLastName",
        "AAAA000101HDFXXX01",
        "2000-01-01",
    ):
        assert private not in public
    assert all(
        row["finality_receipt_id"] is None
        for row in result["authority_metadata"].values()
    )
    nonce = uuid4()
    raw_envelope = ctx.authorize(result, nonce=nonce)
    envelope = json.loads(raw_envelope)
    assert verify_capture_authorization(
        envelope=raw_envelope,
        public_keys={"gate": pub(ctx.signer)},
        scope=ctx.scope,
        attempt_id=ctx.attempt,
        nonce=nonce,
        review_bytes=ctx.review,
        effect_bytes=ctx.final_effect(result),
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
        now_seconds=ctx.issued,
    ).startswith("sha256:")
    assert envelope["body"]["authority_metadata"] == result["authority_metadata"]
    ctx.journal.close()
    ctx.open_journal()
    assert ctx.preauthorize() == result
    assert len((ctx.root / "events.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize(
    "change", ["review", "evidence", "attempt", "proof", "source", "expiry", "role"]
)
def test_rejects_changed_protected_input_before_publication(ctx, change):
    kwargs = {}
    if change == "review":
        kwargs["review_bytes"] = ctx.review + b" "
    elif change == "evidence":
        kwargs["evidence_bytes"] = ctx.evidence_bytes + b" "
    elif change == "attempt":
        kwargs["attempt_id"] = uuid4()
    elif change == "proof":
        kwargs["signed_review"] = ctx.signed[:-1] + b" "
    elif change == "source":
        kwargs["snapshot"] = replace(ctx.snapshot, page_assets={})
    elif change == "expiry":
        kwargs["expires_at"] = ctx.expiry + 1
    elif change == "role":
        kwargs["scope"] = replace(ctx.scope, actor_role="other")
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize(**kwargs)
    assert ctx.published == 0


@pytest.mark.parametrize(
    "change",
    [
        "geometry",
        "crop",
        "field",
        "row",
        "kind",
        "order",
        "incident",
        "boolean_incident",
        "absence",
        "value",
    ],
)
def test_rejects_rebound_invalid_evidence(ctx, change):
    member = ctx.evidence["members"][0]
    if change == "geometry":
        member["fields"]["curp"]["effective_pixels"] = 1
    elif change == "crop":
        member["fields"]["curp"]["crop_sha256"] = "sha256:" + "0" * 64
    elif change == "field":
        del member["fields"]["curp"]
    elif change == "row":
        member["row_id"] = str(uuid4())
    elif change == "kind":
        member["kind"] = "STAFF"
    elif change == "order":
        ctx.evidence["members"].reverse()
    elif change == "incident":
        ctx.evidence["incident_state"]["blocking_incident_count"] = 1
    elif change == "boolean_incident":
        ctx.evidence["incident_state"]["blocking_incident_count"] = False
    elif change == "absence":
        ctx.evidence["members"][2]["absence_geometry"] = None
    elif change == "value":
        ctx.args["players"][0]["curp"] = None
    ctx.rebind()
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize()
    assert ctx.published == 0


@pytest.mark.parametrize(
    "change", ["pixels", "max", "staff", "roles", "field_order", "hash"]
)
def test_rejects_unpinned_or_weakened_policy(ctx, change):
    if change == "pixels":
        ctx.policy["minimum_effective_pixels"] = 11999
    elif change == "max":
        ctx.policy["maximum_roster_size"] = 36
    elif change == "staff":
        ctx.policy["required_staff_slots"] = [True]
    elif change == "roles":
        ctx.policy["allowed_roles"] = []
    elif change == "field_order":
        ctx.policy["field_order"].reverse()
    ctx.policy["policy_hash"] = (
        ctx.policy_hash() if change != "hash" else "sha256:" + "0" * 64
    )
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.make_gate()


@pytest.mark.parametrize("mode", ["false", "signature"])
def test_verified_boolean_or_bad_signature_is_not_a_receipt(ctx, mode):
    ctx.mode = mode
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize()


@pytest.mark.parametrize(
    "change", ["metadata", "event", "receipt", "field", "attempt", "effect", "nonce"]
)
def test_final_authorizer_reverifies_all_evidence(ctx, change):
    result = ctx.preauthorize()
    final = ctx.final_effect(result)
    kwargs = dict(effect_bytes=final)
    if change == "metadata":
        next(iter(result["authority_metadata"].values()))["roster_draft_binding"] = (
            "hmac-sha256:" + "0" * 64
        )
    elif change == "event":
        result["roster_decision"]["decision"] = "DENY"
    elif change == "receipt":
        result["preauthorization_receipt"] = {"verified": True}
    elif change == "field":
        result["player_field_decisions"][0]["value_binding"] = "altered"
    elif change == "attempt":
        kwargs["attempt_id"] = uuid4()
    elif change == "effect":
        kwargs["effect_bytes"] = final + b" "
    elif change == "nonce":
        kwargs["nonce"] = "browser-nonce"
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.authorize(result, **kwargs)


def test_required_absent_staff_and_policy_role_remain_blocked(ctx):
    ctx.policy["required_staff_slots"] = [1, 2]
    ctx.policy["policy_hash"] = ctx.policy_hash()
    ctx.scope = replace(ctx.scope, policy_hash=ctx.policy["policy_hash"])
    ctx.rebind()
    ctx.gate = ctx.make_gate()
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize()
    assert ctx.published == 0


@pytest.mark.parametrize(
    "raw", [b'{"a":1,"a":1}', b'{"a":NaN}', b"[" * 25 + b"]" * 25, b"{} "]
)
def test_private_evidence_requires_bounded_canonical_json(ctx, raw):
    ctx.scope = replace(
        ctx.scope, field_evidence_binding=bind_review_evidence(raw, b"r" * 32)
    )
    ctx.args["scope"] = ctx.scope
    ctx.review = freeze_review_plan(**ctx.args)
    ctx.scope = replace(
        ctx.scope, staff_and_changes_binding=bind_review_plan(ctx.review, b"r" * 32)
    )
    ctx.signed = sign_assertion(
        AssertionProof(
            ctx.attempt,
            ctx.challenge,
            ctx.scope.actor_id,
            b"synthetic-unit-credential",
            scope_hash(ctx.scope),
            4,
            5,
            ctx.expiry,
        ),
        kid="issuer",
        private_key=ctx.issuer,
    )
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize(evidence_bytes=raw)
    assert ctx.published == 0


def test_pinned_maximum_35_is_structurally_supported_without_operational_default(ctx):
    ctx.policy["maximum_roster_size"] = 35
    ctx.policy["policy_hash"] = ctx.policy_hash()
    assert isinstance(ctx.make_gate(), StaffCaptureGateV2)


def test_final_authorizer_requires_original_challenge_issued_time(ctx):
    result = ctx.preauthorize()
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.authorize(result, issued_at=ctx.issued - 1)


def prepare_rectangle_photo(ctx, *, kind="PLAYER"):
    from devnous.copa_telmex import staff_photo_store

    index = 0 if kind == "PLAYER" else 1
    row = ctx.args["players"][0] if index == 0 else ctx.args["staff"][0]["row"]
    page = ctx.scope.source_pages[0]
    geometry = _derive_geometry(
        SimpleNamespace(image_path=ctx.root / "0.png", sha256=page.image_sha256[7:]),
        dict(x=10, y=10, width=40, height=40),
    )
    prepared = staff_photo_store.derive_rectangular_review_photo(
        ctx.images[0],
        geometry,
        max_page_bytes=1_000_000,
    )
    row["photo_path"] = prepared.photo_path
    if kind == "PLAYER":
        import base64

        row.update(
            photo_sha256=prepared.sha256,
            photo_ahash=prepared.ahash,
            photo_data=base64.b64encode(prepared.data).decode("ascii"),
        )
    ctx.evidence["members"][index]["photo_evidence"] = dict(
        mode="SOURCE_RECTANGLE",
        geometry=geometry,
    )
    ctx.rebind()
    return row


@pytest.mark.parametrize("kind", ["PLAYER", "STAFF"])
def test_photo_is_derived_from_source_with_existing_40_pixel_minimum(ctx, kind):
    row = prepare_rectangle_photo(ctx, kind=kind)
    assert row["photo_path"].startswith("ctt-photo:")
    result = ctx.preauthorize()
    assert json.loads(ctx.authorize(result))["body"]["authority_metadata"]


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "path",
        "sha",
        "ahash",
        "base64",
        "geometry",
        "extra",
        "mode",
        "slot",
        "staff_batch",
    ],
)
def test_changed_photo_or_envelope_is_rejected_after_rebinding(ctx, change):
    kind = "STAFF" if change == "staff_batch" else "PLAYER"
    row = prepare_rectangle_photo(ctx, kind=kind)
    index = 1 if kind == "STAFF" else 0
    member = ctx.evidence["members"][index]
    if change == "missing":
        del member["photo_evidence"]
    elif change == "path":
        row["photo_path"] = "ctt-photo:" + "0" * 64
    elif change == "sha":
        row["photo_sha256"] = "0" * 64
    elif change == "ahash":
        row["photo_ahash"] = "0" * 16
    elif change == "base64":
        row["photo_data"] = "invalid"
    elif change == "geometry":
        member["photo_evidence"]["geometry"]["effective_pixels"] = 1
    elif change == "extra":
        member["photo_evidence"]["raw"] = "browser authority"
    elif change == "mode":
        member["photo_evidence"]["mode"] = "URL"
    elif change in ("slot", "staff_batch"):
        member["photo_evidence"] = dict(
            mode="BATCH_PLAYER_SLOT", slot_ref="page:1:jugador_1"
        )
    ctx.rebind()
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize()
    assert ctx.published == 0


@pytest.mark.parametrize("index", [0, 2])
def test_no_photo_or_absent_member_cannot_attach_photo_evidence(ctx, index):
    ctx.evidence["members"][index]["photo_evidence"] = None
    ctx.rebind()
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize()
    assert ctx.published == 0


def test_explicit_required_photo_policy_does_not_allow_missing_player_photo(ctx):
    ctx.policy["required_photo_kinds"] = ["PLAYER"]
    ctx.policy["policy_hash"] = ctx.policy_hash()
    ctx.scope = replace(ctx.scope, policy_hash=ctx.policy["policy_hash"])
    ctx.rebind()
    ctx.gate = ctx.make_gate()
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize()
    assert ctx.published == 0


def test_photo_policy_rejects_unknown_kind(ctx):
    ctx.policy["required_photo_kinds"] = ["BROWSER"]
    ctx.policy["policy_hash"] = ctx.policy_hash()
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.make_gate()


def test_valid_physical_slot_without_source_photo_remains_blocked(ctx):
    prepare_rectangle_photo(ctx)
    ctx.evidence["members"][0]["photo_evidence"] = dict(
        mode="BATCH_PLAYER_SLOT",
        slot_ref="page:1:jugador_3",
    )
    ctx.rebind()
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize()
    assert ctx.published == 0


def test_batch_photo_uses_reviewed_printed_slot_not_final_roster_position(
    ctx, monkeypatch
):
    import fitz
    from PIL import ImageDraw
    from devnous.copa_telmex import staff_photo_store
    from devnous.copa_telmex.batch_admission import validate_manifest
    from test_ctt_batch_photo_alignment import _page

    # This follows the existing algorithm's synthetic face-recognition fixture;
    # grid detection, exact slot extraction and JPEG encoding remain real.
    monkeypatch.setattr(
        "devnous.tournaments.core.ctt_batch_photo_alignment._face_present",
        lambda _image: True,
    )
    image = _page()
    draw = ImageDraw.Draw(image)
    draw.rectangle((51, 238 + 2 * 97 + 5, 115, 238 + 3 * 97 - 5), fill=(50, 50, 50))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    ctx.images[0] = buffer.getvalue()
    document = fitz.open()
    for data in ctx.images:
        page = document.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=data)
    ctx.pdf = document.tobytes(deflate=True)
    document.close()
    ctx.manifest["documents"][0]["pdf_sha256"] = hashlib.sha256(ctx.pdf).hexdigest()
    ctx.scope = replace(
        ctx.scope,
        pdf_sha256=hashlib.sha256(ctx.pdf).hexdigest(),
        manifest_sha256=validate_manifest(ctx.manifest)["manifest_sha256"],
        source_pages=tuple(
            replace(page, image_sha256="sha256:" + hashlib.sha256(data).hexdigest())
            for page, data in zip(ctx.scope.source_pages, ctx.images)
        ),
    )
    (ctx.root / "0.png").write_bytes(ctx.images[0])
    geometry = _derive_geometry(
        SimpleNamespace(
            image_path=ctx.root / "0.png",
            sha256=ctx.scope.source_pages[0].image_sha256[7:],
        ),
        dict(x=10, y=10, width=150, height=100),
    )
    for member in ctx.evidence["members"][:2]:
        member["fields"] = {
            field: copy.deepcopy(geometry) for field in ctx.policy["field_order"]
        }
    ctx.rebind()
    photo = staff_photo_store.derive_batch_review_photo(
        ctx.snapshot, "page:1:jugador_3"
    )
    row = ctx.args["players"][0]
    row.update(
        photo_path=photo.photo_path, photo_sha256=photo.sha256, photo_ahash=photo.ahash
    )
    assert row["roster_index"] == 1
    ctx.evidence["members"][0]["photo_evidence"] = dict(
        mode="BATCH_PLAYER_SLOT", slot_ref="page:1:jugador_3"
    )
    ctx.rebind()
    result = ctx.preauthorize()
    assert json.loads(ctx.authorize(result))["body"]["authority_metadata"]
