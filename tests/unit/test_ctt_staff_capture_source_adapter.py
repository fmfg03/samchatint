"""Integrated source checks with disposable synthetic PDF and provider."""

import asyncio
import copy
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

import fitz
import pytest

from devnous.copa_telmex.batch_admission import validate_manifest
from devnous.copa_telmex.staff_capture_executor import (
    CaptureDenied,
    LockedAttempt,
)
from devnous.copa_telmex.staff_capture_source_adapter import (
    SourceBundle,
    SourceCheckAdapter,
)
from devnous.copa_telmex.staff_review_challenge import (
    canonical_scope_bytes,
    scope_hash,
)
from test_ctt_staff_capture_source_check import sample


def candidate():
    manifest, scope, _, edition = sample()
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "one")
    doc.new_page().insert_text((72, 72), "two")
    pdf = doc.tobytes()
    images = [doc.load_page(i).get_pixmap(alpha=False).tobytes("png") for i in range(2)]
    doc.close()
    manifest["documents"][0]["pdf_sha256"] = sha256(pdf).hexdigest()
    scope = replace(
        scope,
        manifest_sha256=validate_manifest(manifest)["manifest_sha256"],
        pdf_sha256=sha256(pdf).hexdigest(),
        source_pages=tuple(
            replace(page, image_sha256="sha256:" + sha256(image).hexdigest())
            for page, image in zip(scope.source_pages, images)
        ),
    )
    attempt = LockedAttempt(
        uuid4(),
        uuid4(),
        scope.actor_id,
        b"synthetic-credential",
        scope_hash(scope),
        canonical_scope_bytes(scope),
        edition,
    )
    return attempt, scope, manifest, pdf, images


class Provider:
    def __init__(self, manifest, pdf, images, *, fail=False, swap=False):
        self.manifest, self.pdf, self.images = manifest, pdf, images
        self.fail, self.swap = fail, swap
        self.entered = self.closed = False
        self.args = None

    @contextmanager
    def open_bundle(self, attempt_id, document_id, asset_ids):
        self.args = attempt_id, document_id, asset_ids
        self.entered = True
        if self.fail:
            raise RuntimeError("sensitive provider error")

        class SwapStream(BytesIO):
            def seek(inner, offset, whence=0):
                if self.swap and offset == 0:
                    inner.seek(0)
                    inner.write(b"x")
                return super().seek(offset, whence)

        source = SwapStream(self.pdf)
        assets = {
            asset_id: BytesIO(image) for asset_id, image in zip(asset_ids, self.images)
        }
        try:
            yield SourceBundle(self.manifest, source, assets)
        finally:
            source.close()
            for image in assets.values():
                image.close()
            self.closed = True


def test_accepts_source_bound_to_protected_scope():
    attempt, scope, manifest, pdf, images = candidate()
    provider = Provider(manifest, pdf, images)
    adapter = SourceCheckAdapter(
        provider,
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    adapter._check_candidate(attempt)
    assert provider.closed
    assert provider.args == (
        attempt.attempt_id,
        scope.document_id,
        tuple(page.asset_id for page in scope.source_pages),
    )


@pytest.mark.parametrize(
    "change",
    [
        "manifest",
        "pdf",
        "asset",
        "missing",
        "edition",
        "edition_missing",
        "scope",
        "actor",
        "provider",
        "source_limit",
        "asset_limit",
    ],
)
def test_denies_changed_missing_or_untrusted_input(change):
    attempt, scope, manifest, pdf, images = candidate()
    manifest = copy.deepcopy(manifest)
    kwargs = {
        "max_source_bytes": 1_000_000,
        "max_asset_bytes": 1_000_000,
        "max_manifest_bytes": 1_000_000,
    }
    if change == "manifest":
        manifest["documents"][0]["extraction"]["team"]["name"] = "changed"
    elif change == "pdf":
        pdf += b"changed"
    elif change == "asset":
        images[0] += b"changed"
    elif change == "missing":
        images = images[:1]
    elif change == "edition":
        attempt = replace(
            attempt,
            edition_binding=replace(attempt.edition_binding, edition_id=uuid4()),
        )
    elif change == "edition_missing":
        attempt = replace(attempt, edition_binding=None)
    elif change == "scope":
        attempt = replace(attempt, scope_hash="sha256:" + "f" * 64)
    elif change == "actor":
        attempt = replace(attempt, principal_id=uuid4())
    elif change == "source_limit":
        kwargs["max_source_bytes"] = 1
    elif change == "asset_limit":
        kwargs["max_asset_bytes"] = 1
    provider = Provider(manifest, pdf, images, fail=change == "provider")
    adapter = SourceCheckAdapter(provider, **kwargs)
    with pytest.raises(CaptureDenied, match="^capture denied$") as exc:
        adapter._check_candidate(attempt)
    assert exc.value.__suppress_context__
    if provider.entered and not provider.fail:
        assert provider.closed


@pytest.mark.parametrize("source_limit,asset_limit", [(0, 10), (10, 0), (True, 10)])
def test_invalid_trusted_limits_are_denied(source_limit, asset_limit):
    with pytest.raises(CaptureDenied):
        SourceCheckAdapter(
            object(),
            max_source_bytes=source_limit,
            max_asset_bytes=asset_limit,
            max_manifest_bytes=1_000_000,
        )


def test_executor_method_denies_even_after_successful_checks():
    attempt, _, manifest, pdf, images = candidate()
    provider = Provider(manifest, pdf, images)
    adapter = SourceCheckAdapter(
        provider,
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    with pytest.raises(CaptureDenied, match="^capture denied$"):
        asyncio.run(adapter.verify_source(attempt))
    assert provider.closed


def test_snapshot_remains_read_only_and_stable_after_provider_closes():
    import io
    import json

    attempt, scope, manifest, pdf, images = candidate()
    provider = Provider(manifest, pdf, images)
    adapter = SourceCheckAdapter(
        provider,
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    with adapter.open_verified_snapshot(attempt) as snapshot:
        assert provider.closed
        handle = snapshot.pdf
        assert handle.read() == pdf
        assert not handle.writable()
        with pytest.raises(io.UnsupportedOperation):
            handle.write(b"changed")
        with pytest.raises(TypeError):
            snapshot.page_assets[scope.source_pages[0].asset_id] = b"changed"
        manifest["documents"][0]["pdf"] = "changed"
        images[0] = b"changed"
        assert json.loads(snapshot.manifest_json)["documents"][0]["pdf"] == "source.pdf"
        assert snapshot.page_assets[scope.source_pages[0].asset_id] != images[0]
        handle.seek(0)
        assert handle.read() == pdf
    assert handle.closed


@pytest.mark.parametrize("cancellation", [False, True])
def test_snapshot_closes_on_consumer_error_or_cancellation(cancellation):
    attempt, _, manifest, pdf, images = candidate()
    provider = Provider(manifest, pdf, images)
    adapter = SourceCheckAdapter(
        provider,
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    expected = asyncio.CancelledError if cancellation else RuntimeError
    with pytest.raises(expected):
        with adapter.open_verified_snapshot(attempt) as snapshot:
            handle = snapshot.pdf
            raise expected()
    assert provider.closed and handle.closed


def test_manifest_snapshot_limit_fails_closed():
    attempt, _, manifest, pdf, images = candidate()
    provider = Provider(manifest, pdf, images)
    adapter = SourceCheckAdapter(
        provider,
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1,
    )
    with pytest.raises(CaptureDenied):
        with adapter.open_verified_snapshot(attempt):
            pytest.fail("unexpected snapshot")
    assert provider.closed


def test_temporary_files_are_closed_on_validation_failure(monkeypatch):
    import devnous.copa_telmex.staff_capture_source_adapter as adapter_module

    original = adapter_module.tempfile.TemporaryFile
    opened = []

    def tracked(*args, **kwargs):
        handle = original(*args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(adapter_module.tempfile, "TemporaryFile", tracked)
    attempt, _, manifest, pdf, images = candidate()
    provider = Provider(manifest, pdf + b"changed", images)
    adapter = SourceCheckAdapter(
        provider,
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    with pytest.raises(CaptureDenied):
        with adapter.open_verified_snapshot(attempt):
            pytest.fail("unexpected snapshot")
    assert provider.closed
    assert opened and all(handle.closed for handle in opened)


@pytest.mark.parametrize("outcome", ["success", "error", "cancel", "mutate"])
def test_executor_receives_retained_snapshot_and_cleans_up(outcome):
    from devnous.copa_telmex.staff_capture_executor import (
        CaptureResult,
        StaffCaptureExecutor,
        VerifiedDecision,
    )
    from test_ctt_staff_capture_executor import Store, Verifier

    async def run():
        attempt, scope, manifest, pdf, images = candidate()
        provider = Provider(manifest, pdf, images)
        adapter = SourceCheckAdapter(
            provider,
            max_source_bytes=1_000_000,
            max_asset_bytes=1_000_000,
            max_manifest_bytes=1_000_000,
        )
        observed = []

        class SnapshotStore(Store):
            def open_source(self, locked):
                return adapter.open_verified_snapshot(locked)

            async def capture(self, locked, source):
                observed.append(source)
                assert provider.closed
                provider.pdf = b"changed original"
                assert not source.pdf.closed
                assert source.pdf.read() == pdf
                assert list(source.page_assets.values()) == images
                if outcome == "mutate":
                    import os

                    fd = os.open(f"/proc/self/fd/{source.pdf.fileno()}", os.O_WRONLY)
                    try:
                        os.pwrite(fd, b"X", 0)
                    finally:
                        os.close(fd)
                if outcome == "cancel":
                    raise asyncio.CancelledError()
                if outcome == "error":
                    raise RuntimeError("synthetic failure")
                self.result = CaptureResult(locked.attempt_id, uuid4())
                return self.result

        store = SnapshotStore(attempt)
        decision = VerifiedDecision(
            attempt.attempt_id,
            attempt.challenge_id,
            attempt.principal_id,
            attempt.credential_id,
            attempt.scope_hash,
            0,
            1,
        )
        executor = StaffCaptureExecutor(store, Verifier(decision))
        if outcome == "success":
            await executor.execute(attempt.attempt_id, b"valid")
        else:
            expected = asyncio.CancelledError if outcome == "cancel" else CaptureDenied
            with pytest.raises(expected):
                await executor.execute(attempt.attempt_id, b"valid")
            assert store.consumed == 0
            assert store.result is None
        assert len(observed) == 1
        assert observed[0].pdf.closed

    asyncio.run(run())


def test_revalidation_denies_snapshot_modified_by_same_uid():
    import os

    attempt, _, manifest, pdf, images = candidate()
    adapter = SourceCheckAdapter(
        Provider(manifest, pdf, images),
        max_source_bytes=1_000_000,
        max_asset_bytes=1_000_000,
        max_manifest_bytes=1_000_000,
    )
    with adapter.open_verified_snapshot(attempt) as source:
        source.verify_unchanged()
        fd = os.open(f"/proc/self/fd/{source.pdf.fileno()}", os.O_WRONLY)
        try:
            os.pwrite(fd, b"X", 0)
        finally:
            os.close(fd)
        with pytest.raises(CaptureDenied):
            source.verify_unchanged()
