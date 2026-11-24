"""Record validation and calculation boundaries for Direction dossier capture."""

import pytest

import devnous.gastos  # noqa: F401 — initialize the owning runtime first
from samchat.client_executive import dossier_records
from samchat.client_executive.dossier_records import (
    DossierRecordError,
    validate_record,
)

NATIONAL = {"phase": "nacional", "entity": "", "category": "Juvenil"}
STATE = {"phase": "estatal", "entity": "Morelos", "category": "Juvenil"}
REF = "292a2071-c87c-4cd1-b87d-f459d3c8e617"


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


def test_contact_keeps_legacy_shape_and_adds_distinct_verified_birthdays():
    legacy = {
        "responsibility": "Coordinación",
        "name": "Persona de prueba",
    }
    assert validate_record("contact", STATE, legacy, "Acta")["fields"] == legacy

    fields = {
        **legacy,
        "contact_role": "plataforma_sports",
        "person_id": REF.upper(),
        "birth_date": "1990-07-06",
        "birth_date_reference": "Acta autorizada A",
        "spouse_name": "Persona de prueba B",
        "spouse_birth_date": "1991-07-06",
        "spouse_birth_date_reference": "Acta autorizada B",
    }
    result = validate_record("contact", STATE, fields, "Acta")
    assert result["domain"] == "contacts"
    assert result["fields"] == {**fields, "person_id": REF}
    local = validate_record(
        "contact", STATE, {**fields, "contact_role": "representante_local"}, "Acta"
    )
    assert local["fields"]["contact_role"] == "representante_local"


@pytest.mark.parametrize(
    "fields,match",
    [
        ({"contact_role": "plataforma_sports"}, "ámbito y la persona"),
        ({"person_id": REF}, "ámbito y la persona"),
        (
            {
                "contact_role": "plataforma_sports",
                "person_id": REF,
                "birth_date": "1990-02-14",
            },
            "respaldo de la fecha",
        ),
        ({"birth_date_reference": "Acta"}, "fecha de nacimiento para su respaldo"),
        ({"spouse_birth_date": "1990-02-14"}, "junto con su respaldo"),
        (
            {"spouse_birth_date": "1990-02-14", "spouse_birth_date_reference": "Acta"},
            "Identifica el cónyuge",
        ),
        ({"contact_role": "indefinido", "person_id": REF}, "formato"),
    ],
)
def test_contact_requires_attested_person_and_granular_date_evidence(fields, match):
    with pytest.raises(DossierRecordError, match=match):
        validate_record(
            "contact",
            STATE,
            {"name": "Persona de prueba", "responsibility": "Coordinación", **fields},
            "Respaldo autorizado",
        )


def test_visit_can_link_a_canonical_report_with_an_outcome():
    fields = {
        "responsible": "AZ",
        "visit_date": "2026-11-18",
        "report_reference": "INF-018",
        "report_document_id": REF.upper(),
        "outcome": "Se validó la disponibilidad de las instalaciones.",
    }
    captured = validate_record("visit", STATE, fields, "Informe de visita")
    assert captured["fields"] == {**fields, "report_document_id": REF}


@pytest.mark.parametrize("event_type", ["inauguracion", "clausura", "finales"])
def test_national_itinerary_distinguishes_approved_events(event_type):
    fields = {
        "event_type": event_type,
        "event_date": "2027-04-10",
        "event_time": "18:05:03",
        "venue": "Unidad deportiva central",
    }
    assert (
        validate_record("national_itinerary", NATIONAL, fields, "Agenda")["fields"]
        == fields
    )


@pytest.mark.parametrize("event_time", ["24:00", "13:60", "13:00+01:00"])
def test_itinerary_rejects_invalid_or_ambiguous_local_time(event_time):
    with pytest.raises(DossierRecordError, match="formato"):
        validate_record(
            "national_itinerary",
            NATIONAL,
            {
                "event_type": "finales",
                "event_date": "2027-04-10",
                "event_time": event_time,
            },
            "Agenda",
        )


def test_facility_counts_and_service_description_keep_known_zero():
    venue = validate_record(
        "national_event",
        NATIONAL,
        {
            "city": "Cuernavaca",
            "start_date": "2027-04-10",
            "end_date": "2027-04-11",
            "field_count": 0,
            "field_types": "Pasto natural",
        },
        "Agenda",
    )
    assert venue["fields"]["field_count"] == 0
    assert venue["fields"]["field_types"] == "Pasto natural"
    service = validate_record(
        "medical_service",
        NATIONAL,
        {"description": "Cobertura durante las finales."},
        "Contrato",
    )
    assert service["fields"] == {"description": "Cobertura durante las finales."}


def test_medical_transfer_has_only_structured_facts_and_opaque_evidence():
    fields = {
        "incident_date": "2027-04-10",
        "incident_time": "18:05",
        "venue_id": REF,
        "medical_service_id": REF,
    }
    result = validate_record("medical_transfer", NATIONAL, fields, REF.upper())
    assert result["domain"] == "medical_transfers"
    assert result["fields"] == fields and result["reference"] == REF
    with pytest.raises(DossierRecordError, match="ID del respaldo"):
        validate_record("medical_transfer", NATIONAL, fields, "Referencia libre")
    for key in ("diagnosis", "notes", "person_id", "outcome"):
        with pytest.raises(DossierRecordError, match="no permitidos"):
            validate_record(
                "medical_transfer", NATIONAL, {**fields, key: "Dato privado"}, REF
            )


def test_sponsor_guest_names_have_no_path_into_general_marketing_capture():
    guest = {"activation_id": REF, "guest_name": "Persona de prueba"}
    item = validate_record("sponsor_guest", NATIONAL, guest, "Lista autorizada")
    assert item["domain"] == "sponsor_guests" and item["fields"] == guest
    with pytest.raises(DossierRecordError, match="no permitidos"):
        validate_record(
            "activation",
            NATIONAL,
            {"activity": "Evento", "guest_name": guest["guest_name"]},
            "Lista autorizada",
        )


@pytest.mark.asyncio
async def test_sensitive_subdomains_require_affirmative_rules_and_active_parent(
    monkeypatch,
):
    decisions = {
        (dossier_records.PERMISSION_KEYS["medical_transfers"], "ver"): True,
        (dossier_records.PERMISSION_KEYS["sponsor_guests"], "ver"): True,
    }

    async def decision(_session, _employee, key, action):
        return decisions.get((key, action))

    monkeypatch.setattr(dossier_records, "explicit_tool_decision", decision)
    allowed = await dossier_records.read_domains(object(), object())
    assert {"medical_transfers", "sponsor_guests"} <= allowed
    assert {"contacts", "media"}.isdisjoint(allowed)
    assert await dossier_records.capture_permissions(object(), object()) == set()
    decisions[dossier_records.PERMISSION_KEYS["operations"], "ver"] = False
    decisions[dossier_records.PERMISSION_KEYS["marketing"], "ver"] = False
    assert await dossier_records.read_domains(object(), object()) == {"finance"}


@pytest.mark.parametrize("kind", ["financial_link", "expense_line_link"])
def test_hotel_links_classify_service_without_copying_document_amount(kind):
    id_key = "expense_id" if kind == "expense_line_link" else "document_id"
    fields = {id_key: REF, "concept": "hotel_liquidacion", "service_type": "Hospedaje"}
    item = validate_record(kind, NATIONAL, fields, "Factura hotel")
    assert item["fields"] == fields
    assert "amount" not in item["fields"]
    with pytest.raises(DossierRecordError, match="conceptos de hotel"):
        validate_record(kind, NATIONAL, {**fields, "concept": "seguro"}, "Factura")
    with pytest.raises(DossierRecordError, match="no permitidos"):
        validate_record(kind, NATIONAL, {**fields, "amount": "200"}, "Factura")


def test_expense_line_enforces_hotel_service_and_state_visit_scope():
    with pytest.raises(DossierRecordError, match="servicio acreditado"):
        validate_record(
            "expense_line_link",
            NATIONAL,
            {"expense_id": REF, "concept": "hotel_anticipo"},
            "Factura hotel",
        )
    visit = {"expense_id": REF, "concept": "gasto_visita", "visit_id": REF}
    with pytest.raises(DossierRecordError, match="fase"):
        validate_record("expense_line_link", NATIONAL, visit, "Informe")
    with pytest.raises(DossierRecordError, match="vincular la visita"):
        validate_record(
            "expense_line_link",
            STATE,
            {"expense_id": REF, "concept": "gasto_visita"},
            "Informe",
        )
    assert (
        validate_record("expense_line_link", STATE, visit, "Informe")["fields"] == visit
    )


def test_exact_birth_date_requires_own_reference_even_without_contact_role():
    with pytest.raises(DossierRecordError, match="respaldo de la fecha"):
        validate_record(
            "contact",
            STATE,
            {
                "name": "Contacto",
                "responsibility": "Operaciones",
                "birth_date": "1984-01-02",
            },
            "Acta",
        )
    verified = validate_record(
        "contact",
        STATE,
        {
            "name": "Contacto",
            "responsibility": "Operaciones",
            "birth_date": "1984-01-02",
            "birth_date_reference": "Acta autorizada",
        },
        "Constancia de responsabilidad",
    )
    assert verified["fields"]["birth_date"] == "1984-01-02"
