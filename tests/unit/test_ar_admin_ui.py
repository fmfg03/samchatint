from __future__ import annotations

from samchat.ar.admin_ui import (
    render_ar_item_detail_html,
    render_ar_matching_workbench_html,
    render_ar_read_model_html,
)


def _payload() -> dict:
    return {
        "summary": {
            "expected_income_total": 1200,
            "linked_income_total": 500,
            "issued_unlinked_total": 250,
            "collection_gap_count": 2,
            "matching_gap_count": 1,
        },
        "expected_income": [
            {
                "ar_item_id": "expected:line-1",
                "tournament_name": "Copa <Nido>",
                "status": "planned",
                "phase": "Nacional",
                "concept_name": "Patrocinio",
                "expected_income_amount": 1200,
                "linked_income_amount": 500,
                "collection_status": "collection_unknown",
            }
        ],
        "issued_linked": [
            {
                "ar_item_id": "linked:link-1",
                "cfdi_uuid": "uuid-1",
                "concept_name": "Patrocinio",
                "payer_name": "Cliente SA",
                "payer_rfc": "CLI010101AAA",
                "issued_amount": 500,
                "recognized_income_date": "2026-01-15T00:00:00",
                "collection_status": "collection_unknown",
            }
        ],
        "issued_unlinked": [
            {
                "ar_item_id": "candidate:cfdi-2",
                "cfdi_uuid": "uuid-2",
                "issued_date": "2026-02-01T00:00:00",
                "payer_name": "Cliente Dos",
                "payer_rfc": "CLI020202BBB",
                "issued_amount": 250,
                "collection_status": "collection_unknown",
            }
        ],
        "collection_gaps": [
            {
                "source": "issued_linked",
                "item_id": "linked:link-1",
                "payer_name": "Cliente SA",
                "payer_rfc": "CLI010101AAA",
                "amount": 500,
                "collection_status": "collection_unknown",
            }
        ],
        "matching_gaps": [
            {
                "severity": "medium",
                "source": "issued_unlinked",
                "item_id": "candidate:cfdi-2",
                "reason": "missing_budget_income_link",
            }
        ],
    }


def test_render_ar_read_model_html_includes_expected_sections():
    html = render_ar_read_model_html(_payload())

    assert "Cuentas por Cobrar" in html
    assert "Gaps accionables" in html
    assert "Cartera operativa" in html
    assert "Ingreso esperado" in html
    assert "CFDI ligado" in html
    assert "CFDI PSP no ligado" in html
    assert "Gaps de cobranza" in html
    assert "Gaps de matching" in html
    assert "collection_unknown" in html
    assert "Descargar Excel CxC" in html


def test_render_ar_read_model_html_includes_actionable_gap_queue():
    html = render_ar_read_model_html(
        _payload(),
        base_url=(
            "/admin/finanzas/cuentas-por-cobrar?"
            "budget_version_id=version-1&estado=todos"
        ),
        return_to=(
            "/admin/finanzas/cuentas-por-cobrar?"
            "budget_version_id=version-1&estado=todos"
        ),
    )

    assert "Pendientes CxC priorizados" in html
    assert "cfdi_sin_partida" in html
    assert "Vincular CFDI a partida de ingreso" in html
    assert "/admin/finanzas/cuentas-por-cobrar/item/candidate%3Acfdi-2" in html
    assert "return_to=%2Fadmin%2Ffinanzas%2Fcuentas-por-cobrar" in html


def test_render_ar_read_model_html_includes_sortable_operational_columns():
    html = render_ar_read_model_html(
        _payload(),
        base_url="/admin/finanzas/cuentas-por-cobrar?estado=todos",
    )

    assert "sort_by=tournament_name" in html
    assert "sort_by=balance_amount" in html
    assert "Presupuestado sin CFDI" in html
    assert "Saldo" in html


def test_render_ar_read_model_html_links_to_detail_with_return_context():
    html = render_ar_read_model_html(
        _payload(),
        base_url=(
            "/admin/finanzas/cuentas-por-cobrar?"
            "budget_version_id=version-1&estado=todos"
        ),
        return_to=(
            "/admin/finanzas/cuentas-por-cobrar?"
            "budget_version_id=version-1&estado=todos"
        ),
    )

    assert "/admin/finanzas/cuentas-por-cobrar/item/expected%3A" in html
    assert "return_to=%2Fadmin%2Ffinanzas%2Fcuentas-por-cobrar" in html
    assert "budget_version_id=version-1" in html


def test_render_ar_read_model_html_escapes_values():
    html = render_ar_read_model_html(_payload())

    assert "Copa &lt;Nido&gt;" in html
    assert "Copa <Nido>" not in html


def test_render_ar_read_model_html_does_not_confirm_collection():
    html = render_ar_read_model_html(_payload()).lower()

    forbidden_terms = [
        "cobrado confirmado",
        "pago confirmado",
        "saldo pendiente",
        "outstanding confirmado",
    ]
    assert all(term not in html for term in forbidden_terms)


def test_render_ar_read_model_html_handles_empty_payload():
    html = render_ar_read_model_html({"summary": {}})

    assert "Sin pendientes CxC accionables" in html
    assert "Sin ingreso esperado" in html
    assert "Sin CFDI de ingreso ligado" in html
    assert "Sin CFDI PSP candidatos" in html


def test_render_ar_matching_workbench_html_includes_candidate_notice():
    html = render_ar_matching_workbench_html(
        {
            "summary": {"candidate_match_count": 1},
            "items": [
                {
                    "ar_item_id": "linked:1",
                    "source": "issued_linked",
                    "payer_name": "Cliente SA",
                    "payer_rfc": "CLI010101AAA",
                    "amount": 100,
                    "status": "candidate_match",
                    "reason": "amount_and_identity_candidate",
                    "candidate_evidence": [
                        {
                            "bank_movement_id": "bank-1",
                            "bank_amount": 100,
                            "bank_date": "2026-01-16T00:00:00",
                            "signals": ["amount", "rfc"],
                        }
                    ],
                }
            ],
            "unmatched_bank_inflows": [],
        }
    )

    assert "Pre-matching AR" in html
    assert "Evidencia candidata; no prueba cobranza" in html
    assert "candidate_match" in html
    assert "bank-1" in html


def test_render_ar_matching_workbench_html_does_not_confirm_collection():
    html = render_ar_matching_workbench_html(
        {"summary": {}, "items": [], "unmatched_bank_inflows": []}
    ).lower()

    assert "cobranza confirmada" not in html
    assert "cobrado confirmado" not in html


def test_render_ar_matching_workbench_html_hides_match_actions_without_permission():
    html = render_ar_matching_workbench_html(
        {
            "summary": {},
            "items": [
                {
                    "ar_item_id": "linked:1",
                    "source": "issued_linked",
                    "amount": 100,
                    "status": "candidate_match",
                    "candidate_evidence": [{"bank_movement_id": "bank-1"}],
                }
            ],
            "accepted_matches": [{"id": "match-1"}],
            "unmatched_bank_inflows": [],
        },
        can_operate_matches=False,
    )

    assert "sin permiso operativo" in html
    assert "Aceptar match" not in html
    assert "Revertir" not in html


def test_render_ar_item_detail_html_shows_core_sections_and_gaps():
    item = {
        "ar_item_id": "linked:1",
        "source": "issued_linked",
        "operational_status": "Cobranza desconocida",
        "payer_name": "Cliente SA",
        "payer_rfc": "CLI010101AAA",
        "cfdi_uuid": "uuid-1",
        "issued_date": "2026-01-15",
        "issued_amount": 100,
        "linked_income_amount": 100,
        "collected_amount": 0,
        "balance_amount": None,
        "tournament_name": "Copa",
        "tournament_id": "tournament-1",
        "budget_version_id": "version-1",
        "phase": "Nacional",
        "concept_name": "Patrocinio",
        "collection_status": "collection_unknown",
    }

    html = render_ar_item_detail_html(
        item,
        {"matching_gaps": [], "collection_gaps": []},
        return_to="/admin/finanzas/cuentas-por-cobrar?estado=todos",
        can_operate_matches=True,
    )

    assert "Detalle de Cuentas por Cobrar" in html
    assert "CFDI" in html
    assert "Presupuesto" in html
    assert "Cobranza" in html
    assert "Gaps / Siguientes Acciones" in html
    assert "La cobranza no está comprobada" in html
    assert "Ir a pre-matching" in html
    assert "#prematching" in html
    assert "/admin/presupuestos/torneo/tournament-1" in html
    assert "budget_view=income" in html
    assert "#presupuesto-ingresos" in html
    assert "/admin/contabilidad/cuentas-por-cobrar" in html


def test_render_ar_item_detail_html_hides_mutable_actions_without_permission():
    html = render_ar_item_detail_html(
        {"ar_item_id": "candidate:1", "source": "issued_unlinked"},
        {"matching_gaps": [], "collection_gaps": []},
        return_to="/admin/finanzas/cuentas-por-cobrar",
        can_operate_matches=False,
    )

    assert "Sin permiso operativo" in html
    assert "Acciones operativas habilitadas" not in html


def test_render_ar_matching_workbench_has_stable_prematching_anchor():
    html = render_ar_matching_workbench_html(
        {"summary": {}, "items": [], "unmatched_bank_inflows": []}
    )

    assert 'id="prematching"' in html
