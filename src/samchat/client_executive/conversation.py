"""Bounded analytical Sam turns using the exact Direction snapshot.

The canonical assistant models and renderer are reused. No model/tool dispatcher
is invoked here. Interpretations and scenarios never mutate financial records.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select

from samchat.assistant.executive_answer_renderer import render_executive_tool_result

from .home import SCHEMA

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


def sign_analysis(analysis: dict, snapshot_id: str, actor: str) -> str:
    """Bind server-authored recommendations and assumptions to the exact cut."""
    return _signer().dumps(
        {
            "kind": "direction.analysis.v1",
            "actor": actor,
            "snapshot_id": snapshot_id,
            "analysis": analysis,
        }
    )


def load_analysis(token: str, snapshot_id: str, actor: str) -> dict:
    try:
        payload = _signer().loads(token, max_age=TOKEN_TTL_SECONDS)
    except (BadSignature, SignatureExpired) as exc:
        raise ContextError(
            "El análisis expiró; repite la consulta en este contexto."
        ) from exc
    if (
        payload.get("kind") != "direction.analysis.v1"
        or payload.get("actor") != actor
        or payload.get("snapshot_id") != snapshot_id
    ):
        raise ContextError("El análisis no pertenece a esta identidad y corte.")
    return payload["analysis"]


def answer_snapshot(
    snapshot: dict,
    metric_id: str,
    question: str,
    *,
    scenario: dict | None = None,
    previous: dict | None = None,
    report_cell: dict | None = None,
) -> dict:
    """Interpret and calculate using only the already signed financial facts."""
    from .analysis import analyze

    metric = next((m for m in snapshot["indicators"] if m["id"] == metric_id), None)
    if metric is None:
        raise ContextError("Selecciona un indicador de este tablero.")
    if report_cell is not None:
        if scenario:
            raise ContextError(
                "Selecciona un indicador del Resumen para calcular un escenario."
            )
        from .report_layouts import report_cell_evidence

        try:
            cell = report_cell_evidence(snapshot.get("reports") or {}, report_cell)
        except (ValueError, KeyError, TypeError) as exc:
            raise ContextError("La celda no pertenece a este reporte firmado.") from exc
        facts = [
            f"{cell['label']}: {cell['formatted_value']}.",
            f"Periodo: {cell['period']}. Corte: {snapshot['as_of']}.",
            cell["definition"],
            f"Fuente: {cell['source']}.",
        ]
        gaps = [cell["gap"]] if cell.get("gap") else []
        message = "\n".join(
            [
                "Hechos",
                *facts,
                "Límites",
                *(gaps or ["La variación no acredita una causa."]),
                "Para comparar torneos o calcular escenarios, selecciona un indicador del Resumen.",
            ]
        )
        return {
            "supported": True,
            "conclusion": facts[0],
            "facts": facts,
            "missing_evidence": gaps,
            "assistant_message": message,
            "metric_id": metric_id,
            "snapshot_id": snapshot["snapshot_id"],
            "metric": {**metric, **cell},
            "report_cell": report_cell,
            "scenario": None,
            "read_only": True,
        }
    answer = analyze(snapshot, metric, question, spec=scenario, previous=previous)
    answer["assistant_message"] = render_executive_tool_result(
        "direction.executive_snapshot",
        {"conversation_answer": {"rendered_text": answer["assistant_message"]}},
    )
    return {
        **answer,
        "metric_id": metric_id,
        "snapshot_id": snapshot["snapshot_id"],
        "metric": metric,
        "read_only": True,
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
                "analysis": {
                    k: answer.get(k)
                    for k in (
                        "scenario",
                        "conclusion",
                        "recommendations",
                        "missing_evidence",
                    )
                },
            },
        )
    )
    session.add(
        AssistantRun(
            id=uuid4(),
            conversation_id=conversation.id,
            empleado_id=actor_uuid,
            status="completed",
            model="deterministic:direction_context_v2",
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
