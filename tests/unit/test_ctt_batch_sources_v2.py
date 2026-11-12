"""Source-integrity boundaries; no team/authority fixture or live service."""

import hashlib
import json
from uuid import uuid4

import fitz
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devnous.copa_telmex.staff_batch_contracts import (
    BatchContractError,
    canonical,
    source_refs,
    stable_document_id,
    strict_loads,
    validate_source_manifest,
)
from devnous.copa_telmex.staff_batch_source_adapter import (
    PrivateBatchSourceProvider,
    SourceCheckAdapterV2,
)
from devnous.copa_telmex.staff_batch_source_admission import (
    _publish,
    prepare_source_admission_v2,
    sign_source_admission_v2,
    verify_source_admission_v2,
)
from devnous.copa_telmex.staff_source_admission import SourceAdmissionLimits

EDITION = {
    "tenant_id": "synthetic",
    "edition_id": "11111111-1111-4111-8111-111111111111",
    "tournament_id": "22222222-2222-4222-8222-222222222222",
    "edition_year": 2026,
    "roster_slug": "ctt-synthetic",
    "phase": "national",
}
LIMITS = SourceAdmissionLimits(1_048_576, 1_048_576, 4_194_304, 1_048_576, 4_194_304)


@pytest.fixture
def sources(tmp_path):
    root = tmp_path / "input"
    root.mkdir(mode=0o700)
    pdfs = []
    for n in range(2):
        pdf = fitz.open()
        page = pdf.new_page(width=120, height=160)
        page.insert_text((5, 20), "synthetic source " + str(n))
        raw = pdf.tobytes()
        pdf.close()
        path = root / f"part-{n}.pdf"
        path.write_bytes(raw)
        path.chmod(0o600)
        pdfs.append(
            {
                "filename": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "page_count": 1,
            }
        )
    slices = [{"pdf_sha256": p["sha256"], "pages": [1]} for p in pdfs]
    document_id = stable_document_id(EDITION["edition_id"], source_refs(slices))
    manifest = validate_source_manifest(
        {
            "schema_version": "ctt.registration.source-manifest.v2",
            "edition": EDITION,
            "pdfs": pdfs,
            "documents": [
                {"document_id": document_id, "source_slices": slices, "extraction": {}}
            ],
        }
    )
    return root, manifest


def test_identity_ignores_payload_profile_and_manifest_partition(sources):
    _, manifest = sources
    doc = manifest["documents"][0]
    expected = doc["document_id"]
    manifest.pop("manifest_sha256")
    doc["extraction"] = {"profile": "new", "name": "synthetic"}
    changed = validate_source_manifest(manifest)
    assert changed["documents"][0]["document_id"] == expected
    assert (
        stable_document_id(
            EDITION["edition_id"], tuple(reversed(source_refs(doc["source_slices"])))
        )
        != expected
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b"[" * 65 + b"0" + b"]" * 65,
        b'{"a":Infinity}',
        b"",
    ],
)
def test_non_json_or_duplicate_claims_denied(raw):
    with pytest.raises(BatchContractError):
        strict_loads(raw)


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_page", "wrong_hash", "path", "boolean_page", "wrong_edition"],
)
def test_manifest_source_confusion_rejected(sources, mutation):
    _, original = sources
    value = json.loads(canonical(original))
    value.pop("manifest_sha256")
    if mutation == "duplicate_page":
        value["documents"].append(value["documents"][0])
    elif mutation == "wrong_hash":
        value["documents"][0]["document_id"] = "0" * 64
    elif mutation == "path":
        value["pdfs"][0]["filename"] = "../outside.pdf"
    elif mutation == "boolean_page":
        value["documents"][0]["source_slices"][0]["pages"] = [True]
    else:
        value["edition"]["edition_id"] = str(uuid4())
    with pytest.raises(BatchContractError):
        validate_source_manifest(value)


def test_same_physical_page_number_across_parts_and_readonly_snapshots(
    sources, tmp_path
):
    root, manifest = sources
    store = tmp_path / "store"
    store.mkdir(mode=0o700)
    with prepare_source_admission_v2(
        canonical(manifest), root, edition=EDITION, limits=LIMITS
    ) as prepared:
        _publish(prepared, store, uuid4())
        plan = strict_loads(prepared.plan_bytes)
        scope = canonical(
            {
                "schema": "ctt.batch.source-snapshot.v2",
                "tenant_id": EDITION["tenant_id"],
                "edition_id": EDITION["edition_id"],
                "tournament_slug": EDITION["roster_slug"],
                "manifest_sha256": manifest["manifest_sha256"],
                "document_id": manifest["documents"][0]["document_id"],
                "source_pages": [
                    {k: v for k, v in p.items() if k != "document_id"}
                    for p in plan["pages"]
                ],
            }
        )
        adapter = SourceCheckAdapterV2(
            PrivateBatchSourceProvider(store),
            max_source_bytes=LIMITS.max_pdf_bytes,
            max_asset_bytes=LIMITS.max_page_bytes,
            max_manifest_bytes=LIMITS.max_manifest_bytes,
        )
        with adapter.open_verified_snapshot(scope) as snapshot:
            assert len(snapshot.pdfs) == 2
            assert snapshot.pdf_for_page(1) is not snapshot.pdf_for_page(2)
            with pytest.raises(OSError):
                snapshot.pdf_for_page(1).write(b"not mutable")
            # Replacing an origin after snapshot cannot change the pinned bytes.
            target = store / "pdfs" / (manifest["pdfs"][0]["sha256"] + ".pdf")
            target.chmod(0o600)
            target.write_bytes(b"replaced")
            snapshot.verify_unchanged()
        with pytest.raises(BatchContractError):
            with adapter.open_verified_snapshot(scope):
                pass


def test_independent_source_signature_rejects_tamper_and_expiry(sources):
    root, manifest = sources
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    with prepare_source_admission_v2(
        canonical(manifest), root, edition=EDITION, limits=LIMITS
    ) as prepared:
        proof = sign_source_admission_v2(
            prepared.plan_bytes,
            admission_id=uuid4(),
            issued_at=100,
            expires_at=400,
            kid="source-test",
            private_key=private,
        )
        assert (
            verify_source_admission_v2(
                prepared.plan_bytes,
                proof,
                public_keys={"source-test": public},
                now_seconds=101,
            )["kid"]
            == "source-test"
        )
        for now, raw in [
            (400, prepared.plan_bytes),
            (99, prepared.plan_bytes),
            (101, prepared.plan_bytes + b" "),
        ]:
            with pytest.raises(ValueError):
                verify_source_admission_v2(
                    raw, proof, public_keys={"source-test": public}, now_seconds=now
                )


def test_source_symlink_denied_without_following(sources, tmp_path):
    root, manifest = sources
    path = root / manifest["pdfs"][0]["filename"]
    raw = path.read_bytes()
    other = tmp_path / "other.pdf"
    other.write_bytes(raw)
    other.chmod(0o600)
    path.unlink()
    path.symlink_to(other)
    with pytest.raises(ValueError):
        with prepare_source_admission_v2(
            canonical(manifest), root, edition=EDITION, limits=LIMITS
        ):
            pass
