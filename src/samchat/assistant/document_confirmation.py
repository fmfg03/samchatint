from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
)

WRITE_ACTIONS = {
    "accounting.assign_expense_accounting",
    "accounting.link_bank_to_expense",
    "accounting.post_expense_accounting",
    "expenses.create_manual_expense",
    "expenses.create_personal_receipt_workflow",
    "expenses.create_third_party_receipt_workflow",
    "expenses.create_solicitud_personal",
    "expenses.create_solicitud_terceros",
    "operations.create_expense_from_context",
    "operations.create_media_asset",
    "operations.create_solicitud_from_commitment",
    "operations.update_commitment",
    "operations.update_team_status",
    "operations.verify_player_document",
    "receipts.approve_document",
    "receipts.link_expense_to_cfdi",
    "receipts.register_document_payment",
    "receipts.register_document_reembolso",
    "receipts.reject_document",
    "receipts.request_cfdi",
    "receipts.send_document",
}


@dataclass(frozen=True)
class ProposedDocumentAction:
    action_id: str
    canonical_action: str
    title: str
    payload_preview: Dict[str, Any]
    requires_confirmation: bool
    write_blocked: bool
    risk_level: str
    confirmation_prompt: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DocumentConfirmationResult:
    confirmation_id: str
    intake_id: str
    proposed_action_id: str
    canonical_action: str
    confirmed: bool
    executed: bool
    status: str
    blocked_reason: str
    execution_result_summary: str
    safety: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


ActionRouterExecutor = Callable[[str, Dict[str, Any]], Mapping[str, Any]]
AsyncActionRouterExecutor = Callable[
    [str, Dict[str, Any]], Awaitable[Mapping[str, Any]]
]


def stable_payload_hash(payload_preview: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload_preview, ensure_ascii=False, sort_keys=True, default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stable_confirmation_id(
    *,
    intake_id: str,
    proposed_action_id: str,
    canonical_action: str,
    payload_hash: str,
    confirmation_text: str,
) -> str:
    payload = {
        "intake_id": intake_id,
        "proposed_action_id": proposed_action_id,
        "canonical_action": canonical_action,
        "payload_hash": payload_hash,
        "confirmation_text": (confirmation_text or "").strip().lower(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return f"docconf_{digest}"


def stable_action_id(
    *,
    intake_id: str,
    canonical_action: str,
    payload_preview: Mapping[str, Any],
) -> str:
    payload = {
        "intake_id": intake_id,
        "canonical_action": canonical_action,
        "payload_preview": payload_preview,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return f"docact_{digest}"


def requires_confirmation(canonical_action: str) -> bool:
    return canonical_action in WRITE_ACTIONS


def build_confirmation_prompt(
    *,
    canonical_action: str,
    title: str,
    payload_preview: Mapping[str, Any],
) -> str:
    if not requires_confirmation(canonical_action):
        return "Esta es una acción de lectura/preview; no modifica datos."
    preview_bits = []
    for key in sorted(payload_preview.keys())[:6]:
        value = payload_preview.get(key)
        if value in (None, "", [], {}):
            continue
        preview_bits.append(f"{key}={value}")
    preview = "; ".join(preview_bits) if preview_bits else "payload pendiente"
    return (
        f"Confirma explícitamente para ejecutar '{title}'. "
        f"Cambios propuestos: {preview}."
    )


def build_proposed_action(
    *,
    intake_id: str,
    canonical_action: str,
    title: str,
    payload_preview: Mapping[str, Any],
    risk_level: str,
    writes_enabled: bool = False,
) -> ProposedDocumentAction:
    needs_confirmation = requires_confirmation(canonical_action)
    write_blocked = needs_confirmation and not writes_enabled
    prompt = build_confirmation_prompt(
        canonical_action=canonical_action,
        title=title,
        payload_preview=payload_preview,
    )
    return ProposedDocumentAction(
        action_id=stable_action_id(
            intake_id=intake_id,
            canonical_action=canonical_action,
            payload_preview=payload_preview,
        ),
        canonical_action=canonical_action,
        title=title,
        payload_preview=dict(payload_preview),
        requires_confirmation=needs_confirmation,
        write_blocked=write_blocked,
        risk_level=risk_level,
        confirmation_prompt=prompt,
    )


def build_safety_status(
    *,
    proposed_actions: Iterable[ProposedDocumentAction],
    missing_fields: Iterable[str],
    blocked_reason: str = "",
) -> Dict[str, Any]:
    actions: List[ProposedDocumentAction] = list(proposed_actions)
    has_write = any(action.requires_confirmation for action in actions)
    missing = [field for field in missing_fields if field]
    reason = blocked_reason
    if not reason and has_write:
        reason = "write_requires_explicit_confirmation"
    if not reason and missing:
        reason = "missing_required_fields"
    return {
        "can_execute_without_confirmation": False,
        "requires_human_review": bool(has_write or missing or blocked_reason),
        "blocked_reason": reason,
    }


def _result(
    *,
    intake_id: str,
    proposed_action_id: str,
    canonical_action: str,
    confirmed: bool,
    executed: bool,
    status: str,
    blocked_reason: str,
    execution_result_summary: str,
    used_action_router: bool,
    requires_human_review: bool,
    confirmation_text: str,
    payload_hash: str,
) -> DocumentConfirmationResult:
    return DocumentConfirmationResult(
        confirmation_id=stable_confirmation_id(
            intake_id=intake_id,
            proposed_action_id=proposed_action_id,
            canonical_action=canonical_action,
            payload_hash=payload_hash,
            confirmation_text=confirmation_text,
        ),
        intake_id=intake_id,
        proposed_action_id=proposed_action_id,
        canonical_action=canonical_action,
        confirmed=confirmed,
        executed=executed,
        status=status,
        blocked_reason=blocked_reason,
        execution_result_summary=execution_result_summary,
        safety={
            "used_action_router": used_action_router,
            "direct_write_attempted": False,
            "requires_human_review": requires_human_review,
        },
    )


def confirm_document_action(
    *,
    intake_id: str,
    proposed_action: Mapping[str, Any],
    confirmation_text: str,
    expected_payload_hash: Optional[str] = None,
    supported_actions: Sequence[str] | None = None,
    writes_enabled: bool = False,
    action_router_executor: Optional[ActionRouterExecutor] = None,
) -> DocumentConfirmationResult:
    action_id = str(
        proposed_action.get("proposed_action_id")
        or proposed_action.get("action_id")
        or ""
    )
    canonical_action = str(proposed_action.get("canonical_action") or "")
    payload_preview = proposed_action.get("payload_preview") or {}
    if not isinstance(payload_preview, Mapping):
        payload_preview = {}
    payload_hash = stable_payload_hash(payload_preview)
    confirmed = bool((confirmation_text or "").strip())

    if not confirmed:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=False,
            executed=False,
            status="needs_clarification",
            blocked_reason="explicit_confirmation_required",
            execution_result_summary="Falta confirmacion explicita del usuario.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    if not action_id or not canonical_action:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="rejected",
            blocked_reason="invalid_proposed_action",
            execution_result_summary="La accion propuesta no contiene identificador o accion canonica.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    expected_action_id = stable_action_id(
        intake_id=intake_id,
        canonical_action=canonical_action,
        payload_preview=payload_preview,
    )
    if action_id != expected_action_id:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="rejected",
            blocked_reason="proposed_action_id_mismatch",
            execution_result_summary="La accion propuesta no coincide con el payload confirmado.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    if expected_payload_hash and expected_payload_hash != payload_hash:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="rejected",
            blocked_reason="payload_hash_mismatch",
            execution_result_summary="El payload de la accion cambio desde que fue propuesto.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    if supported_actions is not None and canonical_action not in supported_actions:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="rejected",
            blocked_reason="unsupported_canonical_action",
            execution_result_summary="action_router no soporta esta accion canonica.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    is_write = requires_confirmation(canonical_action)
    if is_write and not writes_enabled:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="blocked",
            blocked_reason="writes_disabled",
            execution_result_summary="La accion fue confirmada, pero las escrituras estan deshabilitadas.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    if action_router_executor is None:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="blocked",
            blocked_reason="action_router_executor_missing",
            execution_result_summary="No hay executor de action_router disponible para esta confirmacion.",
            used_action_router=False,
            requires_human_review=is_write,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    try:
        execution = action_router_executor(canonical_action, dict(payload_preview))
    except Exception as exc:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="rejected",
            blocked_reason="action_router_rejected",
            execution_result_summary=str(exc),
            used_action_router=True,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    summary = ""
    if isinstance(execution, Mapping):
        summary = str(
            execution.get("summary")
            or execution.get("message")
            or execution.get("status")
            or "action_router execution completed"
        )
    else:
        summary = "action_router execution completed"
    return _result(
        intake_id=intake_id,
        proposed_action_id=action_id,
        canonical_action=canonical_action,
        confirmed=True,
        executed=True,
        status="executed",
        blocked_reason="",
        execution_result_summary=summary,
        used_action_router=True,
        requires_human_review=False,
        confirmation_text=confirmation_text,
        payload_hash=payload_hash,
    )


async def confirm_document_action_async(
    *,
    intake_id: str,
    proposed_action: Mapping[str, Any],
    confirmation_text: str,
    expected_payload_hash: Optional[str] = None,
    supported_actions: Sequence[str] | None = None,
    writes_enabled: bool = False,
    action_router_executor: Optional[AsyncActionRouterExecutor] = None,
) -> DocumentConfirmationResult:
    action_id = str(
        proposed_action.get("proposed_action_id")
        or proposed_action.get("action_id")
        or ""
    )
    canonical_action = str(proposed_action.get("canonical_action") or "")
    payload_preview = proposed_action.get("payload_preview") or {}
    if not isinstance(payload_preview, Mapping):
        payload_preview = {}
    payload_hash = stable_payload_hash(payload_preview)
    confirmed = bool((confirmation_text or "").strip())

    if not confirmed:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=False,
            executed=False,
            status="needs_clarification",
            blocked_reason="explicit_confirmation_required",
            execution_result_summary="Falta confirmacion explicita del usuario.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    if not action_id or not canonical_action:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="rejected",
            blocked_reason="invalid_proposed_action",
            execution_result_summary="La accion propuesta no contiene identificador o accion canonica.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    expected_action_id = stable_action_id(
        intake_id=intake_id,
        canonical_action=canonical_action,
        payload_preview=payload_preview,
    )
    if action_id != expected_action_id:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="rejected",
            blocked_reason="proposed_action_id_mismatch",
            execution_result_summary="La accion propuesta no coincide con el payload confirmado.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    if expected_payload_hash and expected_payload_hash != payload_hash:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="rejected",
            blocked_reason="payload_hash_mismatch",
            execution_result_summary="El payload de la accion cambio desde que fue propuesto.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    if supported_actions is not None and canonical_action not in supported_actions:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="rejected",
            blocked_reason="unsupported_canonical_action",
            execution_result_summary="action_router no soporta esta accion canonica.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    is_write = requires_confirmation(canonical_action)
    if is_write and not writes_enabled:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="blocked",
            blocked_reason="writes_disabled",
            execution_result_summary="La accion fue confirmada, pero las escrituras estan deshabilitadas.",
            used_action_router=False,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    if action_router_executor is None:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="blocked",
            blocked_reason="action_router_executor_missing",
            execution_result_summary="No hay executor de action_router disponible para esta confirmacion.",
            used_action_router=False,
            requires_human_review=is_write,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    try:
        execution = await action_router_executor(
            canonical_action, dict(payload_preview)
        )
    except Exception as exc:
        return _result(
            intake_id=intake_id,
            proposed_action_id=action_id,
            canonical_action=canonical_action,
            confirmed=True,
            executed=False,
            status="rejected",
            blocked_reason="action_router_rejected",
            execution_result_summary=str(exc),
            used_action_router=True,
            requires_human_review=True,
            confirmation_text=confirmation_text,
            payload_hash=payload_hash,
        )

    summary = ""
    if isinstance(execution, Mapping):
        summary = str(
            execution.get("summary")
            or execution.get("message")
            or execution.get("status")
            or "action_router execution completed"
        )
    else:
        summary = "action_router execution completed"
    return _result(
        intake_id=intake_id,
        proposed_action_id=action_id,
        canonical_action=canonical_action,
        confirmed=True,
        executed=True,
        status="executed",
        blocked_reason="",
        execution_result_summary=summary,
        used_action_router=True,
        requires_human_review=False,
        confirmation_text=confirmation_text,
        payload_hash=payload_hash,
    )
