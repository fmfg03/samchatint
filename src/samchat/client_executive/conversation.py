"""Bounded factual Sam turns using the exact Direction snapshot.

The canonical assistant models and renderer are reused. No model/tool dispatcher
is invoked here: causal analysis, recommendations and scenarios belong to #432.
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select

from samchat.assistant.executive_answer_renderer import render_executive_tool_result

from .home import SCHEMA, format_money

TOKEN_TTL_SECONDS = 900
TOKEN_SALT = "samchat.direction.context.v1"


class ContextError(ValueError):
    """Invalid, expired, foreign or revoked executive context."""


def _signer() -> URLSafeTimedSerializer:
    secret = (os.getenv("SESSION_SECRET_KEY") or "").strip()
    if not secret:
        raise ContextError("La firma del contexto no está configurada.")
    return URLSafeTimedSerializer(secret, salt=TOKEN_SALT)


def sign_context(snapshot: dict, actor: str) -> str:
    """Keep factual values server-authored and bind every context to its actor."""
    return _signer().dumps({"actor": actor, "snapshot": snapshot})


def load_context(token: str, actor: str) -> dict:
    """No client-provided money, filters or IDs are trusted outside this receipt."""
    try:
        payload = _signer().loads(token, max_age=TOKEN_TTL_SECONDS)
    except (BadSignature, SignatureExpired) as exc:
        raise ContextError(
            "El contexto expiró o no es válido; actualiza el tablero."
        ) from exc
    if (
        payload.get("actor") != actor
        or (payload.get("snapshot") or {}).get("schema") != SCHEMA
    ):
        raise ContextError("El contexto no pertenece a esta identidad.")
    return payload["snapshot"]


def answer_snapshot(snapshot: dict, metric_id: str, question: str) -> dict:
    """Answer definition/source/comparison questions without asserting causality."""
    metric = next((m for m in snapshot["indicators"] if m["id"] == metric_id), None)
    if metric is None:
        raise ContextError("Selecciona un indicador de este tablero.")
    text = unicodedata.normalize("NFKD", question.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    known_question = any(
        word in text
        for word in (
            "explica",
            "esto",
            "fuente",
            "significa",
            "compara",
            "definicion",
            "cuanto",
            "cifra",
            "dato",
            "monto",
            "evidencia",
            "corte",
        )
    )
    if re.search(
        r"\b(paga|pagar|aprueba|aprobar|elimina|eliminar|modifica|modificar|"
        r"simula|simular|escenarios?|alternativas?|recomienda|recomendar)\b",
        text,
    ):
        known_question = False
    facts = []
    if known_question:
        coverage = metric["coverage"]
        facts = [
            f"{metric['label']}: {format_money(metric['value'])}.",
            f"{metric['definition']}",
            f"Periodo: {metric['period']}. Corte de consulta: {metric['as_of']}.",
            f"Cobertura: {coverage['covered']} de {coverage['total']} torneos. Estado: {metric['status']}.",
        ]
        if "compara" in text:
            facts.extend(
                f"{r['name']}: {format_money(r['values'].get(metric_id))}."
                for r in snapshot["tournaments"]
            )
    missing = list(metric["gaps"])
    if metric["status"] == "partial":
        missing.append(
            "La cifra es un subtotal de fuentes cubiertas, no el total de toda la cartera."
        )
    if known_question:
        missing.append(
            "La definición y el agregado no prueban la causa de una desviación; falta investigar el detalle."
        )
        body = "Hechos\n" + "\n".join(facts) + "\n\nBrechas\n" + "\n".join(missing)
    else:
        body = (
            "Esta consulta contextual responde cifras, definiciones, cobertura y comparaciones del tablero. "
            "La pregunta requiere investigación o una capacidad fuera de este primer corte. "
            "No ejecuté acciones ni generé un escenario.\n\n"
            f"Indicador seleccionado: {metric['label']}. Corte: {snapshot['as_of']}."
        )
    body += "\n\nHipótesis: ninguna formulada.\nOpiniones: ninguna emitida."
    rendered = render_executive_tool_result(
        "direction.executive_snapshot",
        {
            "conversation_answer": {"rendered_text": body},
        },
    )
    return {
        "assistant_message": rendered,
        "facts": facts,
        "hypotheses": [],
        "opinions": [],
        "missing_evidence": missing,
        "metric_id": metric_id,
        "snapshot_id": snapshot["snapshot_id"],
        "metric": metric,
        "read_only": True,
        "supported": known_question,
    }


async def save_turn(
    session: Any,
    *,
    actor: str,
    snapshot: dict,
    metric_id: str,
    question: str,
    answer: dict,
    conversation_id: str | None,
) -> str:
    """Persist continuity in existing assistant tables, never in finance records."""
    from devnous.gastos.models import (
        AssistantConversation,
        AssistantMessage,
        AssistantRun,
    )

    actor_uuid = UUID(actor)
    if conversation_id:
        try:
            conversation_uuid = UUID(conversation_id)
        except ValueError as exc:
            raise ContextError("Conversación no válida.") from exc
        conversation = (
            await session.execute(
                select(AssistantConversation).where(
                    AssistantConversation.id == conversation_uuid,
                    AssistantConversation.empleado_id == actor_uuid,
                    AssistantConversation.archived.is_(False),
                )
            )
        ).scalar_one_or_none()
        if (
            conversation is None
            or (conversation.metadata_ or {}).get("direction_snapshot_id")
            != snapshot["snapshot_id"]
        ):
            raise ContextError("La conversación no corresponde a este contexto.")
    else:
        conversation = AssistantConversation(
            id=uuid4(),
            empleado_id=actor_uuid,
            title="Dirección · consulta contextual",
            metadata_={
                "module_key": "direction.executive_home",
                "direction_snapshot_id": snapshot["snapshot_id"],
                "direction_scope": snapshot["scope"],
                "direction_period": snapshot["period"],
                "direction_cut": snapshot["as_of"],
            },
        )
        session.add(conversation)
        await session.flush()
    session.add(
        AssistantMessage(conversation_id=conversation.id, role="user", content=question)
    )
    session.add(
        AssistantMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=answer["assistant_message"],
            tool_name="direction.executive_snapshot",
            tool_payload={
                "snapshot_id": snapshot["snapshot_id"],
                "metric": answer["metric"],
            },
        )
    )
    session.add(
        AssistantRun(
            id=uuid4(),
            conversation_id=conversation.id,
            empleado_id=actor_uuid,
            status="completed",
            model="deterministic:direction_context_v1",
            user_message=question,
            assistant_message=answer["assistant_message"],
            tool_trace=[
                {
                    "tool_name": "direction.executive_snapshot",
                    "snapshot_id": snapshot["snapshot_id"],
                    "metric_id": metric_id,
                    "read_only": True,
                }
            ],
            pending_tool_name=None,
            pending_tool_args=None,
        )
    )
    conversation.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return str(conversation.id)
