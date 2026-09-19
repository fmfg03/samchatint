"""Fail-closed gate; disabled definitions cannot reach canonical handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from .contracts import (
    ActionReceipt,
    ActionRequest,
    ResolvedPrincipal,
    find_untrusted_identity_paths,
    input_schema_is_valid,
)
from .receipts import ActionReceiptStore
from .registry import ACTION_NOT_REGISTERED, get_action


INPUT_SCHEMA_INVALID = "INPUT_SCHEMA_INVALID"
PRECONDITION_UNSATISFIED = "PRECONDITION_UNSATISFIED"
TRUSTED_PRINCIPAL = "trusted_principal"
CORRELATION_ID = "correlation_id"
PAYLOAD_IDENTITY_FREE = "payload_identity_free"
IDEMPOTENCY_KEY = "idempotency_key"


@dataclass(frozen=True)
class ActionGateResult:
    receipt: ActionReceipt
    replayed: bool = False


class CanonicalActionDispatcher(Protocol):
    """Future canonical dispatcher, unreachable while v0.1 is disabled."""

    def dispatch(self, definition, request, principal) -> object:
        """Dispatch an enabled action only after its perimeter checks pass."""


class AgentActionService:
    """Contract/policy/receipt perimeter; v0.1 dispatches nothing."""

    def __init__(
        self,
        receipt_store: ActionReceiptStore,
        dispatcher: Optional[CanonicalActionDispatcher] = None,
    ) -> None:
        self._receipt_store = receipt_store
        self._dispatcher = dispatcher

    def evaluate(
        self,
        request: ActionRequest,
        *,
        principal: Optional[ResolvedPrincipal],
    ) -> ActionGateResult:
        definition = get_action(request.action_id)
        if definition is None:
            return self._record(
                ActionReceipt.blocked(
                    action_id=request.action_id,
                    action_version="unregistered",
                    principal=principal,
                    request=request,
                    reason=ACTION_NOT_REGISTERED,
                    invoked_domain=None,
                )
            )

        if not principal or not principal.is_complete():
            return self._record_precondition_unsatisfied(
                definition, request, principal, TRUSTED_PRINCIPAL
            )

        if not (request.correlation_id or "").strip():
            return self._record_precondition_unsatisfied(
                definition, request, principal, CORRELATION_ID
            )

        if find_untrusted_identity_paths(request.payload):
            return self._record_precondition_unsatisfied(
                definition, request, principal, PAYLOAD_IDENTITY_FREE
            )

        if definition.requires_idempotency and not (
            request.idempotency_key or ""
        ).strip():
            return self._record_precondition_unsatisfied(
                definition, request, principal, IDEMPOTENCY_KEY
            )

        if not input_schema_is_valid(definition.input_schema, request.payload):
            return self._record_blocked(
                definition,
                request,
                principal,
                INPUT_SCHEMA_INVALID,
                error_code=INPUT_SCHEMA_INVALID,
            )

        if definition.requires_idempotency:
            prior = self._receipt_store.find_idempotent(
                tenant_id=principal.tenant_id,
                action_id=definition.action_id,
                action_version=definition.action_version,
                idempotency_key=str(request.idempotency_key),
            )
            if prior is not None:
                return ActionGateResult(receipt=prior, replayed=True)

        # This guard deliberately precedes every domain dispatcher.
        if not definition.enabled:
            return self._record_blocked(
                definition,
                request,
                principal,
                str(definition.disabled_reason),
            )

        raise RuntimeError(
            "enabled action dispatch is outside Agent Action API v0.1"
        )

    def _record_blocked(
        self,
        definition,
        request: ActionRequest,
        principal: Optional[ResolvedPrincipal],
        reason: str,
        error_code: Optional[str] = None,
    ) -> ActionGateResult:
        return self._record(
            ActionReceipt.blocked(
                action_id=definition.action_id,
                action_version=definition.action_version,
                principal=principal,
                request=request,
                reason=reason,
                invoked_domain=definition.domain,
                verifier=definition.verifier,
                policy=definition.policy,
                error_code=error_code,
            )
        )

    def _record_precondition_unsatisfied(
        self,
        definition,
        request: ActionRequest,
        principal: Optional[ResolvedPrincipal],
        precondition_code: str,
    ) -> ActionGateResult:
        return self._record_blocked(
            definition,
            request,
            principal,
            precondition_code,
            error_code=PRECONDITION_UNSATISFIED,
        )

    def _record(self, receipt: ActionReceipt) -> ActionGateResult:
        self._receipt_store.append(receipt)
        return ActionGateResult(receipt=receipt)
