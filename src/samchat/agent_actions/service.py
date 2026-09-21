"""Fail-closed gate; disabled definitions cannot reach canonical handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .contracts import (
    ActionReceipt,
    ActionRequest,
    ResolvedPrincipal,
    UNTRUSTED_IDENTITY_FIELDS,
    normalize_and_redact,
)
from .receipts import ActionReceiptStore
from .registry import ACTION_NOT_REGISTERED, get_action


IDENTITY_CONTEXT_MISSING = "IDENTITY_CONTEXT_MISSING"
CORRELATION_ID_REQUIRED = "CORRELATION_ID_REQUIRED"
IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
UNTRUSTED_IDENTITY_FIELD = "UNTRUSTED_IDENTITY_FIELD"


@dataclass(frozen=True)
class ActionGateResult:
    receipt: ActionReceipt
    replayed: bool = False


class AgentActionService:
    """Contract/policy/receipt perimeter; v0.1 dispatches nothing."""

    def __init__(self, receipt_store: ActionReceiptStore) -> None:
        self._receipt_store = receipt_store

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
            return self._record_blocked(
                definition, request, principal, IDENTITY_CONTEXT_MISSING
            )

        if not (request.correlation_id or "").strip():
            return self._record_blocked(
                definition, request, principal, CORRELATION_ID_REQUIRED
            )

        if set(request.payload).intersection(UNTRUSTED_IDENTITY_FIELDS):
            return self._record_blocked(
                definition, request, principal, UNTRUSTED_IDENTITY_FIELD
            )

        if definition.requires_idempotency and not (
            request.idempotency_key or ""
        ).strip():
            return self._record_blocked(
                definition, request, principal, IDEMPOTENCY_KEY_REQUIRED
            )

        if definition.requires_idempotency:
            prior = self._receipt_store.find_idempotent(
                tenant_id=principal.tenant_id,
                actor_id=principal.actor_id,
                action_id=definition.action_id,
                action_version=definition.action_version,
                idempotency_key=str(request.idempotency_key),
            )
            if prior is not None:
                if (
                    prior.actor_id == principal.actor_id
                    and prior.tenant_id == principal.tenant_id
                    and prior.normalized_redacted_inputs
                    == normalize_and_redact(request.payload)
                ):
                    return ActionGateResult(receipt=prior, replayed=True)
                return self._record_blocked(
                    definition,
                    ActionRequest(
                        action_id=request.action_id,
                        correlation_id=request.correlation_id,
                        payload=request.payload,
                    ),
                    principal,
                    IDEMPOTENCY_KEY_REUSED,
                )

        # The enabled guard deliberately precedes every domain dispatcher.
        return self._record_blocked(
            definition,
            request,
            principal,
            str(definition.disabled_reason),
        )

    def _record_blocked(
        self,
        definition,
        request: ActionRequest,
        principal: Optional[ResolvedPrincipal],
        reason: str,
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
            )
        )

    def _record(self, receipt: ActionReceipt) -> ActionGateResult:
        self._receipt_store.append(receipt)
        return ActionGateResult(receipt=receipt)
