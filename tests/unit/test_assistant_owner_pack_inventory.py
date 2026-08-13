from samchat.assistant.owner_pack_inventory import (
    OWNER_PACK_FIELD_SCHEMA_PREPARED,
    OWNER_PACK_INVENTORY_ONLY,
    OWNER_PACK_SOURCE_NOT_QUERIED,
    build_owner_pack_inventory_report,
    build_owner_pack_surface_inventory,
    owner_pack_inventory_contains_execution_claim,
)


def _fields(surface):
    return [field for section in surface.sections for field in section.fields]


def _field(surface, field_name: str):
    for field in _fields(surface):
        if field.field == field_name:
            return field
    raise AssertionError(f"missing field {field_name}")


def test_owner_pack_inventory_lists_all_prepared_surfaces_and_is_read_only() -> None:
    report = build_owner_pack_inventory_report()

    assert report.inventory_id == "owner_pack_inventory_v1"
    assert report.surface_count == 4
    assert {surface.surface_id for surface in report.surfaces} == {
        "entity_folder",
        "national_phase_folder",
        "marketing_activation_report",
        "work_plan_or_query",
    }
    assert report.field_count >= 30
    assert report.execution_status == "not_executed"
    assert report.writes_attempted == 0
    assert report.side_effects_detected == 0
    assert report.audit_language == OWNER_PACK_INVENTORY_ONLY
    assert report.safety_summary["writes_enabled"] is False
    assert report.safety_summary["live_queries_executed"] == 0
    assert owner_pack_inventory_contains_execution_claim(report) is False


def test_entity_inventory_maps_owner_fields_to_evidence_sources() -> None:
    surface = build_owner_pack_surface_inventory("entity_folder")

    assert surface.label == "Carpetas por entidad"
    assert surface.folder_type == "entity_folder_proposal"
    assert surface.field_count >= 10

    expected_teams = _field(surface, "expected_teams")
    assert expected_teams.label == "Equipos esperados por categoria/genero"
    assert expected_teams.evidence_type == "team"
    assert "equipos" in " ".join(expected_teams.canonical_sources)
    assert expected_teams.status == OWNER_PACK_FIELD_SCHEMA_PREPARED
    assert expected_teams.live_query_status == OWNER_PACK_SOURCE_NOT_QUERIED
    assert expected_teams.value_required_from_live_data is True

    payments = _field(surface, "operator_payments")
    assert payments.evidence_type == "finance"
    assert any("payment run" in source for source in payments.canonical_sources)


def test_national_phase_inventory_includes_operations_finance_and_incidents() -> None:
    surface = build_owner_pack_surface_inventory("national_phase_folder")

    assert {section.section_id for section in surface.sections} >= {
        "operations",
        "finance",
        "marketing",
    }
    assert _field(surface, "contracted_hotels_bed_nights").evidence_type == "document"
    assert _field(surface, "hotel_payments").evidence_type == "finance"
    assert _field(surface, "accidents_with_transfers").evidence_type == (
        "medical/event_incident"
    )
    assert "incidencias medicas" in " ".join(surface.canonical_sources)


def test_marketing_inventory_keeps_photos_as_evidence_not_free_text() -> None:
    surface = build_owner_pack_surface_inventory("marketing_activation_report")

    photo_field = _field(surface, "photographic_evidence")
    assert photo_field.evidence_type == "media"
    assert any("fotografias" in source for source in photo_field.canonical_sources)
    assert _field(surface, "activation_result").evidence_type == "marketing"


def test_inventory_can_be_scoped_to_single_surface() -> None:
    report = build_owner_pack_inventory_report(scopes=("entity_folder",))

    assert report.surface_count == 1
    assert report.surfaces[0].surface_id == "entity_folder"
    assert report.field_count == report.surfaces[0].field_count
    assert "team" in report.evidence_types
    assert "marketing" not in report.evidence_types
