"""Batch membership is bound to parent, verifier, exact scope and payload."""

import base64
import copy
import hashlib
import time
from dataclasses import replace
from io import BytesIO
from uuid import uuid4

import pytest
from devnous.copa_telmex.staff_batch_authorization import (
    CONSENT_SCHEMA,
    MEMBER_SCHEMA,
    VERIFICATION_SCHEMA,
    BatchReviewScope,
    BatchSourcePage,
    batch_scope_hash,
    canonical_batch_scope_bytes,
    sign_batch_proof,
)
from devnous.copa_telmex.staff_batch_contracts import stable_document_id, validate_source_manifest
from devnous.copa_telmex.staff_batch_source_adapter import SourceSnapshotV2
from devnous.copa_telmex.staff_capture_effect import bind_review_plan, freeze_review_batch
from PIL import Image
from staff_capture_gate_v2 import StaffPreauthorizationDenied
from staff_capture_gate_v3 import StaffCaptureGateV3
from test_staff_capture_gate_v2 import Context, canonical, pub


@pytest.fixture
def batch(tmp_path):
    c = Context(tmp_path)
    old = c.scope
    refs = [(old.pdf_sha256, p.pdf_page) for p in old.source_pages]
    document = stable_document_id(str(old.edition_id), refs)
    manifest = validate_source_manifest(
        dict(
            schema_version="ctt.registration.source-manifest.v2",
            edition=dict(
                tenant_id=old.tenant_id,
                edition_id=str(old.edition_id),
                tournament_id=str(uuid4()),
                edition_year=2026,
                roster_slug=old.tournament_slug,
                phase="nacional",
            ),
            pdfs=[
                dict(
                    filename="fixture.pdf",
                    sha256=old.pdf_sha256,
                    page_count=max(p.pdf_page for p in old.source_pages),
                )
            ],
            documents=[
                dict(
                    document_id=document,
                    source_slices=[
                        dict(
                            pdf_sha256=old.pdf_sha256, pages=[p.pdf_page for p in old.source_pages]
                        )
                    ],
                    extraction={},
                )
            ],
        )
    )
    scope = BatchReviewScope(
        **{
            name: getattr(old, name)
            for name in (
                "tenant_id",
                "tournament_slug",
                "edition_id",
                "session_id",
                "draft_id",
                "draft_version",
                "team_id",
                "actor_id",
                "actor_role",
                "authority_binding",
                "staff_and_changes_binding",
                "field_evidence_binding",
            )
        },
        draft_content_hash=old.draft_content_hash.removeprefix("sha256:"),
        manifest_sha256=manifest["manifest_sha256"],
        document_id=document,
        policy_hash=c.policy["policy_hash"][7:],
        operation_id=uuid4(),
        run_id=uuid4(),
        member_id=uuid4(),
        source_pages=tuple(
            BatchSourcePage(
                p.page_index,
                old.pdf_sha256,
                p.pdf_page,
                p.asset_id,
                p.image_sha256[7:],
                *Image.open(BytesIO(c.images[index])).size,
            )
            for index, p in enumerate(old.source_pages)
        ),
    )
    args = copy.deepcopy(c.args)
    args["scope"] = scope
    for row in args["players"]:
        row.update(verified_by_human=False, needs_review=False)
    review = freeze_review_batch(**args)
    scope = replace(scope, staff_and_changes_binding=bind_review_plan(review, b"r" * 32))
    now = int(time.time())
    qual = "a" * 64
    checked = dict(
        schema=VERIFICATION_SCHEMA,
        kid="verifier",
        document_id=scope.document_id,
        manifest_sha256=scope.manifest_sha256,
        policy_hash=scope.policy_hash,
        profile_hash="b" * 64,
        scope_hash=batch_scope_hash(scope),
        payload_hash=hashlib.sha256(review).hexdigest(),
        source_binding_hash="c" * 64,
        qualification_hash=qual,
        checks_hash="d" * 64,
        human_edits_hash=hashlib.sha256(canonical([])).hexdigest(),
        issued_at=now,
        expires_at=now + 600,
        verified_by_human=False,
    )
    verification = sign_batch_proof(checked, signing_key=c.bus)
    root = canonical(
        {
            "schema": "ctt.batch.review-root.v1",
            "run_id": str(scope.run_id),
            "principal_id": str(scope.actor_id),
            "tenant_id": scope.tenant_id,
            "edition_id": str(scope.edition_id),
            "manifest_sha256": scope.manifest_sha256,
            "policy_hash": scope.policy_hash,
            "profile_hash": checked["profile_hash"],
            "qualification_hash": qual,
            "max_execution_seconds": 3600,
            "members": [
                dict(
                    document_id=scope.document_id,
                    member_id=str(scope.member_id),
                    scope_hash=batch_scope_hash(scope),
                    verification_hash=hashlib.sha256(verification).hexdigest(),
                    payload_hash=checked["payload_hash"],
                    human_edits_hash=checked["human_edits_hash"],
                )
            ],
        }
    )
    parent = dict(
        schema=CONSENT_SCHEMA,
        kid="issuer",
        consent_id=str(uuid4()),
        challenge_id=str(uuid4()),
        principal_id=str(scope.actor_id),
        credential_id=base64.b64encode(b"fixture").decode(),
        root_hash=hashlib.sha256(root).hexdigest(),
        qualification_hash=qual,
        capability_version=1,
        credential_version=1,
        principal_version=1,
        account_version=1,
        service_version=1,
        counter_before=4,
        counter_after=5,
        auth_profile="device-counter-v1",
        backup_eligible=False,
        backup_state=False,
        auth_policy_hash=None,
        issued_at=now,
        expires_at=now + 600,
    )
    consent = sign_batch_proof(parent, signing_key=c.issuer)
    child = dict(
        schema=MEMBER_SCHEMA,
        kid="issuer",
        authorization_id=str(uuid4()),
        consent_id=parent["consent_id"],
        member_id=str(scope.member_id),
        attempt_id=str(c.attempt),
        operation_id=str(scope.operation_id),
        principal_id=str(scope.actor_id),
        scope_hash=batch_scope_hash(scope),
        root_hash=parent["root_hash"],
        qualification_hash=qual,
        verification_hash=hashlib.sha256(verification).hexdigest(),
        authorization_generation=1,
        worker_id=str(uuid4()),
        lease_epoch=1,
        dispatch_generation=1,
        nonce=str(uuid4()),
        issued_at=now,
        expires_at=now + 180,
    )
    signed = sign_batch_proof(child, signing_key=c.issuer)
    gate = StaffCaptureGateV3(
        identity_key=b"i" * 32,
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
        signing_kid="gate",
        signing_key=c.signer,
        assertion_public_keys={"issuer": pub(c.issuer)},
        verifier_public_keys={"verifier": pub(c.bus)},
        receipt_public_keys={"bus": pub(c.bus)},
        policy_artifact=c.policy,
        expected_policy_hash=c.policy["policy_hash"],
        publisher=c.publish,
        max_page_bytes=1000000,
    )
    args = dict(
        scope=scope,
        attempt_id=c.attempt,
        review_bytes=review,
        signed_review=signed,
        parent_consent=consent,
        verification=verification,
        root_bytes=root,
        issued_at=now,
        expires_at=now + 180,
    )
    c.batch_snapshot = SourceSnapshotV2(
        canonical(manifest),
        {old.pdf_sha256: BytesIO(c.pdf)},
        dict(c.snapshot.page_assets),
        canonical_batch_scope_bytes(scope),
        1000000,
    )
    c.batch_args = args
    yield gate, args, c, child, checked, parent
    c.journal.close()
    c.snapshot.pdf.close()


def test_exact_parent_member_verifier_chain(batch):
    gate, args, *_ = batch
    plan, child = gate._review_batch(**args)
    assert all(r["verified_by_human"] is False for r in plan["players"])
    assert child["scope_hash"] == batch_scope_hash(args["scope"])


@pytest.mark.parametrize(
    "kind",
    ["member", "payload", "root", "verifier", "scope", "operation", "expiry", "qualification"],
)
def test_mixed_or_changed_proofs_rejected(batch, kind):
    gate, args, c, child, checked, parent = batch
    args = copy.copy(args)
    if kind == "root":
        args["root_bytes"] = canonical({"members": []})
    elif kind == "payload":
        args["review_bytes"] = args["review_bytes"].replace(b"PrivateFirstName", b"OtherFirstName")
    elif kind == "scope":
        args["scope"] = replace(args["scope"], document_id="f" * 64)
    elif kind == "verifier":
        checked["payload_hash"] = "f" * 64
        args["verification"] = sign_batch_proof(checked, signing_key=c.bus)
    else:
        if kind == "member":
            child["member_id"] = str(uuid4())
        elif kind == "operation":
            child["operation_id"] = str(uuid4())
        elif kind == "expiry":
            child["expires_at"] += 1
        else:
            child["qualification_hash"] = "f" * 64
        args["signed_review"] = sign_batch_proof(child, signing_key=c.issuer)
    with pytest.raises(StaffPreauthorizationDenied):
        gate._review_batch(**args)


def test_real_v3_receipts_pending_and_finality_domains(batch):
    from datetime import datetime, timezone

    from devnous.copa_telmex.staff_capture_authorization import verify_capture_authorization
    from devnous.copa_telmex.staff_capture_effect import (
        DATABASE_DERIVED_FIELDS,
        LATE_AUTHORITY_FIELDS,
        freeze_capture_batch_effect,
    )
    from devnous.copa_telmex.staff_capture_finality_authorization import (
        verify_capture_finality_authorization,
    )
    from devnous.copa_telmex.staff_capture_finality_effect import (
        BatchFinalityContext,
        freeze_finality_effect,
        pending_rows,
    )

    gate, args, c, child, *_ = batch
    preauth = gate.preauthorize(**args, evidence_bytes=c.evidence_bytes, snapshot=c.batch_snapshot)
    plan = __import__("json").loads(args["review_bytes"])
    team, players, staff = (copy.deepcopy(plan[name]) for name in ("team", "players", "staff"))
    for row in players + [s["row"] for s in staff if s["row"] is not None]:
        row.update(preauth["authority_metadata"][row["id"]])
    for row in [team] + players:
        row.update(c.generated[row["id"]])
    pending = freeze_capture_batch_effect(
        scope=args["scope"],
        team=team,
        players=players,
        staff=staff,
        player_source_refs=[
            f"page:{p['page_index']}:jugador_{p['source_slot']}" for p in plan["player_sources"]
        ],
    )
    envelope = gate.authorize_effect(
        **args,
        nonce=__import__("uuid").UUID(child["nonce"]),
        effect_bytes=pending,
        preauthorization=preauth,
    )
    verify_capture_authorization(
        envelope=envelope,
        public_keys={"gate": pub(c.signer)},
        scope=args["scope"],
        attempt_id=args["attempt_id"],
        nonce=__import__("uuid").UUID(child["nonce"]),
        review_bytes=args["review_bytes"],
        effect_bytes=pending,
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
        member_proof_bytes=args["signed_review"],
        now_seconds=int(time.time()),
    )
    context = BatchFinalityContext(
        "isolated-database",
        16384,
        497,
        args["attempt_id"],
        int(time.time()),
        args["expires_at"],
        uuid4(),
        c.batch_snapshot,
        0,
        True,
    )
    finalargs = dict(
        protected_context=context,
        scope=args["scope"],
        review_attempt_id=args["attempt_id"],
        review_bytes=args["review_bytes"],
        pending_effect_bytes=pending,
        signed_review=args["signed_review"],
        preauthorization=preauth,
    )
    observed = pending_rows(__import__("json").loads(pending))
    bundle = gate.finalize_rows(**finalargs, observed_pending_rows=observed)
    active = copy.deepcopy(observed)
    for item in bundle.records:
        row = (
            active["players"][0]
            if item["kind"] == "PLAYER"
            else active["staff"][item["slot"] - 1]["row"]
        )
        updated = datetime.fromtimestamp(context.issued_at, timezone.utc)
        row.update(
            governance_state="ACTIVE",
            finality_receipt_id=item["receipt"]["receipt_id"],
            updated_at=(
                updated.replace(tzinfo=None) if item["kind"] == "PLAYER" else updated
            ).isoformat(),
        )
        if item["kind"] == "PLAYER":
            row.update(postcommit_revision=1, postcommit_snapshot_hash="sha256:" + "f" * 64)
    effect = freeze_finality_effect(
        scope=args["scope"],
        protected_context=context,
        pending_effect_bytes=pending,
        observed_pending_rows=observed,
        verified_finalities=bundle,
        active_projection_db_derived=active,
        effect_key=b"e" * 32,
        preauthorization=preauth,
        receipt_public_keys={"bus": pub(c.bus)},
    )
    final = gate.authorize_finality_effect(
        **finalargs, finality_effect_bytes=effect, verified_finalities=bundle
    )
    verify_capture_finality_authorization(
        envelope=final,
        public_keys={"gate": pub(c.signer)},
        scope=args["scope"],
        protected_context=context,
        attempt_id=args["attempt_id"],
        nonce=context.nonce,
        review_bytes=args["review_bytes"],
        pending_effect_bytes=pending,
        finality_effect_bytes=effect,
        signed_review=args["signed_review"],
        preauthorization=preauth,
        verified_finalities=bundle,
        review_key=b"r" * 32,
        effect_key=b"e" * 32,
        receipt_public_keys={"bus": pub(c.bus)},
        assertion_public_keys={"issuer": pub(c.issuer)},
        now_seconds=int(time.time()),
    )
    assert all(r["event"]["event_type"].endswith("_v3") for r in bundle.records)
