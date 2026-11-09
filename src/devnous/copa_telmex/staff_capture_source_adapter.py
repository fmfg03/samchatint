"""Private candidate snapshots and source checks for a future capture writer.

The provider and limits must come from trusted service configuration. The
checks do not establish manifest approval, current draft authority, reviewer
faculty, or photo-crop geometry. This module grants no database privileges.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import os
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from io import BytesIO
from types import MappingProxyType
from typing import Any, BinaryIO, ContextManager, Iterator, Mapping, Protocol
from uuid import UUID

from .batch_admission import MAX_PAGES_PER_DOCUMENT
from .staff_capture_executor import CaptureDenied, LockedAttempt
from .staff_capture_source_check import (
    check_manifest_source,
    check_pdf_page_images,
    check_stored_page_assets,
)
from .staff_review_challenge import parse_canonical_scope, scope_hash


@dataclass(frozen=True, repr=False)
class SourceBundle:
    """Streams supplied and closed by a trusted provider context."""

    manifest: Any = field(repr=False)
    pdf: BinaryIO = field(repr=False)
    page_assets: dict[UUID, BinaryIO] = field(repr=False)


@dataclass(frozen=True, repr=False)
class SourceSnapshot:
    """Verified private bytes; valid only inside their enclosing context.

    The PDF is a read-only descriptor of an unlinked temporary snapshot;
    manifest JSON and page bytes are immutable. The holder must use these
    values for capture and must not reopen original provider sources.
    """

    manifest_json: bytes = field(repr=False)
    pdf: BinaryIO = field(repr=False)
    page_assets: Mapping[UUID, bytes] = field(repr=False)
    scope_bytes: bytes = field(repr=False)
    max_source_bytes: int = field(repr=False)

    def verify_unchanged(self) -> None:
        """Check retained bytes immediately before use by trusted capture."""
        position = self.pdf.tell()
        try:
            scope = parse_canonical_scope(self.scope_bytes)
            self.pdf.seek(0)
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = self.pdf.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.max_source_bytes:
                    raise ValueError("snapshot changed")
                digest.update(chunk)
            if digest.hexdigest() != scope.pdf_sha256:
                raise ValueError("snapshot changed")
            for page in scope.source_pages:
                if (
                    "sha256:"
                    + hashlib.sha256(self.page_assets[page.asset_id]).hexdigest()
                ) != page.image_sha256:
                    raise ValueError("snapshot changed")
        except Exception:
            raise CaptureDenied("capture denied") from None
        finally:
            self.pdf.seek(position)


class TrustedSourceProvider(Protocol):
    def open_bundle(
        self, attempt_id: UUID, document_id: str, asset_ids: tuple[UUID, ...]
    ) -> ContextManager[SourceBundle]:
        """Open sources from trusted storage; never use client paths."""
        ...


def _copy_stream(source: BinaryIO, target: BinaryIO, max_bytes: int) -> None:
    if source.tell() != 0:
        raise ValueError("invalid position")
    total = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not isinstance(chunk, bytes):
            raise ValueError("invalid stream")
        if not chunk:
            return
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("stream too large")
        target.write(chunk)


def _manifest_bytes(value: Any, max_bytes: int) -> bytes:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    parts = []
    total = 0
    for part in encoder.iterencode(value):
        data = part.encode("utf-8", "strict")
        total += len(data)
        if total > max_bytes:
            raise ValueError("manifest too large")
        parts.append(data)
    return b"".join(parts)


class SourceCheckAdapter:
    """Bounded source snapshots for a caller-owned locked transaction.

    Checks are synchronous and can be expensive; the future service must bound
    time/resources without allowing cancellation to outlive its transaction.
    The surrounding caller must establish source approval independently.
    """

    def __init__(
        self,
        provider: TrustedSourceProvider,
        *,
        max_source_bytes: int,
        max_asset_bytes: int,
        max_manifest_bytes: int,
    ) -> None:
        if any(
            type(value) is not int or value < 1
            for value in (
                max_source_bytes,
                max_asset_bytes,
                max_manifest_bytes,
            )
        ):
            raise CaptureDenied("capture denied")
        self._provider = provider
        self._max_source_bytes = max_source_bytes
        self._max_asset_bytes = max_asset_bytes
        self._max_manifest_bytes = max_manifest_bytes

    async def verify_source(self, attempt: LockedAttempt) -> None:
        """Remain closed until the writer consumes the returned snapshot."""
        self._check_candidate(attempt)
        raise CaptureDenied("capture denied") from None

    def _check_candidate(self, attempt: LockedAttempt) -> None:
        """Exercise snapshot checks without granting capture authority."""
        with self.open_verified_snapshot(attempt):
            pass

    @contextmanager
    def open_verified_snapshot(
        self, attempt: LockedAttempt
    ) -> Iterator[SourceSnapshot]:
        """Yield checked snapshots and close them on every context exit."""
        stack = ExitStack()
        try:
            try:
                if (
                    type(attempt) is not LockedAttempt
                    or attempt.edition_binding is None
                ):
                    raise ValueError("invalid attempt")
                scope = parse_canonical_scope(attempt.scope_bytes)
                if (
                    scope.actor_id != attempt.principal_id
                    or not isinstance(attempt.scope_hash, str)
                    or not hmac.compare_digest(scope_hash(scope), attempt.scope_hash)
                    or len(scope.source_pages) > MAX_PAGES_PER_DOCUMENT
                ):
                    raise ValueError("scope mismatch")
                ids = tuple(page.asset_id for page in scope.source_pages)
                temporary = stack.enter_context(tempfile.TemporaryFile("w+b"))
                with self._provider.open_bundle(
                    attempt.attempt_id, scope.document_id, ids
                ) as bundle:
                    if type(bundle) is not SourceBundle:
                        raise ValueError("invalid source bundle")
                    raw_manifest = _manifest_bytes(
                        bundle.manifest, self._max_manifest_bytes
                    )
                    manifest = json.loads(raw_manifest)
                    if type(bundle.page_assets) is not dict or set(
                        bundle.page_assets
                    ) != set(ids):
                        raise ValueError("asset mismatch")
                    _copy_stream(bundle.pdf, temporary, self._max_source_bytes)
                    asset_bytes = {}
                    for asset_id in ids:
                        with BytesIO() as target:
                            _copy_stream(
                                bundle.page_assets[asset_id],
                                target,
                                self._max_asset_bytes,
                            )
                            asset_bytes[asset_id] = target.getvalue()
                temporary.flush()
                read_fd = os.open(
                    f"/proc/self/fd/{temporary.fileno()}",
                    os.O_RDONLY | os.O_CLOEXEC,
                )
                try:
                    original_stat = os.fstat(temporary.fileno())
                    read_stat = os.fstat(read_fd)
                    if (original_stat.st_dev, original_stat.st_ino) != (
                        read_stat.st_dev,
                        read_stat.st_ino,
                    ):
                        raise ValueError("snapshot mismatch")
                    pdf = os.fdopen(read_fd, "rb")
                except BaseException:
                    os.close(read_fd)
                    raise
                stack.enter_context(pdf)
                check_manifest_source(
                    manifest,
                    scope,
                    pdf,
                    attempt.edition_binding,
                    max_source_bytes=self._max_source_bytes,
                )
                pdf.seek(0)
                check_pdf_page_images(
                    pdf, scope, max_source_bytes=self._max_source_bytes
                )
                with ExitStack() as asset_stack:
                    check_stored_page_assets(
                        scope,
                        {
                            asset_id: asset_stack.enter_context(BytesIO(data))
                            for asset_id, data in asset_bytes.items()
                        },
                        max_asset_bytes=self._max_asset_bytes,
                    )
                pdf.seek(0)
                snapshot = SourceSnapshot(
                    raw_manifest,
                    pdf,
                    MappingProxyType(asset_bytes),
                    attempt.scope_bytes,
                    self._max_source_bytes,
                )
            except Exception:
                raise CaptureDenied("capture denied") from None
            yield snapshot
        finally:
            stack.close()
