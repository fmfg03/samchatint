"""Private agent drafts and human publication, owned by Batiseñal support.

No provider, transport, credentials, scheduling or startup DDL. Draft content is
untrusted data; it never executes an action or grants access to a business object.
"""

from __future__ import annotations

import json
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
    UniqueConstraint,
    insert,
    select,
)

from ..models import SupportTicketComment
from . import batisenal_service as owner

# Separate metadata already owned by the explicit Batiseñal migrations.
drafts = Table(
    "support_batisenal_agent_drafts",
    owner.metadata,
    Column("id", String(36), primary_key=True),
    Column("ticket_id", ForeignKey(owner.cases.c.ticket_id), nullable=False),
    Column("agent_id", String(36), nullable=False),
    Column("employee", String(36), nullable=False),
    Column("grant_hash", String(64), nullable=False),
    Column("source_kind", String(24), nullable=False),
    Column("request_key", String(80), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("source_version", String(64), nullable=False),
    Column("body", Text, nullable=False),
    Column("response_json", Text, nullable=False),
    Column("attention", String(30), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("ticket_id", "agent_id", "request_key"),
)
publications = Table(
    "support_batisenal_agent_publications",
    owner.metadata,
    Column("draft_id", ForeignKey(drafts.c.id), primary_key=True),
    Column(
        "comment_id",
        ForeignKey(SupportTicketComment.__table__.c.id),
        nullable=False,
        unique=True,
    ),
    Column("reviewer", String(36), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def version(detail):
    """Opaque CAS version of the actual thread, excluding private draft state."""
    return owner.digest(
        [
            detail["id"],
            detail["status"],
            detail["context"],
            detail["title"],
            detail["description"],
            [
                [c["body"], c["author_role"], str(c["created_at"])]
                for c in detail["comments"]
            ],
            [[a["kind"], a["actor"], str(a["created_at"])] for a in detail["audit"]],
        ]
    )


def response_preview(payload, sources):
    """Validate structured advice and bind exact source hashes, without execution."""
    required = {
        "observation",
        "inference",
        "next_step",
        "verification",
        "missing_evidence",
        "source_ids",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise owner.BatisenalError("RESPONSE_FIELDS_INVALID")
    response = {
        k: owner.clean_text(payload[k], 1000) for k in required - {"source_ids"}
    }
    if any(not response[k] for k in ("observation", "next_step", "verification")):
        raise owner.BatisenalError("RESPONSE_INCOMPLETE")
    ids = payload["source_ids"]
    if (
        not isinstance(ids, list)
        or len(ids) > 4
        or any(not isinstance(i, str) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise owner.BatisenalError("SOURCES_INVALID")
    by_id = {s["id"]: s for s in sources}
    if any(i not in by_id for i in ids):
        raise owner.BatisenalError("SOURCE_NOT_AVAILABLE")
    response["sources"] = [
        {k: by_id[i][k] for k in ("id", "path", "sha256")} for i in ids
    ]
    # No provenance or missing operational evidence must remain an escalation.
    attention = (
        "escalated"
        if response["missing_evidence"] or not ids
        else "awaiting_verification"
    )
    body = "\n\n".join(
        [
            "Observación (según el contexto recibido): " + response["observation"],
            "Inferencia no verificada: "
            + (
                response["inference"]
                or "Ninguna; no hay diagnóstico causal verificado."
            ),
            "Puedes intentar: " + response["next_step"],
            "Cómo comprobarlo: " + response["verification"],
            "Evidencia pendiente: "
            + (
                response["missing_evidence"]
                or "Verificación del resultado por la persona solicitante."
            ),
            "Fuentes: "
            + (
                ", ".join(i + " sha256:" + by_id[i]["sha256"] for i in ids)
                or "Sin procedimiento verificado; escalar a soporte."
            ),
            "Estado de atención: "
            + attention
            + ". Esta respuesta no acredita resolución.",
        ]
    )
    return dict(response=response, body=body, attention=attention)


async def append_private_draft(session, **values):
    """Shared storage primitive; caller owns authorization and transaction.

    No commit here: existing save_draft retains its commit, while v0.1 can append
    snapshot/operation/receipt/audit atomically. Never callable as an MCP tool.
    """
    await session.execute(insert(drafts).values(**values))


async def save_draft(
    session,
    principal,
    binding,
    grant_hash,
    ticket_id,
    payload,
    preview,
    *,
    before_commit=None,
):
    """Called only after bridge reauthorization under the case row lock."""
    row = await owner.visible_case(session, principal, ticket_id, lock=True)
    key = owner.request_key(payload.get("request_key"))
    fingerprint = owner.digest(
        [str(row.ticket_id), payload["version"], preview, binding.source_kind]
    )
    if payload.get("preview_hash") != fingerprint:
        raise owner.BatisenalError("PREVIEW_MISMATCH", 409)
    identifier = str(
        uuid5(NAMESPACE_URL, f"batisenal-agent/{ticket_id}/{binding.agent_id}/{key}")
    )
    previous = (
        (await session.execute(select(drafts).where(drafts.c.id == identifier)))
        .mappings()
        .first()
    )
    if previous:
        if previous.request_hash != fingerprint:
            raise owner.BatisenalError("IDEMPOTENCY_CONFLICT", 409)
        if before_commit is not None:
            await before_commit()
        return {"draft_id": identifier, "state": "private_review", "published": False}
    detail = await owner.case_detail(session, principal, ticket_id)
    if version(detail) != payload["version"] or detail["status"] in {
        "resuelto",
        "cerrado",
    }:
        raise owner.BatisenalError("CASE_VERSION_STALE", 409)
    await append_private_draft(
        session,
        id=identifier,
        ticket_id=row.ticket_id,
        agent_id=str(binding.agent_id),
        employee=str(principal.employee),
        grant_hash=grant_hash,
        source_kind=binding.source_kind,
        request_key=key,
        request_hash=fingerprint,
        source_version=payload["version"],
        body=preview["body"],
        response_json=json.dumps(preview["response"]),
        attention=preview["attention"],
        created_at=owner.now(),
    )
    if before_commit is not None:
        await before_commit()
    await session.commit()
    return {"draft_id": identifier, "state": "private_review", "published": False}


def is_case_v1_draft(row):
    """Server-owned discriminator; old preview schema cannot inject this key."""
    value = json.loads(row.response_json)
    return isinstance(value, dict) and value.get("contract") == "case_v1"


async def private_drafts(session, principal, ticket_id):
    row = await owner.visible_case(session, principal, ticket_id)
    if not principal.permits(row.organization, row.project, "support"):
        raise owner.BatisenalError("NOT_FOUND", 404)
    rows = (
        (
            await session.execute(
                select(drafts)
                .outerjoin(publications, publications.c.draft_id == drafts.c.id)
                .where(
                    drafts.c.ticket_id == row.ticket_id,
                    publications.c.draft_id.is_(None),
                )
                .order_by(drafts.c.created_at.desc(), drafts.c.id)
                .limit(20)
            )
        )
        .mappings()
        .all()
    )
    current = version(await owner.case_detail(session, principal, ticket_id))
    return [
        dict(
            id=d.id,
            source_kind=d.source_kind,
            agent_id=d.agent_id,
            body=d.body,
            source_version=d.source_version,
            attention=d.attention,
            stale=d.source_version != current,
        )
        for d in rows
        if not is_case_v1_draft(d)
    ]


async def publish_draft(
    session, principal, ticket_id, draft_id, *, confirmed, reauthorize
):
    """Human-only web action: immutable exact draft, current thread, atomic reply."""
    row = await owner.visible_case(session, principal, ticket_id, lock=True)
    principal = await reauthorize()
    row = await owner.visible_case(session, principal, ticket_id, lock=True)
    if not principal.permits(row.organization, row.project, "support"):
        raise owner.BatisenalError("NOT_FOUND", 404)
    from .batisenal_pilot_service import private_review_only

    if await private_review_only(session, row.ticket_id):
        raise owner.BatisenalError("PILOT_PRIVATE_REVIEW_ONLY", 403)
    if confirmed is not True:
        raise owner.BatisenalError("PREVIEW_CONFIRMATION_REQUIRED")
    draft = (
        (
            await session.execute(
                select(drafts).where(
                    drafts.c.id == draft_id, drafts.c.ticket_id == row.ticket_id
                )
            )
        )
        .mappings()
        .first()
    )
    if not draft:
        raise owner.BatisenalError("NOT_FOUND", 404)
    if is_case_v1_draft(draft):
        raise owner.BatisenalError("PILOT_PRIVATE_REVIEW_ONLY", 403)
    existing = (
        (
            await session.execute(
                select(publications).where(publications.c.draft_id == draft.id)
            )
        )
        .mappings()
        .first()
    )
    if existing:
        return {
            "id": str(row.ticket_id),
            "published": True,
            "comment_id": str(existing.comment_id),
        }
    detail = await owner.case_detail(session, principal, ticket_id)
    if draft.source_version != version(detail) or detail["status"] in {
        "resuelto",
        "cerrado",
    }:
        raise owner.BatisenalError("CASE_VERSION_STALE", 409)
    comment_id = await owner.reply_case(
        session,
        principal,
        ticket_id,
        {
            "request_key": "agent-publication-" + draft.id,
            "confirmed": True,
            "body": draft.body,
            "status": "en_revision",
        },
        commit=False,
    )
    await session.execute(
        insert(publications).values(
            draft_id=draft.id,
            comment_id=UUID(comment_id),
            reviewer=str(principal.employee),
            created_at=owner.now(),
        )
    )
    await session.commit()
    return {"id": str(row.ticket_id), "published": True, "comment_id": comment_id}


activity = Table(
    "support_batisenal_agent_activity",
    owner.metadata,
    Column("id", String(36), primary_key=True),
    Column("ticket_id", ForeignKey(owner.cases.c.ticket_id), nullable=False),
    Column("agent_id", String(36), nullable=False),
    Column("employee", String(36), nullable=False),
    Column("grant_hash", String(64), nullable=False),
    Column("source_kind", String(24), nullable=False),
    Column("request_key", String(80), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("source_version", String(64), nullable=False),
    Column("kind", String(24), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("ticket_id", "agent_id", "request_key"),
)


async def record_activity(
    session, principal, binding, grant_hash, ticket_id, payload, *, before_commit=None
):
    row = await owner.visible_case(session, principal, ticket_id, lock=True)
    key = owner.request_key(payload.get("request_key"))
    kind = payload.get("kind")
    if kind not in {"started", "source_unavailable"}:
        raise owner.BatisenalError("ACTIVITY_INVALID")
    fingerprint = owner.digest([payload["version"], kind, binding.source_kind])
    identifier = str(
        uuid5(NAMESPACE_URL, f"batisenal-activity/{ticket_id}/{binding.agent_id}/{key}")
    )
    previous = (
        (await session.execute(select(activity).where(activity.c.id == identifier)))
        .mappings()
        .first()
    )
    if previous:
        if previous.request_hash != fingerprint:
            raise owner.BatisenalError("IDEMPOTENCY_CONFLICT", 409)
        if before_commit is not None:
            await before_commit()
        return {
            "receipt_id": identifier,
            "kind": kind,
            "source_kind": binding.source_kind,
        }
    detail = await owner.case_detail(session, principal, ticket_id)
    if version(detail) != payload["version"] or detail["status"] in {
        "resuelto",
        "cerrado",
    }:
        raise owner.BatisenalError("CASE_VERSION_STALE", 409)
    await session.execute(
        insert(activity).values(
            id=identifier,
            ticket_id=row.ticket_id,
            agent_id=str(binding.agent_id),
            employee=str(principal.employee),
            grant_hash=grant_hash,
            source_kind=binding.source_kind,
            request_key=key,
            request_hash=fingerprint,
            source_version=payload["version"],
            kind=kind,
            created_at=owner.now(),
        )
    )
    if before_commit is not None:
        await before_commit()
    await session.commit()
    return {"receipt_id": identifier, "kind": kind, "source_kind": binding.source_kind}


async def attention(session, row, detail):
    """Read-time status only. No job or timer; ETA is disabled by default."""
    import os

    try:
        threshold = int(os.getenv("BATISENAL_DELAY_SECONDS", "900"))
        estimate = int(os.getenv("BATISENAL_ESTIMATE_MINUTES", "0"))
        if not 60 <= threshold <= 86400 or not 0 <= estimate <= 1440:
            raise ValueError()
    except ValueError as exc:
        raise owner.BatisenalError("CONFIGURATION_INVALID", 503) from exc
    revision = version(detail)
    current = (
        (
            await session.execute(
                select(activity)
                .where(
                    activity.c.ticket_id == row.ticket_id,
                    activity.c.source_version == revision,
                )
                .order_by(activity.c.created_at.desc(), activity.c.id.desc())
                .limit(1)
            )
        )
        .mappings()
        .first()
    )
    draft = await session.scalar(
        select(drafts.c.id)
        .where(drafts.c.ticket_id == row.ticket_id, drafts.c.source_version == revision)
        .limit(1)
    )
    state = "queued"
    message = (
        "Consulta recibida y en cola para revisión con tu contexto. Puedes continuar;"
        " la respuesta quedará en este caso."
    )
    since = row.updated_at
    if detail["comments"] and detail["comments"][-1]["author_role"] != "requester":
        state, message = (
            "response_available",
            (
                "Hay una respuesta disponible en este caso. Comprueba el resultado"
                " antes de darlo por resuelto."
            ),
        )
    elif draft:
        state, message = (
            "private_review",
            (
                "Hay una propuesta en revisión por soporte; todavía no es una respuesta"
                " publicada."
            ),
        )
    elif current:
        since = current.created_at
        if current.kind == "source_unavailable":
            state, message = (
                "attention_error",
                (
                    "La consulta encontró una fuente no disponible. Conservamos tu"
                    " contexto para seguimiento de soporte."
                ),
            )
        elif current.source_kind == "ai_agent":
            state, message = (
                "consultation_started",
                (
                    "Chatty inició la consulta del caso. Puedes continuar; verás aquí"
                    " la respuesta cuando esté revisada y publicada."
                ),
            )
        else:
            state, message = (
                "synthetic_started",
                (
                    "Prueba sintética iniciada. No hay una consulta real a Chatty"
                    " acreditada."
                ),
            )
    elapsed = (owner.now() - since.replace(tzinfo=owner.timezone.utc)).total_seconds()
    delayed = (
        state not in {"response_available", "attention_error"} and elapsed >= threshold
    )
    if delayed:
        message += (
            " La espera superó el umbral de seguimiento configurado; aún no hay"
            " respuesta publicada ni plazo garantizado."
        )
    return {
        "state": state,
        "message": message,
        "delayed": delayed,
        "estimated_minutes": estimate or None,
        "estimate_is_guarantee": False,
    }
