from types import SimpleNamespace

from devnous.gastos.services.access_control_service import path_to_tool
from devnous.gastos.services.authorization_strategy_service import (
    employee_matches_any_required_role,
    resolve_authorization_strategy,
)


def role_keys(decision):
    return decision.required_role_keys


def test_operaciones_supplier_transfer_over_100k_requires_odilon_and_dg():
    decision = resolve_authorization_strategy(
        area="OPERACIONES",
        erogation_type="supplier_transfer",
        amount_mxn="100000.01",
    )

    assert decision.rule is not None
    assert decision.rule.key == "ops_supplier_transfer_gt_100k"
    assert role_keys(decision) == ("director_operaciones", "dg")
    assert decision.first_approver_roles[0].label == "Director de Operaciones"
    assert decision.second_approver_roles[0].label == "DG"


def test_operaciones_no_deductible_requires_odilon_and_dayf():
    decision = resolve_authorization_strategy(
        area="Operaciones",
        erogation_type="no_deductible",
        amount_mxn="500",
        has_invoice=False,
    )

    assert decision.rule is not None
    assert decision.rule.key == "ops_no_deductible"
    assert role_keys(decision) == ("director_operaciones", "dayf")


def test_goat_supplier_transfer_over_10k_requires_olof_and_dg():
    decision = resolve_authorization_strategy(
        area="Mercadotecnia",
        erogation_type="supplier_transfer",
        amount_mxn="10001",
    )

    assert decision.rule is not None
    assert decision.rule.key == "goat_supplier_transfer_gt_10k"
    assert role_keys(decision) == ("dgoat", "dg")


def test_admin_fixed_asset_requires_luis_angel_and_dg():
    decision = resolve_authorization_strategy(
        area="Administracion",
        erogation_type="fixed_asset",
        amount_mxn="1",
    )

    assert decision.rule is not None
    assert decision.rule.key == "admin_fixed_asset"
    assert role_keys(decision) == ("dayf", "dg")


def test_pending_advance_escalation_rule_takes_precedence_when_flagged():
    normal = resolve_authorization_strategy(
        area="Operaciones",
        erogation_type="tournament_advance",
        amount_mxn="9000",
        has_pending_advance=False,
    )
    pending = resolve_authorization_strategy(
        area="Operaciones",
        erogation_type="tournament_advance",
        amount_mxn="9000",
        has_pending_advance=True,
    )

    assert normal.rule is not None
    assert normal.rule.key == "ops_tournament_advance_le_10k"
    assert pending.rule is not None
    assert pending.rule.key == "ops_tournament_advance_gt_10k_or_pending"
    assert role_keys(pending) == ("director_operaciones",)


def test_employee_name_mapping_matches_customer_roles():
    ops = resolve_authorization_strategy(
        area="Operaciones",
        erogation_type="supplier_transfer",
        amount_mxn="120000",
    )
    no_deducible = resolve_authorization_strategy(
        area="Operaciones",
        erogation_type="no_deductible",
        amount_mxn="100",
        has_invoice=False,
    )
    goat = resolve_authorization_strategy(
        area="Mercadotecnia",
        erogation_type="supplier_transfer",
        amount_mxn="12000",
    )

    assert employee_matches_any_required_role(
        SimpleNamespace(
            nombre="JOSE ODILON TRUJILLO MACEDO",
            correo="otrujillo@plataformasports.com",
        ),
        ops,
    )
    assert employee_matches_any_required_role(
        SimpleNamespace(
            nombre="FEDERICO GONZALEZ Y VEGA",
            correo="fgv@plataformasports.com",
        ),
        ops,
    )
    assert employee_matches_any_required_role(
        SimpleNamespace(
            nombre="LUIS ANGEL OROZCO COLIN",
            correo="laorozco@plataformasports.com",
        ),
        no_deducible,
    )
    assert employee_matches_any_required_role(
        SimpleNamespace(nombre="Olof", correo="olof@goatmkt.com"),
        goat,
    )


def test_authorization_strategy_board_is_registered_in_access_control():
    tool = path_to_tool("/admin/estrategias-autorizacion")

    assert tool is not None
    assert tool.key == "configuracion.estrategias_autorizacion"
    assert "administrar" in tool.actions


def test_unknown_strategy_falls_back_without_enforcing():
    decision = resolve_authorization_strategy(
        area="Operaciones",
        erogation_type="unknown_customer_category",
        amount_mxn="100",
    )

    assert decision.rule is None
    assert decision.required_role_keys == ()
    assert decision.fallback_reason == "no_matching_authorization_strategy_rule"
