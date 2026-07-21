from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from .document_confirmation import (
    ActionRouterExecutor,
    AsyncActionRouterExecutor,
    DocumentConfirmationResult,
    confirm_document_action,
    confirm_document_action_async,
    stable_payload_hash,
)


@dataclass(frozen=True)
class DocumentConfirmationCommand:
    action: str
    proposed_action_id: str
    raw_text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DocumentConversationResult:
    command: Dict[str, Any]
    confirmed: bool
    canceled: bool
    executed: bool
    status: str
    blocked_reason: str
    message: str
    confirmation: Optional[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


CONFIRM_RE = re.compile(
    r"^\s*(?:confirmar\s+accion|confirmar\s+acción|confirm\s+action)\s+([A-Za-z0-9_-]+)\s*$",
    re.IGNORECASE,
)
CANCEL_RE = re.compile(
    r"^\s*(?:cancelar\s+accion|cancelar\s+acción|cancel\s+action)\s+([A-Za-z0-9_-]+)\s*$",
    re.IGNORECASE,
)
DOCUMENT_INTAKE_MARKER = "DOCUMENT_INTAKE_RESULT JSON:"


def parse_document_confirmation_command(
    text: str,
) -> Optional[DocumentConfirmationCommand]:
    raw = text or ""
    match = CONFIRM_RE.match(raw)
    if match:
        return DocumentConfirmationCommand(
            action="confirm",
            proposed_action_id=match.group(1),
            raw_text=raw,
        )
    match = CANCEL_RE.match(raw)
    if match:
        return DocumentConfirmationCommand(
            action="cancel",
            proposed_action_id=match.group(1),
            raw_text=raw,
        )
    return None


def extract_document_intake_result_from_text(text: str) -> Optional[Dict[str, Any]]:
    raw = text or ""
    marker_index = raw.find(DOCUMENT_INTAKE_MARKER)
    if marker_index < 0:
        return None
    payload_start = marker_index + len(DOCUMENT_INTAKE_MARKER)
    remainder = raw[payload_start:].lstrip()
    if not remainder:
        return None
    first_line = remainder.splitlines()[0].strip()
    if not first_line:
        return None
    try:
        parsed = json.loads(first_line)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _actions_by_id(intake_result: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    actions = intake_result.get("proposed_actions") or []
    indexed: Dict[str, Mapping[str, Any]] = {}
    if not isinstance(actions, Sequence):
        return indexed
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        action_id = str(
            action.get("proposed_action_id") or action.get("action_id") or ""
        )
        if action_id:
            indexed[action_id] = action
    return indexed


def render_document_intake_for_conversation(intake_result: Mapping[str, Any]) -> str:
    doc_type = str(intake_result.get("detected_document_type") or "unknown_or_generic")
    summary = str(intake_result.get("summary") or "Documento recibido.")
    missing = [
        str(item) for item in (intake_result.get("missing_fields") or []) if item
    ]
    questions = [
        str(item) for item in (intake_result.get("questions_for_user") or []) if item
    ]
    lines = [
        f"Documento detectado: {doc_type}",
        f"Resumen: {summary}",
    ]
    if doc_type == "expense_receipt":
        return (
            f"Recibí el comprobante. {summary}\n\n"
            "Antes de preparar el borrador, indica si corresponde a un gasto "
            "personal/reembolso o a un pago a tercero. No registré cambios."
        )
    if missing:
        lines.append(f"Faltantes: {', '.join(missing)}")
    if questions:
        lines.append(f"Preguntas: {' '.join(questions)}")
    actions = _actions_by_id(intake_result)
    if actions:
        lines.append("Acciones propuestas:")
        for action_id, action in actions.items():
            title = str(action.get("title") or "Accion propuesta")
            prompt = str(action.get("confirmation_prompt") or "")
            lines.append(f"- {title}")
            lines.append(f"  proposed_action_id: {action_id}")
            if prompt:
                lines.append(f"  confirmacion: {prompt}")
            lines.append(f"  comando: CONFIRMAR accion {action_id}")
            lines.append(f"  cancelar: cancelar accion {action_id}")
    else:
        lines.append("No hay acciones ejecutables propuestas para este documento.")
    return "\n".join(lines)


def _conversation_result(
    *,
    command: DocumentConfirmationCommand,
    confirmed: bool,
    canceled: bool,
    executed: bool,
    status: str,
    blocked_reason: str,
    message: str,
    confirmation: Optional[DocumentConfirmationResult] = None,
) -> DocumentConversationResult:
    return DocumentConversationResult(
        command=command.to_dict(),
        confirmed=confirmed,
        canceled=canceled,
        executed=executed,
        status=status,
        blocked_reason=blocked_reason,
        message=message,
        confirmation=confirmation.to_dict() if confirmation else None,
    )


def handle_document_confirmation_command(
    *,
    text: str,
    intake_result: Mapping[str, Any],
    supported_actions: Sequence[str] | None,
    writes_enabled: bool = False,
    action_router_executor: Optional[ActionRouterExecutor] = None,
) -> DocumentConversationResult:
    command = parse_document_confirmation_command(text)
    if command is None:
        empty = DocumentConfirmationCommand(
            action="none", proposed_action_id="", raw_text=text
        )
        return _conversation_result(
            command=empty,
            confirmed=False,
            canceled=False,
            executed=False,
            status="needs_clarification",
            blocked_reason="not_a_document_confirmation_command",
            message="No se detecto un comando de confirmacion documental.",
        )

    actions = _actions_by_id(intake_result)
    proposed_action = actions.get(command.proposed_action_id)
    if proposed_action is None:
        return _conversation_result(
            command=command,
            confirmed=command.action == "confirm",
            canceled=command.action == "cancel",
            executed=False,
            status="rejected",
            blocked_reason="unknown_proposed_action_id",
            message="No encontre una accion propuesta con ese identificador.",
        )

    if command.action == "cancel":
        return _conversation_result(
            command=command,
            confirmed=False,
            canceled=True,
            executed=False,
            status="canceled",
            blocked_reason="",
            message="Accion documental cancelada. No se ejecuto ningun cambio.",
        )

    missing = [
        str(item) for item in (intake_result.get("missing_fields") or []) if item
    ]
    if missing:
        return _conversation_result(
            command=command,
            confirmed=True,
            canceled=False,
            executed=False,
            status="needs_clarification",
            blocked_reason="missing_required_fields",
            message=f"Faltan datos antes de confirmar: {', '.join(missing)}.",
        )

    payload = proposed_action.get("payload_preview") or {}
    if not isinstance(payload, Mapping):
        payload = {}
    confirmation = confirm_document_action(
        intake_id=str(intake_result.get("intake_id") or ""),
        proposed_action=proposed_action,
        confirmation_text=text,
        expected_payload_hash=stable_payload_hash(payload),
        supported_actions=supported_actions,
        writes_enabled=writes_enabled,
        action_router_executor=action_router_executor,
    )
    if confirmation.executed:
        message = (
            f"Acción confirmada y procesada: {confirmation.execution_result_summary}"
        )
    elif confirmation.blocked_reason == "writes_disabled":
        message = "Accion confirmada, pero no se ejecuto ningun write porque las escrituras estan deshabilitadas."
    else:
        message = confirmation.execution_result_summary
    return _conversation_result(
        command=command,
        confirmed=confirmation.confirmed,
        canceled=False,
        executed=confirmation.executed,
        status=confirmation.status,
        blocked_reason=confirmation.blocked_reason,
        message=message,
        confirmation=confirmation,
    )


async def handle_document_confirmation_command_async(
    *,
    text: str,
    intake_result: Mapping[str, Any],
    supported_actions: Sequence[str] | None,
    writes_enabled: bool = False,
    action_router_executor: Optional[AsyncActionRouterExecutor] = None,
) -> DocumentConversationResult:
    command = parse_document_confirmation_command(text)
    if command is None:
        empty = DocumentConfirmationCommand(
            action="none", proposed_action_id="", raw_text=text
        )
        return _conversation_result(
            command=empty,
            confirmed=False,
            canceled=False,
            executed=False,
            status="needs_clarification",
            blocked_reason="not_a_document_confirmation_command",
            message="No se detecto un comando de confirmacion documental.",
        )

    actions = _actions_by_id(intake_result)
    proposed_action = actions.get(command.proposed_action_id)
    if proposed_action is None:
        return _conversation_result(
            command=command,
            confirmed=command.action == "confirm",
            canceled=command.action == "cancel",
            executed=False,
            status="rejected",
            blocked_reason="unknown_proposed_action_id",
            message="No encontre una accion propuesta con ese identificador.",
        )

    if command.action == "cancel":
        return _conversation_result(
            command=command,
            confirmed=False,
            canceled=True,
            executed=False,
            status="canceled",
            blocked_reason="",
            message="Accion documental cancelada. No se ejecuto ningun cambio.",
        )

    missing = [
        str(item) for item in (intake_result.get("missing_fields") or []) if item
    ]
    if missing:
        return _conversation_result(
            command=command,
            confirmed=True,
            canceled=False,
            executed=False,
            status="needs_clarification",
            blocked_reason="missing_required_fields",
            message=f"Faltan datos antes de confirmar: {', '.join(missing)}.",
        )

    payload = proposed_action.get("payload_preview") or {}
    if not isinstance(payload, Mapping):
        payload = {}
    confirmation = await confirm_document_action_async(
        intake_id=str(intake_result.get("intake_id") or ""),
        proposed_action=proposed_action,
        confirmation_text=text,
        expected_payload_hash=stable_payload_hash(payload),
        supported_actions=supported_actions,
        writes_enabled=writes_enabled,
        action_router_executor=action_router_executor,
    )
    if confirmation.executed:
        message = (
            f"Acción confirmada y procesada: {confirmation.execution_result_summary}"
        )
    elif confirmation.blocked_reason == "writes_disabled":
        message = "Accion confirmada, pero no se ejecuto ningun write porque las escrituras estan deshabilitadas."
    else:
        message = confirmation.execution_result_summary
    return _conversation_result(
        command=command,
        confirmed=confirmation.confirmed,
        canceled=False,
        executed=confirmation.executed,
        status=confirmation.status,
        blocked_reason=confirmation.blocked_reason,
        message=message,
        confirmation=confirmation,
    )
