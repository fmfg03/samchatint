"""Read content-addressed private files for one protected capture attempt.

No file is admitted or approved by this provider. A separately authorized
admission process must populate this store and protect its OS identity.
Snapshots and all byte/source checks remain the SourceCheckAdapter's job.
"""

import json
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
import stat

from .staff_capture_executor import CaptureDenied, LockedAttempt
from .staff_capture_source_adapter import SourceBundle
from .staff_review_challenge import parse_canonical_scope, scope_hash


def _pairs(items):
    result = {}
    for name, value in items:
        if name in result:
            raise ValueError("duplicate manifest field")
        result[name] = value
    return result


class PrivateAttemptSourceProvider:
    """Pinned immutable locator; no request may supply filesystem paths."""

    def __init__(self, attempt: LockedAttempt, root: Path, *, max_manifest_bytes: int):
        try:
            if (
                type(attempt) is not LockedAttempt
                or type(max_manifest_bytes) is not int
                or max_manifest_bytes < 1
            ):
                raise ValueError("invalid source configuration")
            self._scope = parse_canonical_scope(attempt.scope_bytes)
            if scope_hash(self._scope) != attempt.scope_hash:
                raise ValueError("invalid scope")
            self._attempt, self._root = attempt.attempt_id, Path(root)
            if not self._root.is_absolute() or ".." in self._root.parts:
                raise ValueError("private root required")
            self._limit = max_manifest_bytes
        except Exception:
            raise CaptureDenied("capture denied") from None

    @staticmethod
    def _check_directory(fd):
        info = os.fstat(fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("private directory required")

    @staticmethod
    def _open_file(directory, name, stack):
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
                or info.st_nlink != 1
            ):
                raise ValueError("private file required")
            handle = os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
        return stack.enter_context(handle)

    @contextmanager
    def open_bundle(self, attempt_id, document_id, asset_ids):
        stack = ExitStack()
        try:
            try:
                scope = self._scope
                ids = tuple(page.asset_id for page in scope.source_pages)
                if (
                    attempt_id != self._attempt
                    or document_id != scope.document_id
                    or tuple(asset_ids) != ids
                ):
                    raise ValueError("source locator mismatch")
                # Pin directories by descriptor; reject symlink components.
                fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                stack.callback(os.close, fd)
                for part in self._root.parts[1:]:
                    fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=fd,
                    )
                    stack.callback(os.close, fd)
                self._check_directory(fd)
                directories = {}
                for kind in ("manifests", "pdfs", "pages"):
                    child = os.open(
                        kind,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=fd,
                    )
                    stack.callback(os.close, child)
                    self._check_directory(child)
                    directories[kind] = child
                manifest_file = self._open_file(
                    directories["manifests"],
                    scope.manifest_sha256 + ".json",
                    stack,
                )
                manifest_bytes = manifest_file.read(self._limit + 1)
                if len(manifest_bytes) > self._limit:
                    raise ValueError("manifest too large")
                manifest = json.loads(manifest_bytes, object_pairs_hook=_pairs)
                pdf = self._open_file(
                    directories["pdfs"], scope.pdf_sha256 + ".pdf", stack
                )
                pages = {
                    page.asset_id: self._open_file(
                        directories["pages"],
                        page.image_sha256.removeprefix("sha256:") + ".png",
                        stack,
                    )
                    for page in scope.source_pages
                }
                bundle = SourceBundle(manifest, pdf, pages)
            except Exception:
                raise CaptureDenied("capture denied") from None
            yield bundle
        finally:
            stack.close()
