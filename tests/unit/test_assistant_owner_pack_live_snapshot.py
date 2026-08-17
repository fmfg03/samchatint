import json
from pathlib import Path

from samchat.assistant.owner_pack_live_snapshot import (
    OWNER_PACK_LIVE_MISSING,
    OWNER_PACK_LIVE_SNAPSHOT_ONLY,
    OWNER_PACK_LIVE_SUPPORTED,
    build_owner_pack_live_snapshot_report,
    build_owner_pack_live_surface_snapshot,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _field(surface, field_name: str):
    for item in surface.fields:
        if item.field == field_name:
            return item
    raise AssertionError(f"missing field {field_name}")


def test_entity_live_snapshot_reads_workspace_without_writes(tmp_path: Path) -> None:
    entity_dir = tmp_path / "copa-telmex" / "entities" / "jalisco"
    _write_json(
        entity_dir / "operations.json",
        {
            "entity_name": "Jalisco",
            "expected_teams_by_category_gender": [
                {"categoria": "Juvenil", "genero": "Varonil", "equipos": 12}
            ],
            "real_teams_by_category_gender": [
                {"categoria": "Juvenil", "genero": "Varonil", "equipos": 10}
            ],
            "players_by_category_age_gender": [{"edad": 15, "jugadores": 80}],
        },
    )
    _write_json(
        entity_dir / "finance.json",
        {
            "entity_name": "Jalisco",
            "operator_transfers": [{"fecha": "2026-07-01", "monto": 10000}],
        },
    )

    surface = build_owner_pack_live_surface_snapshot(
        surface_id="entity_folder",
        tournament_slug="Copa Telmex",
        entity_name="Jalisco",
        root_dir=tmp_path,
    )

    assert surface.execution_status == "not_executed"
    assert surface.writes_attempted == 0
    assert surface.side_effects_detected == 0
    assert surface.audit_language == OWNER_PACK_LIVE_SNAPSHOT_ONLY
    assert len(surface.workspace_files_found) == 2
    assert _field(surface, "entity_name").status == OWNER_PACK_LIVE_SUPPORTED
    assert _field(surface, "expected_teams").value[0]["equipos"] == 12
    assert _field(surface, "operator_payments").status == OWNER_PACK_LIVE_SUPPORTED
    assert _field(surface, "round_progression").status == OWNER_PACK_LIVE_MISSING


def test_national_live_snapshot_reads_operations_finance_marketing(tmp_path: Path) -> None:
    national_dir = tmp_path / "dcc" / "national"
    _write_json(
        national_dir / "operations.json",
        {
            "tournament_category_dates_city": "DCC Nacional Sub-17, CDMX, nov 2026",
            "hotels_and_bed_nights": [{"hotel": "Hotel Uno", "camas_noche": 120}],
            "sports_facility": "Unidad Deportiva Norte",
            "accidents_with_transfer": [{"jugador": "reservado", "traslado": True}],
        },
    )
    _write_json(
        national_dir / "finance.json",
        {
            "hotel_payments_advance_settlement": [{"monto": 50000}],
            "supplier_payments": [{"proveedor": "Ambulancias", "monto": 12000}],
        },
    )
    _write_json(
        national_dir / "marketing.json",
        {
            "onsite_brand_activation_providers": [{"proveedor": "Sponsor A"}],
            "photo_evidence": [{"archivo": "foto1.jpg"}],
        },
    )

    report = build_owner_pack_live_snapshot_report(
        surface_id="national_phase_folder",
        tournament_slug="DCC",
        root_dir=tmp_path,
    )
    surface = report.surfaces[0]

    assert report.safety_summary["writes_enabled"] is False
    assert report.supported_field_count == surface.supported_field_count
    assert _field(surface, "contracted_hotels_bed_nights").status == (
        OWNER_PACK_LIVE_SUPPORTED
    )
    assert _field(surface, "hotel_payments").status == OWNER_PACK_LIVE_SUPPORTED
    assert _field(surface, "brand_activation_evidence").status == (
        OWNER_PACK_LIVE_SUPPORTED
    )
    assert _field(surface, "contracted_meals").status == OWNER_PACK_LIVE_MISSING


def test_live_snapshot_fail_closes_when_workspace_has_no_files(tmp_path: Path) -> None:
    report = build_owner_pack_live_snapshot_report(
        surface_id="entity_folder",
        tournament_slug="Copa Telmex",
        entity_name="Jalisco",
        root_dir=tmp_path,
    )
    surface = report.surfaces[0]

    assert report.supported_field_count == 0
    assert surface.workspace_files_found == []
    assert _field(surface, "expected_teams").status == OWNER_PACK_LIVE_MISSING
    assert "No se encontro evidencia viva" in report.summary
