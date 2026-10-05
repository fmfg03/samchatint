#!/usr/bin/env python3
"""Reconcile local visual and automatic pilot evidence without canonical writes.

The private output retains personal evidence. Only aggregate counts reach stdout.
No database, provider, admission, or apply operation is supported.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path


class EvidenceError(ValueError):
    """Source integrity or identity cannot be established."""


def sha256(path: Path) -> str:
    """Hash a local source document without modifying it."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path):
    """Load source evidence; never include personal content in errors."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceError("Missing or invalid local evidence document") from exc


def source_identity(source: dict) -> tuple:
    """Require exact PDF name, hash, and ordered, distinct page identity."""
    pdf, digest, pages = (
        source.get("pdf"),
        source.get("pdf_sha256"),
        source.get("pages"),
    )
    if (
        not isinstance(pdf, str)
        or not pdf
        or Path(pdf).name != pdf
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
        or not isinstance(pages, list)
        or not pages
        or any(type(p) is not int or p < 1 for p in pages)
        or len(set(pages)) != len(pages)
    ):
        raise EvidenceError("Invalid exact source identity")
    return pdf, digest, tuple(pages)


def pending_fields(value, prefix="") -> list[str]:
    """Collect evidence paths that explicitly record unresolved information."""
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if (key.startswith("pending_") and item is True) or (
                key in {"status", "resolution", "presence"}
                and isinstance(item, str)
                and any(
                    term in item.lower()
                    for term in ("unresolved", "uncertain", "missing", "ambiguous")
                )
            ):
                result.append(path)
            result.extend(pending_fields(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(pending_fields(item, f"{prefix}[{index}]"))
    return result


def compare_fields(visual: dict, automatic: dict) -> list[dict]:
    """Compare field evidence, retaining missing fields separately from nulls."""
    reviewed = visual.get("source_review_fields", {})
    generated = automatic.get("fields", {})
    differences = []
    for ref in sorted(set(reviewed) | set(generated)):
        left, right = reviewed.get(ref, {}), generated.get(ref, {})
        left_value, right_value = left.get("value"), right.get("value")
        if ref not in reviewed or ref not in generated or left_value != right_value:
            differences.append(
                {
                    "field_ref": ref,
                    "visual_field_present": ref in reviewed,
                    "automatic_field_present": ref in generated,
                    "visual_value": left_value,
                    "automatic_value": right_value,
                    "visual_evidence": left,
                    "automatic_evidence": right,
                }
            )
    return differences


def reconcile(reference: Path, automatic_root: Path, pdf_root: Path) -> dict:
    """Prepare a faithful local report; fail closed on missing/ambiguous sources."""
    inventory_path = automatic_root / "inventory.json"
    report_path = automatic_root / "report.json"
    inventory, report = load_json(inventory_path), load_json(report_path)
    try:
        raw_lines = reference.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in raw_lines if line.strip()]
    except (OSError, ValueError) as exc:
        raise EvidenceError("Missing or invalid reference JSONL") from exc
    if inventory.get("reference_sha256") != sha256(reference):
        raise EvidenceError("Reference JSONL hash mismatch")
    if not rows or inventory.get("reference_documents") != len(rows):
        raise EvidenceError("Reference document count mismatch")
    files = inventory.get("files", [])
    document_entries = report.get("documents", [])
    seen_ids, seen_sources, documents = set(), set(), []
    for row in rows:
        document_id = row.get("team_document_id")
        identity = source_identity(row)
        if not document_id or document_id in seen_ids or identity in seen_sources:
            raise EvidenceError("Missing or ambiguous reference document identity")
        seen_ids.add(document_id)
        seen_sources.add(identity)
        pdf, digest, pages = identity
        file_matches = [item for item in files if item.get("pdf") == pdf]
        if len(file_matches) != 1 or file_matches[0].get("sha256") != digest:
            raise EvidenceError("Missing or ambiguous inventory PDF identity")
        if max(pages) > file_matches[0].get("pages", 0):
            raise EvidenceError("Source pages exceed inventoried PDF")
        try:
            actual_digest = sha256(pdf_root / pdf)
        except OSError as exc:
            raise EvidenceError("Missing source PDF") from exc
        if actual_digest != digest:
            raise EvidenceError("Source PDF hash mismatch")
        matches = [
            item for item in document_entries if item.get("document_id") == document_id
        ]
        if len(matches) != 1:
            raise EvidenceError("Missing or ambiguous automatic document identity")
        entry = matches[0]
        source = entry.get("source", {})
        if (
            source_identity(source) != identity
            or source.get("team_document_id") != document_id
        ):
            raise EvidenceError("Automatic document source mismatch")
        prediction_path = Path(entry.get("prediction_path", ""))
        if not prediction_path.is_absolute():
            prediction_path = automatic_root / prediction_path
        if prediction_path.resolve().parent != (automatic_root / "documents").resolve():
            raise EvidenceError("Automatic prediction outside documents directory")
        prediction = load_json(prediction_path)
        auto_pages = prediction.get("source_pages", [])
        if [item.get("page") for item in auto_pages] != list(pages) or any(
            item.get("pdf") != pdf or item.get("pdf_sha256") != digest
            for item in auto_pages
        ):
            raise EvidenceError("Automatic prediction page source mismatch")
        for field in prediction.get("fields", {}).values():
            evidence = field.get("evidence", {})
            if evidence and (
                evidence.get("pdf") != pdf
                or evidence.get("pdf_sha256") != digest
                or evidence.get("page") not in pages
            ):
                raise EvidenceError("Automatic field source mismatch")
        documents.append(
            {
                "document_id": document_id,
                "source": source,
                "source_pdf_hash_verified": True,
                "visual_reference": row,
                "automatic_report_entry": entry,
                "automatic_prediction": prediction,
                "automatic_prediction_sha256": sha256(prediction_path),
                "differences": compare_fields(row, prediction),
                "pending_visual_field_paths": pending_fields(row),
                "pending_automatic_field_paths": pending_fields(prediction),
                "admission": {
                    "state": "PENDING_CANONICAL_ADMISSION",
                    "authenticated_actor_id": None,
                    "canonical_receipts": None,
                    "blockers": [
                        "AUTHENTICATED_CANONICAL_ACTOR_REQUIRED",
                        "TOURNAMENT_EDITION_SCOPE_REQUIRED",
                        "GOVERNED_SESSION_ASSET_DRAFT_RECEIPTS_REQUIRED",
                        "IMMUTABLE_BATCH_ADMISSION_CONTRACT_PENDING",
                        "MULTIPLE_TECHNICAL_STAFF_PERSISTENCE_PENDING",
                        "COMMIT_TIME_INCIDENT_AND_ELIGIBILITY_RECALCULATION_REQUIRED",
                    ],
                },
            }
        )
    return {
        "schema_version": "ctt-pilot-reconciliation-v1",
        "no_database_write": True,
        "no_provider_calls": True,
        "canonical_import_ready": False,
        "grants_eligibility": False,
        "reference_sha256": sha256(reference),
        "automatic_inventory": inventory,
        "automatic_report_metadata": {
            k: v for k, v in report.items() if k != "documents"
        },
        "documents": documents,
        "summary": {
            "documents": len(documents),
            "differences": sum(len(d["differences"]) for d in documents),
            "pending_admissions": len(documents),
            "source_pdfs_verified": len({d["source"]["pdf"] for d in documents}),
        },
    }


def write_private_report(output: Path, report: dict) -> None:
    """Create a new private report, refusing overwrite and source symlinks."""
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main(argv=None) -> int:
    """Run a strictly local preparation; deliberately no apply argument."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--automatic-root", type=Path, required=True)
    parser.add_argument("--pdf-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = reconcile(args.reference, args.automatic_root, args.pdf_root)
        write_private_report(args.output, report)
    except (EvidenceError, OSError) as exc:
        # Never print paths, field values, or OS error content containing PII.
        reason = str(exc) if isinstance(exc, EvidenceError) else "Output unavailable"
        parser.exit(1, f"Reconciliation blocked: {reason}\n")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
