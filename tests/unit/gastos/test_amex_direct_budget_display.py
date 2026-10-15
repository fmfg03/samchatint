"""Budget detail preserves missing fiscal evidence and the assignment owner."""

from devnous.gastos.routes.admin_budget_ui import _render_budget_movement_details


def test_direct_pending_base_is_not_zero_or_a_legacy_assignment_form():
    html = _render_budget_movement_details(
        [{"id": "line"}],
        [
            {
                "expense_id": "expense",
                "source_kind": "amex_direct",
                "concept_key": "category",
                "budget_line_id": "line",
                "kind": "pending_fiscal_evidence",
                "amount": None,
            }
        ],
        version_id="version",
        tournament_key="project",
        edition_year=2026,
        phase_filter=None,
        can_edit=True,
    )
    assert "Pendiente fiscal" in html
    assert "Base fiscal pendiente de evidencia" in html
    assert "$0.00" not in html
    assert "/documentos/control-presupuestal/amex-directo" in html
    assert "assign-existing" not in html


def test_evidenced_zero_stays_zero():
    html = _render_budget_movement_details(
        [],
        [{"concept_key": "category", "kind": "pending_accounting", "amount": 0}],
        version_id="version",
        tournament_key="project",
        edition_year=2026,
        phase_filter=None,
        can_edit=False,
    )
    assert "$0.00" in html
