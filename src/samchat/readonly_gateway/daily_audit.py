"""UTC daily technical audit, with explicit bounded 30-day retention."""

from __future__ import annotations

import os
import re
import stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .audit import AuditFileBinding, DurableJsonlAudit
from .contracts import ReadError, utc_now


class DailyAudit:
    def __init__(
        self, path: Path, uid: int, gid: int, *, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self.path, self.uid, self.gid, self.clock = path, uid, gid, clock
        self._lock = threading.RLock()
        self._day = ""
        self._audit: DurableJsonlAudit | None = None
        self._check_directory()

    def _check_directory(self) -> None:
        try:
            info = self.path.lstat()
        except OSError as exc:
            raise ReadError("AUDIT_UNAVAILABLE") from exc
        if (
            not self.path.is_absolute()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != self.uid
            or info.st_gid != self.gid
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ReadError("AUDIT_UNAVAILABLE")

    def append(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            self._check_directory()
            day = self.clock().astimezone(timezone.utc).date().isoformat()
            path = self.path / f"audit-{day}.jsonl"
            if self._day != day:
                try:
                    descriptor = os.open(
                        path,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | os.O_CLOEXEC
                        | os.O_NOFOLLOW,
                        0o600,
                    )
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ReadError("AUDIT_UNAVAILABLE") from exc
                else:
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    directory = os.open(
                        self.path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
                    )
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                self._audit = DurableJsonlAudit(
                    AuditFileBinding(path, self.uid, self.gid, mode=0o600)
                )
                self._day = day
            if self._audit is None:
                raise ReadError("AUDIT_UNAVAILABLE")
            self._audit.append(event)

    def prune(self, now: datetime | None = None) -> list[str]:
        """Remove only closed, verified daily logs beyond 30 UTC calendar days."""
        now = (now or self.clock()).astimezone(timezone.utc)
        cutoff = now.date() - timedelta(days=29)
        removed: list[str] = []
        with self._lock:
            self._check_directory()
            for path in sorted(self.path.iterdir()):
                match = re.fullmatch(r"audit-(\d{4}-\d{2}-\d{2})\.jsonl", path.name)
                if not match:
                    continue
                day = datetime.strptime(match[1], "%Y-%m-%d").date()
                if day >= cutoff:
                    continue
                DurableJsonlAudit(
                    AuditFileBinding(path, self.uid, self.gid, mode=0o600)
                )
                path.unlink()
                removed.append(path.name)
            if removed:
                directory = os.open(
                    self.path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        return removed
