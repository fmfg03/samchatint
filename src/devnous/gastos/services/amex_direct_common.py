"""Direct AMEX authority, exact identities and transactional receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select

from ..models import (
    AmexDirectAccountingCut,
    AmexDirectCompletionReceipt,
    AmexDirectEvent,
    AmexDirectItem,
    AmexStatementDirectContext,
    AmexStatementDirectState,
    AmexStatementImport,
    Empleado,
)
from .access_control_service import explicit_tool_decision
from .amex_direct_schema_service import (
    DirectSchemaUnavailable,
    require_amex_direct_schema,
)
from .amex_recognition_service import lock_recognition

__all__ = ["DirectSchemaUnavailable"]

FINANCE_ROLES = frozenset({"finanzas", "admin", "superadmin", "super_admin"})


class DirectAccessError(PermissionError):
    """Non-personal denial code; a rule cannot manufacture a source scope."""


class DirectConflict(ValueError):
    """Safe source/receipt conflict; callers roll back an unsuccessful write."""


@dataclass
class DirectResult:
    status: str
    reason: str | None = None
    statement: Any = None
    context: Any = None
    card: Any = None
    items: list[Any] = field(default_factory=list)
    cut: Any = None
    poliza: Any = None
    source_key: str | None = None
    source_version: int | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    _expenses: dict[Any, Any] = field(default_factory=dict, repr=False)
    _proofs: dict[Any, Any] = field(default_factory=dict, repr=False)
    _lines: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _liability: Any = field(default=None, repr=False)
    _sources: dict[Any, Any] = field(default_factory=dict, repr=False)
    _settlements: dict[Any, Any] = field(default_factory=dict, repr=False)
    _prior_proofs: dict[Any, Any] = field(default_factory=dict, repr=False)
    _completion: Any = field(default=None, repr=False)


def digest(value: Any) -> str:
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(
            value, sort_keys=True, default=str, separators=(",", ":")
        ).encode()
    )
    return hashlib.sha256(raw).hexdigest()


def money(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result.quantize(Decimal(".01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DirectConflict("invalid_amount") from exc


def normalize_rfc(value: Any) -> str:
    result = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}", result):
        raise DirectConflict("invalid_rfc")
    return result


def descriptor_key(value: str) -> str:
    return " ".join(str(value).split()).upper()


def configured_scopes() -> list[dict[str, Any]]:
    try:
        value = json.loads(os.environ.get("AMEX_DIRECT_AUTHORIZED_CONTEXTS", "[]"))
        if not isinstance(value, list):
            return []
        required = {
            "entity_code",
            "company_code",
            "receiver_rfc",
            "card_account_ids",
            "actor_scopes",
        }
        return [
            row for row in value if isinstance(row, dict) and required.issubset(row)
        ]
    except (ValueError, TypeError):
        return []


ACCOUNT_TREATMENT_GRANT_KEYS = frozenset(
    {
        "benefit_budget_line_ids",
        "benefit_budget_pairs",
        "partner_debtor_account_ids",
    }
)


def base_context_config(config: dict[str, Any]) -> dict[str, Any]:
    """Only additive treatment grants are independent from the fiscal context."""
    result = {k: v for k, v in config.items() if k not in ACCOUNT_TREATMENT_GRANT_KEYS}
    if isinstance(config.get("actor_scopes"), list):
        result["actor_scopes"] = [
            (
                {k: v for k, v in row.items() if k not in ACCOUNT_TREATMENT_GRANT_KEYS}
                if isinstance(row, dict)
                else row
            )
            for row in config["actor_scopes"]
        ]
    return result


def context_config_matches(recorded_hash: str, config: dict[str, Any]) -> bool:
    """Structural candidate only; load_statement also requires a trusted policy."""
    return recorded_hash in {digest(config), digest(base_context_config(config))}


def configuration_for(
    card_id: UUID, company_code: str | None = None, receiver_rfc: str | None = None
) -> dict[str, Any] | None:
    matches = [
        row
        for row in configured_scopes()
        if str(card_id) in row.get("card_account_ids", [])
        and (company_code is None or row["company_code"] == company_code)
        and (
            receiver_rfc is None
            or str(row["receiver_rfc"]).strip().upper() == receiver_rfc
        )
    ]
    return matches[0] if len(matches) == 1 else None


def actor_scope(config: dict[str, Any], actor_id: UUID) -> dict[str, Any] | None:
    matches = [
        scope
        for scope in config.get("actor_scopes", [])
        if scope.get("actor_id") == str(actor_id)
    ]
    return matches[0] if len(matches) == 1 else None


async def authorize(
    session: Any,
    actor_id: UUID,
    action: str,
    *,
    config: dict[str, Any] | None = None,
    finance: bool = False,
    budget: bool = False,
    locked: bool = False,
) -> Any:
    actor = await session.get(
        Empleado, actor_id, populate_existing=True, with_for_update=locked
    )
    if actor is None or not actor.activo:
        raise DirectAccessError("active_actor_required")
    read_decision = await explicit_tool_decision(session, actor, "amex_direct", "ver")
    edit_decision = (
        await explicit_tool_decision(session, actor, "amex_direct", "editar")
        if action not in {"ver", "fuente", "exportar"}
        else None
    )
    decision = await explicit_tool_decision(session, actor, "amex_direct", action)
    if read_decision is False or edit_decision is False:
        raise DirectAccessError("explicit_action_denied")
    if decision is False:
        raise DirectAccessError("explicit_action_denied")
    if finance and actor.rol not in FINANCE_ROLES:
        raise DirectAccessError("finance_actor_required")
    if config is not None:
        if digest(config) not in {digest(v) for v in configured_scopes()}:
            raise DirectAccessError("authorized_context_changed")
        if actor_scope(config, actor_id) is None:
            raise DirectAccessError("actor_scope_denied")
        if (
            budget
            and str(actor_id) not in config.get("budget_actor_ids", [])
            and decision is not True
        ):
            raise DirectAccessError("configured_budget_actor_required")
    if (
        not finance
        and not budget
        and actor.rol not in FINANCE_ROLES
        and decision is not True
    ):
        raise DirectAccessError("accounting_action_required")
    return actor


async def load_statement(
    session: Any,
    actor_id: UUID,
    import_id: UUID,
    action: str,
    *,
    locked: bool = False,
    finance: bool = False,
    budget: bool = False,
    requested_company_code: str | None = None,
    requested_receiver_rfc: str | None = None,
) -> tuple[Any, Any, Any, dict[str, Any]]:
    # Authorize identity/action before querying corporate source content.
    await authorize(
        session, actor_id, action, finance=finance, budget=budget, locked=locked
    )
    accessible = [
        row for row in configured_scopes() if actor_scope(row, actor_id) is not None
    ]
    if not accessible:
        raise DirectAccessError("actor_scope_denied")
    card_ids = [UUID(value) for row in accessible for value in row["card_account_ids"]]
    await require_amex_direct_schema(session)
    query = (
        select(AmexStatementImport)
        .where(
            AmexStatementImport.id == import_id,
            AmexStatementImport.card_account_id.in_(card_ids),
        )
        .execution_options(populate_existing=True)
    )
    if locked:
        query = query.with_for_update()
    statement = (await session.execute(query)).scalar_one_or_none()
    if statement is None or statement.workflow_mode != "direct":
        raise DirectConflict("direct_statement_required")
    state = await session.get(
        AmexStatementDirectState,
        import_id,
        populate_existing=True,
        with_for_update=locked,
    )
    context = (
        await session.get(AmexStatementDirectContext, state.context_id)
        if state and state.context_id
        else None
    )
    correcting = requested_company_code is not None
    if correcting and (not finance or action != "contexto"):
        raise DirectAccessError("finance_context_correction_required")
    config = configuration_for(
        statement.card_account_id,
        (
            requested_company_code
            if correcting
            else (context.company_code if context else None)
        ),
        (
            requested_receiver_rfc
            if correcting
            else (context.receiver_rfc if context else None)
        ),
    )
    if config is None:
        raise DirectConflict("contexto_fiscal_pendiente")
    await authorize(
        session, actor_id, action, config=config, finance=finance, budget=budget
    )
    mismatch = context is not None and context.config_hash != digest(config)
    if mismatch and not correcting:
        from .amex_direct_account_treatment_service import policy_for_config

        try:
            await policy_for_config(session, config, context)
            mismatch = False
        except DirectConflict:
            pass
    if context and not correcting and mismatch:
        cut = (
            await session.execute(
                select(AmexDirectAccountingCut.id).where(
                    AmexDirectAccountingCut.import_id == import_id
                )
            )
        ).first()
        if not cut:
            cut = (
                await session.execute(
                    select(AmexDirectCompletionReceipt.id).where(
                        AmexDirectCompletionReceipt.import_id == import_id
                    )
                )
            ).first()
        if not cut or action not in {"ver", "fuente", "exportar"}:
            raise DirectConflict("authorized_context_changed")
    if locked:
        # The existing statement wrappers own import BEFORE global recognition.
        await lock_recognition(session)
    return statement, state, context, config


async def require_unfrozen(session: Any, import_id: UUID) -> None:
    if (
        await session.execute(
            select(AmexDirectAccountingCut.id).where(
                AmexDirectAccountingCut.import_id == import_id
            )
        )
    ).first():
        raise DirectConflict("direct_cut_immutable")
    if (
        await session.execute(
            select(AmexDirectCompletionReceipt.id).where(
                AmexDirectCompletionReceipt.import_id == import_id
            )
        )
    ).first():
        raise DirectConflict("direct_cut_immutable")


def item_hash(item: Any, effective_source: Any = None) -> str:
    facts = {
        key: getattr(item, key, None)
        for key in (
            "import_id",
            "import_item_id",
            "imported_expense_id",
            "movement_kind",
            "raw_signed_amount",
            "profile",
            "profile_version",
            "source_json",
            "context_id",
            "context_version",
            "source_version",
            "cfdi_id",
            "status_receipt_id",
            "merchant_alias_id",
            "declared_uuid",
            "issuer_rfc",
            "match_status",
            "match_proof",
            "tournament_id",
            "project_code",
            "edition_year",
            "budget_concept_id",
            "budget_line_id",
            "budget_version_id",
            "budget_assignment_id",
            "budget_status",
            "credit_excluded",
        )
    }
    if getattr(item, "account_treatment_decision_id", None) is not None:
        facts["account_treatment_decision_id"] = item.account_treatment_decision_id
    if getattr(item, "no_cfdi_decision_id", None) is not None:
        facts["no_cfdi_decision_id"] = item.no_cfdi_decision_id
    if effective_source is not None and effective_source.recovery_id:
        facts["effective_source"] = {
            "expense_id": effective_source.imported_expense_id,
            "movement_kind": effective_source.movement_kind,
            "source": effective_source.source_json,
            "recovery_id": effective_source.recovery_id,
            "revision_id": effective_source.recovery_revision_id,
            "proof_hash": effective_source.recovery_proof_hash,
        }
    if effective_source is not None and getattr(
        effective_source, "sign_revision_id", None
    ):
        facts["sign_correction"] = {
            "expense_id": effective_source.imported_expense_id,
            "movement_kind": effective_source.movement_kind,
            "profile": effective_source.profile,
            "source": effective_source.source_json,
            "revision_id": effective_source.sign_revision_id,
            "row_id": effective_source.sign_row_id,
            "proof_hash": effective_source.sign_proof_hash,
        }
    return digest(facts)


async def record_event(
    session: Any,
    item: Any,
    state: Any,
    actor_id: UUID,
    action: str,
    payload: dict[str, Any],
    *,
    bump: bool = True,
) -> None:
    if bump:
        item.source_version += 1
        state.source_version += 1
    from .amex_direct_effective_source_service import effective_source_for_item

    # Keep the decision/status/version and its hash in one guarded update.
    # SQLAlchemy 2.1 also autoflushes before Core SELECTs.
    with session.no_autoflush:
        source = await effective_source_for_item(session, item)
    item.source_hash = item_hash(item, source)
    item.updated_at = datetime.now(timezone.utc)
    state.updated_at = item.updated_at
    await session.flush()
    session.add(
        AmexDirectEvent(
            item_id=item.id,
            action=action,
            actor_id=actor_id,
            source_version=item.source_version,
            source_hash=item.source_hash,
            payload_hash=digest(payload),
            payload_json=json.loads(json.dumps(payload, default=str)),
        )
    )


async def load_items(
    session: Any, import_id: UUID, *, locked: bool = False
) -> list[Any]:
    query = (
        select(AmexDirectItem)
        .where(AmexDirectItem.import_id == import_id)
        .order_by(AmexDirectItem.import_item_id)
        .execution_options(populate_existing=True)
    )
    if locked:
        query = query.with_for_update()
    return list((await session.execute(query)).scalars().all())


async def resolve_item_import(
    session: Any,
    *,
    actor_id: UUID,
    action: str,
    item_id: UUID | None = None,
    expense_id: UUID | None = None,
    budget: bool = False,
) -> UUID:
    """Resolve only a declared scoped source after actor/action authorization."""
    from ..models import AmexDirectItem
    from .amex_direct_effective_source_service import effective_sources_select

    await authorize(session, actor_id, action, budget=budget)
    configs = [c for c in configured_scopes() if actor_scope(c, actor_id) is not None]
    if not configs:
        raise DirectAccessError("actor_scope_denied")
    for config in configs:
        await authorize(session, actor_id, action, config=config, budget=budget)
    await require_amex_direct_schema(session)
    sources = effective_sources_select()
    query = (
        select(AmexDirectItem.import_id)
        .join(sources, sources.c.id == AmexDirectItem.id)
        .join(AmexStatementImport, AmexStatementImport.id == AmexDirectItem.import_id)
        .where(
            AmexStatementImport.workflow_mode == "direct",
            AmexStatementImport.card_account_id.in_(
                [UUID(v) for c in configs for v in c["card_account_ids"]]
            ),
        )
    )
    query = (
        query.where(AmexDirectItem.id == item_id)
        if item_id
        else query.where(sources.c.imported_expense_id == expense_id)
    )
    parent = (await session.execute(query)).scalar_one_or_none()
    if parent is None:
        raise DirectConflict("direct_item_missing")
    return cast(UUID, parent)


async def load_budget_items(
    session: Any, import_id: UUID, config: dict[str, Any], actor_id: UUID
) -> list[Any]:
    """Read CP projections in declared project scope before loading fiscal content."""
    from sqlalchemy import and_, or_

    scope = actor_scope(config, actor_id)
    if scope is None:
        raise DirectAccessError("actor_scope_denied")
    tournaments = set(scope.get("tournament_ids", [])) & set(
        config.get("tournament_ids", [])
    )
    projects = set(scope.get("project_codes", [])) & set(
        config.get("project_codes", [])
    )
    years = set(scope.get("edition_years", [])) & set(config.get("edition_years", []))
    project_predicate = and_(
        AmexDirectItem.edition_year.in_(years),
        or_(
            AmexDirectItem.tournament_id.in_([UUID(v) for v in tournaments]),
            and_(
                AmexDirectItem.tournament_id.is_(None),
                AmexDirectItem.project_code.in_(projects),
            ),
        ),
    )
    from .amex_direct_account_treatment_service import benefit_grants

    try:
        benefit_grants(config, actor_id)
        benefit_available = str(actor_id) in config.get("budget_actor_ids", [])
    except DirectAccessError:
        benefit_available = False
    benefit_cleanup_available = str(actor_id) in config.get("budget_actor_ids", [])
    predicate = project_predicate
    if benefit_available or benefit_cleanup_available:
        from ..models import AmexDirectAccountTreatmentDecision

        predicate = or_(
            project_predicate,
            AmexDirectItem.account_treatment_decision_id.in_(
                select(AmexDirectAccountTreatmentDecision.id).where(
                    AmexDirectAccountTreatmentDecision.action == "set",
                    AmexDirectAccountTreatmentDecision.treatment == "benefit_expense",
                )
            ),
        )
    query = (
        select(AmexDirectItem)
        .join(
            AmexStatementDirectState,
            AmexStatementDirectState.import_id == AmexDirectItem.import_id,
        )
        .where(
            AmexDirectItem.import_id == import_id,
            AmexDirectItem.context_id == AmexStatementDirectState.context_id,
            predicate,
        )
        .order_by(AmexDirectItem.import_item_id)
    )
    rows = list((await session.execute(query)).scalars().all())
    if not (benefit_available or benefit_cleanup_available):
        return rows
    # Filter only metadata before the caller loads fiscal content.
    from .amex_direct_budget_service import _item_budget_scope, _line

    allowed = []
    for item in rows:
        if getattr(item, "account_treatment_decision_id", None):
            try:
                await _item_budget_scope(
                    session,
                    config,
                    actor_id,
                    item,
                    line=(
                        await _line(session, item.budget_line_id)
                        if item.budget_line_id
                        else None
                    ),
                    budget_actor=True,
                    cleanup=(
                        benefit_cleanup_available
                        and item.budget_status in {"sent", "assigned"}
                    ),
                )
            except (DirectAccessError, DirectConflict):
                continue
        allowed.append(item)
    return allowed
