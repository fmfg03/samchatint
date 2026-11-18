"""Record validation and calculation boundaries for Direction dossier capture."""

import pytest

import devnous.gastos  # noqa: F401 — initialize the owning runtime first
from samchat.client_executive.dossier_records import (
    DossierRecordError,
    validate_record,
)

NATIONAL = {"phase": "nacional", "entity": "", "category": "Juvenil"}
STATE = {"phase": "estatal", "entity": "Morelos", "category": "Juvenil"}


def test_event_duration_counts_days_including_same_day():
    item = validate_record(
        "national_event",
        NATIONAL,
        {"city": "Cuernavaca", "start_date": "2027-11-03", "end_date": "2027-11-03"},
        "Calendario autorizado",
    )
    assert item["fields"]["duration_days"] == 1


def test_known_zero_never_replaces_missing():
    no_counts = validate_record(
        "medical_service",
        NATIONAL,
        {"provider": "Servicio contratado"},
        "Parte oficial",
    )
    zero = validate_record(
        "medical_service", NATIONAL, {"transfers": 0}, "Parte oficial"
    )
    assert "transfers" not in no_counts["fields"]
    assert zero["fields"]["transfers"] == 0


@pytest.mark.parametrize(
    "kind,scope,fields,match",
    [
        (
            "medical_service",
            NATIONAL,
            {"transfers": 0, "diagnosis": "x"},
            "no permitidos",
        ),
        ("national_event", STATE, {}, "fase"),
        ("team_target", STATE, {"expected_teams": "1.5", "gender": "mixto"}, "formato"),
        (
            "state_phase",
            STATE,
            {"description": "Informe", "referee_fee": "100"},
            "moneda",
        ),
        ("visit", {**STATE, "entity": ""}, {}, "entidad"),
        (
            "activation",
            NATIONAL,
            {"activity": "Clínica", "visitors": "0"},
            "patrocinador",
        ),
        (
            "contact",
            STATE,
            {
                "responsibility": "Operador",
                "name": "Nombre registrado",
                "personal_date": "2026-01-02",
            },
            "descripción",
        ),
        (
            "national_event",
            NATIONAL,
            {"city": "X", "start_date": "2027-01-02", "end_date": "2027-01-01"},
            "antecede",
        ),
    ],
)
def test_capture_rejects_invalid_scope_values_and_private_fields(
    kind, scope, fields, match
):
    with pytest.raises(DossierRecordError, match=match):
        validate_record(kind, scope, fields, "Evidencia autorizada")


@pytest.mark.parametrize("advanced", ["true", "false"])
def test_advancement_preserves_confirmed_no(advanced):
    item = validate_record(
        "team_result",
        STATE,
        {"team_name": "Atlético", "advanced": advanced},
        "Acta oficial",
    )
    assert item["fields"]["advanced"] is (advanced == "true")


def test_fee_captures_zero_and_preserves_currency():
    item = validate_record(
        "state_phase",
        STATE,
        {"description": "Reglamento", "referee_fee": "0", "currency": "MXN"},
        "Acta oficial",
    )
    assert item["fields"]["referee_fee"] == "0.00"
    assert item["fields"]["currency"] == "MXN"
    assert "transport_fee" not in item["fields"]


@pytest.mark.parametrize(
    "kind,scope,fields,evidence,match",
    [
        ("x", STATE, {}, "Acta", "fase"),
        ("team_target", {**STATE, "category": ""}, {}, "Acta", "categoría"),
        ("medical_service", NATIONAL, {}, "", "respaldo"),
        ("medical_service", NATIONAL, {}, "Parte", "número de traslados"),
        ("uniform_delivery", STATE, {}, "Inventario", "Fecha de entrega"),
        ("team_result", STATE, {"team_name": "Atlético"}, "Acta", "posición"),
        (
            "visit",
            STATE,
            {"responsible": 12, "visit_date": "2026-02-10"},
            "Acta",
            "texto",
        ),
        (
            "contact",
            STATE,
            {"responsibility": "Responsable", "name": "Persona", "email": "x"},
            "Autorización",
            "correo",
        ),
        (
            "team_target",
            STATE,
            {"expected_teams": "NaN", "gender": "femenil"},
            "Acta",
            "formato",
        ),
        (
            "financial_link",
            NATIONAL,
            {"document_id": "bad", "concept": "hotel_anticipo"},
            "Factura",
            "formato",
        ),
        (
            "financial_link",
            NATIONAL,
            {
                "document_id": "292a2071-c87c-4cd1-b87d-f459d3c8e617",
                "concept": "ayuda_operador",
            },
            "Factura",
            "fase",
        ),
        (
            "financial_link",
            STATE,
            {
                "document_id": "292a2071-c87c-4cd1-b87d-f459d3c8e617",
                "concept": "gasto_visita",
            },
            "Informe",
            "vincular la visita",
        ),
    ],
)
def test_capture_additional_input_boundaries(kind, scope, fields, evidence, match):
    with pytest.raises(DossierRecordError, match=match):
        validate_record(kind, scope, fields, evidence)


def test_player_breakdown_requires_age_cutoff_and_valid_unique_total():
    base = {
        "reported_gender": "Femenino",
        "registrations": 8,
        "unique_people": 7,
        "age": 12,
    }
    with pytest.raises(DossierRecordError, match="fecha de corte"):
        validate_record("player_breakdown", STATE, base, "Informe autorizado")
    with pytest.raises(DossierRecordError, match="exceden"):
        validate_record(
            "player_breakdown",
            STATE,
            {**base, "unique_people": 9, "age_cutoff": "2026-12-31"},
            "Informe autorizado",
        )
    saved = validate_record(
        "player_breakdown",
        STATE,
        {**base, "age_cutoff": "2026-12-31"},
        "Informe autorizado",
    )
    assert saved["fields"]["registrations"] == 8
    assert saved["fields"]["unique_people"] == 7


@pytest.mark.parametrize(
    "category,categories,expected",
    [
        ("Juvenil", ["Juvenil"], True),
        ("Varonil", ["Juvenil"], False),
        ("", ["Juvenil"], True),
        ("Juvenil", None, True),
        ("Juvenil", ["", 1], False),
    ],
)
def test_financial_category_compatibility(category, categories, expected):
    from samchat.client_executive.dossier_records import document_category_matches

    assert document_category_matches(category, categories) is expected
