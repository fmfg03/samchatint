"""Read one authorized dossier for both the Direction UI and the private tool."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any
from uuid import UUID

from samchat.client_executive.dossier_directory_private import (
    RepresentativeSourceIdentity,
    read_directory_private,
)
from samchat.client_executive.dossier_finance import read_expense_lines, scope_matches
from samchat.client_executive.dossier_records import (
    document_category_matches,
    known_editions,
    read_records,
)
from samchat.client_executive.dossier_sources import load_dossier_sources


def finance_link_matches(capture: dict, document: dict) -> bool:
    phase = document.get("phase")
    if (phase or str(document.get("phase_label") or "").strip()) and phase != capture[
        "phase"
    ]:
        return False
    categories = document.get("categories") or (
        [document["category"]] if document.get("category") else []
    )
    return document_category_matches(capture["category"], categories)


def operations_edition(records: list[dict]) -> str | None:
    links = [
        row["fields"]["operations_tournament_id"]
        for row in records
        if row["kind"] == "operations_edition"
    ]
    return links[0] if len(links) == 1 else None


def _document_context(capture: dict) -> dict:
    return {
        "entity": capture["entity"],
        "phase": capture["phase"],
        "category": capture["category"],
        "dossier_concept": capture["fields"]["concept"],
        "service_type": capture["fields"].get("service_type"),
        "visit_id": capture["fields"].get("visit_id"),
    }


def _finance_view(
    documents: list[dict], records: list[dict], expense_lines: list[dict], edition: int
) -> tuple[list[dict], list[dict]]:
    # Keep original document totals and attributed line totals mutually exclusive.
    whole_links = {
        row["fields"]["document_id"]: row
        for row in records
        if row["kind"] == "financial_link"
    }
    line_links = {
        row["fields"]["expense_id"]: row
        for row in records
        if row["kind"] == "expense_line_link"
    }
    attributed = []
    for line in expense_lines:
        capture = line_links.get(line["id"])
        if (
            capture is None
            or capture["fields"].get("source_document_id") != line["document_id"]
            or line["document_id"] in whole_links
            or not scope_matches(line, capture["phase"], capture["category"])
        ):
            continue
        attributed.append(
            {
                "id": line["id"],
                "label": line.get("expense_reference") or line.get("reference"),
                "document_id": line["document_id"],
                "expense_reference": line.get("expense_reference"),
                "reference": line.get("reference"),
                "amount": line["amount"],
                "currency": line["currency"],
                "expense_date": line["expense_date"],
                "updated_at": line["updated_at"],
                "source": line["source"],
                "status": "documented",
                **_document_context(capture),
            }
        )
    partitioned = {row["document_id"] for row in attributed}
    resolved = []
    for document in documents:
        key = document.get("id")
        if key in partitioned:
            continue
        capture = whole_links.get(key)
        if capture and finance_link_matches(capture, document):
            metadata = _document_context(capture)
            # A capture may leave category empty in a valid multi-category record.
            metadata["category"] = metadata["category"] or document.get("category")
            resolved.append({**document, **metadata})
        elif document.get("edition_year", edition) == edition:
            resolved.append(document)
    return resolved, attributed


def _label_operator_transfers(documents: list[dict], *, full_source: bool) -> None:
    """Group transfers by entity and day without ordering payments within a tie."""
    if not full_source:
        return
    first: dict[str, str] = {}
    qualified = []
    for row in documents:
        entity = row.get("entity")
        effective = row.get("effective_payment_date")
        method = str(row.get("payment_method") or "").strip().casefold()
        if (
            entity
            and isinstance(effective, str)
            and row.get("dossier_concept") == "ayuda_operador"
            and row.get("proof_available") is True
            and method in {"transferencia", "transferencia bancaria", "spei"}
        ):
            first[entity] = min(effective, first.get(entity, effective))
            qualified.append(row)
    for row in qualified:
        row["aid_payment_stage"] = (
            "Primera fecha vinculada con transferencia acreditada"
            if first[row["entity"]] == row["effective_payment_date"]
            else "Transferencia posterior acreditada"
        )


async def read_dossier_view(
    session: Any,
    *,
    tournament: dict,
    edition_year: int,
    domains: set[str],
    cut_date: date | None = None,
    directory_contacts_allowed: bool = False,
    source_identity: RepresentativeSourceIdentity | None = None,
    employee_id: UUID | None = None,
    years: list[int] | None = None,
    record_reader=read_records,
    source_reader=load_dossier_sources,
    registry_reader=None,
    expense_reader=read_expense_lines,
) -> dict:
    """Call only after resolving identity, tournament scope, and current domains."""
    if registry_reader is None:
        from samchat.client_executive.dossier_registry import load_dossier_registry

        registry_reader = load_dossier_registry
    if years is None:
        years = await known_editions(
            session,
            tournament["id"],
            operations_allowed="operations" in domains,
            finance_allowed="finance" in domains,
        )
    registry = await registry_reader(
        session,
        tournament=tournament,
        edition_year=edition_year,
        operations_allowed="operations" in domains,
        contacts_allowed=directory_contacts_allowed or "contacts" in domains,
        cut_date=cut_date,
    )
    if cut_date is not None:
        # A dated roster cut cannot lend its date to current dossier facts.
        historic = {
            key: value
            for key, value in registry.items()
            if key
            in {"team_roster", "registration", "entities", "categories", "cut_date"}
        }
        return {
            "sources": historic,
            "captures": {"status": "not_applicable", "rows": []},
            "expense_candidates": [],
            "resolved_finance_documents": [],
            "edition_verified": (
                edition_year in years or registry.get("edition_verified") is True
            ),
        }

    captures = await record_reader(
        session,
        tournament_id=tournament["id"],
        edition_year=edition_year,
        allowed_domains=domains,
    )
    rows = captures["rows"]
    report_links = [
        row["fields"]["report_document_id"]
        for row in rows
        if row["kind"] == "visit" and row["fields"].get("report_document_id")
    ]
    linked_documents = (
        tuple(
            dict.fromkeys(
                [
                    row["fields"]["document_id"]
                    for row in rows
                    if row["kind"] == "financial_link"
                ]
                + report_links
            )
        )
        if "finance" in domains
        else ()
    )
    raw_sources = await source_reader(
        session,
        tournament=tournament,
        edition_year=edition_year,
        finance_allowed="finance" in domains,
        contacts_allowed="contacts" in domains,
        media_allowed="media" in domains,
        linked_document_ids=linked_documents,
        operations_allowed="operations" in domains,
        marketing_allowed="marketing" in domains,
        operations_edition_id=operations_edition(rows),
    )
    sources = dict(raw_sources)
    sources.pop("directory_private", None)
    sources["entities"] = sorted(
        set(sources.get("entities") or []) | set(registry.get("entities") or [])
    )
    sources["categories"] = sorted(
        set(sources.get("categories") or []) | set(registry.get("categories") or [])
    )
    for key in ("directory", "team_roster"):
        if key in registry:
            section = registry[key]
            sources[key] = {
                **section,
                "rows": [dict(row) for row in section.get("rows", [])],
            }
    directory = sources.get("directory")
    if directory is not None:
        registered_entities = {
            row["entity"]
            for row in sources.get("team_roster", {}).get("rows", [])
            if row.get("entity")
        }
        for row in directory["rows"]:
            row["participation_status"] = (
                "Con equipos en el padrón de esta edición"
                if row.get("entity") in registered_entities
                else "Participación sin acreditar en las fuentes consultadas"
            )
        if (
            "contacts" in domains
            and employee_id is not None
            and source_identity is not None
        ):
            sources["directory_private"] = await read_directory_private(
                directory,
                source_identity,
                employee_id=employee_id,
            )
    documents = sources.get("finance", {}).get("rows", [])
    expense: dict[str, Any] = {"status": "denied", "rows": []}
    if "finance" in domains:
        expense = await expense_reader(
            session,
            tournament_id=tournament["id"],
            edition_year=edition_year,
            document_ids=tuple(row["id"] for row in documents if row.get("id")),
            expense_ids=tuple(
                row["fields"]["expense_id"]
                for row in rows
                if row["kind"] == "expense_line_link"
            ),
        )
    resolved, attributed = _finance_view(documents, rows, expense["rows"], edition_year)
    visits = {row["id"]: row for row in rows if row["kind"] == "visit"}
    for item in (*resolved, *attributed):
        visit = visits.get(item.get("visit_id"))
        if (
            visit
            and visit["entity"] == item["entity"]
            and visit["phase"] == item["phase"]
        ):
            item["visit_date"] = visit["fields"]["visit_date"]
    _label_operator_transfers(
        resolved,
        full_source=(
            sources.get("finance", {}).get("status") == "available"
            and captures.get("status") == "available"
        ),
    )
    line_status = expense["status"]
    if line_status == "available":
        linked_lines = Counter(item["document_id"] for item in attributed)
        candidates = Counter(item["document_id"] for item in expense["rows"])
        known_documents = {item.get("id") for item in documents}
        requested = {
            row["fields"]["expense_id"]
            for row in rows
            if row["kind"] == "expense_line_link"
        }
        if (
            requested != {item["id"] for item in attributed}
            or not linked_lines.keys() <= known_documents
            or any(candidates[key] > count for key, count in linked_lines.items())
        ):
            line_status = "partial"
    sources["finance_lines"] = {
        "source": "Gastos · Partidas de informes",
        "status": line_status,
        "rows": attributed,
    }
    return {
        "sources": sources,
        "captures": captures,
        "expense_candidates": expense["rows"],
        "resolved_finance_documents": resolved,
        "edition_verified": edition_year in years
        or any(
            section.get("edition_verified") is True
            for section in sources.values()
            if isinstance(section, dict)
        )
        or registry.get("edition_verified") is True,
    }
