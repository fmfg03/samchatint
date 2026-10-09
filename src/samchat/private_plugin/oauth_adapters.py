"""Unwired bridge from transactional OAuth records to existing MCP boundaries."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from .oauth_identity import CurrentEmployee, ExistingGrant, ExistingLink
from .oauth_store import AUDIT, EMPLOYEES, GRANTS, LINKS, OAuthStoreError
from .perimeter import digest


class SQLAlchemyIdentityRecords:
    """Fresh read transaction per lookup; no external DSN or session caching."""

    def __init__(self, store):
        self.store = store

    def _one(self, statement):
        try:
            with self.store.engine.connect() as connection:
                return connection.execute(statement).mappings().one_or_none()
        except Exception:
            raise OAuthStoreError("STORE_UNAVAILABLE") from None

    def _namespace(self, table):
        return (
            table.c.issuer == self.store.issuer,
            table.c.installation_id == self.store.installation_id,
            table.c.organization_id == self.store.organization_id,
        )

    def read_grant(self, issuer, subject, token_id):
        if issuer != self.store.issuer:
            return None
        row = self._one(
            select(GRANTS).where(
                *self._namespace(GRANTS),
                GRANTS.c.subject == subject,
                GRANTS.c.token_id == token_id,
            )
        )
        if row is None:
            return None
        fields = dict(row)
        fields["scopes"] = frozenset(fields.pop("scopes_json"))
        return ExistingGrant(**fields)

    def read_link(self, link_id):
        row = self._one(
            select(LINKS).where(*self._namespace(LINKS), LINKS.c.link_id == link_id)
        )
        return ExistingLink(**dict(row)) if row else None

    def read_employee(self, employee_id):
        row = self._one(select(EMPLOYEES).where(EMPLOYEES.c.id == employee_id))
        return CurrentEmployee(str(row["id"]), row["activo"]) if row else None


@dataclass(frozen=True)
class ReadReceipt:
    receipt_id: str
    action: str
    outcome: str
    evidence_digest: str
    scope_digest: str


class SQLAlchemyReadAudit:
    """Read receipts in existing agent_action_receipts shape, commit before ID.

    Separate read receipts do not make business mutations atomic. Mutation
    support remains disabled; OAuth mutations use their own shared unit of work.
    """

    def __init__(self, store, *, now):
        self.store, self._now = store, now

    def append(self, *, actor_id, action, outcome):
        return self._append(
            actor_id=actor_id,
            action=action,
            outcome=outcome,
            evidence_digest=digest({"action": action, "outcome": outcome}),
            scope_digest=digest(
                {
                    "actor": actor_id,
                    "installation": self.store.installation_id,
                    "organization": self.store.organization_id,
                }
            ),
        )

    def append_read(self, *, actor_id, action, evidence_digest, scope_digest):
        return self._append(
            actor_id=actor_id,
            action=action,
            outcome="READ_VERIFIED",
            evidence_digest=evidence_digest,
            scope_digest=scope_digest,
        )

    def _append(self, **values):
        with self.store.transaction(now=self._now()) as transaction:
            receipt = transaction.append_boundary_audit(**values)
        return receipt

    def get_for_actor(self, receipt_id, *, actor_id):
        try:
            receipt_id, actor_id = str(UUID(receipt_id)), str(UUID(actor_id))
            with self.store.engine.connect() as connection:
                row = (
                    connection.execute(
                        select(AUDIT).where(
                            AUDIT.c.id == receipt_id,
                            AUDIT.c.actor_id == actor_id,
                            AUDIT.c.invoked_domain == "private_mcp",
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None or row["policy_envelope"] != {
                "installation_id": self.store.installation_id,
                "organization_id": self.store.organization_id,
                "issuer": self.store.issuer,
            }:
                return None
            hashes = row["normalized_redacted_inputs"]
            return ReadReceipt(
                receipt_id,
                row["action_id"],
                row["result"],
                hashes["evidence_digest"],
                hashes["scope_digest"],
            )
        except Exception:
            raise OAuthStoreError("STORE_UNAVAILABLE") from None
