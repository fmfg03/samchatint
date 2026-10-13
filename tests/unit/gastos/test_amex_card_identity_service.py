"""Explicit identity guards, audit and unchanged accounting source contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.models import AmexCardAccount, CuentaContable, Empleado
from devnous.gastos.services import amex_recognition_service as service


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class Session:
    def __init__(self):
        self.actor = SimpleNamespace(id=uuid4(), activo=True, rol="finanzas")
        self.card = SimpleNamespace(
            id=uuid4(), active=True, last4="1005", liability_cuenta_contable_id=uuid4()
        )
        self.liability = SimpleNamespace(activo=True, codigo="2110-000-003")
        self.informe = SimpleNamespace(id=uuid4(), estado="aprobado")
        self.added = []
        self.queries = []
        self.bound = False
        self.cut = False

    async def get(self, model, ident, **kwargs):
        assert kwargs == {"populate_existing": True, "with_for_update": True}
        return {
            Empleado: self.actor,
            AmexCardAccount: self.card,
            CuentaContable: self.liability,
        }[model]

    async def execute(self, statement, *args):
        sql = str(statement)
        self.queries.append(sql)
        if "FROM documentos" in sql:
            return Result([self.informe] if self.informe else [])
        if "FROM amex_accounting_cuts" in sql:
            return Result([uuid4()] if self.cut else [])
        if "FROM amex_recognition_representations" in sql:
            return Result([uuid4()] if self.bound else [])
        return Result()

    def add(self, row):
        self.added.append(row)


@pytest.fixture
def setup(monkeypatch):
    from devnous.gastos.services.amex_accounting_posting_service import (
        ALLOWED_AMEX_LIABILITY_CODES,
    )

    session = Session()
    session.liability.codigo = next(iter(ALLOWED_AMEX_LIABILITY_CODES))
    expense = SimpleNamespace(
        id=uuid4(),
        estado_gasto="activo",
        origen="informe",
        pagado_con_amex_empresa=True,
        coi_estado="pendiente",
        ultimos_4_digitos=None,
        gasto_cantidad=123,
        cuenta_gastos_id=uuid4(),
    )
    loader = AsyncMock(return_value=[expense])
    monkeypatch.setattr(service, "load_expenses", loader)
    return session, expense, loader


async def assign(session, expense, **overrides):
    args = dict(
        expense_ids=[expense.id],
        card_account_id=getattr(session.card, "id", None),
        actor_id=session.actor.id,
        reason="Confirmado por Finanzas",
        confirmed=True,
    )
    args.update(overrides)
    return await service.assign_missing_amex_card(session, **args)


@pytest.mark.asyncio
async def test_assignment_idempotency_audit_and_review_fingerprint(setup):
    session, expense, _ = setup
    before = service.review_source_key(expense)
    result = await assign(session, expense)
    assert result.status == "assigned" and result.updated_count == 1
    assert result.unchanged_count == 0
    assert expense.ultimos_4_digitos == "1005"
    assert expense.gasto_cantidad == 123 and session.informe.estado == "aprobado"
    assert service.review_source_key(expense) != before
    assert len(session.added) == 1
    audit = session.added[0]
    assert audit.accion == "editar" and audit.aprobador_id == session.actor.id
    assert audit.entidad_id == expense.id
    assert str(session.card.id) in audit.comentario
    assert "None -> 1005" in audit.comentario
    assert "Confirmado por Finanzas" in audit.comentario
    repeat = await assign(session, expense)
    assert repeat.status == "unchanged" and repeat.unchanged_count == 1
    assert len(session.added) == 1
    assert "pg_advisory_xact_lock" in session.queries[0]
    assert any("FOR UPDATE" in sql for sql in session.queries)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("estado_gasto", "cancelado", "invalid_amex_report_expense"),
        ("origen", "amex_batch", "invalid_amex_report_expense"),
        ("pagado_con_amex_empresa", False, "invalid_amex_report_expense"),
        ("coi_estado", "contabilizado", "invalid_amex_report_expense"),
        ("coi_estado", "reversar", "invalid_amex_report_expense"),
        ("ultimos_4_digitos", "2006", "amex_card_identity_conflict"),
    ],
)
async def test_mixed_selection_validates_all_before_mutating(
    setup, field, value, reason
):
    session, expense, loader = setup
    second = SimpleNamespace(**vars(expense))
    second.id = uuid4()
    setattr(second, field, value)
    loader.return_value = [expense, second]
    result = await assign(session, expense, expense_ids=[expense.id, second.id])
    assert result.status == "pending" and result.reason == reason
    assert expense.ultimos_4_digitos is None and not session.added


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target,field,value,reason",
    [
        ("actor", "activo", False, "unauthorized_amex_accounting_actor"),
        ("actor", "rol", "empleado", "unauthorized_amex_accounting_actor"),
        ("card", "active", False, "invalid_amex_card_identity"),
        ("card", "last4", "bad", "invalid_amex_card_identity"),
        ("liability", "activo", False, "invalid_amex_liability"),
        ("liability", "codigo", "9999", "invalid_amex_liability"),
        ("informe", "estado", "cancelado", "invalid_amex_informe"),
        ("informe", "estado", "rechazado", "invalid_amex_informe"),
    ],
)
async def test_invalid_authority_and_catalog(setup, target, field, value, reason):
    session, expense, _ = setup
    setattr(getattr(session, target), field, value)
    result = await assign(session, expense)
    assert result.reason == reason and not session.added


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"confirmed": False}, "missing_amex_card_confirmation"),
        ({"reason": " "}, "missing_amex_card_reason"),
        ({"expense_ids": []}, "missing_amex_representation"),
    ],
)
async def test_requires_explicit_selection_and_confirmation(setup, overrides, reason):
    session, expense, _ = setup
    result = await assign(session, expense, **overrides)
    assert result.reason == reason and not session.added


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target,reason",
    [
        ("bound", "amex_representation_already_bound"),
        ("cut", "amex_informe_already_cut"),
    ],
)
async def test_recognized_source_is_immutable(setup, target, reason):
    session, expense, _ = setup
    setattr(session, target, True)
    result = await assign(session, expense)
    assert result.reason == reason and expense.ultimos_4_digitos is None


@pytest.mark.asyncio
async def test_missing_or_duplicate_expenses_never_write(setup):
    session, expense, loader = setup
    result = await assign(session, expense, expense_ids=[expense.id, expense.id])
    assert result.reason == "missing_amex_representation"
    loader.return_value = []
    result = await assign(session, expense)
    assert result.reason == "missing_amex_representation" and not session.added


@pytest.mark.asyncio
async def test_catalog_missing_actor_card_account_and_report(setup):
    session, expense, _ = setup
    session.actor = None
    result = await service.assign_missing_amex_card(
        session,
        expense_ids=[expense.id],
        card_account_id=session.card.id,
        actor_id=uuid4(),
        reason="Prueba",
        confirmed=True,
    )
    assert result.reason == "unauthorized_amex_accounting_actor"
    session.actor = SimpleNamespace(id=uuid4(), activo=True, rol="finanzas")
    card = session.card
    session.card = None
    assert (
        await assign(session, expense, card_account_id=card.id)
    ).reason == "invalid_amex_card_identity"
    session.card = card
    session.liability = None
    assert (await assign(session, expense)).reason == "invalid_amex_liability"


@pytest.mark.asyncio
async def test_missing_and_ambiguous_report_are_rejected(setup):
    session, expense, _ = setup
    session.informe = None
    assert (await assign(session, expense)).reason == "invalid_amex_informe"


@pytest.mark.asyncio
@pytest.mark.parametrize("mark", [True, False])
async def test_bulk_mark_and_unmark_protect_recognized_rows(setup, mark):
    from devnous.gastos.services.amex_expense_service import (
        AmexExpenseValidationError,
        set_company_amex_status,
    )

    session, expense, _ = setup
    expense.cuenta_contable_id = uuid4()
    session.bound = True
    original_execute = session.execute

    async def execute(statement, *args):
        if "FROM expense_reports" in str(statement):
            return Result([expense])
        return await original_execute(statement, *args)

    session.execute = execute
    with pytest.raises(
        AmexExpenseValidationError, match="amex_representation_already_bound"
    ):
        await set_company_amex_status(
            session,
            cuenta_id=expense.cuenta_gastos_id,
            expense_ids=[expense.id],
            mark_as_amex=mark,
            actor=session.actor,
            card_account_id=session.card.id,
        )
    assert expense.ultimos_4_digitos is None and not session.added


@pytest.mark.asyncio
async def test_new_company_mark_writes_explicit_identity_and_two_audits(setup):
    from devnous.gastos.services.amex_expense_service import set_company_amex_status

    session, expense, _ = setup
    expense.pagado_con_amex_empresa = False
    expense.cuenta_contable_id = uuid4()
    original_execute = session.execute

    async def execute(statement, *args):
        if "FROM expense_reports" in str(statement):
            return Result([expense])
        return await original_execute(statement, *args)

    session.execute = execute
    changed = await set_company_amex_status(
        session,
        cuenta_id=expense.cuenta_gastos_id,
        expense_ids=[expense.id],
        mark_as_amex=True,
        actor=session.actor,
        card_account_id=session.card.id,
    )
    assert changed == [expense]
    assert expense.pagado_con_amex_empresa and expense.ultimos_4_digitos == "1005"
    assert len(session.added) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["actor", "card_selection", "reason", "card"])
async def test_mark_requires_active_actor_explicit_valid_card_and_reason(
    setup, invalid
):
    from devnous.gastos.services.amex_expense_service import (
        AmexExpenseError,
        set_company_amex_status,
    )

    session, expense, _ = setup
    actor = session.actor
    card_id = session.card.id
    reason = "Identificación confirmada"
    if invalid == "actor":
        session.actor.activo = False
    elif invalid == "card_selection":
        card_id = None
    elif invalid == "reason":
        reason = " "
    else:
        session.card.active = False
    with pytest.raises(AmexExpenseError):
        await set_company_amex_status(
            session,
            cuenta_id=expense.cuenta_gastos_id,
            expense_ids=[expense.id],
            mark_as_amex=True,
            actor=actor,
            card_account_id=card_id,
            reason=reason,
        )
    assert expense.ultimos_4_digitos is None and not session.added


@pytest.mark.asyncio
async def test_edit_transition_validates_non_amex_source_without_mutating(setup):
    session, expense, loader = setup
    expense.pagado_con_amex_empresa = False
    card, reason = await service.validate_amex_card_edit_transition(
        session, expense_id=expense.id, card_account_id=session.card.id
    )
    assert card is session.card and reason is None
    loader.assert_awaited_once_with(session, [expense.id])
    assert "pg_advisory_xact_lock" in session.queries[0]
    assert any("FOR UPDATE" in query for query in session.queries)
    assert expense.pagado_con_amex_empresa is False
    assert expense.ultimos_4_digitos is None and not session.added


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protected,reason",
    [
        ("bound", "amex_representation_already_bound"),
        ("cut", "amex_informe_already_cut"),
        ("coi", "invalid_amex_report_expense"),
        ("imported", "invalid_amex_report_expense"),
        ("conflict", "amex_card_identity_conflict"),
        ("missing", "missing_amex_representation"),
        ("invalid_card", "invalid_amex_card_identity"),
    ],
)
async def test_edit_transition_rejects_protected_source_before_mark(
    setup, protected, reason
):
    session, expense, loader = setup
    expense.pagado_con_amex_empresa = False
    if protected in {"bound", "cut"}:
        setattr(session, protected, True)
    elif protected == "coi":
        expense.coi_estado = "contabilizado"
    elif protected == "imported":
        expense.origen = "amex_batch"
    elif protected == "conflict":
        expense.ultimos_4_digitos = "2006"
    elif protected == "missing":
        loader.return_value = []
    else:
        session.card.active = False
    card, invalid = await service.validate_amex_card_edit_transition(
        session, expense_id=expense.id, card_account_id=session.card.id
    )
    assert card is None and invalid == reason
    assert expense.pagado_con_amex_empresa is False and not session.added


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["borrador", "enviado", "aprobado"])
async def test_owner_pending_transition_preserves_source_and_avoids_catalog(
    setup, state
):
    session, expense, loader = setup
    expense.pagado_con_amex_empresa = False
    session.informe.estado = state
    session.get = AsyncMock(
        side_effect=AssertionError("Catalog/actor access forbidden")
    )
    reason = await service.validate_amex_pending_edit_transition(
        session, expense_id=expense.id
    )
    assert reason is None
    loader.assert_awaited_once_with(session, [expense.id])
    session.get.assert_not_awaited()
    assert "pg_advisory_xact_lock" in session.queries[0]
    assert any("FOR UPDATE" in query for query in session.queries)
    assert expense.pagado_con_amex_empresa is False
    assert expense.ultimos_4_digitos is None and not session.added


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protected,reason",
    [
        ("bound", "amex_representation_already_bound"),
        ("cut", "amex_informe_already_cut"),
        ("coi", "invalid_amex_report_expense"),
        ("reversar", "invalid_amex_report_expense"),
        ("imported", "invalid_amex_report_expense"),
        ("missing", "missing_amex_representation"),
        ("identity", "amex_card_identity_requires_finance"),
        ("cancelled", "invalid_amex_informe"),
        ("rejected", "invalid_amex_informe"),
    ],
)
async def test_owner_pending_transition_rejects_frozen_or_identified_source(
    setup, protected, reason
):
    session, expense, loader = setup
    expense.pagado_con_amex_empresa = False
    session.get = AsyncMock(side_effect=AssertionError("Catalog access forbidden"))
    if protected in {"bound", "cut"}:
        setattr(session, protected, True)
    elif protected in {"coi", "reversar"}:
        expense.coi_estado = "contabilizado" if protected == "coi" else "reversar"
    elif protected == "imported":
        expense.origen = "amex_batch"
    elif protected == "missing":
        loader.return_value = []
    elif protected == "identity":
        expense.ultimos_4_digitos = "1005"
    else:
        session.informe.estado = (
            "cancelado" if protected == "cancelled" else "rechazado"
        )
    before = vars(expense).copy()
    result = await service.validate_amex_pending_edit_transition(
        session, expense_id=expense.id
    )
    assert result == reason
    session.get.assert_not_awaited()
    assert vars(expense) == before and not session.added
