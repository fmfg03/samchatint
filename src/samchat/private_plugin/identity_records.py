"""Read-only local evidence adapter, not a production identity migration.

The caller supplies an already-open database with reviewed records. This module
has no provisioning, grant issuance, schema initialization or connection API.
The local table contract is documented separately from SamChat's live schema.
"""

import json
import sqlite3

from .oauth_identity import CurrentEmployee, ExistingGrant, ExistingLink


class SQLiteExistingIdentityRecords:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def _one(self, query, values):
        # A caller-held read transaction can retain revoked grants indefinitely
        # in its SQLite snapshot, even though every method issues another SELECT.
        # Do not commit/rollback a transaction owned by somebody else.
        if self._connection.in_transaction:
            raise ValueError("IDENTITY_TRANSACTION_ACTIVE")
        rows = self._connection.execute(query, values).fetchmany(2)
        if len(rows) > 1:
            raise ValueError("IDENTITY_RECORD_AMBIGUOUS")
        return tuple(rows[0]) if rows else None

    def read_grant(self, issuer, subject, token_id):
        row = self._one(
            "SELECT grant_id, issuer, subject, token_id, client_id, "
            "installation_id, link_id, scopes_json, expires_at, active, revoked, "
            "employee_id, organization_id, profile_id "
            "FROM local_identity_grants WHERE issuer=? AND subject=? AND token_id=?",
            (issuer, subject, token_id),
        )
        if row is None:
            return None
        scopes = json.loads(row[7])
        if (
            type(scopes) is not list
            or not scopes
            or any(type(s) is not str for s in scopes)
            or len(set(scopes)) != len(scopes)
        ):
            raise ValueError("IDENTITY_RECORD_INVALID")
        return ExistingGrant(
            *row[:7],
            frozenset(scopes),
            row[8],
            _boolean(row[9]),
            _boolean(row[10]),
            *row[11:14],
        )

    def read_link(self, link_id):
        row = self._one(
            "SELECT link_id, issuer, subject, installation_id, employee_id, "
            "organization_id, profile_id, active FROM local_identity_links "
            "WHERE link_id=?",
            (link_id,),
        )
        return ExistingLink(*row[:7], _boolean(row[7])) if row else None

    def read_employee(self, employee_id):
        row = self._one(
            "SELECT employee_id, active FROM local_existing_employees "
            "WHERE employee_id=?",
            (employee_id,),
        )
        return CurrentEmployee(row[0], _boolean(row[1])) if row else None


def _boolean(value):
    if type(value) is not int or value not in (0, 1):
        raise ValueError("IDENTITY_RECORD_INVALID")
    return bool(value)
