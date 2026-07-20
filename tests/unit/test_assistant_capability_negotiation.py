from samchat.assistant.capability_negotiation import (
    CAPABILITY_INQUIRY,
    CAPABILITY_REGISTRY,
    PARTIALLY_SUPPORTED,
    SUPPORTED_WITH_INPUTS,
    CapabilitySpec,
    detect_capability_goal,
    evaluate_capability,
    render_capability_response,
)

RECEIPT_ACTIONS = [
    "expenses.create_personal_receipt_workflow",
    "expenses.create_third_party_receipt_workflow",
]


def test_detects_receipt_capability_question_without_treating_it_as_query() -> None:
    goal = detect_capability_goal(
        "si te subo un comprobante puedes hacerme la cuenta de gastos "
        "y la solicitud de pago?"
    )

    assert goal is not None
    assert goal.interaction_mode == CAPABILITY_INQUIRY
    assert goal.capability_id == "expenses.receipt_to_payment_request"


def test_detects_policy_to_coi_capability_without_phrase_specific_rule() -> None:
    goal = detect_capability_goal("Si te subo una póliza me la subes al COI")

    assert goal is not None
    assert goal.capability_id == "accounting.policy_to_coi"
    assert goal.destination_system == "coi"


def test_operational_pending_payment_query_is_not_capability_inquiry() -> None:
    assert detect_capability_goal("Qué pagos están pendientes") is None


def test_receipt_capability_requires_inputs_when_actions_and_flag_exist() -> None:
    goal = detect_capability_goal(
        "si te subo un comprobante puedes preparar la cuenta de gastos y el pago"
    )
    assert goal is not None

    result = evaluate_capability(
        goal,
        supported_actions=RECEIPT_ACTIONS,
        role="empleado",
        flags={"ASSISTANT_RECEIPT_WORKFLOW_WRITES_ENABLED": True},
    )

    assert result.status == SUPPORTED_WITH_INPUTS
    assert result.missing_fields == ("uploaded_document", "payment_subject_type")


def test_disabled_receipt_writes_reports_partial_support_without_internal_names() -> (
    None
):
    goal = detect_capability_goal(
        "si te subo un comprobante puedes preparar la cuenta de gastos y el pago"
    )
    assert goal is not None
    result = evaluate_capability(
        goal,
        supported_actions=RECEIPT_ACTIONS,
        role="admin",
        flags={"ASSISTANT_RECEIPT_WORKFLOW_WRITES_ENABLED": False},
    )

    rendered = render_capability_response(goal, result)

    assert result.status == PARTIALLY_SUPPORTED
    assert "extraer" in rendered
    assert "expenses." not in rendered
    assert "{" not in rendered


def test_coi_is_partial_when_terminal_connector_does_not_exist() -> None:
    goal = detect_capability_goal("Si te subo una póliza me la subes al COI")
    assert goal is not None

    result = evaluate_capability(
        goal,
        supported_actions=[],
        role="finanzas",
    )

    assert result.status == PARTIALLY_SUPPORTED
    assert "terminal_actions_unavailable" in result.reason_codes


def test_new_capability_is_detected_from_registry_without_router_branch(
    monkeypatch,
) -> None:
    monkeypatch.setitem(
        CAPABILITY_REGISTRY,
        "contracts.contract_to_archive",
        CapabilitySpec(
            capability_id="contracts.contract_to_archive",
            public_name="Archivar un contrato",
            input_artifact_types=("contract",),
            desired_outcome="archive_contract",
            destination_system="drive",
            input_aliases=("contrato",),
            outcome_aliases=("archivar",),
            destination_aliases=("drive",),
            required_actions=("contracts.archive",),
            available_steps=("extract_contract",),
            required_fields=("uploaded_document",),
            allowed_roles=("user",),
            requires_confirmation=True,
            implementation_complete=False,
        ),
    )

    goal = detect_capability_goal("Si te subo un contrato, puedes archivarlo en Drive?")

    assert goal is not None
    assert goal.capability_id == "contracts.contract_to_archive"
