"""The only v0.1 Agent Action definitions, deliberately disabled."""

from __future__ import annotations

from typing import Dict, Optional

from .contracts import ActionDefinition


ACTION_NOT_REGISTERED = "ACTION_NOT_REGISTERED"
CANONICAL_SCOPE_UNPROVEN = "CANONICAL_SCOPE_UNPROVEN"


_ACTIONS: Dict[str, ActionDefinition] = {
    "expense.get_status": ActionDefinition(
        action_id="expense.get_status",
        action_version="v0.1",
        action_type="query",
        domain="gastos",
        canonical_handler="expense.full_workflow_snapshot",
        enabled=False,
        disabled_reason=CANONICAL_SCOPE_UNPROVEN,
        required_inputs=("expense_id",),
        required_scope_binding=(
            "expense visibility resolved from trusted principal"
        ),
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
        required_inputs=("tournament_id", "edition_year"),
        required_scope_binding=(
            "budget version visibility resolved from trusted principal"
        ),
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
        required_inputs=("expense_id",),
        required_scope_binding=(
            "expense visibility resolved from trusted principal"
        ),
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
        required_inputs=(
            "monto_solicitado",
            "proveedor_cliente_id",
            "torneo_id",
        ),
        required_scope_binding=(
            "actor sourced from session and authorized for target tournament"
        ),
        verifier="draft document state verifier",
    ),
}


def get_action(action_id: str) -> Optional[ActionDefinition]:
    return _ACTIONS.get((action_id or "").strip())


def registered_actions() -> tuple[ActionDefinition, ...]:
    return tuple(_ACTIONS[key] for key in sorted(_ACTIONS))
