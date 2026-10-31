"""Private source files reject mutable aliases and unsafe paths."""

import json
import os

import pytest

from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_private_sources import (
    PrivateAttemptSourceProvider,
)
from devnous.copa_telmex.staff_capture_source_adapter import SourceCheckAdapter
from test_ctt_staff_capture_source_adapter import candidate


def files(tmp_path):
    attempt, scope, manifest, pdf, images = candidate()
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    for kind in ("manifests", "pdfs", "pages"):
        (root / kind).mkdir(mode=0o700)
    content = [
        (
            root / "manifests" / (scope.manifest_sha256 + ".json"),
            json.dumps(manifest).encode(),
        ),
        (root / "pdfs" / (scope.pdf_sha256 + ".pdf"), pdf),
    ]
    content += [
        (root / "pages" / (page.image_sha256[7:] + ".png"), image)
        for page, image in zip(scope.source_pages, images)
    ]
    for path, data in content:
        path.write_bytes(data)
        path.chmod(0o600)
    return root, attempt, scope, content


def test_private_provider_uses_protected_hashes_and_snapshot(tmp_path):
    root, attempt, _, _ = files(tmp_path)
    provider = PrivateAttemptSourceProvider(attempt, root, max_manifest_bytes=1000000)
    adapter = SourceCheckAdapter(
        provider,
        max_source_bytes=1000000,
        max_asset_bytes=1000000,
        max_manifest_bytes=1000000,
    )
    with adapter.open_verified_snapshot(attempt) as snapshot:
        snapshot.verify_unchanged()
    assert snapshot.pdf.closed


@pytest.mark.parametrize(
    "attack", ["symlink", "hardlink", "permissions", "directory", "fifo"]
)
def test_unsafe_aliases_and_permissions_are_denied(tmp_path, attack):
    root, attempt, scope, content = files(tmp_path)
    path = content[1][0]
    if attack == "symlink":
        alternate = tmp_path / "other.pdf"
        path.rename(alternate)
        path.symlink_to(alternate)
    elif attack == "hardlink":
        os.link(path, tmp_path / "alias.pdf")
    elif attack == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif attack == "permissions":
        path.chmod(0o644)
    else:
        (root / "pdfs").chmod(0o755)
    provider = PrivateAttemptSourceProvider(attempt, root, max_manifest_bytes=1000000)
    with pytest.raises(CaptureDenied):
        with provider.open_bundle(
            attempt.attempt_id,
            scope.document_id,
            tuple(page.asset_id for page in scope.source_pages),
        ):
            pass
