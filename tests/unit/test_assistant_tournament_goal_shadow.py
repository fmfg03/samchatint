from dataclasses import FrozenInstanceError, replace

import pytest

from samchat.assistant.tournament_goal_shadow import (
    CONTRACT_VERSION,
    EXECUTION_STATUS,
    ALLOWED_VISIBILITY_AREAS,
    TournamentSnapshot,
    ValidationFinding,
    build_tournament_business_diff,
    build_tournament_goal_shadow,
    canonical_json,
    canonical_sha256,
    clone_tournament_draft,
    validate_tournament_draft,
)


def _source(**updates):
    payload = {
        "id": "tournament-2026",
        "name": "Copa 2026",
        "description": "Edición nacional",
        "active": True,
        "display_order": 4,
        "cuenta_contable_relacionada": "500-01",
        "etapas": ["Estatal", "Nacional"],
        "categorias": ["Juvenil", "Libre"],
        "form_visibility_areas": ["Operaciones"],
        "updated_at": "2026-07-01T12:00:00+00:00",
    }
    payload.update(updates)
    return TournamentSnapshot.from_mapping(payload)


def test_canonical_json_and_hash_ignore_mapping_insertion_order():
    first = {"b": [2, 1], "a": {"z": True, "x": None}}
    second = {"a": {"x": None, "z": True}, "b": [2, 1]}

    assert canonical_json(first) == canonical_json(second)
    assert canonical_sha256(first) == canonical_sha256(second)
    assert len(canonical_sha256(first)) == 64


def test_snapshot_accepts_canonical_local_model_field_names():
    source = _source()

    assert source.source_namespace == "gastos.tournaments"
    assert source.tournament_id == "tournament-2026"
    assert source.accounting_account == "500-01"
    assert source.stages == ("Estatal", "Nacional")
    assert source.categories == ("Juvenil", "Libre")
    assert source.to_dict()["snapshot_hash"] == source.snapshot_hash
    assert source.snapshot_hash.startswith("sha256:")


def test_source_authority_hash_is_validated_and_binds_every_derived_hash():
    authority_hash = "sha256:" + "a" * 64
    source = _source(source_hash=authority_hash)
    shadow = build_tournament_goal_shadow(source, requested_name="Copa 2027")

    assert source.source_authority_hash == authority_hash
    assert source.snapshot_hash == authority_hash
    assert shadow.draft.base_snapshot_hash == authority_hash
    assert shadow.business_diff.base_snapshot_hash == authority_hash
    assert shadow.to_dict()["source"]["snapshot_hash"] == authority_hash
    assert authority_hash in canonical_json(shadow.to_dict())

    other = build_tournament_goal_shadow(
        _source(source_hash="sha256:" + "b" * 64),
        requested_name="Copa 2027",
    )
    assert shadow.draft.draft_hash != other.draft.draft_hash
    assert shadow.work_product_hash != other.work_product_hash


def test_source_authority_hash_rejects_non_verifiable_values():
    with pytest.raises(ValueError, match="Source authority hash"):
        _source(source_hash="sha256:not-a-digest")


def test_snapshot_requires_identity_and_rejects_non_sequence_lists():
    with pytest.raises(ValueError, match="id is required"):
        TournamentSnapshot.from_mapping({"name": "Copa"})
    with pytest.raises(ValueError, match="name is required"):
        TournamentSnapshot.from_mapping({"id": "one"})
    with pytest.raises(TypeError, match="must be sequences"):
        TournamentSnapshot.from_mapping(
            {"id": "one", "name": "Copa", "etapas": {"bad": "shape"}}
        )


def test_clone_is_inert_and_copies_only_configuration_fields():
    source = _source()
    draft = clone_tournament_draft(
        source,
        requested_name="Copa 2027",
        overrides={"categories": ["Juvenil", "Mayor"], "active": False},
    )

    assert draft.name == "Copa 2027"
    assert draft.categories == ("Juvenil", "Mayor")
    assert draft.base_tournament_id == source.tournament_id
    assert draft.base_snapshot_hash == source.snapshot_hash
    assert draft.execution_status == EXECUTION_STATUS
    assert draft.operational_writes_allowed is False
    assert draft.schema_version == CONTRACT_VERSION
    assert "id" not in draft.to_dict()


def test_clone_rejects_unapproved_fields_instead_of_leaking_authority():
    with pytest.raises(ValueError, match="Unsupported tournament draft fields"):
        clone_tournament_draft(
            _source(),
            requested_name="Copa 2027",
            overrides={"id": "forged", "created_at": "now"},
        )


def test_validation_detects_stale_base_invalid_name_duplicates_and_blanks():
    source = _source()
    draft = clone_tournament_draft(
        source,
        requested_name="Copa 2026",
        overrides={"categories": ["Juvenil", "juvenil", ""]},
    )
    stale_draft = replace(draft, base_snapshot_hash="0" * 64)
    validation = validate_tournament_draft(source, stale_draft)
    codes = [finding.code for finding in validation.findings]

    assert validation.valid is False
    assert validation.error_count == 4
    assert codes == [
        "base_snapshot_stale",
        "name_matches_base",
        "categories_blank_value",
        "categories_duplicate_value",
    ]


def test_validation_allows_missing_optional_taxonomies_with_warnings():
    source = _source(etapas=None, categorias=None)
    draft = clone_tournament_draft(source, requested_name="Copa 2027")
    validation = validate_tournament_draft(source, draft)

    assert validation.valid is True
    assert validation.error_count == 0
    assert validation.warning_count == 2
    assert [item.code for item in validation.findings] == [
        "stages_missing",
        "categories_missing",
    ]


def test_validation_rejects_negative_order_and_unknown_visibility_areas():
    source = _source()
    draft = clone_tournament_draft(
        source,
        requested_name="Copa 2027",
        overrides={
            "display_order": -1,
            "visibility_areas": ["operaciones", "Recursos Humanos"],
        },
    )
    validation = validate_tournament_draft(source, draft)

    assert validation.valid is False
    assert [item.code for item in validation.findings] == [
        "display_order_negative",
        "visibility_area_invalid",
    ]
    assert tuple(ALLOWED_VISIBILITY_AREAS) == (
        "Finanzas",
        "Mercadotecnia",
        "Operaciones",
        "Dirección",
    )


def test_unavailable_components_generate_deterministic_warnings_and_waiting_input():
    source = _source(unavailable_components=["media", "matches_and_schedule", "media"])
    shadow = build_tournament_goal_shadow(source, requested_name="Copa 2027")

    assert source.unavailable_components == ("matches_and_schedule", "media")
    assert shadow.validation.valid is True
    assert [item.to_dict() for item in shadow.validation.findings] == [
        {
            "code": "source_component_unavailable",
            "severity": "warning",
            "field": "source.matches_and_schedule",
            "message": (
                "La fuente local no expone todavía el componente "
                "matches_and_schedule."
            ),
        },
        {
            "code": "source_component_unavailable",
            "severity": "warning",
            "field": "source.media",
            "message": "La fuente local no expone todavía el componente media.",
        },
    ]
    assert shadow.missing_information == (
        "source_component:matches_and_schedule",
        "source_component:media",
    )
    assert shadow.plan.steps[-1].status == "waiting_input"


def test_business_diff_is_ordered_labeled_and_omits_unchanged_fields():
    source = _source()
    draft = clone_tournament_draft(
        source,
        requested_name="Copa 2027",
        overrides={
            "description": None,
            "visibility_areas": ["Operaciones", "Dirección"],
        },
    )
    diff = build_tournament_business_diff(source, draft)

    assert [entry.field for entry in diff.entries] == [
        "name",
        "description",
        "visibility_areas",
    ]
    assert [entry.change_type for entry in diff.entries] == [
        "changed",
        "removed",
        "changed",
    ]
    assert diff.entries[0].label == "Nombre"
    assert diff.base_snapshot_hash == source.snapshot_hash
    assert diff.draft_hash == draft.draft_hash


def test_complete_work_product_has_visible_plan_hash_and_no_execution_authority():
    shadow = build_tournament_goal_shadow(
        _source(),
        requested_name="Copa 2027",
        overrides={"categories": ["Juvenil", "Mayor"]},
        goal="Crear la Copa 2027 desde la edición anterior",
    )
    payload = shadow.to_dict()

    assert shadow.validation.valid is True
    assert payload["contract_version"] == CONTRACT_VERSION
    assert payload["execution_status"] == EXECUTION_STATUS
    assert payload["operational_writes_allowed"] is False
    assert payload["blocked_capabilities"] == [
        "operational_writes",
        "route_execution",
        "external_notifications",
    ]
    assert [step["status"] for step in payload["plan"]["steps"]] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "pending",
    ]
    assert len(payload["work_product_hash"]) == 64
    assert payload["work_product_hash"] == shadow.work_product_hash
    assert (
        shadow.to_dict()
        == build_tournament_goal_shadow(
            _source(),
            requested_name="Copa 2027",
            overrides={"categories": ["Juvenil", "Mayor"]},
            goal="Crear la Copa 2027 desde la edición anterior",
        ).to_dict()
    )


def test_invalid_work_product_blocks_review_and_lists_missing_information():
    shadow = build_tournament_goal_shadow(_source(), requested_name="")

    assert shadow.validation.valid is False
    assert shadow.plan.steps[-1].status == "blocked"
    assert shadow.missing_information == ("name",)


def test_external_name_uniqueness_finding_is_folded_into_plan_without_orm():
    duplicate = ValidationFinding(
        code="name_already_exists",
        severity="error",
        field="name",
        message="Ya existe un torneo local con ese nombre.",
    )
    shadow = build_tournament_goal_shadow(
        _source(),
        requested_name="Copa 2027",
        additional_findings=[duplicate],
    )

    assert shadow.validation.valid is False
    assert shadow.validation.findings[-1] == duplicate
    assert shadow.plan.steps[-1].status == "blocked"
    assert shadow.missing_information == ("name",)


def test_external_findings_reject_unknown_shapes_and_severities():
    with pytest.raises(TypeError, match="must be ValidationFinding"):
        build_tournament_goal_shadow(
            _source(),
            requested_name="Copa 2027",
            additional_findings=[{"code": "bad"}],
        )
    with pytest.raises(ValueError, match="severity"):
        build_tournament_goal_shadow(
            _source(),
            requested_name="Copa 2027",
            additional_findings=[
                ValidationFinding("bad", "notice", "name", "Bad severity")
            ],
        )


def test_contracts_are_frozen_and_canonical_json_rejects_unknown_types():
    source = _source()
    with pytest.raises(FrozenInstanceError):
        source.name = "mutated"
    with pytest.raises(TypeError, match="Unsupported canonical JSON value"):
        canonical_json({"bad": object()})
