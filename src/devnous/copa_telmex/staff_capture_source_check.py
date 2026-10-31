"""Check manifest and PDF bytes against a candidate reviewed scope.

This check does not approve a manifest or authenticate its source. The scope
and its manifest binding must come from a separately protected trusted record.
It does not verify stored assets, photo crops, or capture authority.
"""

from __future__ import annotations

import hashlib
import hmac
from uuid import UUID
from typing import Any, BinaryIO

from .batch_admission import validate_manifest
from .staff_capture_edition import TrustedEditionBinding
from .staff_capture_executor import CaptureDenied
from .staff_review_challenge import ReviewScope


def check_manifest_source(
    manifest: Any,
    scope: ReviewScope,
    source: BinaryIO,
    binding: TrustedEditionBinding,
    *,
    max_source_bytes: int,
) -> None:
    """Reject a changed document or PDF; no data or filenames enter errors."""
    try:
        if (
            type(scope) is not ReviewScope
            or source.tell() != 0
            or type(max_source_bytes) is not int
            or max_source_bytes < 1
        ):
            raise ValueError("invalid source")
        if (
            type(binding) is not TrustedEditionBinding
            or binding.tenant_id != scope.tenant_id
            or binding.edition_id != scope.edition_id
            or not isinstance(binding.tournament_id, UUID)
            or type(binding.edition_year) is not int
            or not isinstance(binding.roster_slug, str)
            or not binding.roster_slug
        ):
            raise ValueError("edition mismatch")
        normalized = validate_manifest(
            manifest,
            expected_tournament_id=str(binding.tournament_id),
            expected_edition_year=binding.edition_year,
        )
        if normalized["edition"]["roster_slug"] != binding.roster_slug:
            raise ValueError("edition mismatch")
        if not hmac.compare_digest(
            normalized["manifest_sha256"], scope.manifest_sha256
        ):
            raise ValueError("manifest mismatch")
        documents = [
            doc
            for doc in normalized["documents"]
            if doc["document_id"] == scope.document_id
        ]
        if len(documents) != 1:
            raise ValueError("document mismatch")
        document = documents[0]
        if not hmac.compare_digest(
            document["pdf_sha256"], scope.pdf_sha256
        ) or document["pages"] != [page.pdf_page for page in scope.source_pages]:
            raise ValueError("source mismatch")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = source.read(1024 * 1024)
            if not isinstance(chunk, bytes):
                raise ValueError("invalid source")
            if not chunk:
                break
            total += len(chunk)
            if total > max_source_bytes:
                raise ValueError("source too large")
            digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), scope.pdf_sha256):
            raise ValueError("source mismatch")
    except Exception:
        raise CaptureDenied("capture denied") from None


def check_pdf_page_images(
    source: BinaryIO, scope: ReviewScope, *, max_source_bytes: int
) -> None:
    """Render bound PDF pages and compare their PNG hashes.

    A private snapshot avoids loading large source PDFs into memory
    and ensures the verified bytes are the bytes rendered by this function.
    Photo crops and their geometry require separate verification.
    """
    import math
    import tempfile

    try:
        import fitz

        if (
            type(scope) is not ReviewScope
            or source.tell() != 0
            or type(max_source_bytes) is not int
            or max_source_bytes < 1
        ):
            raise ValueError("invalid source")
        digest = hashlib.sha256()
        total = 0
        with tempfile.TemporaryFile(mode="w+b") as snapshot:
            while True:
                chunk = source.read(1024 * 1024)
                if not isinstance(chunk, bytes):
                    raise ValueError("invalid source")
                if not chunk:
                    break
                total += len(chunk)
                if total > max_source_bytes:
                    raise ValueError("source too large")
                digest.update(chunk)
                snapshot.write(chunk)
            if not hmac.compare_digest(digest.hexdigest(), scope.pdf_sha256):
                raise ValueError("source mismatch")
            snapshot.flush()
            with fitz.open(f"/proc/self/fd/{snapshot.fileno()}", filetype="pdf") as pdf:
                if pdf.is_encrypted or not pdf.is_pdf:
                    raise ValueError("invalid PDF")
                for page in scope.source_pages:
                    if page.pdf_page > pdf.page_count:
                        raise ValueError("page missing")
                    source_page = pdf.load_page(page.pdf_page - 1)
                    rect = source_page.rect
                    width, height = math.ceil(rect.width), math.ceil(rect.height)
                    if (
                        width < 1
                        or height < 1
                        or width > 8000
                        or height > 8000
                        or width * height > 25_000_000
                    ):
                        raise ValueError("invalid page dimensions")
                    pix = source_page.get_pixmap(alpha=False)
                    if (
                        pix.width > 8000
                        or pix.height > 8000
                        or pix.width * pix.height > 25_000_000
                    ):
                        raise ValueError("invalid page dimensions")
                    image = pix.tobytes("png")
                    actual = "sha256:" + hashlib.sha256(image).hexdigest()
                    if not hmac.compare_digest(actual, page.image_sha256):
                        raise ValueError("page image mismatch")
    except Exception:
        raise CaptureDenied("capture denied") from None


def check_stored_page_assets(
    scope: ReviewScope,
    assets: dict[UUID, BinaryIO],
    *,
    max_asset_bytes: int,
) -> None:
    """Compare protected page streams with exact bound asset IDs and hashes.

    Callers must obtain streams through a trusted asset store and keep them
    stable for use; this function does not validate photo crops or geometry.
    """
    try:
        if (
            type(scope) is not ReviewScope
            or type(assets) is not dict
            or type(max_asset_bytes) is not int
            or max_asset_bytes < 1
            or set(assets) != {page.asset_id for page in scope.source_pages}
        ):
            raise ValueError("invalid assets")
        for page in scope.source_pages:
            source = assets[page.asset_id]
            if source.tell() != 0:
                raise ValueError("invalid asset")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = source.read(1024 * 1024)
                if not isinstance(chunk, bytes):
                    raise ValueError("invalid asset")
                if not chunk:
                    break
                total += len(chunk)
                if total > max_asset_bytes:
                    raise ValueError("asset too large")
                digest.update(chunk)
            if not hmac.compare_digest(
                "sha256:" + digest.hexdigest(), page.image_sha256
            ):
                raise ValueError("asset mismatch")
    except Exception:
        raise CaptureDenied("capture denied") from None
