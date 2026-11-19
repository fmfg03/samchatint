"""Governed capture and associations for gaps in canonical tournament sources."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from devnous.gastos.services.access_control_service import (
    AccessControlLookupError,
    explicit_tool_decision,
)

STATE = ("estatal",)
NATIONAL = ("nacional",)
BOTH = STATE + NATIONAL

PERMISSION_KEYS = {
    "operations": "direccion.expediente_operaciones",
    "finance": "direccion.expediente_finanzas",
    "marketing": "direccion.expediente_mercadotecnia",
    "contacts": "direccion.expediente_contactos",
    "media": "direccion.expediente_fotografias",
}


def _field(label: str, value_type: str = "text", required: bool = True) -> dict:
    return {"label": label, "type": value_type, "required": required}


def _capture(label: str, domain: str, phases: tuple[str, ...], **fields: dict) -> dict:
    return {"label": label, "domain": domain, "phases": phases, "fields": fields}


CAPTURE_FIELDS = {
    "operations_edition": _capture(
        "Vínculo de la edición con Operaciones SOUL",
        "operations",
        NATIONAL,
        operations_tournament_id=_field(
            "ID del torneo en Operaciones SOUL", "operations_id"
        ),
    ),
    "participant": _capture(
        "Participación de la entidad",
        "operations",
        STATE,
        description=_field("Evidencia de participación"),
    ),
    "contact": _capture(
        "Responsable de Plataforma Sports o de la entidad",
        "contacts",
        STATE,
        responsibility=_field("Responsabilidad"),
        name=_field("Nombre"),
        email=_field("Correo", "email", False),
        phone=_field("Teléfono", "tel", False),
        birth_date=_field("Fecha de nacimiento", "date", False),
        personal_date=_field("Otra fecha personal autorizada", "date", False),
        personal_date_label=_field("Descripción de la otra fecha", "text", False),
    ),
    "team_target": _capture(
        "Meta de equipos",
        "operations",
        STATE,
        gender=_field("Rama", "gender"),
        expected_teams=_field("Equipos esperados", "integer"),
        actual_teams=_field(
            "Equipos reales de la fase según el respaldo", "integer", False
        ),
    ),
    "player_breakdown": _capture(
        "Desglose acreditado de jugadores",
        "operations",
        BOTH,
        reported_gender=_field("Género consignado por la fuente", "text", False),
        age=_field("Edad a la fecha de corte", "integer", False),
        age_cutoff=_field("Fecha de corte de edad", "date", False),
        registrations=_field("Inscripciones", "integer"),
        unique_people=_field("Personas únicas acreditadas", "integer", False),
    ),
    "state_phase": _capture(
        "Descripción y cuotas de la fase estatal",
        "operations",
        STATE,
        description=_field("Descripción"),
        referee_fee=_field("Cuota de arbitraje", "money", False),
        transport_fee=_field("Cuota de transporte", "money", False),
        currency=_field("Moneda", "currency", False),
    ),
    "team_result": _capture(
        "Avance, clasificación al nacional o posición final",
        "operations",
        BOTH,
        team_name=_field("Equipo"),
        round=_field("Ronda", "text", False),
        advanced=_field("Avanzó de ronda", "boolean", False),
        national_qualifier=_field("Clasificó al nacional", "boolean", False),
        final_position=_field("Posición final", "positive_integer", False),
    ),
    "uniform_delivery": _capture(
        "Entrega de uniformes",
        "operations",
        STATE,
        delivery_date=_field("Fecha de entrega", "date"),
        location=_field("Lugar"),
        units=_field("Unidades", "integer", False),
    ),
    "travel": _capture(
        "Viaje",
        "operations",
        BOTH,
        description=_field("Delegación o personal"),
        departure=_field("Salida", "date"),
        return_date=_field("Regreso", "date", False),
        origin=_field("Origen"),
        destination=_field("Destino"),
    ),
    "national_event": _capture(
        "Calendario y sede nacional",
        "operations",
        NATIONAL,
        city=_field("Ciudad"),
        start_date=_field("Inicio", "date"),
        end_date=_field("Fin", "date"),
        sports_complex=_field("Unidad deportiva", "text", False),
        fields=_field("Canchas", "text", False),
    ),
    "lodging": _capture(
        "Hotel y camas-noche",
        "operations",
        NATIONAL,
        hotel=_field("Hotel"),
        bed_nights=_field("Camas-noche", "integer"),
    ),
    "meals": _capture(
        "Alimentos",
        "operations",
        NATIONAL,
        meal_type=_field("Tipo de alimento"),
        servings=_field("Raciones", "integer"),
    ),
    "medical_service": _capture(
        "Servicio médico y traslados",
        "operations",
        NATIONAL,
        provider=_field("Servicio o proveedor", "text", False),
        transfers=_field("Accidentes que requirieron traslado", "integer", False),
    ),
    "visit": _capture(
        "Visita de AZ, CL u otro responsable",
        "operations",
        STATE,
        responsible=_field("Responsable de la visita"),
        visit_date=_field("Fecha", "date"),
        report_reference=_field("Referencia del informe", "text", False),
    ),
    "financial_link": _capture(
        "Vínculo de una solicitud o informe existente",
        "finance",
        BOTH,
        document_id=_field("Documento de SamChat", "document"),
        concept=_field("Concepto del expediente", "finance_concept"),
        visit_id=_field("Visita asociada", "visit", False),
    ),
    "activation": _capture(
        "Activación y visitantes de patrocinadores",
        "marketing",
        NATIONAL,
        supplier=_field("Proveedor de activación", "text", False),
        sponsor=_field("Patrocinador", "text", False),
        visitors=_field("Visitantes vinculados", "integer", False),
        activity=_field("Actividad"),
        activity_date=_field("Fecha", "date", False),
        results=_field("Resultados", "text", False),
    ),
    "activation_photo": _capture(
        "Fotografía de una activación",
        "media",
        NATIONAL,
        photo_id=_field("Fotografía de la fuente autorizada", "photo"),
        activation_id=_field("Activación vinculada", "activation"),
    ),
}

FINANCE_CONCEPTS = {
    "ayuda_operador": "Ayudas y pagos al operador",
    "uniformes": "Uniformes",
    "balones": "Balones",
    "equipamiento": "Equipamiento",
    "gasto_visita": "Gastos de visita",
    "viaje_personal": "Viajes del personal",
    "hotel_anticipo": "Anticipo de hotel",
    "hotel_liquidacion": "Liquidación de hotel",
    "hotel_otro": "Otro concepto hotelero",
    "proveedor": "Proveedor nacional",
    "servicio_medico": "Servicio médico",
    "seguro": "Seguro",
}
STATE_CONCEPTS = {
    "ayuda_operador",
    "uniformes",
    "balones",
    "equipamiento",
    "gasto_visita",
}
GENDERS = {"femenil": "Femenil", "varonil": "Varonil", "mixto": "Mixto"}


class DossierRecordError(ValueError):
    """Validation error whose message never includes submitted private values."""


async def capture_permissions(
    session: Any, employee: Any, action: str = "editar"
) -> set[str]:
    permitted = set()
    try:
        for domain, key in PERMISSION_KEYS.items():
            if await explicit_tool_decision(session, employee, key, action) is True:
                permitted.add(domain)
    except AccessControlLookupError:
        return set()
    return permitted


async def read_domains(session: Any, employee: Any) -> set[str]:
    """Apply explicit denials and require grants for sensitive Direction sections."""
    if employee is None or not getattr(employee, "activo", True):
        return set()
    decisions: dict[str, bool | None] = {}
    try:
        for domain, key in PERMISSION_KEYS.items():
            decisions[domain] = await explicit_tool_decision(
                session, employee, key, "ver"
            )
    except AccessControlLookupError:
        return set()
    domains = {
        domain
        for domain in ("operations", "finance", "marketing")
        if decisions[domain] is not False
    }
    if "operations" in domains and decisions["contacts"] is True:
        domains.add("contacts")
    if "marketing" in domains and decisions["media"] is True:
        domains.add("media")
    return domains


async def known_editions(
    session: Any,
    tournament_id: str,
    *,
    finance_allowed: bool = True,
    operations_allowed: bool = True,
) -> list[int]:
    """List explicitly recorded editions visible in the authorized domains."""
    tournament_id = str(UUID(tournament_id))
    queries = []
    if finance_allowed:
        queries.append(
            """SELECT edicion AS edition FROM documentos
               WHERE torneo_id = CAST(:tournament AS uuid) AND edicion BETWEEN 2000 AND 2100"""
        )
    if operations_allowed and await session.scalar(
        text("SELECT to_regclass('copa_telmex_tournament_editions') IS NOT NULL")
    ):
        queries.append("""SELECT edition_year FROM copa_telmex_tournament_editions
               WHERE tournament_id = CAST(:tournament AS uuid) AND active""")
    if operations_allowed and await session.scalar(
        text("SELECT to_regclass('direction_dossier_records') IS NOT NULL")
    ):
        queries.append(
            """SELECT edition_year FROM direction_dossier_records
               WHERE tournament_id = CAST(:tournament AS uuid) AND kind = 'operations_edition'"""
        )
    if not queries:
        return []
    rows = await session.execute(
        text(" UNION ".join(queries) + " ORDER BY 1 DESC"),
        {"tournament": tournament_id},
    )
    return list(dict.fromkeys(rows.scalars()))


def _clean_string(value: Any, max_length: int) -> str:
    if not isinstance(value, str):
        raise DossierRecordError("El valor debe ser texto.")
    result = value.strip()
    if len(result) > max_length or any(
        ord(character) < 32 and character not in "\n\t" for character in result
    ):
        raise DossierRecordError(
            "El texto supera el límite permitido o contiene caracteres inválidos."
        )
    return result


def _typed_value(value: Any, value_type: str) -> str | int | bool:
    if value_type in {"text", "email", "tel"}:
        result = _clean_string(value, 800)
        if value_type == "email" and (
            "@" not in result or any(ch.isspace() for ch in result)
        ):
            raise DossierRecordError("El correo debe tener un formato válido.")
        return result
    try:
        if value_type == "date":
            return date.fromisoformat(value).isoformat()
        if value_type in {"integer", "positive_integer", "money"}:
            if isinstance(value, bool):
                raise ValueError
            amount = Decimal(str(value))
            minimum = 1 if value_type == "positive_integer" else 0
            if not amount.is_finite() or not minimum <= amount <= 1_000_000_000:
                raise ValueError
            if value_type == "money" and amount == amount.quantize(Decimal("0.01")):
                return format(amount, ".2f")
            if value_type != "money" and amount == int(amount):
                return int(amount)
        if (
            value_type == "currency"
            and isinstance(value, str)
            and value in {"MXN", "USD", "EUR"}
        ):
            return value
        if value_type == "gender" and isinstance(value, str) and value in GENDERS:
            return value
        if value_type == "boolean":
            if value is True or value == "true":
                return True
            if value is False or value == "false":
                return False
        if (
            value_type == "finance_concept"
            and isinstance(value, str)
            and value in FINANCE_CONCEPTS
        ):
            return value
        if value_type in {"document", "visit", "activation", "photo", "operations_id"}:
            return str(UUID(str(value)))
    except (InvalidOperation, AttributeError, TypeError, ValueError) as exc:
        raise DossierRecordError("Un campo tiene un formato o valor inválido.") from exc
    raise DossierRecordError("Un campo tiene un formato o valor inválido.")


def validate_record(
    kind: str, scope: dict, fields: dict, evidence_reference: str
) -> dict:
    spec = CAPTURE_FIELDS.get(kind)
    if not spec or scope.get("phase") not in spec["phases"]:
        raise DossierRecordError("El tipo de captura no corresponde a esta fase.")
    entity = _clean_string(scope.get("entity") or "", 120)
    category = _clean_string(scope.get("category") or "", 80)
    phase = scope["phase"]
    if kind == "operations_edition" and (entity or category):
        raise DossierRecordError("El vínculo SOUL corresponde a toda la edición.")
    if phase == "estatal" and not entity:
        raise DossierRecordError("Selecciona una entidad para la captura estatal.")
    if (
        kind in {"team_target", "team_result", "national_event", "player_breakdown"}
        and not category
    ):
        raise DossierRecordError("Selecciona una categoría para esta captura.")
    if set(fields) - spec["fields"].keys():
        raise DossierRecordError("La captura contiene campos no permitidos.")
    evidence = _clean_string(evidence_reference, 240)
    if not evidence:
        raise DossierRecordError("Indica una referencia de respaldo.")
    cleaned = {}
    for key, field in spec["fields"].items():
        value = fields.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if field["required"]:
                raise DossierRecordError(f"Completa el campo {field['label']}.")
        else:
            cleaned[key] = _typed_value(value, field["type"])
    for start, end in (("start_date", "end_date"), ("departure", "return_date")):
        if (
            start in cleaned
            and end in cleaned
            and str(cleaned[end]) < str(cleaned[start])
        ):
            raise DossierRecordError("La fecha de término antecede a la de inicio.")
    if kind == "national_event":
        cleaned["duration_days"] = (
            date.fromisoformat(str(cleaned["end_date"]))
            - date.fromisoformat(str(cleaned["start_date"]))
        ).days + 1
    if (
        kind == "state_phase"
        and ({"referee_fee", "transport_fee"} & cleaned.keys())
        and "currency" not in cleaned
    ):
        raise DossierRecordError("Indica la moneda de las cuotas.")
    if kind == "medical_service" and not cleaned:
        raise DossierRecordError("Captura el servicio o el número de traslados.")
    if kind == "team_result" and not (cleaned.keys() - {"team_name", "round"}):
        raise DossierRecordError("Captura un avance, clasificación o posición.")
    if kind == "activation" and "visitors" in cleaned and "sponsor" not in cleaned:
        raise DossierRecordError("Indica el patrocinador vinculado a los visitantes.")
    if kind == "contact" and ("personal_date" in cleaned) != (
        "personal_date_label" in cleaned
    ):
        raise DossierRecordError("Indica tanto la fecha personal como su descripción.")
    if kind == "player_breakdown":
        if ("age" in cleaned) != ("age_cutoff" in cleaned):
            raise DossierRecordError("Indica la edad junto con su fecha de corte.")
        age = cleaned.get("age")
        if isinstance(age, int) and age > 130:
            raise DossierRecordError("La edad registrada no es válida.")
        unique_people = cleaned.get("unique_people")
        if isinstance(unique_people, int) and unique_people > int(
            cleaned["registrations"]
        ):
            raise DossierRecordError("Las personas únicas exceden las inscripciones.")
    if kind == "financial_link":
        concept = cleaned["concept"]
        if (concept in STATE_CONCEPTS) != (phase == "estatal"):
            raise DossierRecordError(
                "El concepto financiero no corresponde a esta fase."
            )
        if ("visit_id" in cleaned) != (concept == "gasto_visita"):
            raise DossierRecordError(
                "El gasto por visita requiere vincular la visita correspondiente."
            )
    return {
        "phase": phase,
        "entity": entity,
        "category": category,
        "domain": spec["domain"],
        "fields": cleaned,
        "reference": evidence,
    }


async def read_records(
    session: Any, *, tournament_id: str, edition_year: int, allowed_domains: set[str]
) -> dict:
    if not await session.scalar(
        text("SELECT to_regclass('direction_dossier_records') IS NOT NULL")
    ):
        return {"status": "unavailable", "rows": []}
    rows = await session.execute(
        text("""
            SELECT id::text AS id, kind, domain, phase, entity, category, fields,
                   evidence_reference, updated_at, revision
            FROM direction_dossier_records
            WHERE tournament_id = CAST(:tournament AS uuid)
              AND edition_year = :edition AND domain = ANY(:domains)
            ORDER BY phase, entity, category, kind, updated_at DESC, id
            LIMIT 1001
        """),
        {
            "tournament": tournament_id,
            "edition": edition_year,
            "domains": sorted(allowed_domains),
        },
    )
    records = [dict(row) for row in rows.mappings().all()]
    return {
        "status": "partial" if len(records) > 1000 else "available",
        "rows": records[:1000],
    }


def document_category_matches(category: str, categories: Any) -> bool:
    """Share capture/read category compatibility without inventing missing scope."""
    values = (
        [v for v in categories if isinstance(v, str) and v.strip()]
        if isinstance(categories, list)
        else []
    )
    return not category or categories is None or categories == [] or category in values


async def _verify_reference(session: Any, capture: dict, params: dict) -> None:
    fields = capture["fields"]
    operations_id = fields.get("operations_tournament_id")
    if operations_id and operations_id != params["tournament"]:
        has_catalog = await session.scalar(
            text("SELECT to_regclass('tournament_operations_links') IS NOT NULL")
        )
        declared = (
            await session.scalar(
                text("""
                SELECT operations_tournament_id FROM tournament_operations_links
                WHERE tournament_id = CAST(:tournament AS uuid)
            """),
                params,
            )
            if has_catalog
            else None
        )
        if declared != operations_id:
            raise DossierRecordError(
                "Primero acredita el vínculo del proyecto con Operaciones en Torneos."
            )
    if "document_id" in fields:
        result = await session.execute(
            text("""
                SELECT id, categorias FROM documentos
                WHERE id = CAST(:document AS uuid) AND torneo_id = CAST(:tournament AS uuid)
                  AND (edicion = :edition OR edicion IS NULL)
                  AND (fase IS NULL OR btrim(fase) = '' OR lower(btrim(fase)) = :phase)
                  AND tipo IN ('SOLICITUD', 'INFORME')
                FOR SHARE
            """),
            params,
        )
        document = result.mappings().one_or_none()
        if document is None:
            raise DossierRecordError(
                "El documento no coincide con el torneo, la edición o la fase."
            )
        if not document_category_matches(capture["category"], document["categorias"]):
            raise DossierRecordError("El documento no coincide con la categoría.")
    for kind, field in (("visit", "visit_id"), ("activation", "activation_id")):
        if field not in fields:
            continue
        result = await session.execute(
            text("""
                SELECT id FROM direction_dossier_records
                WHERE id = CAST(:linked_record AS uuid) AND kind = :linked_kind
                  AND tournament_id = CAST(:tournament AS uuid) AND edition_year = :edition
                  AND phase = :phase AND entity = :entity
                  AND (category = :category OR category = '')
                FOR SHARE
            """),
            {**params, "linked_record": fields[field], "linked_kind": kind},
        )
        if result.scalar_one_or_none() is None:
            raise DossierRecordError("El vínculo no pertenece al alcance seleccionado.")


async def save_record(
    session: Any,
    *,
    tournament_id: str,
    edition_year: int,
    employee_id: str,
    scope: dict,
    kind: str,
    fields: dict,
    evidence_reference: str,
    record_id: str | None = None,
    expected_revision: int | None = None,
    request_id: str | None = None,
) -> dict:
    """Write one authorized capture and its audit row in the caller's transaction."""
    capture = validate_record(kind, scope, fields, evidence_reference)
    if not await session.scalar(
        text("SELECT to_regclass('direction_dossier_records') IS NOT NULL")
    ):
        raise DossierRecordError(
            "La captura del expediente no está habilitada en esta instalación."
        )
    params = {
        **capture,
        "tournament": str(UUID(tournament_id)),
        "edition": edition_year,
        "actor": str(UUID(employee_id)),
        "kind": kind,
        "record": str(UUID(record_id or request_id or str(uuid4()))),
        "expected_revision": expected_revision,
        "document": capture["fields"].get("document_id"),
        "fields": json.dumps(capture["fields"], ensure_ascii=False),
    }
    tournament_is_active = await session.scalar(
        text("""
            SELECT active FROM tournaments WHERE id = CAST(:tournament AS uuid) FOR SHARE
        """),
        params,
    )
    if not tournament_is_active:
        raise DossierRecordError("El torneo dejó de estar disponible para la captura.")
    await _verify_reference(session, capture, params)
    mutate = """
        INSERT INTO direction_dossier_records
            (id, tournament_id, edition_year, phase, entity, category, domain,
             kind, fields, document_id, evidence_reference, revision, updated_by)
        VALUES (
            CAST(:record AS uuid), CAST(:tournament AS uuid), :edition, :phase, :entity,
            :category, :domain, :kind, CAST(:fields AS jsonb), CAST(:document AS uuid),
            :reference, 1, CAST(:actor AS uuid)
        ) ON CONFLICT (id) DO NOTHING
    """
    if record_id is not None:
        mutate = """
            UPDATE direction_dossier_records SET
                fields = CAST(:fields AS jsonb), document_id = CAST(:document AS uuid),
                evidence_reference = :reference, updated_by = CAST(:actor AS uuid),
                updated_at = now(), revision = revision + 1
            WHERE id = CAST(:record AS uuid) AND tournament_id = CAST(:tournament AS uuid)
              AND edition_year = :edition AND phase = :phase AND entity = :entity
              AND category = :category AND domain = :domain AND kind = :kind
              AND revision = :expected_revision
        """
    try:
        result = await session.execute(
            text(mutate + " RETURNING id::text AS id, revision, updated_at"),
            params,
        )
        row = result.mappings().one_or_none()
        if row is None and record_id is None:
            replay = await session.execute(
                text("""
                    SELECT id::text AS id, revision, updated_at
                    FROM direction_dossier_records
                    WHERE id = CAST(:record AS uuid) AND tournament_id = CAST(:tournament AS uuid)
                      AND edition_year = :edition AND phase = :phase AND entity = :entity
                      AND category = :category AND domain = :domain AND kind = :kind
                      AND fields = CAST(:fields AS jsonb) AND evidence_reference = :reference
                      AND updated_by = CAST(:actor AS uuid) AND revision = 1
                """),
                params,
            )
            row = replay.mappings().one_or_none()
            if row is not None:
                return dict(row)
        if row is None:
            raise DossierRecordError(
                "La captura cambió. Recarga el expediente y revisa el registro."
            )
        await session.execute(
            text("""
                INSERT INTO direction_dossier_revisions
                    (record_id, revision, actor_id, evidence_reference, snapshot)
                SELECT id, revision, updated_by, evidence_reference, to_jsonb(record)
                FROM direction_dossier_records AS record
                WHERE id = CAST(:record AS uuid) AND revision = :revision
            """),
            {**params, "revision": row["revision"]},
        )
    except IntegrityError as exc:
        raise DossierRecordError(
            "El documento ya tiene un vínculo o cambió su referencia."
        ) from exc
    return dict(row)
