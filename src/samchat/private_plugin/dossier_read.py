"""Opt-in dossier read adapter for the authenticated Direction connector."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from jsonschema import Draft202012Validator

from .contracts import Denied, Identity
from .direction_read import DirectionContext, DirectionReadAdapter, _scope_key

ACTION = "direction_read_dossier"
CONTRACT = "samchat.private.direction.dossier.v1"
_PAGE_SIZE = 20
_PHASES = ("estatal", "nacional")
_SOURCE_DOMAINS = {
    "directory": "operations",
    "team_roster": "operations",
    "registration": "operations",
    "operations": "operations",
    "finance": "finance",
    "finance_lines": "finance",
    "marketing": "marketing",
}
_PROTECTED_SECTIONS = frozenset(
    {"contacts", "sponsor_guests", "medical_transfers", "media"}
)
_HISTORIC_SOURCES = frozenset({"team_roster", "registration"})
_SCOPE_ID = {
    "type": "string",
    "pattern": r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$",
}
_YEAR = {"type": "integer", "minimum": 2000, "maximum": 2100}
_DATE = {"type": "string", "pattern": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
_SHORT = {"type": "string", "minLength": 1, "maxLength": 160}
_OFFSET = {"type": "integer", "minimum": 0, "maximum": 100_000}
_DOSSIER_PATH = "/direccion/expediente-vivo"


def _closed(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


_SELECTORS = {
    "tournament_id": _SCOPE_ID,
    "year": _YEAR,
    "portfolio_id": _SCOPE_ID,
    "entity": _SHORT,
    "phase": {"enum": list(_PHASES)},
    "category": _SHORT,
    "cut_date": _DATE,
    "section": {"enum": [*_SOURCE_DOMAINS, "captures"]},
}
DOSSIER_INPUT_SCHEMA = _closed(
    {**_SELECTORS, "offset": _OFFSET}, ["tournament_id", "year"]
)
_INPUT_VALIDATOR = Draft202012Validator(DOSSIER_INPUT_SCHEMA)

# Closed producer-and-field allowlist. Capture notes, personal and clinical
# records, attachment bytes, evidence-reference text and raw URLs never enter it.
_FINANCE_AMOUNTS = (
    "amount_requested",
    "amount_document",
    "amount_paid",
    "currency",
    "effective_payment_date",
    "requested_at",
    "proof_available",
    "document_status",
    "dossier_concept",
    "service_type",
    "payment_method",
    "aid_payment_stage",
    "expense_date",
    "amount",
    "reference",
    "expense_reference",
    "supplier",
    "visit_date",
    "visit_id",
)
_SOURCE_FIELDS = {
    "directory": (
        "state_code",
        "state_slug",
        "zone",
        "representation_status",
        "participation_status",
        "representative_name",
    ),
    "team_roster": (
        "name",
        "state_code",
        "state_slug",
        "gender",
        "branch",
        "player_registrations",
    ),
    "registration": (
        "gender",
        "teams",
        "registrations",
        "unique_people_identified",
        "player_records",
        "registrations_without_verified_identity",
        "age_cutoff",
        "ages",
    ),
    "operations": (
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "team",
        "date",
        "match_status",
        "phase_label",
        "played",
        "points",
        "goal_difference",
        "field_number",
    ),
    "finance": _FINANCE_AMOUNTS,
    "finance_lines": _FINANCE_AMOUNTS,
    "marketing": (),
}
_CAPTURE_FIELDS = {
    "participant": (),
    "operations_edition": (),
    "team_target": ("gender", "expected_teams", "actual_teams"),
    "player_breakdown": (
        "reported_gender",
        "age",
        "age_cutoff",
        "registrations",
        "unique_people",
    ),
    "state_phase": ("referee_fee", "transport_fee", "currency"),
    "team_result": (
        "team_name",
        "round",
        "advanced",
        "national_qualifier",
        "final_position",
    ),
    "uniform_delivery": ("delivery_date", "location", "units"),
    "travel": ("departure", "return_date", "origin", "destination"),
    "national_event": (
        "city",
        "start_date",
        "end_date",
        "duration_days",
        "sports_complex",
        "fields",
        "field_count",
        "field_types",
    ),
    "national_itinerary": ("event_type", "event_date", "event_time", "venue"),
    "lodging": ("hotel", "bed_nights"),
    "meals": ("meal_type", "meal_kind", "servings"),
    "medical_service": ("transfers",),
    "visit": ("visit_date", "responsible", "visit_id"),
    "activation": (
        "supplier",
        "supplier_on_site",
        "sponsor",
        "visitors",
        "activity",
        "activity_date",
    ),
}
_KIND_FIELDS = {
    "operations": frozenset(_CAPTURE_FIELDS) - {"activation"},
    "marketing": frozenset({"activation"}),
}

_AGE_BAND = _closed(
    {
        "age": _nullable({"type": "integer", "minimum": 0, "maximum": 150}),
        "registrations": {"type": "integer", "minimum": 0},
    }
)
_FACT_VALUE = {
    "anyOf": [
        {"type": "string", "maxLength": 200},
        {"type": ["integer", "boolean", "null"]},
        {"type": "array", "items": _AGE_BAND, "maxItems": 120},
    ]
}
_FIELDS = _closed(
    {
        field: _FACT_VALUE
        for fields in (*_SOURCE_FIELDS.values(), *_CAPTURE_FIELDS.values())
        for field in fields
    },
    [],
)
_PROVENANCE = {
    "source": _nullable(_SHORT),
    "status": _SHORT,
    "updated_at": _nullable(_SHORT),
    "observed_at": _nullable(_SHORT),
}
_FACT = _closed(
    {
        **_PROVENANCE,
        "section": {"enum": [*_SOURCE_DOMAINS, "captures"]},
        "type": _SHORT,
        "entity": _nullable(_SHORT),
        "phase": _nullable({"enum": list(_PHASES)}),
        "category": _nullable(_SHORT),
        "fields": _FIELDS,
    }
)
DOSSIER_OUTPUT_SCHEMA = _closed(
    {
        "schema": {"const": CONTRACT},
        "read_only": {"const": True},
        "scope": _closed(
            {
                "tournament_id": _SCOPE_ID,
                "portfolio_id": _nullable(_SCOPE_ID),
                "edition_year": _YEAR,
                "edition_verified": {"type": "boolean"},
                "entity": _nullable(_SHORT),
                "phase": _nullable({"enum": list(_PHASES)}),
                "category": _nullable(_SHORT),
                "cut_date": _nullable(_DATE),
                "section": _nullable({"enum": [*_SOURCE_DOMAINS, "captures"]}),
            }
        ),
        "tournament_ids": {
            "type": "array",
            "items": _SCOPE_ID,
            "minItems": 1,
            "maxItems": 1,
        },
        "consulted_at": _nullable(_SHORT),
        "sources": _closed(
            {
                section: _closed(_PROVENANCE)
                for section in (*_SOURCE_DOMAINS, "captures")
            },
            [],
        ),
        "facts": {"type": "array", "items": _FACT, "maxItems": _PAGE_SIZE},
        "protected_sections": {
            "type": "array",
            "items": {"enum": sorted(_PROTECTED_SECTIONS)},
            "uniqueItems": True,
        },
        "web_url": {"type": "string", "pattern": r"^https://", "maxLength": 2500},
        "pagination": _closed(
            {
                "offset": _OFFSET,
                "next_offset": _nullable(_OFFSET),
                "page_size": {"const": _PAGE_SIZE},
                "matched_available": {"type": "integer", "minimum": 0},
            }
        ),
    }
)
_OUTPUT_VALIDATOR = Draft202012Validator(DOSSIER_OUTPUT_SCHEMA)


@dataclass(frozen=True)
class DossierQuery:
    tournament_id: str
    year: int
    portfolio_id: str | None = None
    entity: str | None = None
    phase: str | None = None
    category: str | None = None
    cut_date: date | None = None
    section: str | None = None
    offset: int = 0


def parse_dossier_query(arguments: dict) -> DossierQuery:
    if type(arguments) is not dict or not _INPUT_VALIDATOR.is_valid(arguments):
        raise Denied("INVALID_ARGUMENTS")
    if type(arguments["year"]) is not int or (
        "offset" in arguments and type(arguments["offset"]) is not int
    ):
        raise Denied("INVALID_ARGUMENTS")
    values = dict(arguments)
    for key in ("tournament_id", "portfolio_id"):
        if key in values and str(UUID(values[key])) != values[key]:
            raise Denied("INVALID_ARGUMENTS")
    for key in ("entity", "category"):
        if key in values and _short(values[key]) != values[key]:
            raise Denied("INVALID_ARGUMENTS")
    if "cut_date" in values:
        try:
            cut_date = date.fromisoformat(values["cut_date"])
        except ValueError:
            raise Denied("INVALID_ARGUMENTS") from None
        values["cut_date"] = cut_date
        if cut_date.year < 2000 or cut_date.year > 2100:
            raise Denied("INVALID_ARGUMENTS")
    return DossierQuery(**values)


def _short(value: Any, limit: int = 160) -> str | None:
    if isinstance(value, (date, datetime)):
        value = value.isoformat()
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    return value.strip()


def _facts_only(values: Mapping[str, Any], allowed: tuple[str, ...]) -> dict:
    result: dict[str, Any] = {}
    for key in allowed:
        if key not in values:
            continue
        value = values[key]
        if key == "ages":
            if isinstance(value, list):
                bands = [
                    {"age": band.get("age"), "registrations": band.get("registrations")}
                    for band in value
                    if isinstance(band, dict)
                ]
                result[key] = [band for band in bands if _AGE_VALIDATOR.is_valid(band)][
                    :120
                ]
        elif value is None or type(value) in {int, bool}:
            result[key] = value
        elif isinstance(value, Decimal):
            result[key] = _short(str(value), 200) if value.is_finite() else None
        else:
            result[key] = _short(value, 200)
    return result


_AGE_VALIDATOR = Draft202012Validator(_AGE_BAND)


def _includes(values: Any, expected: str) -> bool:
    return isinstance(values, (list, tuple)) and expected in values


def _matches(row: Mapping[str, Any], query: DossierQuery) -> bool:
    return (
        (
            query.entity is None
            or query.entity == row.get("entity")
            or query.entity == row.get("state_slug")
            or _includes(row.get("entities"), query.entity)
        )
        and (query.phase is None or row.get("phase") == query.phase)
        and (
            query.category is None
            or row.get("category") == query.category
            or _includes(row.get("categories"), query.category)
        )
    )


def _provenance(row: Mapping[str, Any], source: Mapping[str, Any]) -> dict:
    return {
        "source": _short(row.get("source")) or _short(source.get("source")),
        "status": _short(row.get("status"))
        or _short(source.get("status"))
        or "unavailable",
        "updated_at": _short(row.get("updated_at")) or _short(source.get("updated_at")),
        "observed_at": _short(row.get("observed_at"))
        or _short(source.get("observed_at")),
    }


def _fact(
    section: str,
    kind: str,
    row: Mapping[str, Any],
    source: Mapping[str, Any],
    fields: dict,
) -> dict:
    return {
        **_provenance(row, source),
        "type": kind,
        "section": section,
        "entity": _short(row.get("entity")),
        "category": _short(row.get("category")),
        "phase": row.get("phase") if row.get("phase") in _PHASES else None,
        "fields": fields,
    }


def _rows(section: Mapping[str, Any]) -> list[dict]:
    rows = section.get("rows") or []
    return [row for row in rows if isinstance(row, dict)]


def _with_entity_label(query: DossierQuery, view: dict) -> DossierQuery:
    if query.entity is None or view.get("edition_verified") is not True:
        return query
    directory = view["sources"].get("directory", {})
    rows = _rows(directory) + _rows(view["sources"].get("team_roster", {}))
    labels = {
        label
        for row in rows
        if row.get("state_slug") == query.entity
        and (label := _short(row.get("entity")))
    }
    return replace(query, entity=labels.pop()) if len(labels) == 1 else query


def _source_facts(
    view: dict, query: DossierQuery, domains: set[str]
) -> tuple[dict, list[dict]]:
    sources: dict = {}
    facts: list[dict] = []
    verified = view.get("edition_verified") is True
    available_sources = view["sources"]
    for section, domain in _SOURCE_DOMAINS.items():
        if domain not in domains or query.section not in (None, section):
            continue
        if query.cut_date and section not in _HISTORIC_SOURCES:
            continue
        source = available_sources.get(section)
        if source is None:
            continue
        sources[section] = _provenance({}, source)
        if not verified:
            sources[section]["status"] = "needs_link"
            continue
        fields = _SOURCE_FIELDS[section]
        if section == "directory" and "contacts" not in domains:
            fields = tuple(field for field in fields if field != "representative_name")
        rows = (
            view.get("resolved_finance_documents", [])
            if section == "finance"
            else _rows(source)
        )
        for row in rows:
            if not _matches(row, query):
                continue
            kind = row.get("kind") if section == "finance" else None
            kind = kind if kind in {"SOLICITUD", "INFORME"} else section
            values = _facts_only(row, fields)
            if section == "registration" and "age_cutoff" not in values:
                values["age_cutoff"] = _short(source.get("age_cutoff"))
            facts.append(_fact(section, kind, row, source, values))
    return sources, facts


def _append_captures(
    view: dict, query: DossierQuery, domains: set[str], sources: dict, facts: list[dict]
) -> None:
    if (
        query.cut_date
        or query.section not in (None, "captures")
        or view.get("edition_verified") is not True
        or not domains.intersection(_KIND_FIELDS)
    ):
        return
    captures = view["captures"]
    source = {"source": "Expediente Vivo", **captures}
    sources["captures"] = _provenance({}, source)
    for row in _rows(captures):
        kind = row.get("kind")
        domain = row.get("domain")
        if (
            domain not in domains
            or not isinstance(kind, str)
            or kind not in _KIND_FIELDS.get(domain, ())
            or not _matches(row, query)
        ):
            continue
        fields = _facts_only(row.get("fields") or {}, _CAPTURE_FIELDS[kind])
        if kind == "visit":
            if fields.get("responsible") not in {"AZ", "CL"}:
                fields.pop("responsible", None)
            fields["visit_id"] = _short(row.get("id"))
        facts.append(_fact("captures", kind, row, source, fields))


@dataclass(frozen=True)
class DossierReadResult:
    data: dict
    authority: tuple[Any, ...]


class DossierReadAdapter:
    """Read one proven scope. A specific current owner grant is required."""

    def __init__(
        self,
        *,
        direction: DirectionReadAdapter,
        grant_for_query: Callable[[DirectionContext, DossierQuery], Awaitable[bool]],
        read_domains: Callable[..., Awaitable[set[str]]],
        read_view: Callable[..., Awaitable[dict]],
        web_origin: str,
    ):
        parsed = urlsplit(web_origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in web_origin)
        ):
            raise ValueError("Expected an approved HTTPS web origin")
        self._direction = direction
        self._grant = grant_for_query
        self._read_domains = read_domains
        self._view = read_view
        self._origin = web_origin.rstrip("/")

    async def _authorize(
        self, identity: Identity, query: DossierQuery
    ) -> tuple[DirectionContext, dict, set[str], tuple[Any, ...]]:
        context, args, scope, access, organization = (
            await self._direction.authorize_tournament(
                identity=identity,
                portfolio_id=query.portfolio_id,
                tournament_id=query.tournament_id,
            )
        )
        if await self._grant(context, query) is not True:
            raise Denied("FORBIDDEN")
        domains = set(await self._read_domains(context.session, context.employee))
        if not access["finance"]:
            domains.discard("finance")
        domains.intersection_update((*_PROTECTED_SECTIONS, *_SOURCE_DOMAINS.values()))
        if not domains.intersection(_SOURCE_DOMAINS.values()):
            raise Denied("FORBIDDEN")
        authority = (
            _scope_key(scope),
            organization,
            args["superadmin"],
            frozenset(domains),
        )
        return context, scope["selected"][0], domains, authority

    def _web_url(self, query: DossierQuery) -> str:
        selectors = {
            "tournament_id": query.tournament_id,
            "edition_year": query.year,
            "entity": query.entity,
            "phase": query.phase,
            "category": query.category,
            "cut_date": query.cut_date.isoformat() if query.cut_date else None,
        }
        values = {key: value for key, value in selectors.items() if value is not None}
        return f"{self._origin}{_DOSSIER_PATH}?{urlencode(values)}"

    async def verify_release(
        self, *, identity: Identity, query: DossierQuery, authority: tuple[Any, ...]
    ) -> None:
        _, _, _, current = await self._authorize(identity, query)
        if current != authority:
            raise Denied("SCOPE_CHANGED")

    async def read(
        self, *, identity: Identity, query: DossierQuery
    ) -> DossierReadResult:
        context, tournament, domains, authority = await self._authorize(identity, query)
        view = await self._view(
            context.session,
            tournament=tournament,
            edition_year=query.year,
            domains=domains.intersection(_SOURCE_DOMAINS.values()),
            cut_date=query.cut_date,
            directory_contacts_allowed="contacts" in domains and query.cut_date is None,
        )
        await self.verify_release(identity=identity, query=query, authority=authority)
        resolved = _with_entity_label(query, view)
        sources, facts = _source_facts(view, resolved, domains)
        _append_captures(view, resolved, domains, sources, facts)
        next_offset = query.offset + _PAGE_SIZE
        verified = view.get("edition_verified") is True
        data = {
            "schema": CONTRACT,
            "read_only": True,
            "scope": {
                "tournament_id": query.tournament_id,
                "portfolio_id": query.portfolio_id,
                "edition_year": query.year,
                "edition_verified": verified,
                "entity": resolved.entity,
                "phase": query.phase,
                "category": query.category,
                "cut_date": query.cut_date.isoformat() if query.cut_date else None,
                "section": query.section,
            },
            "tournament_ids": [query.tournament_id],
            "consulted_at": _short(
                view.get("queried_at") or view["sources"].get("queried_at")
            ),
            "sources": sources,
            "facts": facts[query.offset : next_offset],
            "protected_sections": (
                sorted(domains & _PROTECTED_SECTIONS)
                if verified and not query.cut_date
                else []
            ),
            "web_url": self._web_url(resolved),
            "pagination": {
                "matched_available": len(facts),
                "offset": query.offset,
                "next_offset": (
                    next_offset
                    if next_offset < len(facts) and next_offset <= 100_000
                    else None
                ),
                "page_size": _PAGE_SIZE,
            },
        }
        if not _OUTPUT_VALIDATOR.is_valid(data):
            raise Denied("CANONICAL_RESULT_INVALID")
        return DossierReadResult(data=data, authority=authority)


def create_direction_dossier_reader(
    *,
    direction: DirectionReadAdapter,
    grant_for_query: Callable[[DirectionContext, DossierQuery], Awaitable[bool]],
    web_origin: str,
) -> DossierReadAdapter:
    """Explicit canonical binding for a reviewed private connector host."""
    from samchat.client_executive.dossier_records import read_domains
    from samchat.client_executive.dossier_view import read_dossier_view

    return DossierReadAdapter(
        direction=direction,
        grant_for_query=grant_for_query,
        read_domains=read_domains,
        read_view=read_dossier_view,
        web_origin=web_origin,
    )
