"""Reconciliation controls use canonical assignment truth and existing targets."""

from uuid import uuid4

import pytest

import devnous.gastos  # noqa: F401
from devnous.gastos.routes.admin_budget_ui import (
    _render_budget_movement_details,
    render_budget_partida_matrix,
)

CID, LINE, TID, VERSION = (str(uuid4()) for _ in range(4))


def line(**kwargs):
    return {
        "id": LINE,
        "budget_concept_id": CID,
        "concept_name": "Hospedaje",
        "phase": "Fase Nacional",
        "line_direction": "expense",
        "budget_version_id": VERSION,
        "tournament_id": TID,
        **kwargs,
    }


def movement(**kwargs):
    return {
        "movement_key": "expense:" + str(uuid4()),
        "concept_key": CID,
        "budget_concept_name": "Hospedaje",
        "effective_phase": "Fase Nacional",
        "kind": "pending_accounting",
        "amount": 116,
        "reconciliation_status": "unassigned",
        "budget_line_id": None,
        "document_reference": "S-26000450",
        **kwargs,
    }


def render(mov=None, *, visible=None, targets=None, **kwargs):
    return _render_budget_movement_details(
        [line()] if visible is None else visible,
        [movement()] if mov is None else [mov],
        version_id=VERSION,
        tournament_key="LTTB",
        edition_year=2026,
        phase_filter="Fase Nacional",
        can_edit=False,
        can_reconcile=kwargs.pop("can_reconcile", True),
        active_concept_ids=kwargs.pop("active_concept_ids", {CID}),
        reconciliation_lines=[line()] if targets is None else targets,
        reconciliation_csrf_token="csrf-from-signed-session",
        tournament_id=TID,
        **kwargs,
    )


def test_approved_budget_offers_existing_target_review_without_economic_fields():
    html = render()
    assert "/conciliacion/preview" in html
    assert "Revisar conciliación" in html
    assert "Pendiente de vincular" in html
    assert "Pendiente de contabilización" in html
    assert 'name="budget_line_id"' in html and LINE in html
    assert 'name="budget_amount"' not in html
    assert 'name="phase"' not in html
    assert "assign-existing" not in html


def test_phase_discriminates_two_raw_lines_without_choosing_by_amount():
    html = render(targets=[line(), line(id=str(uuid4()), phase="Fase Estatal")])
    assert 'name="budget_line_id"' in html
    assert f'value="{LINE}"' in html


@pytest.mark.parametrize(
    "targets,active,expected",
    [
        ([], {CID}, "Sin línea existente elegible"),
        ([line(), line(id=str(uuid4()))], {CID}, "Más de una partida compatible"),
        ([line()], set(), "Partida retirada o no disponible"),
    ],
)
def test_blocked_or_ambiguous_cases_are_informational(targets, active, expected):
    html = render(targets=targets, active_concept_ids=active)
    assert expected in html
    assert "/conciliacion/preview" not in html


def test_missing_phase_is_not_inferred_from_a_single_line():
    html = render(movement(effective_phase=""))
    assert "Fase pendiente de clasificación" in html
    assert "/conciliacion/preview" not in html


def test_readers_see_reason_without_a_mutation_control():
    html = render(can_reconcile=False)
    assert "Pendiente de vincular" in html
    assert "/conciliacion/preview" not in html
    assert 'name="reason"' not in html


@pytest.mark.parametrize(
    "amex",
    [
        {"source_kind": "amex_direct"},
        {"origen": "amex_direct"},
        {"budget_assignment_id": "special-proof"},
    ],
)
def test_amex_remains_in_its_owner(amex):
    html = render(movement(**amex))
    assert "/documentos/control-presupuestal/amex-directo" in html
    assert "/conciliacion/preview" not in html


def test_assignment_outside_visible_filter_is_not_an_unassigned_movement():
    html = render(
        movement(budget_line_id=LINE, reconciliation_status="assigned"),
        visible=[],
    )
    assert "Partida asignada fuera del filtro actual" in html
    assert "Sin presupuesto autorizado" not in html
    assert "/conciliacion/preview" not in html
    assert "assign-existing" not in html


def test_missing_canonical_unassigned_status_does_not_grant_new_action():
    html = render(movement(reconciliation_status=None))
    assert "/conciliacion/preview" not in html


def test_financial_diagnostics_remain_separate_and_escape_data():
    html = render(
        movement(
            kind="pending_fiscal_evidence",
            amount=None,
            budget_concept_name="<script>unsafe</script>",
            reason='<img src=x onerror="bad">',
        )
    )
    assert "Base fiscal pendiente de evidencia" in html
    assert "Pendiente fiscal" in html and "$0.00" not in html
    assert "<script>" not in html and "<img src=" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    assert "/conciliacion/preview" not in html


def test_matrix_forwards_reconciliation_context_and_preserves_amounts():
    html = render_budget_partida_matrix(
        [line(budget_amount=500)],
        plan_map={},
        actuals_map={},
        actual_movements=[movement()],
        version_id=VERSION,
        tournament_key="LTTB",
        can_edit=False,
        can_reconcile=True,
        edition_year=2026,
        matrix_mode="expenses",
        reconciliation_lines=[line()],
        active_concept_ids={CID},
        reconciliation_csrf_token="csrf",
        tournament_id=TID,
    )
    assert "/conciliacion/preview" in html
    assert "116.00" in html
    assert "500.00" in html


def test_income_matrix_has_no_expense_reconciliation_action():
    html = render_budget_partida_matrix(
        [],
        plan_map={},
        actuals_map={},
        actual_movements=[movement()],
        version_id=VERSION,
        tournament_key="LTTB",
        can_edit=False,
        can_reconcile=True,
        matrix_mode="income",
        active_concept_ids={CID},
        reconciliation_lines=[line()],
        reconciliation_csrf_token="csrf",
        tournament_id=TID,
    )
    assert "/conciliacion/preview" not in html


@pytest.mark.parametrize(
    "kind,source_field,real,committed",
    [
        ("ledger_expense", "accounting_line_id", 123.45, 0),
        ("commitment", "document_id", 0, 123.45),
        ("pending_accounting", "expense_id", 0, 0),
    ],
)
def test_real_projection_html_measures_and_review_xlsx_line_metadata(
    kind,
    source_field,
    real,
    committed,
):
    """Use the canonical projection, without faking its destination or measures.

    Review XLSX exports line metadata, not movement destinations/measures. There
    is no budget-movement PDF export; executive PDF has a different report grain.
    Those unavailable per-movement export comparisons are deliberately not claimed.
    """
    from copy import deepcopy
    from io import BytesIO

    from openpyxl import load_workbook

    from samchat.budgets import service
    from samchat.budgets.exporter import generate_budget_review_xlsx

    source_id = str(uuid4())
    source = movement(
        **{source_field: source_id},
        kind=kind,
        amount=123.45,
        month_number=40,
    )
    # The canonical key is derived from the source identity, never the UI input.
    source.pop("movement_key")
    before = deepcopy(source)
    destination = line(
        budget_amount=500,
        account_code_final="5300-012-018",
        tournament_code="LTTB",
        tournament_name="Liga",
    )
    other = line(
        id=str(uuid4()),
        phase="Fase Estatal",
        budget_amount=777,
        account_code_final="5300-012-031",
        tournament_code="LTTB",
        tournament_name="Liga",
    )
    lines = [destination, other]
    store = service._monthly_actual_store()
    service._project_explicit_movement_assignments(
        movements=[source],
        store=store,
        assignment_rows=[
            {
                "assignment_id": str(uuid4()),
                "movement_key": service.budget_movement_key(source),
                "budget_concept_id": CID,
                "budget_line_id": LINE,
            }
        ],
        line_rows=lines,
        confirmed={},
    )
    assert source["budget_line_id"] == LINE
    assert source["reconciliation_status"] == "assigned"
    assert source["kind"] == before["kind"]
    assert source["amount"] == before["amount"]
    assert source[source_field] == before[source_field]
    bucket = store.get(LINE, {}).get(40, {})
    assert bucket.get("real_expense_cash", 0) == real
    assert bucket.get("committed_unpaid", 0) == committed
    assert other["id"] not in store  # Same concept, different phase, no leakage.
    assert "__unassigned__" not in store

    html = render_budget_partida_matrix(
        lines,
        plan_map={},
        actuals_map=store,
        actual_movements=[source],
        version_id=VERSION,
        tournament_key="LTTB",
        can_edit=False,
        edition_year=2026,
        matrix_mode="expenses",
        show_committed=True,
    )
    assert 'id="budget-amount-' + LINE + '"' in html
    assert "Fase Nacional" in html and "5300-012-018" in html
    assert "123.45" in html
    assert "Sin línea presupuestal" not in html
    if kind == "pending_accounting":
        assert "Pendiente de contabilización" in html
        assert "Partida asignada fuera del filtro actual" not in html

    # The existing review workbook receives the same unchanged authorized lines.
    # Its line sheet can verify phase/account/authorized amount, not source grain.
    workbook = load_workbook(
        BytesIO(
            generate_budget_review_xlsx(
                snapshot={},
                versions=[],
                lines=lines,
            )
        )
    )
    rows = list(workbook["Lineas"].iter_rows(values_only=True))
    assert rows[1][0:5] == (
        "LTTB",
        "Liga",
        "Fase Nacional",
        "Hospedaje",
        "5300-012-018",
    )
    assert rows[1][7] == destination["budget_amount"] == 500
    assert rows[2][0:5] == (
        "LTTB",
        "Liga",
        "Fase Estatal",
        "Hospedaje",
        "5300-012-031",
    )
    assert rows[2][7] == other["budget_amount"] == 777
