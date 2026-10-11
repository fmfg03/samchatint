"""Durable append-only technical audit with a verified hash chain."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import ReadError

ALLOWED_EVENT_KEYS = frozenset(
    {
        "correlation_id",
        "operation",
        "policy_version",
        "at_utc",
        "source_kind",
        "phase",
        "actor_binding_digest",
        "decision",
        "reason",
        "bytes",
        "duration_ms",
    }
)
MAX_EVENT_BYTES = 4096
GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditFileBinding:
    path: Path
    owner_uid: int
    owner_gid: int
    mode: int = 0o640
    create_for_disposable_test: bool = False
    fsync_directory: bool = True

    def check(self) -> None:
        if (
            not self.path.is_absolute()
            or self.owner_uid < 0
            or self.owner_gid < 0
            or self.mode & 0o137
        ):
            raise ReadError("AUDIT_UNAVAILABLE")


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


class DurableJsonlAudit:
    """Fail-closed audit; writes only minimised scalar event metadata."""

    def __init__(self, binding: AuditFileBinding) -> None:
        binding.check()
        self.binding = binding
        self._lock = threading.RLock()
        self._sequence, self._previous = self.verify()

    def _open(self, *, append: bool) -> int:
        flags = os.O_CLOEXEC | (os.O_WRONLY | os.O_APPEND if append else os.O_RDONLY)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if append and self.binding.create_for_disposable_test:
            flags |= os.O_CREAT
        try:
            descriptor = os.open(self.binding.path, flags, self.binding.mode)
        except OSError as exc:
            raise ReadError("AUDIT_UNAVAILABLE") from exc
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != self.binding.owner_uid
            or info.st_gid != self.binding.owner_gid
            or stat.S_IMODE(info.st_mode) != self.binding.mode
        ):
            os.close(descriptor)
            raise ReadError("AUDIT_UNAVAILABLE")
        return descriptor

    @staticmethod
    def _event(event: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(event, Mapping) or set(event) - ALLOWED_EVENT_KEYS:
            raise ReadError("AUDIT_UNAVAILABLE")
        normalized = dict(event)
        if any(
            value is not None and type(value) not in {str, int, float, bool}
            for value in normalized.values()
        ):
            raise ReadError("AUDIT_UNAVAILABLE")
        if len(_canonical(normalized)) > MAX_EVENT_BYTES:
            raise ReadError("AUDIT_UNAVAILABLE")
        return normalized

    def verify(self) -> tuple[int, str]:
        if not self.binding.path.exists():
            if self.binding.create_for_disposable_test:
                descriptor = self._open(append=True)
                os.fsync(descriptor)
                os.close(descriptor)
                if self.binding.fsync_directory:
                    parent = os.open(
                        self.binding.path.parent, os.O_RDONLY | os.O_CLOEXEC
                    )
                    try:
                        os.fsync(parent)
                    finally:
                        os.close(parent)
                return 0, GENESIS
            raise ReadError("AUDIT_UNAVAILABLE")
        descriptor = self._open(append=False)
        sequence, previous = 0, GENESIS
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                for raw in stream:
                    if len(raw) > MAX_EVENT_BYTES * 2 or not raw.endswith(b"\n"):
                        raise ReadError("AUDIT_UNAVAILABLE")
                    try:
                        record = json.loads(raw)
                    except (UnicodeError, ValueError) as exc:
                        raise ReadError("AUDIT_UNAVAILABLE") from exc
                    if set(record) != {
                        "sequence",
                        "previous_digest",
                        "event",
                        "record_digest",
                    }:
                        raise ReadError("AUDIT_UNAVAILABLE")
                    if (
                        record["sequence"] != sequence + 1
                        or record["previous_digest"] != previous
                    ):
                        raise ReadError("AUDIT_UNAVAILABLE")
                    event = self._event(record["event"])
                    digest = hashlib.sha256(
                        _canonical(
                            {
                                "sequence": record["sequence"],
                                "previous_digest": previous,
                                "event": event,
                            }
                        )
                    ).hexdigest()
                    if digest != record["record_digest"]:
                        raise ReadError("AUDIT_UNAVAILABLE")
                    sequence, previous = record["sequence"], digest
        finally:
            os.close(descriptor)
        return sequence, previous

    def append(self, event: Mapping[str, Any]) -> None:
        normalized = self._event(event)
        with self._lock:
            sequence, previous = self.verify()
            if sequence != self._sequence or previous != self._previous:
                raise ReadError("AUDIT_UNAVAILABLE")
            body = {
                "sequence": sequence + 1,
                "previous_digest": previous,
                "event": normalized,
            }
            digest = hashlib.sha256(_canonical(body)).hexdigest()
            encoded = _canonical({**body, "record_digest": digest}) + b"\n"
            descriptor = self._open(append=True)
            try:
                if os.write(descriptor, encoded) != len(encoded):
                    raise ReadError("AUDIT_UNAVAILABLE")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._sequence, self._previous = sequence + 1, digest
