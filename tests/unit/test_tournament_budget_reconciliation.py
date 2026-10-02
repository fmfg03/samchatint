"""Regression coverage for cross-department routing and mixed expense reports."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from devnous.gastos.services import project_authorization_service as routing
from samchat.budgets import service as budgets


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self.first()


class RoutingSession:
    def __init__(self, existing=None, beneficiary_positions=()):
        self.existing = existing
        self.positions = list(beneficiary_positions)
        self.queries = []
        self.writes = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append((sql, params))
        if sql.lstrip().startswith("INSERT"):
            self.writes.append(params)
            return Result([])
        if "FROM documento_authorization_routes" in sql:
            return Result([self.existing] if self.existing else [])
        if "SELECT position_key" in sql:
            return Result(self.positions)
        if "FROM cuentas_de_gastos" in sql:
            return Result([{"torneo_id": "account-tournament"}])
        if "FROM project_authorization_rules" in sql:
            return Result(
                [
                    {
                        "eligible_position_keys": ["director_operaciones"],
                        "requires_operations_reference": True,
                    }
                ]
            )
        if "SELECT empleado_id" in sql:
            return Result(["configured-holder"])
        raise AssertionError(sql)


@pytest.mark.asyncio
@pytest.mark.parametrize("direct_tournament", [None, "direct-tournament"])
async def test_routing_uses_account_only_when_document_tournament_is_missing(
    direct_tournament,
):
    session = RoutingSession()
    doc = SimpleNamespace(
        id="report",
        empleado_id="finance-requester",
        beneficiario_empleado_id=None,
        torneo_id=direct_tournament,
        cuenta_gastos_id="account",
    )
    route = await routing.resolve_and_snapshot_document_route(session, doc)
    assert route.requires_operations_reference
    assert route.eligible_position_keys == ("director_operaciones",)
    rule_params = next(
        p for sql, p in session.queries if "project_authorization_rules" in sql
    )
    assert rule_params["tournament_id"] == (direct_tournament or "account-tournament")
    assert bool([sql for sql, _ in session.queries if "cuentas_de_gastos" in sql]) == (
        direct_tournament is None
    )
    assert len(session.writes) == 1


@pytest.mark.asyncio
async def test_existing_route_is_not_recomputed_from_account():
    session = RoutingSession(
        existing={
            "eligible_position_keys": ["direccion_general"],
            "requires_operations_reference": False,
            "source": "existing",
        }
    )
    route = await routing.resolve_and_snapshot_document_route(
        session, SimpleNamespace(id="report")
    )
    assert route.eligible_position_keys == ("direccion_general",)
    assert len(session.queries) == 1
    assert not session.writes


@pytest.mark.asyncio
async def test_account_route_preserves_beneficiary_exception():
    session = RoutingSession(beneficiary_positions=["director_operaciones"])
    doc = SimpleNamespace(
        id="report", empleado_id="requester", torneo_id=None, cuenta_gastos_id="account"
    )
    route = await routing.resolve_and_snapshot_document_route(session, doc)
    assert not route.requires_operations_reference
    assert route.eligible_position_keys == (
        "direccion_general",
        "direccion_administracion_finanzas",
    )


class AssignmentSession:
    def __init__(self, assignments, lines):
        self.assignments, self.lines = assignments, lines

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM budget_movement_assignments" in sql:
            return Result(self.assignments)
        if "FROM budget_lines" in sql:
            return Result(self.lines)
        raise AssertionError(sql)


@pytest.mark.asyncio
@pytest.mark.parametrize("old_key", ["document:report", "accounting:hotel-posting"])
async def test_hotel_assignment_does_not_capture_other_report_concepts(old_key):
    session = AssignmentSession(
        [
            {
                "movement_key": old_key,
                "budget_concept_id": "hotel",
                "budget_line_id": "hotel-line",
            }
        ],
        [
            {"id": "hotel-line", "budget_concept_id": "hotel"},
            {"id": "food-line", "budget_concept_id": "food"},
        ],
    )
    movements = [
        {
            "document_id": "report",
            "expense_id": "hotel-expense",
            "accounting_line_id": "hotel-posting",
            "concept_key": "hotel",
            "kind": "commitment",
            "amount": 90,
            "month_number": 1,
        },
        {
            "document_id": "report",
            "expense_id": "food-expense",
            "accounting_line_id": "food-posting",
            "concept_key": "food",
            "kind": "commitment",
            "amount": 40,
            "month_number": 1,
        },
    ]
    store = budgets._monthly_actual_store()
    await budgets._apply_explicit_movement_assignments(
        session, budget_version_id="version", movements=movements, store=store
    )
    assert movements[0]["budget_line_id"] == "hotel-line"
    assert movements[1]["budget_line_id"] == "food-line"
    assert movements[0]["movement_key"] != movements[1]["movement_key"]
    assert store["hotel-line"][1]["committed_unpaid"] == 90
    assert store["food-line"][1]["committed_unpaid"] == 40


@pytest.mark.asyncio
async def test_legacy_document_assignment_preserves_paid_single_concept_request():
    session = AssignmentSession(
        [
            {
                "movement_key": "document:paid-request",
                "budget_concept_id": "food",
                "budget_line_id": "food-line",
            }
        ],
        [{"id": "food-line", "budget_concept_id": "food"}],
    )
    movement = {
        "document_id": "paid-request",
        "accounting_line_id": "posting",
        "concept_key": "food",
        "kind": "ledger_expense",
        "amount": 100,
        "month_number": 1,
    }
    store = budgets._monthly_actual_store()
    await budgets._apply_explicit_movement_assignments(
        session, budget_version_id="version", movements=[movement], store=store
    )
    assert movement["budget_line_id"] == "food-line"
    assert store["food-line"][1]["real_expense_cash"] == 100


def test_movement_identity_is_specific_to_posting_expense_or_document_concept():
    assert (
        budgets.budget_movement_key(
            {"document_id": "report", "expense_id": "e", "accounting_line_id": "a"}
        )
        == "accounting:a"
    )
    assert (
        budgets.budget_movement_key({"document_id": "report", "expense_id": "e"})
        == "expense:e"
    )
    assert (
        budgets.budget_movement_key({"document_id": "report", "concept_key": "food"})
        == "document:report:concept:food"
    )


class SettlementSession:
    def __init__(self, state="pagado", amount=100, amex=False, multiple_reports=False):
        self.state, self.amount, self.amex = state, amount, amex
        self.multiple_reports = multiple_reports

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "budget_report_settlement_reports" in sql:
            return Result(
                [
                    {
                        "document_id": "report",
                        "cuenta_gastos_id": "account",
                        "report_count": 2 if self.multiple_reports else 1,
                    }
                ]
            )
        if "budget_report_settlement_requests" in sql:
            return Result(
                [
                    {
                        "cuenta_gastos_id": "account",
                        "tipo": "SOLICITUD",
                        "estado": self.state,
                        "monto_solicitado": self.amount,
                        "concepto_pago": "Reembolso de saldo a favor — I-report",
                        "pagado_en": date(2026, 9, 24),
                        "fecha_pago": None,
                    }
                ]
            )
        if "budget_report_settlement_expenses" in sql:
            return Result(
                [
                    {
                        "cuenta_gastos_id": "account",
                        "gasto_cantidad": 100,
                        "pagado_con_amex_empresa": self.amex,
                        "origen": None,
                        "estado_gasto": "activo",
                    }
                ]
            )
        if "budget_report_settlement_adjustments" in sql:
            return Result([])
        raise AssertionError(sql)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,amount,amex,multiple_reports,settled",
    [
        ("pagado", 100, False, False, True),
        ("aprobado", 100, False, False, False),
        ("cancelado", 100, False, False, False),
        ("pagado", 50, False, False, False),
        ("pagado", 120, False, False, False),
        ("pagado", 100, True, False, False),
        ("pagado", 100, False, True, False),
    ],
)
async def test_only_full_paid_reimbursement_settles_report(
    state,
    amount,
    amex,
    multiple_reports,
    settled,
):
    session = SettlementSession(state, amount, amex, multiple_reports)
    result = await budgets._load_paid_report_reimbursement_dates(
        session, report_ids=["report"]
    )
    assert (result == {"report": date(2026, 9, 24)}) if settled else (result == {})


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_key", [False, True])
async def test_assignment_rejects_a_movement_from_another_concept(
    monkeypatch, legacy_key
):
    class Session:
        def __init__(self):
            self.writes = []

        async def execute(self, statement, params=None):
            if str(statement).lstrip().startswith("INSERT"):
                self.writes.append(params)
            return Result(
                [
                    {
                        "tournament_id": "t",
                        "edition_year": 2026,
                        "version_status": "draft",
                    }
                ]
            )

    session = Session()
    monkeypatch.setattr(
        budgets,
        "build_budget_actuals_snapshot",
        AsyncMock(
            return_value={
                "movements": [
                    {
                        "document_id": "report",
                        "accounting_line_id": "posting",
                        "concept_key": "food",
                    }
                ],
            }
        ),
    )
    with pytest.raises(ValueError, match="no corresponde"):
        await budgets.assign_budget_movement_to_line(
            session,
            budget_version_id="version",
            budget_concept_id="hotel",
            budget_line_id="line",
            movement_key="document:report" if legacy_key else "accounting:posting",
            actor_empleado_id="actor",
            ensure_schema=False,
        )
    assert not session.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["draft", "frozen", None])
async def test_assignment_checks_target_version_and_persists_only_valid_movement(
    monkeypatch, state
):
    class Session:
        def __init__(self):
            self.writes = []

        async def execute(self, statement, params=None):
            if str(statement).lstrip().startswith("INSERT"):
                self.writes.append(params)
            return Result(
                [{"tournament_id": "t", "edition_year": 2026, "version_status": state}]
                if state
                else []
            )

    session = Session()
    monkeypatch.setattr(
        budgets,
        "build_budget_actuals_snapshot",
        AsyncMock(
            return_value={
                "movements": [
                    {
                        "document_id": "report",
                        "accounting_line_id": "posting",
                        "concept_key": "hotel",
                    }
                ],
            }
        ),
    )
    kwargs = dict(
        session=session,
        budget_version_id="version",
        budget_concept_id="hotel",
        budget_line_id="line",
        movement_key="accounting:posting",
        actor_empleado_id="actor",
        ensure_schema=False,
    )
    if state == "draft":
        await budgets.assign_budget_movement_to_line(**kwargs)
        assert len(session.writes) == 1
    else:
        with pytest.raises(ValueError, match="Partida inválida"):
            await budgets.assign_budget_movement_to_line(**kwargs)
        assert not session.writes


@pytest.mark.asyncio
async def test_full_paid_refund_changes_real_snapshot_and_not_report_state():
    class Session(SettlementSession):
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "budget_report_settlement_" in sql:
                return await super().execute(statement, params)
            if "WITH candidate_lines AS" in sql:
                return Result(
                    [
                        {
                            "document_id": "report",
                            "document_type": "INFORME",
                            "document_state": "aprobado",
                            "document_commitment_at": date(2026, 9, 1),
                            "concept_key": "food",
                            "accounting_line_id": "posting",
                            "cuenta_codigo": "5300-001",
                            "debe": 100,
                            "haber": 0,
                            "fecha_poliza": date(2026, 9, 1),
                        }
                    ]
                )
            return Result([])

    snapshot = await budgets.build_budget_actuals_snapshot(
        Session(), edition_year=2026, version_id="version"
    )
    movement = snapshot["movements"][0]
    assert movement["kind"] == "ledger_expense"
    assert movement["document_state"] == "aprobado"
    assert movement["month_number"] == budgets._budget_week_number(
        date(2026, 9, 24), 2026
    )
    assert movement["amount"] == 100


@pytest.mark.asyncio
async def test_paid_refund_without_result_posting_remains_pending_accounting():
    class Session(SettlementSession):
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "budget_report_settlement_" in sql:
                return await super().execute(statement, params)
            if "e.id::text AS expense_id" in sql and "JOIN LATERAL" in sql:
                return Result(
                    [
                        {
                            "document_id": "report",
                            "document_type": "INFORME",
                            "document_state": "aprobado",
                            "expense_id": "expense",
                            "budget_concept_id": "food",
                            "amount": 100,
                            "aprobado_en": date(2026, 9, 1),
                        }
                    ]
                )
            return Result([])

    snapshot = await budgets.build_budget_actuals_snapshot(
        Session(), edition_year=2026, version_id="version"
    )
    assert snapshot["movements"][0]["kind"] == "pending_accounting"
    assert snapshot["movements"][0]["month_number"] == budgets._budget_week_number(
        date(2026, 9, 24), 2026
    )


@pytest.mark.asyncio
async def test_no_account_and_no_tournament_does_not_invent_route():
    session = RoutingSession()
    route = await routing.resolve_and_snapshot_document_route(
        session, SimpleNamespace(id="report", empleado_id="employee", torneo_id=None)
    )
    assert route is None
    assert not session.writes
