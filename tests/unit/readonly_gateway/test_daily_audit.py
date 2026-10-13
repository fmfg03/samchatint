import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from samchat.readonly_gateway.contracts import ReadError
from samchat.readonly_gateway.daily_audit import DailyAudit


class DailyAuditTests(unittest.TestCase):
    def test_retention_boundary_utc_and_no_unrelated_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            now = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
            audit = DailyAudit(path, os.getuid(), os.getgid(), clock=lambda: now)
            event = {"phase": "attempt", "operation": "samchat.document_status"}
            audit.append(event)
            old = now - timedelta(days=31)
            DailyAudit(path, os.getuid(), os.getgid(), clock=lambda: old).append(event)
            boundary = now - timedelta(days=29)
            DailyAudit(path, os.getuid(), os.getgid(), clock=lambda: boundary).append(
                event
            )
            unrelated = path / "operator-note.txt"
            unrelated.write_text("synthetic")
            self.assertEqual(audit.prune(), ["audit-2026-09-08.jsonl"])
            self.assertTrue(unrelated.exists())
            self.assertTrue((path / "audit-2026-09-10.jsonl").exists())

    def test_untrusted_directory_and_tampered_archive_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            now = datetime(2026, 10, 9, tzinfo=timezone.utc)
            audit = DailyAudit(
                path, os.getuid(), os.getgid(), clock=lambda: now - timedelta(days=31)
            )
            audit.append({"phase": "attempt"})
            archive = path / "audit-2026-09-08.jsonl"
            archive.write_text("{}\n")
            with self.assertRaises(ReadError):
                audit.prune(now)
            self.assertTrue(archive.exists())
            path.chmod(0o755)
            with self.assertRaises(ReadError):
                DailyAudit(path, os.getuid(), os.getgid())
