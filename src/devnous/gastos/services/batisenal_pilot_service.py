"""Owner-provisioned synthetic fixture + human-consented pilot; never startup DDL."""

from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    String,
    Table,
    UniqueConstraint,
    insert,
    inspect,
    select,
    update,
)

from samchat.batisenal_policy_fence import acquire_policy_fence_async

from . import batisenal_service as owner

fixtures = Table(
    "support_batisenal_pilot_fixtures",
    owner.metadata,
    Column("ticket_id", ForeignKey(owner.cases.c.ticket_id), primary_key=True),
    Column("attestation_sha256", String(64), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("starts_at", BigInteger),
    Column("expires_at", BigInteger),
    CheckConstraint(
        "(starts_at IS NULL AND expires_at IS NULL) OR (starts_at IS NOT NULL AND"
        " expires_at IS NOT NULL AND expires_at = starts_at + 3600)"
    ),
    CheckConstraint("length(attestation_sha256) = 64"),
)
pilots = Table(
    "support_batisenal_pilots",
    owner.metadata,
    Column("id", String(36), primary_key=True),
    Column("ticket_id", ForeignKey(fixtures.c.ticket_id), nullable=False),
    Column("employee", String(36), nullable=False),
    Column("profile_id", String(128), nullable=False),
    Column("issuer", String(512), nullable=False),
    Column("installation_id", String(128), nullable=False),
    Column("organization", String(64), nullable=False),
    Column("project", String(64), nullable=False),
    Column("agent_id", String(36), nullable=False),
    Column("source_kind", String(24), nullable=False),
    Column("starts_at", BigInteger, nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("revoked", Boolean, nullable=False),
    UniqueConstraint(
        "ticket_id", "employee", "profile_id", "issuer", "installation_id"
    ),
    CheckConstraint(
        "starts_at >= 0 AND expires_at > starts_at AND expires_at <= starts_at + 3600"
    ),
    CheckConstraint("source_kind IN ('synthetic_stub', 'ai_agent')"),
)
receipts = Table(
    "support_batisenal_pilot_receipts",
    owner.metadata,
    Column("id", String(36), primary_key=True),
    Column("pilot_id", ForeignKey(pilots.c.id), nullable=False),
    Column("actor", String(36), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("created_at", BigInteger, nullable=False),
)

consents = Table(
    "support_batisenal_pilot_consents",
    owner.metadata,
    Column("draft_hash", String(64), primary_key=True),
    Column("employee", String(36), nullable=False),
    Column("scope_hash", String(64), nullable=False),
    Column("oauth_fingerprint", String(64), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("expires_at", BigInteger, nullable=False),
    Column("used", Boolean, nullable=False),
)


class Pilot:
    def __init__(
        self,
        *,
        case_id,
        organization,
        project,
        issuer,
        installation_id,
        clock,
        source_kind="synthetic_stub",
    ):
        self.case_id = str(UUID(case_id))
        self.organization, self.project = organization, project
        self.issuer, self.installation_id = issuer, installation_id
        self.clock, self.source_kind = clock, source_kind
        if source_kind not in {"synthetic_stub", "ai_agent"}:
            raise ValueError("PILOT_CONFIGURATION_INVALID")

    def now(self):
        value = self.clock()
        if type(value) is not int or value < 0:
            raise owner.BatisenalError("PILOT_CLOCK_INVALID", 503)
        return value

    async def fixture(self, session):
        row = (
            (
                await session.execute(
                    select(fixtures).where(fixtures.c.ticket_id == UUID(self.case_id))
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row.active is not True:
            raise owner.BatisenalError("SYNTHETIC_FIXTURE_REQUIRED", 403)
        return row

    async def enroll(self, session, principal, link, *, reauthorize):
        """Only composition's CSRF-confirmed human flow calls this; no MCP method.

        Fixture attestation must already exist from an explicit owner-run synthetic
        setup. Titles or caller booleans never attest synthetic provenance.
        """
        await acquire_policy_fence_async(session)
        if (
            link is None
            or link.employee_id != str(principal.employee)
            or link.active is not True
            or link.issuer != self.issuer
            or link.installation_id != self.installation_id
            or link.organization_id != self.organization
        ):
            raise owner.BatisenalError("PILOT_IDENTITY_DENIED", 403)
        row = await owner.visible_case(session, principal, self.case_id, lock=True)
        if (row.organization, row.project) != (self.organization, self.project):
            raise owner.BatisenalError("PILOT_SCOPE_DENIED", 403)
        fixture = await self.fixture(session)
        identifier = str(
            uuid5(
                NAMESPACE_URL,
                owner.digest(
                    [
                        self.case_id,
                        link.employee_id,
                        link.profile_id,
                        self.issuer,
                        self.installation_id,
                    ]
                ),
            )
        )
        previous = (
            (await session.execute(select(pilots).where(pilots.c.id == identifier)))
            .mappings()
            .one_or_none()
        )
        if previous:
            await reauthorize()
            self.validate(previous)
            return previous  # Re-consent cannot renew or resurrect the pilot.
        now = self.now()
        if fixture.starts_at is None:
            changed = await session.execute(
                update(fixtures)
                .where(
                    fixtures.c.ticket_id == UUID(self.case_id),
                    fixtures.c.starts_at.is_(None),
                )
                .values(starts_at=now, expires_at=now + 3600)
            )
            if changed.rowcount != 1:
                raise owner.BatisenalError("PILOT_CONCURRENT_CONSENT", 409)
            starts_at, expires_at = now, now + 3600
        else:
            starts_at, expires_at = fixture.starts_at, fixture.expires_at
        if not starts_at <= now < expires_at:
            raise owner.BatisenalError("PILOT_EXPIRED_OR_REVOKED", 403)
        values = dict(
            id=identifier,
            ticket_id=UUID(self.case_id),
            employee=link.employee_id,
            profile_id=link.profile_id,
            issuer=self.issuer,
            installation_id=self.installation_id,
            organization=self.organization,
            project=self.project,
            agent_id=str(uuid4()),
            source_kind=self.source_kind,
            starts_at=starts_at,
            expires_at=expires_at,
            revoked=False,
        )
        await session.execute(insert(pilots).values(**values))
        await session.execute(
            insert(receipts).values(
                id=str(uuid4()),
                pilot_id=identifier,
                actor=link.employee_id,
                kind="consented",
                created_at=now,
            )
        )
        await reauthorize()
        if self.now() >= values["expires_at"]:
            raise owner.BatisenalError("PILOT_EXPIRED_OR_REVOKED", 403)
        await session.commit()
        return values

    def validate(self, row):
        if (
            row.revoked is not False
            or not row.starts_at <= self.now() < row.expires_at
            or row.expires_at > row.starts_at + 3600
        ):
            raise owner.BatisenalError("PILOT_EXPIRED_OR_REVOKED", 403)

    async def resolve(self, session, identity, *, lock=False):
        query = select(pilots).where(
            pilots.c.ticket_id == UUID(self.case_id),
            pilots.c.employee == identity.actor_id,
            pilots.c.profile_id == identity.profile_id,
            pilots.c.issuer == identity.issuer,
            pilots.c.installation_id == identity.installation_id,
            pilots.c.organization == identity.organization_id,
            pilots.c.project == self.project,
        )
        if lock:
            query = query.with_for_update()
        row = (await session.execute(query)).mappings().one_or_none()
        if row is None:
            raise owner.BatisenalError("PILOT_NOT_AUTHORIZED", 403)
        fixture = await self.fixture(session)
        if (row.starts_at, row.expires_at) != (fixture.starts_at, fixture.expires_at):
            raise owner.BatisenalError("PILOT_WINDOW_INVALID", 403)
        self.validate(row)
        return row

    async def revoke(self, session, employee):
        await acquire_policy_fence_async(session)
        rows = (
            (
                await session.execute(
                    select(pilots)
                    .where(
                        pilots.c.ticket_id == UUID(self.case_id),
                        pilots.c.employee == employee,
                        pilots.c.issuer == self.issuer,
                        pilots.c.installation_id == self.installation_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            if not row.revoked:
                await session.execute(
                    update(pilots).where(pilots.c.id == row.id).values(revoked=True)
                )
                await session.execute(
                    insert(receipts).values(
                        id=str(uuid4()),
                        pilot_id=row.id,
                        actor=employee,
                        kind="revoked",
                        created_at=self.now(),
                    )
                )
        await session.commit()
        return {"revoked": True}


async def private_review_only(session, ticket_id):
    """Owner attestation keeps the pilot private even after unmount/restart.

    Older installations without this optional migration retain ordinary support.
    Database errors propagate; they never become permission to publish.
    """
    connection = await session.connection()
    installed = await connection.run_sync(
        lambda sync: inspect(sync).has_table(fixtures.name)
    )
    if not installed:
        return False
    return (
        await session.execute(
            select(fixtures.c.ticket_id).where(
                fixtures.c.ticket_id == UUID(str(ticket_id))
            )
        )
    ).first() is not None
