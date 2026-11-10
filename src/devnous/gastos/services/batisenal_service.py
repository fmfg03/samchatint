"""Scoped Batiseñal extension of the canonical support ticket store.

No schema creation, permission provisioning, background delivery or external IO.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from PIL import Image, UnidentifiedImageError
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SUPPORT_TICKET_STATUSES, SupportTicket, SupportTicketComment
from .support_ticket_service import validate_comment_body, validate_ticket_input

MARKER = "batisenal:private"
metadata = MetaData()
# Separate metadata deliberately excludes these tables from runtime create_all.
cases = Table(
    "support_batisenal_cases",
    metadata,
    Column("ticket_id", ForeignKey(SupportTicket.__table__.c.id), primary_key=True),
    Column("organization", String(64), nullable=False),
    Column("project", String(64), nullable=False),
    Column("actor", String(36), nullable=False),
    Column("request_key", String(80), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("context_json", Text, nullable=False),
    Column("image", LargeBinary),
    Column("image_hash", String(64)),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("actor", "organization", "project", "request_key"),
)
events = Table(
    "support_batisenal_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("ticket_id", ForeignKey(cases.c.ticket_id), nullable=False, index=True),
    Column("actor", String(36), nullable=False),
    Column("kind", String(24), nullable=False),
    Column("request_key", String(80), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("status", String(30), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("ticket_id", "actor", "request_key"),
)


class BatisenalError(ValueError):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class Policy:
    max_image_bytes: int = 2 * 1024 * 1024
    max_pixels: int = 4_000_000
    retention_days: int = 90

    @classmethod
    def environment(cls):
        try:
            policy = cls(
                *(
                    int(os.getenv(name, default))
                    for name, default in (
                        ("BATISENAL_MAX_IMAGE_BYTES", "2097152"),
                        ("BATISENAL_MAX_PIXELS", "4000000"),
                        ("BATISENAL_RETENTION_DAYS", "90"),
                    )
                )
            )
        except ValueError as exc:
            raise BatisenalError("CONFIGURATION_INVALID", 503) from exc
        if not (
            1024 <= policy.max_image_bytes <= 8 * 1024 * 1024
            and 1 <= policy.max_pixels <= 8_000_000
            and 1 <= policy.retention_days <= 3650
        ):
            raise BatisenalError("CONFIGURATION_INVALID", 503)
        return policy


@dataclass(frozen=True)
class Principal:
    employee: UUID
    # Exact (organization, project, capability) grants from existing profiles.
    grants: frozenset[tuple[str, str, str]]

    def permits(self, org, project, capability):
        if not isinstance(org, str) or not isinstance(project, str):
            return False
        return (org, project, capability) in self.grants


def principal_from_tokens(employee, tokens, *, can_request, can_support):
    if not getattr(employee, "activo", False) or getattr(
        employee, "impersonator_empleado_id", None
    ):
        raise BatisenalError("IDENTITY_NOT_ALLOWED", 403)
    grants = set()
    for token in tokens:
        match = re.fullmatch(
            r"batisenal:([a-z0-9_-]{1,64}):([a-z0-9_-]{1,64}):(request|support)", token
        )
        if match and (
            (match[3] == "request" and can_request)
            or (match[3] == "support" and can_support)
        ):
            grants.add((match[1], match[2], match[3]))
    if not grants:
        raise BatisenalError("SCOPE_NOT_CONFIGURED", 403)
    return Principal(UUID(str(employee.id)), frozenset(grants))


def clean_text(value, maximum=8000):
    if not isinstance(value, str) or len(value) > maximum:
        raise BatisenalError("TEXT_INVALID")
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value).strip()
    value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [OCULTO]", value)
    # Defensive common-secret removal; never a substitute for user review.
    value = re.sub(
        r"(?i)\b(password|contraseña|token|secret|api[_-]?key|"
        r"authorization)\s*[:=]\s*\S+",
        r"\1=[OCULTO]",
        value,
    )
    value = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{12,}|eyJ[A-Za-z0-9_-]+"
        r"\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b",
        "[OCULTO]",
        value,
    )
    # Avoid transmitting pasted URL query/fragment credentials.
    value = re.sub(r"(https?://[^\s?#]+)[?#][^\s]*", r"\1", value)
    return value


def normalize_image(encoded, policy):
    if encoded is None:
        return None
    if not isinstance(encoded, str) or len(encoded) > 4 * (
        (policy.max_image_bytes + 2) // 3
    ):
        raise BatisenalError("IMAGE_TOO_LARGE", 413)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise BatisenalError("IMAGE_INVALID") from exc
    if len(raw) > policy.max_image_bytes:
        raise BatisenalError("IMAGE_TOO_LARGE", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if (
                    image.format not in {"PNG", "JPEG"}
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise BatisenalError("IMAGE_TYPE_INVALID", 415)
                if image.width * image.height > policy.max_pixels:
                    raise BatisenalError("IMAGE_DIMENSIONS_INVALID", 413)
                image.load()
                # New pixels only: removes EXIF, ICC, comments, filename, and trailing
                # data.
                pixels = Image.new("RGB", image.size, "white")
                if image.mode == "RGBA":
                    pixels.paste(image, mask=image.getchannel("A"))
                else:
                    pixels.paste(image.convert("RGB"))
                output = io.BytesIO()
                pixels.save(output, format="PNG")
                result = output.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise BatisenalError("IMAGE_INVALID") from exc
    if len(result) > policy.max_image_bytes:
        raise BatisenalError("IMAGE_TOO_LARGE", 413)
    return result


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def request_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", value):
        raise BatisenalError("IDEMPOTENCY_KEY_INVALID")
    return value


def now():
    return datetime.now(timezone.utc)


def is_expired(value):
    return value.replace(tzinfo=timezone.utc) < now()


async def visible_case(session, principal, ticket_id, *, lock=False):
    try:
        ticket_id = UUID(str(ticket_id))
    except ValueError as exc:
        raise BatisenalError("NOT_FOUND", 404) from exc
    query = (
        select(cases, SupportTicket.__table__)
        .join(SupportTicket.__table__, cases.c.ticket_id == SupportTicket.id)
        .where(cases.c.ticket_id == ticket_id)
    )
    if lock:
        query = query.with_for_update()
    row = (await session.execute(query)).mappings().first()
    if row is None or not (
        principal.permits(row.organization, row.project, "support")
        or (
            str(principal.employee) == row.actor
            and principal.permits(row.organization, row.project, "request")
        )
    ):
        raise BatisenalError("NOT_FOUND", 404)
    if is_expired(row.expires_at):
        raise BatisenalError("RETENTION_EXPIRED", 410)
    return row


async def create_case(session, principal, payload, policy):
    if payload.get("confirmed") is not True:
        raise BatisenalError("PREVIEW_CONFIRMATION_REQUIRED")
    org, project = payload.get("organization"), payload.get("project")
    if not principal.permits(org, project, "request"):
        raise BatisenalError("SCOPE_DENIED", 403)
    key = request_key(payload.get("request_key"))
    title, description, _, _ = validate_ticket_input(
        asunto=clean_text(payload.get("title"), 200),
        descripcion=clean_text(payload.get("description")),
        categoria="bug",
        prioridad="normal",
    )
    context = payload.get("context", {})
    if not isinstance(context, dict) or set(context) - {
        "module",
        "action",
        "folio",
        "error",
    }:
        raise BatisenalError("CONTEXT_INVALID")
    context = {k: clean_text(v, 500) for k, v in context.items()}
    image = normalize_image(payload.get("image"), policy)
    image_hash = hashlib.sha256(image).hexdigest() if image else None
    fingerprint = digest([org, project, title, description, context, image_hash])
    identifier = uuid5(
        NAMESPACE_URL, f"batisenal/{principal.employee}/{org}/{project}/{key}"
    )
    previous = (
        (await session.execute(select(cases).where(cases.c.ticket_id == identifier)))
        .mappings()
        .first()
    )
    if previous:
        await visible_case(session, principal, identifier)
        if previous.request_hash != fingerprint:
            raise BatisenalError("IDEMPOTENCY_CONFLICT", 409)
        return str(identifier)
    try:
        await session.execute(
            insert(SupportTicket.__table__).values(
                id=identifier,
                requester_empleado_id=principal.employee,
                asunto=title,
                descripcion=description,
                categoria="bug",
                prioridad="normal",
                estado="abierto",
                page_url=MARKER,
                created_at=now(),
                updated_at=now(),
            )
        )
        await session.execute(
            insert(cases).values(
                ticket_id=identifier,
                organization=org,
                project=project,
                actor=str(principal.employee),
                request_key=key,
                request_hash=fingerprint,
                context_json=json.dumps(context),
                image=image,
                image_hash=image_hash,
                expires_at=now() + timedelta(days=policy.retention_days),
            )
        )
        await session.execute(
            insert(events).values(
                id=str(uuid4()),
                ticket_id=identifier,
                actor=str(principal.employee),
                kind="created",
                request_key=key,
                request_hash=fingerprint,
                status="abierto",
                created_at=now(),
            )
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await visible_case(session, principal, identifier)
        if existing.request_hash != fingerprint:
            raise BatisenalError("IDEMPOTENCY_CONFLICT", 409)
    return str(identifier)


async def list_cases(session: AsyncSession, principal: Principal) -> list[dict]:
    predicates = []
    for org, project, capability in principal.grants:
        predicate = and_(cases.c.organization == org, cases.c.project == project)
        if capability == "request":
            predicate = and_(predicate, cases.c.actor == str(principal.employee))
        predicates.append(predicate)
    if not predicates:
        return []
    rows = (
        (
            await session.execute(
                select(
                    cases.c.ticket_id,
                    cases.c.organization,
                    cases.c.project,
                    SupportTicket.asunto,
                    SupportTicket.estado,
                    SupportTicket.updated_at,
                )
                .join(SupportTicket.__table__, cases.c.ticket_id == SupportTicket.id)
                .where(or_(*predicates), cases.c.expires_at > now())
                .order_by(SupportTicket.__table__.c.updated_at.desc())
                .limit(200)
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def case_detail(session, principal, ticket_id):
    from .batisenal_agent_service import attention, drafts, publications

    row = await visible_case(session, principal, ticket_id)
    comments = (
        (
            await session.execute(
                select(
                    SupportTicketComment.body,
                    SupportTicketComment.author_role,
                    SupportTicketComment.created_at,
                    drafts.c.source_kind,
                    drafts.c.agent_id,
                    publications.c.reviewer,
                )
                .outerjoin(
                    publications, publications.c.comment_id == SupportTicketComment.id
                )
                .outerjoin(drafts, drafts.c.id == publications.c.draft_id)
                .where(SupportTicketComment.ticket_id == row.ticket_id)
                .order_by(SupportTicketComment.created_at, SupportTicketComment.id)
            )
        )
        .mappings()
        .all()
    )
    audit = (
        (
            await session.execute(
                select(
                    events.c.kind, events.c.actor, events.c.status, events.c.created_at
                )
                .where(events.c.ticket_id == row.ticket_id)
                .order_by(events.c.created_at, events.c.id)
            )
        )
        .mappings()
        .all()
    )
    result = dict(
        id=str(row.ticket_id),
        title=row.asunto,
        description=row.descripcion,
        organization=row.organization,
        project=row.project,
        status=row.estado,
        context=json.loads(row.context_json),
        has_image=bool(row.image_hash),
        expires_at=row.expires_at,
        comments=[dict(c) for c in comments],
        audit=[dict(e) for e in audit],
        can_support=principal.permits(row.organization, row.project, "support"),
    )

    result["attention"] = await attention(session, row, result)
    return result


async def reply_case(session, principal, ticket_id, payload, *, commit=True):
    """Canonical reply owner; trusted composition may own the outer transaction."""
    row = await visible_case(session, principal, ticket_id, lock=True)
    key = request_key(payload.get("request_key"))
    if payload.get("confirmed") is not True:
        raise BatisenalError("PREVIEW_CONFIRMATION_REQUIRED")
    body = validate_comment_body(clean_text(payload.get("body")))
    status = payload.get("status", row.estado)
    staff = principal.permits(row.organization, row.project, "support")
    from .batisenal_pilot_service import private_review_only

    if await private_review_only(session, row.ticket_id):
        raise BatisenalError("PILOT_PRIVATE_REVIEW_ONLY", 403)
    if not isinstance(status, str) or status not in SUPPORT_TICKET_STATUSES:
        raise BatisenalError("STATUS_INVALID")
    if not staff and status != row.estado:
        raise BatisenalError("STATUS_DENIED", 403)
    fingerprint = digest([body, payload.get("status")])
    previous = (
        (
            await session.execute(
                select(events).where(
                    events.c.ticket_id == row.ticket_id,
                    events.c.actor == str(principal.employee),
                    events.c.request_key == key,
                )
            )
        )
        .mappings()
        .first()
    )
    if previous:
        if previous.request_hash != fingerprint:
            raise BatisenalError("IDEMPOTENCY_CONFLICT", 409)
        return previous.id
    comment_id = uuid4()
    try:
        await session.execute(
            insert(SupportTicketComment.__table__).values(
                id=comment_id,
                ticket_id=row.ticket_id,
                author_empleado_id=principal.employee,
                author_role="staff" if staff else "requester",
                body=body,
                created_at=now(),
            )
        )
        await session.execute(
            update(SupportTicket.__table__)
            .where(SupportTicket.id == row.ticket_id)
            .values(
                estado=status,
                updated_at=now(),
                resolved_at=now() if status in {"resuelto", "cerrado"} else None,
            )
        )
        await session.execute(
            insert(events).values(
                id=str(comment_id),
                ticket_id=row.ticket_id,
                actor=str(principal.employee),
                kind="reply",
                request_key=key,
                request_hash=fingerprint,
                status=status,
                created_at=now(),
            )
        )
        if commit:
            await session.commit()
        return str(comment_id)
    except IntegrityError:
        if not commit:
            raise
        await session.rollback()
        # A concurrent identical retry must not duplicate a response or audit entry.
        await visible_case(session, principal, ticket_id)
        previous = (
            (
                await session.execute(
                    select(events).where(
                        events.c.ticket_id == row.ticket_id,
                        events.c.actor == str(principal.employee),
                        events.c.request_key == key,
                    )
                )
            )
            .mappings()
            .first()
        )
        if not previous or previous.request_hash != fingerprint:
            raise BatisenalError("IDEMPOTENCY_CONFLICT", 409)
        return previous.id
