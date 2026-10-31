"""Manifest and source precheck with synthetic data; no authority implied."""

import copy
from dataclasses import replace
import hashlib
from io import BytesIO
from uuid import UUID, uuid4

import pytest

from devnous.copa_telmex.batch_admission import sha256_value, validate_manifest
from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_source_check import (
    TrustedEditionBinding,
    check_manifest_source,
)
from devnous.copa_telmex.staff_review_challenge import ReviewScope, SourcePage


def sample():
    payload = b"synthetic PDF bytes"
    staff = []
    for slot, role in [(1, "director_tecnico"), (2, "auxiliar")]:
        evidence = {"first_name": {"value": None, "status": "missing"}}
        item = {
            "role": role,
            "slot": slot,
            "slot_ref": role,
            "evidence": evidence,
        }
        staff.append({**item, "staff_entry_id": sha256_value(item)})
    manifest = {
        "schema_version": "ctt.registration.batch-admission.v1",
        "edition": {
            "tournament_id": str(uuid4()),
            "edition_year": 2026,
            "roster_slug": "copa-telmex-2026",
        },
        "documents": [
            {
                "document_id": "a" * 64,
                "pdf": "source.pdf",
                "pdf_sha256": hashlib.sha256(payload).hexdigest(),
                "pages": [1, 2],
                "extraction": {"team": {}, "players": [], "staff": staff},
            }
        ],
    }
    sha, mac = "sha256:" + "b" * 64, "hmac-sha256:" + "c" * 64
    scope = ReviewScope(
        tenant_id="synthetic",
        tournament_slug="sample",
        edition_id=uuid4(),
        session_id=uuid4(),
        draft_id=uuid4(),
        draft_version=1,
        draft_content_hash=sha,
        team_id=uuid4(),
        manifest_sha256=validate_manifest(manifest)["manifest_sha256"],
        document_id="a" * 64,
        pdf_sha256=hashlib.sha256(payload).hexdigest(),
        source_pages=(
            SourcePage(1, uuid4(), sha, 1),
            SourcePage(2, uuid4(), sha, 2),
        ),
        actor_id=uuid4(),
        actor_role="coordinador",
        authority_binding=mac,
        staff_and_changes_binding=mac,
        decision_hash=sha,
        review_receipt_id=sha,
        policy_hash=sha,
        operation_id=sha,
    )
    binding = TrustedEditionBinding(
        scope.tenant_id,
        scope.edition_id,
        UUID(manifest["edition"]["tournament_id"]),
        manifest["edition"]["edition_year"],
        manifest["edition"]["roster_slug"],
    )
    return manifest, scope, payload, binding


def test_accepts_exact_manifest_and_stream():
    manifest, scope, payload, binding = sample()
    check_manifest_source(
        manifest, scope, BytesIO(payload), binding, max_source_bytes=1024
    )


@pytest.mark.parametrize("change", ["manifest", "pdf", "page", "document", "offset"])
def test_rejects_changed_source_or_noninitial_stream(change):
    manifest, scope, payload, binding = sample()
    manifest = copy.deepcopy(manifest)
    stream = BytesIO(payload)
    if change == "manifest":
        manifest["documents"][0]["extraction"]["team"]["name"] = "other"
    elif change == "pdf":
        stream = BytesIO(payload + b"changed")
    elif change == "page":
        scope = replace(scope, source_pages=scope.source_pages[:1])
    elif change == "document":
        scope = replace(scope, document_id="f" * 64)
    else:
        stream.seek(1)
    with pytest.raises(CaptureDenied, match="^capture denied$"):
        check_manifest_source(manifest, scope, stream, binding, max_source_bytes=1024)


def test_scope_round_trip_is_canonical_and_rejects_changes():
    from devnous.copa_telmex.staff_review_challenge import (
        ChallengePrecheckError,
        canonical_scope_bytes,
        parse_canonical_scope,
    )

    _, scope, _, _ = sample()
    raw = canonical_scope_bytes(scope)
    assert parse_canonical_scope(raw) == scope
    for invalid in (
        raw + b" ",
        b"{}",
        raw.replace(b'"tenant_id":', b'"tenant_id":"fake","tenant_id":'),
    ):
        with pytest.raises(ChallengePrecheckError):
            parse_canonical_scope(invalid)


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "other"),
        ("edition_id", uuid4()),
        ("tournament_id", uuid4()),
        ("edition_year", 2025),
        ("roster_slug", "other"),
    ],
)
def test_trusted_edition_binding_must_match(field, value):
    manifest, scope, payload, binding = sample()
    with pytest.raises(CaptureDenied):
        check_manifest_source(
            manifest,
            scope,
            BytesIO(payload),
            replace(binding, **{field: value}),
            max_source_bytes=1024,
        )


def test_pdf_pages_are_rendered_from_verified_source():
    import fitz

    from devnous.copa_telmex.staff_capture_source_check import (
        check_pdf_page_images,
    )

    _, scope, _, _ = sample()
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "page one")
    doc.new_page().insert_text((72, 72), "page two")
    payload = doc.tobytes()
    pages = []
    for index, source in enumerate(scope.source_pages):
        png = doc.load_page(index).get_pixmap(alpha=False).tobytes("png")
        pages.append(
            replace(
                source,
                image_sha256="sha256:" + hashlib.sha256(png).hexdigest(),
            )
        )
    doc.close()
    scope = replace(
        scope,
        pdf_sha256=hashlib.sha256(payload).hexdigest(),
        source_pages=tuple(pages),
    )
    check_pdf_page_images(BytesIO(payload), scope, max_source_bytes=1_000_000)
    wrong_page = replace(pages[0], image_sha256="sha256:" + "f" * 64)
    with pytest.raises(CaptureDenied):
        check_pdf_page_images(
            BytesIO(payload),
            replace(scope, source_pages=(wrong_page, pages[1])),
            max_source_bytes=1_000_000,
        )
    with pytest.raises(CaptureDenied):
        check_pdf_page_images(
            BytesIO(payload + b"changed"), scope, max_source_bytes=1_000_000
        )
    missing = replace(pages[1], pdf_page=3)
    with pytest.raises(CaptureDenied):
        check_pdf_page_images(
            BytesIO(payload),
            replace(scope, source_pages=(pages[0], missing)),
            max_source_bytes=1_000_000,
        )
    invalid = b"not a PDF"
    with pytest.raises(CaptureDenied):
        check_pdf_page_images(
            BytesIO(invalid),
            replace(scope, pdf_sha256=hashlib.sha256(invalid).hexdigest()),
            max_source_bytes=1_000_000,
        )


def test_source_size_budget_is_enforced_before_pdf_processing():
    from devnous.copa_telmex.staff_capture_source_check import (
        check_pdf_page_images,
    )

    manifest, scope, payload, binding = sample()
    with pytest.raises(CaptureDenied):
        check_manifest_source(
            manifest,
            scope,
            BytesIO(payload),
            binding,
            max_source_bytes=len(payload) - 1,
        )
    with pytest.raises(CaptureDenied):
        check_pdf_page_images(
            BytesIO(payload), scope, max_source_bytes=len(payload) - 1
        )
    with pytest.raises(CaptureDenied):
        check_pdf_page_images(BytesIO(payload), scope, max_source_bytes=0)


def test_stored_assets_match_exact_ids_hashes_and_size():
    from devnous.copa_telmex.staff_capture_source_check import (
        check_stored_page_assets,
    )

    _, scope, _, _ = sample()
    contents = [b"synthetic image 1", b"synthetic image 2"]
    pages = tuple(
        replace(page, image_sha256="sha256:" + hashlib.sha256(content).hexdigest())
        for page, content in zip(scope.source_pages, contents)
    )
    scope = replace(scope, source_pages=pages)

    def streams():
        return {
            page.asset_id: BytesIO(content) for page, content in zip(pages, contents)
        }

    check_stored_page_assets(scope, streams(), max_asset_bytes=1024)
    changed = streams()
    changed[pages[0].asset_id] = BytesIO(b"tampered")
    with pytest.raises(CaptureDenied):
        check_stored_page_assets(scope, changed, max_asset_bytes=1024)
    missing = streams()
    del missing[pages[0].asset_id]
    with pytest.raises(CaptureDenied):
        check_stored_page_assets(scope, missing, max_asset_bytes=1024)
    with pytest.raises(CaptureDenied):
        check_stored_page_assets(scope, streams(), max_asset_bytes=1)
