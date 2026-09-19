"""The only v0.1 Agent Action definitions, deliberately disabled."""

from __future__ import annotations

from typing import Dict, Optional

from .contracts import (
    ActionDefinition,
    ActionInputSchema,
    ActionPolicy,
    ActionPrecondition,
    InputFieldSchema,
)


ACTION_NOT_REGISTERED = "ACTION_NOT_REGISTERED"
CANONICAL_SCOPE_UNPROVEN = "CANONICAL_SCOPE_UNPROVEN"


def _preconditions() -> tuple[ActionPrecondition, ...]:
    return (
        ActionPrecondition(
            code="trusted_principal",
            description=(
                "Actor and tenant come from authenticated server state."
            ),
            enforced_by="agent_action_service",
        ),
        ActionPrecondition(
            code="correlation_id",
            description=(
                "Every attempt carries a trace correlation identifier."
            ),
            enforced_by="agent_action_service",
        ),
        ActionPrecondition(
            code="payload_identity_free",
            description=(
                "Caller payload contains no identity or authority attributes."
            ),
            enforced_by="agent_action_service",
        ),
        ActionPrecondition(
            code="canonical_scope_bound",
            description=(
                "Row and tenant visibility are proven by the canonical domain."
            ),
            enforced_by="domain_owner",
        ),
    )


def _draft_preconditions() -> tuple[ActionPrecondition, ...]:
    return _preconditions() + (
        ActionPrecondition(
            code="idempotency_key",
            description="Draft requests include an idempotency key.",
            enforced_by="agent_action_service",
        ),
    )


_ACTIONS: Dict[str, ActionDefinition] = {
    "expense.get_status": ActionDefinition(
        action_id="expense.get_status",
        action_version="v0.1",
        action_type="query",
        domain="gastos",
        canonical_handler="expense.full_workflow_snapshot",
        enabled=False,
        disabled_reason=CANONICAL_SCOPE_UNPROVEN,
        input_schema=ActionInputSchema(
            fields=(InputFieldSchema("expense_id", "string"),),
            allow_additional_fields=False,
        ),
        policy=ActionPolicy("samchat.expense.read", "v0.1"),
        preconditions=_preconditions(),
        verifier="expense status source verifier",
    ),
    "budget.get_availability": ActionDefinition(
        action_id="budget.get_availability",
        action_version="v0.1",
        action_type="query",
        domain="presupuestos",
        canonical_handler="budgets.snapshot",
        enabled=False,
        disabled_reason=CANONICAL_SCOPE_UNPROVEN,
        input_schema=ActionInputSchema(
            fields=(
                InputFieldSchema("tournament_id", "string"),
                InputFieldSchema("edition_year", "integer"),
            ),
            allow_additional_fields=False,
        ),
        policy=ActionPolicy("samchat.budget.read", "v0.1"),
        preconditions=_preconditions(),
        verifier="budget availability source verifier",
    ),
    "expense.diagnose_blocker": ActionDefinition(
        action_id="expense.diagnose_blocker",
        action_version="v0.1",
        action_type="diagnostic",
        domain="gastos",
        canonical_handler="expense.full_workflow_snapshot",
        enabled=False,
        disabled_reason=CANONICAL_SCOPE_UNPROVEN,
        input_schema=ActionInputSchema(
            fields=(InputFieldSchema("expense_id", "string"),),
            allow_additional_fields=False,
        ),
        policy=ActionPolicy("samchat.expense.diagnostic", "v0.1"),
        preconditions=_preconditions(),
        verifier="expense blocker source verifier",
    ),
    "transfer.create_draft": ActionDefinition(
        action_id="transfer.create_draft",
        action_version="v0.1",
        action_type="draft",
        domain="gastos",
        canonical_handler="expenses.create_solicitud_terceros",
        enabled=False,
        disabled_reason=CANONICAL_SCOPE_UNPROVEN,
        input_schema=ActionInputSchema(
            fields=(
                InputFieldSchema("monto_solicitado", "decimal"),
                InputFieldSchema("proveedor_cliente_id", "string"),
                InputFieldSchema("torneo_id", "string"),
            ),
            allow_additional_fields=False,
        ),
        policy=ActionPolicy("samchat.transfer.draft", "v0.1"),
        preconditions=_draft_preconditions(),
        verifier="draft document state verifier",
    ),
}


def get_action(action_id: str) -> Optional[ActionDefinition]:
    return _ACTIONS.get((action_id or "").strip())


def registered_actions() -> tuple[ActionDefinition, ...]:
    return tuple(_ACTIONS[key] for key in sorted(_ACTIONS))
