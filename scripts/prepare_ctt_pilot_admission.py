#!/usr/bin/env python3
"""Prepare a private, non-writing admission manifest from reconciled evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from devnous.copa_telmex.batch_admission import (
    SCHEMA_VERSION,
    sha256_value,
    source_filename,
    validate_manifest,
)


def _field(person: dict, name: str):
    value = (person.get("fields") or {}).get(name)
    return value.get("value") if isinstance(value, dict) else None


def _staff(person: dict, slot: int) -> dict:
    role = "director_tecnico" if slot == 1 else "auxiliar"
    if person.get("role") != role:
        raise ValueError("Technical staff order is invalid")
    evidence = {
        name: value
        for name, value in (person.get("fields") or {}).items()
        if isinstance(value, dict)
    }
    identity = {
        "role": role,
        "slot": slot,
        "slot_ref": person.get("slot_ref"),
        "evidence": evidence,
    }
    return {
        "staff_entry_id": sha256_value(identity),
        "slot": slot,
        "role": role,
        "slot_ref": person.get("slot_ref"),
        "first_name": _field(person, "first_name"),
        "last_name": _field(person, "surnames"),
        "birth_date": _field(person, "birth_date"),
        "curp": _field(person, "curp"),
        "photo": person.get("photo"),
        "presence": person.get("presence"),
        "evidence": evidence,
    }


def prepare(reconciliation: dict, pdf_root: Path) -> dict:
    documents = []
    for item in reconciliation.get("documents") or []:
        source = item["source"]
        pdf_name = source_filename(source["pdf"])
        pdf = pdf_root / pdf_name
        if hashlib.sha256(pdf.read_bytes()).hexdigest() != source["pdf_sha256"]:
            raise ValueError("Source PDF hash mismatch")
        reference = item["visual_reference"]
        people = list(reference.get("people") or [])
        if len(people) != 2:
            raise ValueError("Technical staff evidence missing")
        extraction = dict(reference.get("source_reviewed_extraction_candidate") or {})
        extraction["staff"] = [_staff(people[0], 1), _staff(people[1], 2)]
        documents.append(
            {
                "document_id": item["document_id"],
                "pdf": pdf_name,
                "pdf_sha256": source["pdf_sha256"],
                "pages": source["pages"],
                "extraction": extraction,
            }
        )
    return validate_manifest(
        {
            "schema_version": SCHEMA_VERSION,
            "edition": {
                "tournament_id": "67c556df-f953-4f99-8cab-87ed4d111b05",
                "edition_year": 2026,
                "roster_slug": "copa-telmex-2026",
            },
            "documents": documents,
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconciliation", type=Path, required=True)
    parser.add_argument("--pdf-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = prepare(
        json.loads(args.reconciliation.read_text(encoding="utf-8")), args.pdf_root
    )
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "documents": len(manifest["documents"]),
                "manifest_sha256": manifest["manifest_sha256"],
                "writes_database": False,
                "grants_eligibility": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
