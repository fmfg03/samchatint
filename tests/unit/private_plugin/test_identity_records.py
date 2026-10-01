"""Persistent synthetic records; no production schema or real accounts."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from samchat.private_plugin.identity_records import SQLiteExistingIdentityRecords


class IdentityRecordsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "fixture.sqlite"
        self.writer = sqlite3.connect(self.path)
        self.addCleanup(self.writer.close)
        self.writer.executescript(
            "CREATE TABLE local_identity_grants(grant_id,issuer,subject,token_id,"
            "client_id,installation_id,link_id,scopes_json,expires_at,active,revoked);"
            "CREATE TABLE local_identity_links(link_id,issuer,subject,installation_id,"
            "employee_id,organization_id,profile_id,active);"
            "CREATE TABLE local_existing_employees(employee_id,active);"
        )
        self.writer.execute(
            "INSERT INTO local_identity_grants VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "grant",
                "issuer",
                "subject",
                "jti",
                "client",
                "install",
                "link",
                json.dumps(["direction:read"]),
                200,
                1,
                0,
            ),
        )
        self.writer.execute(
            "INSERT INTO local_identity_links VALUES(?,?,?,?,?,?,?,?)",
            ("link", "issuer", "subject", "install", "employee", "org", "opaque", 1),
        )
        self.writer.execute("INSERT INTO local_existing_employees VALUES('employee',1)")
        self.writer.commit()
        self.reader = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.addCleanup(self.reader.close)
        self.records = SQLiteExistingIdentityRecords(self.reader)

    def test_reopen_readonly_and_current_revocation(self):
        grant = self.records.read_grant("issuer", "subject", "jti")
        self.assertEqual(grant.scopes, frozenset({"direction:read"}))
        self.assertFalse(grant.revoked)
        self.assertEqual(self.records.read_link("link").profile_id, "opaque")
        self.assertTrue(self.records.read_employee("employee").active)
        self.writer.execute("UPDATE local_identity_grants SET revoked=1")
        self.writer.execute("UPDATE local_existing_employees SET active=0")
        self.writer.commit()
        self.assertTrue(self.records.read_grant("issuer", "subject", "jti").revoked)
        self.assertFalse(self.records.read_employee("employee").active)
        self.assertEqual(self.reader.total_changes, 0)

    def test_isolation_and_parameterized_inputs(self):
        self.assertIsNone(self.records.read_grant("other", "subject", "jti"))
        self.assertIsNone(self.records.read_link("' OR 1=1 --"))
        self.assertIsNone(self.records.read_employee("other"))

    def test_ambiguous_or_corrupt_records_rejected(self):
        self.writer.execute("INSERT INTO local_existing_employees VALUES('employee',1)")
        self.writer.commit()
        with self.assertRaises(ValueError):
            self.records.read_employee("employee")
        for scopes in ("{}", '["x","x"]', "[1]", "[]"):
            self.writer.execute(
                "UPDATE local_identity_grants SET scopes_json=?", (scopes,)
            )
            self.writer.commit()
            with self.assertRaises(ValueError):
                self.records.read_grant("issuer", "subject", "jti")
        self.writer.execute("UPDATE local_identity_links SET active=2")
        self.writer.commit()
        with self.assertRaises(ValueError):
            self.records.read_link("link")
