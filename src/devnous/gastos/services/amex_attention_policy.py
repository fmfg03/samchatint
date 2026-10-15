"""Presentation-only AMEX attention policy, using explicit beneficiary identity.

Version 2026-10-13: Odilon's reports are attended from Cuenta/INFORME.
This policy never changes bank rows or accounting/fiscal eligibility.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import raiseload

from ..models import CuentaDeGastos, Documento, Empleado, ProveedorCliente

POLICY_VERSION = "2026-10-13"
EXCLUDED_EMPLOYEE_IDS = ("f9aebcf3-ef28-423c-8a77-f41c9e4e885a",)


@dataclass(frozen=True)
class AttentionPolicy:
    valid: bool
    excluded_employee_ids: frozenset[UUID] = frozenset()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttentionDecision:
    visible: bool
    reason: str
    beneficiary_id: UUID | None = None


@dataclass
class AttentionContext:
    policy: AttentionPolicy
    document_decisions: dict[UUID, AttentionDecision] = field(default_factory=dict)
    expense_decisions: dict[UUID, AttentionDecision] = field(default_factory=dict)

    @property
    def warnings(self) -> tuple[str, ...]:
        return self.policy.warnings


def validate_attention_policy(
    configured_ids: Iterable[Any], existing_ids: Iterable[Any]
) -> AttentionPolicy:
    """Invalid or missing configured identities disable exclusion visibly."""
    try:
        ids = frozenset(UUID(str(value)) for value in configured_ids)
        existing = frozenset(UUID(str(value)) for value in existing_ids)
    except (ValueError, TypeError, AttributeError):
        return AttentionPolicy(
            False, warnings=("amex_attention_invalid_configuration",)
        )
    if not ids or not ids.issubset(existing):
        return AttentionPolicy(False, warnings=("amex_attention_identity_not_found",))
    return AttentionPolicy(True, ids)


def _beneficiary(
    value: Any, provider_employee_ids: Mapping[UUID, UUID]
) -> tuple[str, UUID | None]:
    if value is None:
        return "missing", None
    employee = getattr(value, "beneficiario_empleado_id", None)
    provider = getattr(value, "beneficiario_proveedor_cliente_id", None)
    alternate = getattr(value, "beneficiario_alterno_tipo", None)
    if employee and provider:
        if str(alternate or "").strip():
            return "ambiguous", None
        try:
            employee_id = UUID(str(employee))
            provider_id = UUID(str(provider))
        except (ValueError, TypeError, AttributeError):
            return "ambiguous", None
        if provider_employee_ids.get(provider_id) != employee_id:
            return "ambiguous", None
        return "employee", employee_id
    if employee and str(alternate or "").strip():
        return "ambiguous", None
    if provider:
        try:
            return "provider", UUID(str(provider))
        except (ValueError, TypeError, AttributeError):
            return "ambiguous", None
    if str(alternate or "").strip():
        return "ambiguous", None
    if not employee:
        return "missing", None
    try:
        return "employee", UUID(str(employee))
    except (ValueError, TypeError, AttributeError):
        return "ambiguous", None


def classify_amex_attention(
    document: Any,
    account: Any = None,
    *,
    candidate_count: int = 1,
    policy: AttentionPolicy,
    provider_employee_ids: Mapping[UUID, UUID] | None = None,
) -> AttentionDecision:
    """Resolve explicit document/account beneficiaries; never use capturer."""
    if not policy.valid:
        return AttentionDecision(True, "amex_attention_configuration_disabled")
    if candidate_count != 1:
        return AttentionDecision(True, "amex_attention_multiple_or_missing_informes")
    links = provider_employee_ids or {}
    doc_kind, doc_id = _beneficiary(document, links)
    acc_kind, acc_id = _beneficiary(account, links)
    if doc_kind == "ambiguous" or acc_kind == "ambiguous":
        return AttentionDecision(True, "amex_attention_ambiguous_beneficiary")
    if doc_id and acc_id and (doc_id != acc_id or doc_kind != acc_kind):
        return AttentionDecision(True, "amex_attention_beneficiary_conflict")
    if doc_kind == "provider" or acc_kind == "provider":
        return AttentionDecision(True, "amex_attention_provider")
    resolved = doc_id or acc_id
    if resolved is None:
        return AttentionDecision(True, "amex_attention_missing_beneficiary")
    excluded = resolved in policy.excluded_employee_ids
    return AttentionDecision(
        not excluded,
        "amex_attention_by_account" if excluded else "amex_attention_normal",
        resolved,
    )


async def _load_amex_attention_context(
    session: Any,
    *,
    documents: Iterable[Any] = (),
    expenses: Iterable[Any] = (),
    configured_ids: Iterable[Any] = EXCLUDED_EMPLOYEE_IDS,
) -> AttentionContext:
    """Batch-load explicit account/report metadata without joins dropping rows."""
    documents, expenses = list(documents), list(expenses)
    configured_ids = tuple(configured_ids)
    try:
        configured = [UUID(str(value)) for value in configured_ids]
    except (ValueError, TypeError, AttributeError):
        policy = validate_attention_policy(configured_ids, ())
    else:
        found = (
            (
                await session.execute(
                    select(Empleado.id).where(Empleado.id.in_(configured))
                )
            )
            .scalars()
            .all()
        )
        policy = validate_attention_policy(configured_ids, found)
    context = AttentionContext(policy)
    account_ids = {
        getattr(row, "cuenta_gastos_id", None) for row in documents + expenses
    } - {None}
    accounts = {}
    candidates: dict[UUID, list[Any]] = {}
    if account_ids:
        accounts = {
            row.id: row
            for row in (
                await session.execute(
                    select(CuentaDeGastos)
                    .options(raiseload("*"))
                    .where(CuentaDeGastos.id.in_(account_ids))
                )
            )
            .scalars()
            .all()
        }
        for row in (
            (
                await session.execute(
                    select(Documento)
                    .options(raiseload("*"))
                    .where(
                        Documento.cuenta_gastos_id.in_(account_ids),
                        Documento.tipo == "INFORME",
                        Documento.estado != "cancelado",
                    )
                )
            )
            .scalars()
            .all()
        ):
            candidates.setdefault(row.cuenta_gastos_id, []).append(row)
    # A provider may explicitly represent the same employee. Load only exact
    # persisted links referenced by this read; never infer identity from names.
    provider_ids = {
        row.beneficiario_proveedor_cliente_id
        for row in (
            documents
            + list(accounts.values())
            + [report for reports in candidates.values() for report in reports]
        )
        if getattr(row, "beneficiario_proveedor_cliente_id", None) is not None
    }
    provider_employee_ids = {}
    if provider_ids:
        provider_employee_ids = {
            provider_id: employee_id
            for provider_id, employee_id in (
                await session.execute(
                    select(ProveedorCliente.id, ProveedorCliente.empleado_id).where(
                        ProveedorCliente.id.in_(provider_ids),
                        ProveedorCliente.empleado_id.is_not(None),
                    )
                )
            ).all()
        }
    for document in documents:
        account_id = getattr(document, "cuenta_gastos_id", None)
        count = len(candidates.get(account_id, [])) if account_id else 1
        context.document_decisions[document.id] = classify_amex_attention(
            document,
            accounts.get(account_id),
            candidate_count=count,
            policy=policy,
            provider_employee_ids=provider_employee_ids,
        )
    for expense in expenses:
        # Imported bank rows remain visible regardless of their declared holder.
        if getattr(expense, "origen", None) == "amex_batch":
            context.expense_decisions[expense.id] = AttentionDecision(True, "bank_row")
            continue
        account_id = getattr(expense, "cuenta_gastos_id", None)
        reports = candidates.get(account_id, []) if account_id is not None else []
        context.expense_decisions[expense.id] = classify_amex_attention(
            reports[0] if len(reports) == 1 else None,
            accounts.get(account_id),
            candidate_count=len(reports),
            policy=policy,
            provider_employee_ids=provider_employee_ids,
        )
    return context


async def load_amex_attention_context(session: Any, **kwargs: Any) -> AttentionContext:
    """Read-only policy metadata loading even with pending caller changes."""
    with session.no_autoflush:
        return await _load_amex_attention_context(session, **kwargs)
