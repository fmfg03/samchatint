"""Scoped HTML and governed capture routes for the live tournament dossier."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import date
from inspect import isawaitable
from typing import Any
from urllib.parse import parse_qsl, urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from itsdangerous import BadData, URLSafeTimedSerializer

from samchat.client_executive.dossier_directory_private import (
    PRIVATE_DIRECTORY_SOURCE,
    RepresentativeSourceIdentity,
)
from samchat.client_executive.dossier_finance import read_expense_lines
from samchat.client_executive.dossier_records import (
    CAPTURE_FIELDS,
    CONTACT_ROLES,
    FINANCE_CONCEPTS,
    GENDERS,
    MEAL_KINDS,
    NATIONAL_ITINERARY_EVENTS,
    SENSITIVE_PARENT_DOMAINS,
    STATE_CONCEPTS,
    DossierRecordError,
    capture_permissions,
    known_editions,
    read_domains,
    read_records,
    save_record,
    validate_record,
)
from samchat.client_executive.dossier_registry import load_dossier_registry
from samchat.client_executive.dossier_sources import (
    load_dossier_sources,
    resolve_dossier_photo,
)
from samchat.client_executive.dossier_ui import (
    DOMAIN_LABELS,
    DOSSIER_PATH,
    FIELD_LABELS,
    PHASES,
    render_capture_preview,
    render_dossier,
)
from samchat.client_executive.dossier_view import (
    finance_link_matches,
    operations_edition,
    read_dossier_view,
)
from samchat.client_executive.service import _authorized_tournaments

from .dependencies import get_current_empleado, get_db_session

MAX_CAPTURE_BYTES = 16_384
CAPTURE_TTL_SECONDS = 900
CSRF_KEY = "direction_dossier_csrf"
PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
SECURE_PAGE = (
    "default-src 'none'; style-src 'self'; img-src 'self'; "
    "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
)
TEAM_PAGE_SIZE = 100
FIELD_CHOICES = {
    "finance_concept": FINANCE_CONCEPTS,
    "gender": GENDERS,
    "contact_role": CONTACT_ROLES,
    "itinerary_event": NATIONAL_ITINERARY_EVENTS,
    "meal_kind": MEAL_KINDS,
}
PRIVATE_DIRECTORY_FIELDS = (
    "representative_birthday",
    "spouse_name",
    "spouse_birthday",
    "zone",
)
SOURCE_METADATA = {
    "label",
    "id",
    "source",
    "status",
    "entity",
    "entities",
    "phase",
    "updated_at",
    "image_url",
    "category_id",
    "edition_year",
    "photo_id",
    "person_id",
    "representative_id",
    "representation_status",
    "state_slug",
    "document_id",
    "visit_id",
    "dossier_concept",
    "service_type",
    "curp",
    "birth_date",
}


def _page_headers(nonce: str = "") -> dict[str, str]:
    csp = f"{SECURE_PAGE}; script-src 'nonce-{nonce}'" if nonce else SECURE_PAGE
    return {**PRIVATE_HEADERS, "Content-Security-Policy": csp}


def _year(value: str) -> int | None:
    if not value:
        return None
    if len(value) == 4 and value.isascii() and value.isdecimal():
        number = int(value)
        if 2000 <= number <= 2100:
            return number
    raise HTTPException(422, "La edición no es válida.")


def _cut_date(value: str) -> date | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value) if len(value) == 10 else None
        if parsed is not None and value == parsed.isoformat():
            return parsed
    except ValueError:
        pass
    raise HTTPException(422, "La fecha del corte no es válida.")


def _team_page(value: str) -> int:
    if not value:
        return 1
    if len(value) <= 4 and value.isascii() and value.isdecimal() and int(value):
        return int(value)
    raise HTTPException(422, "La página del padrón no es válida.")


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(422, "El identificador no es válido.") from None


def _selection(value: str, allowed, label: str) -> str:
    if value and value not in allowed:
        raise HTTPException(422, f"{label} fuera del alcance consultado.")
    return value


def _choices(values) -> list[tuple[str, str]]:
    return [(value, value) for value in sorted(set(values), key=str.casefold) if value]


def _edition_url(tournament_id: str, edition_year: int, **filters) -> str:
    values = {"tournament_id": tournament_id, "edition_year": edition_year, **filters}
    return f"{DOSSIER_PATH}?{urlencode(values)}"


def _record_domain(kind):
    domain = CAPTURE_FIELDS[kind]["domain"]
    return SENSITIVE_PARENT_DOMAINS.get(domain, domain)


def _target_progress(target: dict, registration: dict) -> list[tuple]:
    fields = target["fields"]
    canonical = next(
        (
            row
            for row in registration.get("rows", [])
            if all(
                row.get(key) == target[key] for key in ("phase", "entity", "category")
            )
            and row.get("gender") == fields["gender"]
            and row.get("teams") is not None
        ),
        None,
    )
    if canonical is not None:
        actual, source = canonical["teams"], canonical.get(
            "source"
        ) or registration.get("source")
    elif "actual_teams" in fields:
        actual, source = fields["actual_teams"], "Captura en SamChat"
    else:
        actual, source = None, None
    expected = fields["expected_teams"]
    progress = (
        f"{actual * 100 / expected:.1f} %" if actual is not None and expected else None
    )
    result = [
        ("Equipos reales en esta fase", actual),
        ("Fuente del conteo", source),
        ("Avance de la meta", progress),
    ]
    if actual is None:
        result.append(
            (
                "Alcance",
                "Falta un conteo acreditado para esta fase, entidad y categoría.",
            )
        )
    return result


def _record_requires_finance(row: dict) -> bool:
    return row["kind"] == "medical_transfer" or (
        row["kind"] == "visit" and bool(row["fields"].get("report_document_id"))
    )


def _view_record(
    row: dict,
    sources: dict,
    refs: dict,
    tournament_id: str,
    year: int,
    *,
    finance_allowed: bool,
) -> dict:
    kind = row["kind"]
    config = CAPTURE_FIELDS[kind]
    fields = row["fields"]
    protected_reference = _record_requires_finance(row) and not finance_allowed
    details = [
        (field["label"], _field_value(field["type"], fields.get(key)))
        for key, field in config["fields"].items()
        if field["type"]
        not in {
            "photo",
            "activation",
            "document",
            "visit",
            "expense",
            "contact_person",
            "medical_service",
            "medical_venue",
        }
        and not (kind == "team_target" and key == "actual_teams")
        and not (protected_reference and key == "report_reference")
    ]
    if kind == "national_event":
        details.append(("Duración (días)", fields.get("duration_days")))
    state = "documented"
    categories: list[str] = []
    document_url = None
    if kind == "team_target":
        details.extend(_target_progress(row, sources.get("registration", {})))
    if kind == "financial_link":
        canonical = refs["finance"].get(fields["document_id"])
        resolved = refs["resolved_finance"].get(fields["document_id"])
        if canonical is None:
            details.append(
                ("Documento asociado", "Fuente no disponible en esta edición")
            )
            state = "unavailable"
        elif resolved is not None and finance_link_matches(row, canonical):
            canonical = resolved
            categories = canonical.get("categories") or []
            details.extend(
                [
                    ("Fuente del documento", canonical.get("source")),
                    ("Estado del documento asociado", canonical.get("status")),
                    ("Actualización del documento", canonical.get("updated_at")),
                ]
            )
            details.extend(_source_details(canonical, include_category=False))
        else:
            details.append(
                ("Documento asociado", "El alcance actual requiere conciliación")
            )
            state = "requires_reconciliation"
    if kind == "expense_line_link":
        canonical = refs["finance_lines"].get(fields["expense_id"])
        if canonical is not None:
            details.append(("Fuente de la partida", canonical["source"]))
            details.append(("Actualización de la partida", canonical["updated_at"]))
            details.extend(_source_details(canonical, include_category=False))
        elif fields["expense_id"] in refs["known_expenses"]:
            state = "requires_reconciliation"
        else:
            state = "unavailable"
    if kind == "visit" and finance_allowed and fields.get("report_document_id"):
        report = refs["finance"].get(fields["report_document_id"])
        if (
            report is not None
            and report.get("kind") == "INFORME"
            and finance_link_matches(row, report)
        ):
            details.append(
                ("Referencia del informe en Gastos", report.get("reference"))
            )
            document_url = f"/documentos/{UUID(fields['report_document_id'])}"
    if kind == "medical_transfer":
        venue = refs["captures"].get(fields["venue_id"], {}).get("fields", {})
        service = (
            refs["captures"].get(fields["medical_service_id"], {}).get("fields", {})
        )
        details.extend(
            [
                ("Sede del traslado", venue.get("sports_complex") or venue.get("city")),
                (
                    "Servicio médico",
                    service.get("provider") or service.get("description"),
                ),
            ]
        )
    if kind == "sponsor_guest":
        activation = refs["captures"].get(fields["activation_id"], {}).get("fields", {})
        details.extend(
            [
                ("Activación", activation.get("activity")),
                ("Patrocinador", activation.get("sponsor")),
            ]
        )
    details.append(
        (
            "Referencia de respaldo",
            "Acceso restringido" if protected_reference else row["evidence_reference"],
        )
    )
    item = {
        **row,
        "label": config["label"],
        "details": details,
        "categories": categories,
        "source": "dossier_capture",
        "status": state,
        "can_edit": not protected_reference,
        "document_url": document_url,
        "search_text": " ".join(
            [config["label"], row["category"], row["entity"], "SamChat", *categories]
        ),
    }
    if kind == "activation_photo":
        item["image_url"] = f"{DOSSIER_PATH}/fotografias/{row['id']}?" + urlencode(
            {"tournament_id": tournament_id, "edition_year": year}
        )
    return item


def _field_value(field_type: str, value):
    return FIELD_CHOICES.get(field_type, {}).get(value, value)


def _source_details(
    row: dict, *, include_category: bool = True
) -> list[tuple[str, Any]]:
    details: list[tuple[str, Any]] = []
    for key, value in row.items():
        if key in SOURCE_METADATA or (key == "category" and not include_category):
            continue
        if key == "name" and value == row.get("label"):
            continue
        if key == "branch" and value == row.get("gender"):
            continue
        if key == "position" and row.get("representation_status"):
            continue
        if key == "ages" and isinstance(value, list):
            details.append(
                (
                    "Edades al corte",
                    [
                        f"{age['age'] if age.get('age') is not None else 'Edad sin dato'}: "
                        f"{age['registrations']}"
                        for age in value
                    ],
                )
            )
        elif key == "expense_dates" and isinstance(value, list):
            details.append((FIELD_LABELS[key], value))
        elif key == "expenses_by_currency" and isinstance(value, list):
            details.append(
                (
                    FIELD_LABELS[key],
                    [
                        f"{total['amount'] if total['amount'] is not None else 'Sin importe'} "
                        f"{total['currency'] or 'Moneda sin dato'} · {total['expenses']} "
                        f"{'partida' if total['expenses'] == 1 else 'partidas'}"
                        for total in value
                    ],
                )
            )
        elif isinstance(value, (str, int, float, bool)) or value is None:
            label = FIELD_LABELS.get(key, key.replace("_", " ").capitalize())
            details.append((label, value))
    return details


def _view_source(row: dict, section: dict, domain: str) -> dict:
    label = (
        row.get("label")
        or (row.get("position") if domain == "directory" else None)
        or DOMAIN_LABELS[domain]
    )
    source = row.get("source") or section.get("source")
    detail_row = (
        {key: row[key] for key in PRIVATE_DIRECTORY_FIELDS if key in row}
        if domain == "directory_private"
        else row
    )
    return {
        "label": label,
        "domain": domain,
        "entity": row.get("entity") or "",
        "entities": row.get("entities") or [],
        "categories": row.get("categories") or [],
        "phase": row.get("phase") or "",
        "category": row.get("category") or "",
        "source": source,
        "status": row.get("status") or section.get("status"),
        "updated_at": row.get("updated_at"),
        "details": _source_details(detail_row)
        + (
            [
                ("Corte de edad", section.get("age_cutoff")),
                ("Género según", section.get("gender_basis")),
            ]
            if domain == "registration" and "ages" in row
            else []
        ),
        "search_text": " ".join(
            str(value)
            for value in (
                DOMAIN_LABELS[domain],
                label if domain == "team_roster" else None,
                row.get("entity"),
                row.get("category"),
                row.get("phase"),
                source,
            )
            if value
        ),
    }


def _source_rows(sources: dict, shown: dict | None = None):
    shown = shown or {}
    for domain in DOMAIN_LABELS:
        section = sources.get(domain, {})
        for row in shown.get(domain, section.get("rows") or []):
            yield domain, section, row


def _scope_choices(sources: dict, records: list[dict]) -> tuple[set[str], set[str]]:
    entities = set(sources.get("entities") or [])
    categories = set()
    for row in (*[row for _, _, row in _source_rows(sources)], *records):
        if row.get("entity"):
            entities.add(row["entity"])
        if row.get("category"):
            categories.add(row["category"])
    options = sources.get("options") or {}
    categories.update(options.get("categories") or [])
    categories.update(sources.get("categories") or [])
    return entities, categories


def _belongs_to_entity(row: dict, entity: str) -> bool:
    return row.get("entity") == entity or entity in (row.get("entities") or [])


def _scope_matches(row: dict, selected: dict) -> bool:
    return (
        (not selected["entity"] or _belongs_to_entity(row, selected["entity"]))
        and (not selected["phase"] or row.get("phase") == selected["phase"])
        and (
            not selected["category"]
            or row.get("category") == selected["category"]
            or selected["category"] in (row.get("categories") or [])
        )
    )


def _sections(
    rows: list[dict],
    selected: dict,
    entities: set[str],
    domains: set[str],
    *,
    roster_only=False,
):
    names = []
    if not roster_only and selected["phase"] != "estatal":
        names.append(("nacional", "", "Fase nacional"))
    if selected["phase"] != "nacional":
        names.extend(
            ("", entity, entity)
            for entity, _label in _choices(
                {selected["entity"]} if selected["entity"] else entities
            )
        )

    def in_section(row, phase, entity):
        if phase == "nacional":
            return row.get("phase") == phase
        if entity:
            return row.get("phase") != "nacional" and _belongs_to_entity(row, entity)
        return (
            not row.get("entity")
            and not row.get("entities")
            and row.get("phase") != "nacional"
        )

    filtered = [row for row in rows if _scope_matches(row, selected)]
    if not selected["entity"] and any(in_section(row, "", "") for row in filtered):
        names.append(("", "", "Sin entidad o fase asignada"))
    result = []
    for phase, entity, name in names:
        matched = [row for row in filtered if in_section(row, phase, entity)]
        occupied = {row.get("kind") for row in matched}
        groups = []
        for domain, label in DOMAIN_LABELS.items():
            group_rows = [
                row
                for row in matched
                if (_record_domain(row["kind"]) if row.get("kind") else row["domain"])
                == domain
            ]
            pending = [
                spec["label"]
                for kind, spec in CAPTURE_FIELDS.items()
                if kind not in occupied
                and spec["domain"] in domains
                and _record_domain(kind) == domain
                and ("nacional" if phase == "nacional" else "estatal") in spec["phases"]
                and (phase == "nacional" or entity)
            ]
            if domain == "finance" and domain in domains and (phase or entity):
                concepts_present = {
                    row["fields"]["concept"]
                    for row in group_rows
                    if row.get("kind") in {"financial_link", "expense_line_link"}
                    and row.get("status") == "documented"
                }
                pending = [
                    label
                    for concept, label in FINANCE_CONCEPTS.items()
                    if (concept in STATE_CONCEPTS) == (phase != "nacional")
                    and concept not in concepts_present
                ]
            if group_rows or pending:
                groups.append(
                    {
                        "label": label,
                        "rows": group_rows,
                        "pending": pending,
                        "pending_label": (
                            "Sin vínculo" if domain == "finance" else "Sin captura"
                        ),
                    }
                )
        if groups or phase == "nacional" or entity:
            result.append({"name": name, "groups": groups})
    return result


def _team_slice(
    sources: dict,
    selected: dict,
    page: int,
    tournament_id: str,
    year: int,
    cut: date | None,
) -> tuple[list[dict], dict]:
    registration = sources.get("team_roster", {})
    rows = [
        row for row in registration.get("rows", []) if _scope_matches(row, selected)
    ]
    if not rows:
        return [], {}
    pages = (len(rows) + TEAM_PAGE_SIZE - 1) // TEAM_PAGE_SIZE
    page = min(page, pages)
    start = (page - 1) * TEAM_PAGE_SIZE
    end = min(start + TEAM_PAGE_SIZE, len(rows))
    filters = {key: value for key, value in selected.items() if value}
    if cut is not None:
        filters["cut_date"] = cut.isoformat()
    return rows[start:end], {
        "first": start + 1,
        "last": end,
        "total": len(rows),
        "partial": registration.get("status") == "partial",
        "previous": (
            _edition_url(tournament_id, year, **filters, team_page=page - 1)
            if page > 1
            else None
        ),
        "next": (
            _edition_url(tournament_id, year, **filters, team_page=page + 1)
            if page < pages
            else None
        ),
    }


def _capture_config(
    allowed: set,
    sources: dict,
    records: list[dict],
    edition_year: int,
    expenses: list[dict],
    *,
    finance_allowed: bool,
) -> dict:
    financial_links = {
        row["fields"]["document_id"]: row
        for row in records
        if row["kind"] == "financial_link"
    }
    choices = {
        "boolean": [("true", "Sí"), ("false", "No")],
        "currency": [(value, value) for value in ("MXN", "USD", "EUR")],
        **{key: list(values.items()) for key, values in FIELD_CHOICES.items()},
        "visit": [
            (row["id"], f"{row['fields']['visit_date']} · {row['entity']}")
            for row in records
            if row["kind"] == "visit"
        ],
        "activation": [
            (row["id"], row["fields"]["activity"])
            for row in records
            if row["kind"] == "activation"
        ],
        "photo": [
            (row["id"], row["label"])
            for row in sources.get("marketing", {}).get("candidates", [])
        ],
        "medical_venue": [
            (
                row["id"],
                row["fields"].get("sports_complex") or row["fields"]["city"],
            )
            for row in records
            if row["kind"] == "national_event"
        ],
        "medical_service": [
            (
                row["id"],
                row["fields"].get("provider")
                or row["fields"].get("description")
                or "Servicio médico registrado",
            )
            for row in records
            if row["kind"] == "medical_service"
        ],
    }
    suggestions = {
        "contact_person": [
            (
                row["representative_id"],
                f"{row['representative_name']} · {row['entity']}",
            )
            for row in sources.get("directory", {}).get("rows", [])
            if row.get("representative_id") and row.get("representative_name")
        ],
        "expense": [
            (
                expense["id"],
                expense.get("expense_reference")
                or expense.get("reference")
                or expense["id"],
            )
            for expense in expenses
        ],
    }
    html_types = {
        "integer": "number",
        "positive_integer": "number",
        "money": "number",
        "date": "date",
        "time": "time",
        "email": "email",
        "tel": "tel",
    }
    result = {}
    for kind, spec in CAPTURE_FIELDS.items():
        if spec["domain"] not in allowed or (
            kind == "medical_transfer" and not finance_allowed
        ):
            continue
        fields = {}
        for key, field in spec["fields"].items():
            if key == "report_document_id" and not finance_allowed:
                continue
            value_type = field["type"]
            fields[key] = {
                **field,
                "html_type": html_types.get(value_type, "text"),
                "min": 1 if value_type == "positive_integer" else 0,
                "max_length": 800,
            }
            if value_type in choices:
                fields[key]["choices"] = choices[value_type]
            if value_type in suggestions:
                fields[key]["suggestions"] = suggestions[value_type]
            if value_type == "document":
                fields[key]["suggestions"] = [
                    (row["id"], row.get("reference") or row.get("label") or row["id"])
                    for row in sources.get("finance", {}).get("rows", [])
                    if row.get("id")
                    and (key != "report_document_id" or row.get("kind") == "INFORME")
                    and (
                        row.get("edition_year", edition_year) == edition_year
                        or (
                            row["id"] in financial_links
                            and finance_link_matches(financial_links[row["id"]], row)
                        )
                    )
                ]
        result[kind] = {
            **spec,
            "phase_options": [(phase, PHASES[phase]) for phase in spec["phases"]],
            "fields": fields,
        }
    return result


async def _form_values(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Formato de formulario no admitido.")
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_CAPTURE_BYTES:
            raise HTTPException(413, "La captura excede el tamaño admitido.")
        payload.extend(chunk)
    try:
        pairs = parse_qsl(
            payload.decode("utf-8"),
            keep_blank_values=True,
            errors="strict",
            max_num_fields=24,
        )
    except (ValueError, UnicodeError):
        raise HTTPException(422, "El formulario no es válido.") from None
    values = dict(pairs)
    if len(values) != len(pairs):
        raise HTTPException(422, "El formulario contiene campos repetidos.")
    return values


def _form_signature(values: dict[str, str], employee_id: str) -> str:
    binding = {
        key: value for key, value in values.items() if key not in {"intent", "receipt"}
    }
    body = json.dumps(
        {"employee_id": employee_id, "values": binding},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _capture_signer() -> URLSafeTimedSerializer:
    secret = (os.getenv("SESSION_SECRET_KEY") or "").strip()
    if not secret:
        raise HTTPException(503, "La firma de captura no está configurada.")
    return URLSafeTimedSerializer(secret, salt="samchat.direction.dossier.capture.v1")


async def _trusted_directory_identity(
    resolver, employee_id: UUID
) -> RepresentativeSourceIdentity | None:
    try:
        identity = resolver(employee_id)
        if isawaitable(identity):
            identity = await identity
    except Exception:
        # A connector identity failure must not expose claims or disable the public dossier.
        return None
    if (
        isinstance(identity, RepresentativeSourceIdentity)
        and identity.employee_id == employee_id
    ):
        return identity
    return None


def create_dossier_router(guard, source_access, is_superadmin) -> APIRouter:
    """Register dossier paths under the same positions and portfolios as Direction."""
    router = APIRouter(tags=["direction-executive"])

    async def scoped_tournament(session, employee, requested=""):
        await guard(session, employee)
        tournaments = await _authorized_tournaments(
            session, str(employee.id), is_superadmin=is_superadmin(employee)
        )
        if not requested:
            return (tournaments[0] if tournaments else None), tournaments
        requested = _uuid(requested)
        selected = next((item for item in tournaments if item["id"] == requested), None)
        if selected is None:
            raise HTTPException(403, "El torneo está fuera del alcance asignado.")
        return selected, tournaments

    async def authorized_sources(session, employee) -> set[str]:
        access = await source_access(session, employee)
        domains = await read_domains(session, employee)
        if not access["finance"]:
            domains.discard("finance")
        return domains

    async def scoped_editions(
        session, tournament: dict, domains: set[str]
    ) -> list[int]:
        return await known_editions(
            session,
            tournament["id"],
            operations_allowed="operations" in domains,
            finance_allowed="finance" in domains,
        )

    async def edition_data(
        session,
        tournament: dict,
        edition_year: int,
        domains: set[str],
        years: list[int],
        cut_date: date | None = None,
        request: Request | None = None,
        employee_id: UUID | None = None,
    ):
        registry_reader = load_dossier_registry
        source_identity = None
        resolver = (
            getattr(request.app.state, "direction_ctt_private_identity", None)
            if request is not None and cut_date is None and "contacts" in domains
            else None
        )
        if callable(resolver) and employee_id is not None:
            registry = await registry_reader(
                session,
                tournament=tournament,
                edition_year=edition_year,
                operations_allowed="operations" in domains,
                contacts_allowed=True,
                cut_date=None,
            )

            async def cached_registry(*_args, **_kwargs):
                return registry

            registry_reader = cached_registry
            if any(
                row.get("representative_id")
                for row in registry.get("directory", {}).get("rows", [])
            ):
                source_identity = await _trusted_directory_identity(
                    resolver, employee_id
                )
        view = await read_dossier_view(
            session,
            tournament=tournament,
            edition_year=edition_year,
            domains=domains,
            cut_date=cut_date,
            years=years,
            source_identity=source_identity,
            employee_id=employee_id,
            record_reader=read_records,
            source_reader=load_dossier_sources,
            registry_reader=registry_reader,
            expense_reader=read_expense_lines,
        )
        output_sources = {**view["sources"]}
        if source_identity is None:
            output_sources.pop("directory_private", None)
        if (
            request is not None
            and cut_date is None
            and "contacts" in domains
            and output_sources.get("directory", {}).get("rows")
        ):
            output_sources.setdefault(
                "directory_private",
                {"source": PRIVATE_DIRECTORY_SOURCE, "status": "denied", "rows": []},
            )
        return {**view, "sources": output_sources}

    @router.get("/direccion", include_in_schema=False)
    async def direction_entry(
        session=Depends(get_db_session), employee=Depends(get_current_empleado)
    ):
        await guard(session, employee)
        return RedirectResponse(
            "/direccion/inicio", status_code=303, headers=PRIVATE_HEADERS
        )

    @router.get("/direccion/expediente-vivo", response_class=HTMLResponse)
    async def dossier(
        request: Request,
        session=Depends(get_db_session),
        employee=Depends(get_current_empleado),
    ):
        params = request.query_params
        cut = _cut_date(params.get("cut_date", ""))
        team_page = _team_page(params.get("team_page", ""))
        tournament, tournaments = await scoped_tournament(
            session, employee, params.get("tournament_id", "")
        )
        domains = await authorized_sources(session, employee)
        editions = (
            await scoped_editions(session, tournament, domains) if tournament else []
        )
        edition_year = _year(params.get("edition_year", "")) or next(
            iter(editions), None
        )
        selected = {key: params.get(key, "") for key in ("entity", "phase", "category")}
        nonce = secrets.token_urlsafe(24)
        context = {
            "tournament": tournament,
            "tournaments": [(item["id"], item["name"]) for item in tournaments],
            "editions": [(year, year) for year in editions],
            "edition_year": edition_year,
            "sources": [],
            "nonce": nonce,
            "saved": params.get("guardado") == "1",
            "cut_date": cut.isoformat() if cut else None,
        }
        if tournament and edition_year:
            dossier_view = await edition_data(
                session,
                tournament,
                edition_year,
                domains,
                editions,
                cut,
                request=request,
                employee_id=UUID(str(employee.id)),
            )
            captures, sources = dossier_view["captures"], dossier_view["sources"]
            records = captures["rows"]
            entities, categories = _scope_choices(sources, records)
            _selection(selected["phase"], PHASES, "Fase")
            _selection(selected["entity"], entities, "Entidad")
            _selection(selected["category"], categories, "Categoría")
            finance_rows = {
                row["id"]: row
                for row in sources.get("finance", {}).get("rows", [])
                if "finance" in domains and row.get("id")
            }
            linked_finance_ids = {
                row["fields"]["document_id"]
                for row in records
                if row["kind"] == "financial_link"
                and row["fields"]["document_id"] in finance_rows
                and finance_link_matches(
                    row, finance_rows[row["fields"]["document_id"]]
                )
            }
            roster_rows, roster_pages = _team_slice(
                sources, selected, team_page, tournament["id"], edition_year, cut
            )
            source_rows = {
                "finance": [
                    row
                    for row in dossier_view["resolved_finance_documents"]
                    if row.get("id") not in linked_finance_ids
                ],
                "finance_lines": [],
                "team_roster": roster_rows,
            }
            rows = [
                _view_source(row, section, domain)
                for domain, section, row in _source_rows(sources, source_rows)
            ]
            expense_candidates = dossier_view["expense_candidates"]
            references = {
                "finance": finance_rows,
                "resolved_finance": {
                    row["id"]: row
                    for row in dossier_view["resolved_finance_documents"]
                    if "finance" in domains and row.get("id")
                },
                "finance_lines": {
                    row["id"]: row
                    for row in sources.get("finance_lines", {}).get("rows", [])
                },
                "known_expenses": {row["id"] for row in expense_candidates},
                "captures": {row["id"]: row for row in records},
            }
            rows.extend(
                _view_record(
                    row,
                    sources,
                    references,
                    tournament["id"],
                    edition_year,
                    finance_allowed="finance" in domains,
                )
                for row in records
            )
            write_access = set()
            if cut is None and captures.get("status") in {"available", "partial"}:
                write_access = domains & await capture_permissions(session, employee)
            capture_config = _capture_config(
                write_access,
                sources,
                records,
                edition_year,
                expense_candidates,
                finance_allowed="finance" in domains,
            )
            if not dossier_view["edition_verified"]:
                capture_config = {
                    kind: config
                    for kind, config in capture_config.items()
                    if kind == "operations_edition"
                }
            elif sources.get("operations", {}).get(
                "edition_verified"
            ) and not operations_edition(records):
                capture_config.pop("operations_edition", None)
            context.update(
                sources=[
                    (label, sources[domain])
                    for domain, label in DOMAIN_LABELS.items()
                    if domain in sources
                ]
                + (
                    [("Captura", {**captures, "source": "dossier_capture"})]
                    if cut is None
                    else []
                ),
                registration_summary=(
                    _view_source(
                        sources["registration"]["summary"],
                        sources["registration"],
                        "registration",
                    )
                    if sources.get("registration", {}).get("summary")
                    and not any(selected.values())
                    else None
                ),
                queried_at=sources.get("queried_at"),
                roster_observed_at=sources.get("team_roster", {}).get("observed_at"),
                roster_pages=roster_pages,
                selected=selected,
                sections=_sections(
                    rows,
                    selected,
                    entities,
                    domains if cut is None else set(),
                    roster_only=cut is not None,
                ),
                entity_options=_choices(entities),
                category_options=_choices(categories),
                phase_options=list(PHASES.items()),
                writable=bool(capture_config),
                csrf=(
                    request.session.setdefault(CSRF_KEY, secrets.token_urlsafe(32))
                    if cut is None
                    else ""
                ),
                capture_config=capture_config,
                blank={**selected, "fields": {}},
            )
        return HTMLResponse(render_dossier(**context), headers=_page_headers(nonce))

    @router.post("/direccion/expediente-vivo/captura", response_class=HTMLResponse)
    async def capture(
        request: Request,
        session=Depends(get_db_session),
        employee=Depends(get_current_empleado),
    ):
        values = await _form_values(request)
        expected = request.session.get(CSRF_KEY, "")
        submitted = values.get("csrf", "")
        if (
            not expected
            or not submitted.isascii()
            or not secrets.compare_digest(expected, submitted)
        ):
            raise HTTPException(403, "La captura no pertenece a esta sesión.")
        tournament, _tournaments = await scoped_tournament(
            session, employee, _uuid(values.get("tournament_id", ""))
        )
        edition_year = _year(values.get("edition_year", ""))
        if edition_year is None:
            raise HTTPException(422, "Selecciona una edición.")
        kind = values.get("kind", "")
        config = CAPTURE_FIELDS.get(kind)
        if config is None:
            raise HTTPException(422, "Tipo de captura no válido.")
        control_fields = {
            "csrf",
            "tournament_id",
            "edition_year",
            "phase",
            "entity",
            "category",
            "kind",
            "evidence_reference",
            "intent",
            "record_id",
            "expected_revision",
            "request_id",
            "receipt",
        }
        if set(values) - control_fields - {f"field_{key}" for key in config["fields"]}:
            raise HTTPException(422, "El formulario contiene campos no permitidos.")
        if config["domain"] not in await capture_permissions(session, employee):
            raise HTTPException(403, "La captura requiere permiso específico.")
        domains = await authorized_sources(session, employee)
        if config["domain"] not in domains:
            raise HTTPException(403, "No tienes acceso a la fuente de esta captura.")
        finance_allowed = "finance" in domains
        if not finance_allowed and (
            kind == "medical_transfer"
            or (kind == "visit" and "field_report_document_id" in values)
        ):
            raise HTTPException(403, "El respaldo requiere acceso a Finanzas.")
        editions = await scoped_editions(session, tournament, domains)
        dossier_view = await edition_data(
            session, tournament, edition_year, domains, editions
        )
        captures, sources = dossier_view["captures"], dossier_view["sources"]
        if kind != "operations_edition" and not dossier_view["edition_verified"]:
            raise HTTPException(422, "Vincula primero una fuente para esta edición.")
        if captures.get("status") not in {"available", "partial"}:
            raise HTTPException(503, "La fuente de captura no está disponible.")
        scope = {key: values.get(key, "") for key in ("phase", "entity", "category")}
        entities, categories = _scope_choices(sources, captures["rows"])
        if kind != "participant":
            _selection(scope["entity"], entities, "Entidad")
        _selection(scope["category"], categories, "Categoría")
        fields = {
            key.removeprefix("field_"): value
            for key, value in values.items()
            if key.startswith("field_")
        }
        evidence = values.get("evidence_reference", "")
        record_id = _uuid(values["record_id"]) if values.get("record_id") else None
        revision = values.get("expected_revision", "")
        if bool(record_id) != bool(revision) or (
            revision
            and (
                not revision.isascii() or not revision.isdecimal() or len(revision) > 9
            )
        ):
            raise HTTPException(422, "La revisión de captura no es válida.")
        if record_id:
            existing = next(
                (row for row in captures["rows"] if row["id"] == record_id), None
            )
            if not existing or existing["kind"] != kind:
                raise HTTPException(404, "El registro no existe en este alcance.")
            if not finance_allowed and _record_requires_finance(existing):
                raise HTTPException(403, "El respaldo requiere acceso a Finanzas.")
            if any(existing[key] != value for key, value in scope.items()):
                raise HTTPException(422, "El alcance del registro es fijo.")
            if str(existing["revision"]) != revision:
                raise HTTPException(409, "La captura cambió. Vuelve a consultarla.")
        try:
            validated = validate_record(kind, scope, fields, evidence)
        except DossierRecordError as exc:
            raise HTTPException(422, str(exc)) from None
        person_id = validated["fields"].get("person_id")
        if (
            kind == "contact"
            and validated["fields"].get("contact_role") == "representante_local"
            and person_id
            and not any(
                contact.get("representative_id") == person_id
                and contact.get("entity") == validated["entity"]
                and contact.get("representation_status") == "confirmed"
                for contact in sources.get("directory", {}).get("rows", [])
            )
        ):
            raise HTTPException(
                422,
                "El representante indicado no coincide con el directorio y la entidad.",
            )
        if kind == "activation_photo":
            photo = next(
                (
                    item
                    for item in sources.get("marketing", {}).get("candidates", [])
                    if item["id"] == validated["fields"]["photo_id"]
                ),
                None,
            )
            if photo is None or (
                (scope["category"] or photo.get("category"))
                and scope["category"] != photo.get("category")
            ):
                raise HTTPException(
                    422, "La fotografía no acredita esta edición y categoría."
                )
        employee_id = str(employee.id)
        signer = _capture_signer()
        if values.get("intent") == "preview":
            values["request_id"] = str(uuid4())
            values["receipt"] = signer.dumps(_form_signature(values, employee_id))
            details = [
                ("Fase", PHASES[validated["phase"]]),
                ("Entidad", validated["entity"]),
                ("Categoría", validated["category"]),
                ("Referencia de respaldo", validated["reference"]),
            ]
            details.extend(
                (
                    field["label"],
                    _field_value(field["type"], validated["fields"].get(key)),
                )
                for key, field in config["fields"].items()
            )
            return HTMLResponse(
                render_capture_preview(
                    tournament=tournament["name"],
                    edition_year=edition_year,
                    return_url=_edition_url(tournament["id"], edition_year),
                    label=config["label"],
                    details=details,
                    hidden={
                        key: value for key, value in values.items() if key != "intent"
                    },
                ),
                headers=_page_headers(),
            )
        if values.get("intent") != "confirm":
            raise HTTPException(422, "Revisa la captura antes de guardarla.")
        try:
            received = signer.loads(
                values.get("receipt", ""), max_age=CAPTURE_TTL_SECONDS
            )
            if not isinstance(received, str) or not secrets.compare_digest(
                received, _form_signature(values, employee_id)
            ):
                raise BadData("Receipt mismatch")
        except BadData:
            raise HTTPException(
                403, "La revisión caducó o cambió. Repite la revisión."
            ) from None
        try:
            await save_record(
                session,
                tournament_id=tournament["id"],
                edition_year=edition_year,
                employee_id=employee_id,
                scope=scope,
                kind=kind,
                fields=fields,
                evidence_reference=evidence,
                record_id=record_id,
                expected_revision=int(revision) if revision else None,
                request_id=_uuid(values.get("request_id", "")),
            )
            await session.commit()
        except DossierRecordError as exc:
            await session.rollback()
            raise HTTPException(409, str(exc)) from None
        return RedirectResponse(
            _edition_url(tournament["id"], edition_year, guardado=1),
            status_code=303,
            headers=PRIVATE_HEADERS,
        )

    @router.get(
        "/direccion/expediente-vivo/fotografias/{record_id}", include_in_schema=False
    )
    async def photo(
        record_id: str,
        request: Request,
        session=Depends(get_db_session),
        employee=Depends(get_current_empleado),
    ):
        tournament, _tournaments = await scoped_tournament(
            session, employee, _uuid(request.query_params.get("tournament_id", ""))
        )
        domains = await read_domains(session, employee)
        if "media" not in domains:
            raise HTTPException(403, "La fotografía requiere autorización específica.")
        edition_year = _year(request.query_params.get("edition_year", ""))
        if edition_year is None:
            raise HTTPException(422, "Selecciona una edición.")
        record_id = _uuid(record_id)
        captures = await read_records(
            session,
            tournament_id=tournament["id"],
            edition_year=edition_year,
            allowed_domains=domains & {"media", "marketing", "operations"},
        )
        record = next(
            (
                row
                for row in captures["rows"]
                if row["id"] == record_id
                and row["kind"] == "activation_photo"
                and row["phase"] == "nacional"
            ),
            None,
        )
        if not record or not any(
            activation["id"] == record["fields"]["activation_id"]
            and activation["kind"] == "activation"
            and activation["phase"] == record["phase"]
            and activation["entity"] == record["entity"]
            and activation["category"] in ("", record["category"])
            for activation in captures["rows"]
        ):
            raise HTTPException(404, "Fotografía no disponible.")
        result = await resolve_dossier_photo(
            session,
            tournament,
            edition_year,
            record["fields"]["photo_id"],
            expected_category=record["category"] or None,
            operations_edition_id=operations_edition(captures["rows"]),
        )
        if result is None:
            raise HTTPException(404, "Fotografía no disponible.")
        image, media_type = result
        return Response(
            image,
            media_type=media_type,
            headers={
                **PRIVATE_HEADERS,
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    return router
