"""Multi-original, byte-verified source snapshots for the batch path only.

All files come from an operator-populated CAS. This adapter establishes source
integrity, not admission, reviewer identity, field correctness or capture power.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, Mapping
from uuid import UUID

from .staff_batch_contracts import (
    BatchContractError,
    canonical,
    canonical_uuid,
    sha256_hex,
    source_refs,
    strict_loads,
    validate_source_manifest,
)
from .staff_source_admission import _check_directory, _open_root, _open_source


def _scope(raw: bytes) -> dict:
    value = strict_loads(raw, 262144)
    if (
        value.get("schema")
        not in {"ctt.staff-review.batch-scope.v3", "ctt.batch.source-snapshot.v2"}
        or canonical(value) != raw
    ):
        raise BatchContractError("BATCH_SOURCE_SCOPE_INVALID")
    if value["schema"] == "ctt.batch.source-snapshot.v2" and set(value) != {
        "schema",
        "tenant_id",
        "tournament_slug",
        "edition_id",
        "manifest_sha256",
        "document_id",
        "source_pages",
    }:
        raise BatchContractError("BATCH_SOURCE_SCOPE_INVALID")
    for key in ("manifest_sha256", "document_id"):
        sha256_hex(value[key])
    canonical_uuid(value["edition_id"])
    pages = value.get("source_pages")
    if type(pages) is not list or not 1 <= len(pages) <= 4:
        raise BatchContractError("BATCH_SOURCE_SCOPE_INVALID")
    assets, physical = set(), set()
    for index, page in enumerate(pages, 1):
        if (
            type(page) is not dict
            or set(page)
            != {
                "page_index",
                "pdf_sha256",
                "pdf_page",
                "asset_id",
                "image_sha256",
                "width",
                "height",
            }
            or page["page_index"] != index
            or type(page["page_index"]) is not int
        ):
            raise BatchContractError("BATCH_SOURCE_SCOPE_INVALID")
        sha256_hex(page["pdf_sha256"])
        sha256_hex(page["image_sha256"])
        canonical_uuid(page["asset_id"])
        if (
            any(
                type(page[k]) is not int or page[k] < 1
                for k in ("pdf_page", "width", "height")
            )
            or max(page["width"], page["height"]) > 8000
            or page["width"] * page["height"] > 25_000_000
        ):
            raise BatchContractError("BATCH_SOURCE_SCOPE_INVALID")
        ref = (page["pdf_sha256"], page["pdf_page"])
        if page["asset_id"] in assets or ref in physical:
            raise BatchContractError("BATCH_SOURCE_SCOPE_INVALID")
        assets.add(page["asset_id"])
        physical.add(ref)
    return dict(value)


def _hash_stream(stream: BinaryIO, limit: int) -> str:
    previous = stream.tell()
    try:
        stream.seek(0)
        total, digest = 0, hashlib.sha256()
        while data := stream.read(1024 * 1024):
            if type(data) is not bytes:
                raise BatchContractError("BATCH_SOURCE_INVALID")
            total += len(data)
            if total > limit:
                raise BatchContractError("BATCH_SOURCE_LIMIT")
            digest.update(data)
        return digest.hexdigest()
    finally:
        stream.seek(previous)


@dataclass(frozen=True, repr=False)
class SourceBundleV2:
    manifest: dict = field(repr=False)
    pdfs: Mapping[str, BinaryIO] = field(repr=False)
    page_assets: Mapping[UUID, BinaryIO] = field(repr=False)


@dataclass(frozen=True, repr=False)
class SourceSnapshotV2:
    manifest_json: bytes = field(repr=False)
    pdfs: Mapping[str, BinaryIO] = field(repr=False)
    page_assets: Mapping[UUID, bytes] = field(repr=False)
    scope_bytes: bytes = field(repr=False)
    max_source_bytes: int = field(repr=False)

    def verify_unchanged(self) -> None:
        scope = _scope(self.scope_bytes)
        manifest = validate_source_manifest(strict_loads(self.manifest_json))
        if manifest["manifest_sha256"] != scope["manifest_sha256"]:
            raise BatchContractError("BATCH_SOURCE_CHANGED")
        for sha, stream in self.pdfs.items():
            if _hash_stream(stream, self.max_source_bytes) != sha:
                raise BatchContractError("BATCH_SOURCE_CHANGED")
        for page in scope["source_pages"]:
            if (
                hashlib.sha256(self.page_assets[UUID(page["asset_id"])]).hexdigest()
                != page["image_sha256"]
            ):
                raise BatchContractError("BATCH_SOURCE_CHANGED")

    def pdf_for_page(self, page_index: int) -> BinaryIO:
        """Return the already pinned descriptor, never reopen a source path."""
        if type(page_index) is not int or page_index < 1:
            raise BatchContractError("BATCH_SOURCE_INVALID")
        pages = _scope(self.scope_bytes)["source_pages"]
        if page_index > len(pages):
            raise BatchContractError("BATCH_SOURCE_INVALID")
        return self.pdfs[pages[page_index - 1]["pdf_sha256"]]

    def derive_player_photo(self, slot_ref: str):
        """Keep the calibrated printed-slot crop using logical page identity."""
        import re

        from PIL import Image

        from devnous.tournaments.core.ctt_batch_photo_alignment import (
            extract_batch_photo,
            prepare_batch_page,
        )

        from .staff_photo_store import encode_review_photo

        self.verify_unchanged()
        parsed = re.fullmatch(r"page:([1-4]):jugador_([1-9]|1[0-9]|20)", slot_ref)
        if parsed is None:
            raise BatchContractError("BATCH_PHOTO_SOURCE_INVALID")
        page_index, slot = map(int, parsed.groups())
        pages = _scope(self.scope_bytes)["source_pages"]
        if (
            page_index > len(pages)
            or (page_index == 1 and slot > 8)
            or (page_index > 1 and slot < 9)
        ):
            raise BatchContractError("BATCH_PHOTO_SOURCE_INVALID")
        page = pages[page_index - 1]
        with Image.open(BytesIO(self.page_assets[UUID(page["asset_id"])])) as source:
            image = source.convert("RGB")
        grid = prepare_batch_page(image, page_index)
        crop, status = extract_batch_photo(
            image, page_index=page_index, slot=slot, prepared=grid
        )
        if status != "photo" or crop is None:
            raise BatchContractError("BATCH_PHOTO_SOURCE_INVALID")
        return encode_review_photo(crop)


class PrivateBatchSourceProvider:
    def __init__(self, root: Path, *, max_manifest_bytes: int = 8 * 1024 * 1024):
        if type(max_manifest_bytes) is not int or max_manifest_bytes < 1:
            raise BatchContractError("BATCH_SOURCE_LIMIT")
        self.root, self.limit = Path(root), max_manifest_bytes

    @contextmanager
    def open_bundle(self, scope_bytes: bytes):
        scope = _scope(scope_bytes)
        with ExitStack() as stack:
            root = _open_root(self.root, stack)
            folders = {}
            for name in ("manifests", "pdfs", "pages"):
                fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=root,
                )
                stack.callback(os.close, fd)
                _check_directory(fd)
                folders[name] = fd
            manifest_file = _open_source(
                folders["manifests"], scope["manifest_sha256"] + ".json", stack
            )
            raw = manifest_file.read(self.limit + 1)
            manifest = validate_source_manifest(strict_loads(raw, self.limit))
            pdfs = {
                sha: _open_source(folders["pdfs"], sha + ".pdf", stack)
                for sha in sorted({p["pdf_sha256"] for p in scope["source_pages"]})
            }
            pages = {
                UUID(p["asset_id"]): _open_source(
                    folders["pages"], p["image_sha256"] + ".png", stack
                )
                for p in scope["source_pages"]
            }
            yield SourceBundleV2(
                manifest, MappingProxyType(pdfs), MappingProxyType(pages)
            )


class SourceCheckAdapterV2:
    def __init__(
        self,
        provider,
        *,
        max_source_bytes: int,
        max_asset_bytes: int,
        max_manifest_bytes: int,
    ):
        if any(
            type(n) is not int or n < 1
            for n in (max_source_bytes, max_asset_bytes, max_manifest_bytes)
        ):
            raise BatchContractError("BATCH_SOURCE_LIMIT")
        self.provider = provider
        self.max_source_bytes, self.max_asset_bytes, self.max_manifest_bytes = (
            max_source_bytes,
            max_asset_bytes,
            max_manifest_bytes,
        )

    @contextmanager
    def open_verified_snapshot(self, scope_bytes: bytes):
        """Re-render every selected original page and compare exact CAS bytes."""
        import fitz
        from PIL import Image

        scope = _scope(scope_bytes)
        with ExitStack() as stack:
            with self.provider.open_bundle(scope_bytes) as bundle:
                if type(bundle) is not SourceBundleV2:
                    raise BatchContractError("BATCH_SOURCE_INVALID")
                manifest = validate_source_manifest(
                    bundle.manifest, scope["edition_id"]
                )
                raw_manifest = canonical(manifest)
                if (
                    len(raw_manifest) > self.max_manifest_bytes
                    or manifest["manifest_sha256"] != scope["manifest_sha256"]
                ):
                    raise BatchContractError("BATCH_SOURCE_CHANGED")
                if (
                    manifest["edition"]["tenant_id"] != scope["tenant_id"]
                    or manifest["edition"]["roster_slug"] != scope["tournament_slug"]
                ):
                    raise BatchContractError("BATCH_SOURCE_SCOPE_INVALID")
                doc = next(
                    (
                        d
                        for d in manifest["documents"]
                        if d["document_id"] == scope["document_id"]
                    ),
                    None,
                )
                refs = tuple(
                    (p["pdf_sha256"], p["pdf_page"]) for p in scope["source_pages"]
                )
                if (
                    doc is None
                    or source_refs(doc["source_slices"]) != refs
                    or set(bundle.pdfs) != {r[0] for r in refs}
                    or set(bundle.page_assets)
                    != {UUID(p["asset_id"]) for p in scope["source_pages"]}
                ):
                    raise BatchContractError("BATCH_SOURCE_SCOPE_INVALID")
                snapshots = {}
                for sha, original in bundle.pdfs.items():
                    temporary = stack.enter_context(tempfile.TemporaryFile("w+b"))
                    original.seek(0)
                    total = 0
                    while data := original.read(1024 * 1024):
                        total += len(data)
                        if total > self.max_source_bytes:
                            raise BatchContractError("BATCH_SOURCE_LIMIT")
                        temporary.write(data)
                    temporary.flush()
                    temporary.seek(0)
                    if _hash_stream(temporary, self.max_source_bytes) != sha:
                        raise BatchContractError("BATCH_SOURCE_CHANGED")
                    fd = os.open(
                        f"/proc/self/fd/{temporary.fileno()}",
                        os.O_RDONLY | os.O_CLOEXEC,
                    )
                    stream = stack.enter_context(os.fdopen(fd, "rb"))
                    snapshots[sha] = stream
                images = {}
                for page in scope["source_pages"]:
                    asset_id = UUID(page["asset_id"])
                    original = bundle.page_assets[asset_id]
                    original.seek(0)
                    image_bytes = original.read(self.max_asset_bytes + 1)
                    if (
                        len(image_bytes) > self.max_asset_bytes
                        or hashlib.sha256(image_bytes).hexdigest()
                        != page["image_sha256"]
                    ):
                        raise BatchContractError("BATCH_SOURCE_CHANGED")
                    with Image.open(BytesIO(image_bytes)) as image:
                        if image.size != (page["width"], page["height"]):
                            raise BatchContractError("BATCH_SOURCE_CHANGED")
                    stream = snapshots[page["pdf_sha256"]]
                    with fitz.open(
                        f"/proc/self/fd/{stream.fileno()}", filetype="pdf"
                    ) as pdf:
                        if (
                            pdf.is_encrypted
                            or not pdf.is_pdf
                            or page["pdf_page"] > len(pdf)
                        ):
                            raise BatchContractError("BATCH_SOURCE_CHANGED")
                        physical = pdf.load_page(page["pdf_page"] - 1)
                        if (
                            physical.rect.width > 8000
                            or physical.rect.height > 8000
                            or physical.rect.width * physical.rect.height > 25_000_000
                        ):
                            raise BatchContractError("BATCH_SOURCE_LIMIT")
                        pix = physical.get_pixmap(alpha=False)
                        if (pix.width, pix.height) != (
                            page["width"],
                            page["height"],
                        ) or pix.tobytes("png") != image_bytes:
                            raise BatchContractError("BATCH_SOURCE_CHANGED")
                    images[asset_id] = image_bytes
            snapshot = SourceSnapshotV2(
                raw_manifest,
                MappingProxyType(snapshots),
                MappingProxyType(images),
                scope_bytes,
                self.max_source_bytes,
            )
            snapshot.verify_unchanged()
            yield snapshot
