"""Read source declarations without rewriting financial or upload history."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REGISTRY_PATH = (
    Path(__file__).resolve().parents[3] / "config/budget_source_provenance_2026.json"
)
REGISTRY_SHA256 = "5258290ec65019483b2c540ce662bbd42cf9b385e2a6d17c80561295f1de5b0a"


def _amount(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _registry(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != REGISTRY_SHA256:
        raise ValueError("Budget source authority changed without review")
    document = json.loads(raw)
    if (
        document.get("schema_version") != "samchat.budget.source_provenance.v1"
        or document.get("source_kind") != "copa_20261028"
        or document.get("receipt", {}).get("status") != "COMMITTED_VERIFIED"
        or document.get("currency") != "MXN"
        or document.get("tax_basis") != "includes_vat"
    ):
        raise ValueError("Unrecognized budget source authority")
    rows = document.get("lines")
    if not isinstance(rows, list) or len(rows) != 43:
        raise ValueError("Incomplete committed budget source authority")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("id")) in indexed:
            raise ValueError("Duplicate or invalid budget source identity")
        marker = row.get("marker") or {}
        if (
            not row.get("id")
            or not row.get("budget_version_id")
            or not row.get("tournament_id")
            or not row.get("budget_concept_id")
            or type(marker.get("row")) is not int
            or marker["row"] < 1
            or marker.get("plan_sha256") != document["receipt"]["plan_sha256"]
            or _amount(marker.get("amount")) is None
            or _amount(marker.get("amount")) != _amount(row.get("budget_amount"))
            or not isinstance(marker.get("source_sha256"), str)
            or len(marker["source_sha256"]) != 64
        ):
            raise ValueError("Budget source authority disagrees with its receipt")
        indexed[str(row["id"])] = row
    return document, indexed, hashlib.sha256(raw).hexdigest()


def resolve_budget_line_source(line: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve existing declarations or exact committed Copa provenance.

    The returned basis describes the budget only. It does not attest the fiscal
    basis of expenses, ledger entries or payments and does not authorize a delta.
    """
    unavailable = {
        "status": "unavailable",
        "currency": None,
        "tax_basis": None,
        "evidence_ref": None,
    }
    metadata = line.get("metadata")
    if not isinstance(metadata, Mapping):
        return unavailable
    marker = metadata.get("copa_20261028")
    if marker is not None:
        # A forged legacy declaration cannot override a mismatched Copa marker.
        if not isinstance(marker, Mapping):
            return unavailable
        try:
            registry, indexed, registry_sha256 = _registry(REGISTRY_PATH)
        except (OSError, ValueError, KeyError, TypeError):
            return unavailable
        expected = indexed.get(str(line.get("id") or ""))
        if expected is None:
            return unavailable
        for key in (
            "budget_version_id",
            "tournament_id",
            "budget_concept_id",
            "phase",
        ):
            if str(line.get(key) or "") != str(expected[key] or ""):
                return unavailable
        if dict(marker) != expected["marker"] or _amount(
            line.get("budget_amount")
        ) != _amount(expected["budget_amount"]):
            return unavailable
        return {
            "status": "verified",
            "currency": registry["currency"],
            "tax_basis": registry["tax_basis"],
            "evidence_ref": {
                "kind": "copa_20261028_committed_receipt",
                "source_sha256": marker["source_sha256"],
                "row": marker["row"],
                "plan_sha256": marker["plan_sha256"],
                "receipt_sha256": registry["receipt"]["receipt_sha256"],
                "registry_sha256": registry_sha256,
            },
        }
    declared = metadata.get("upload_source")
    if (
        isinstance(declared, Mapping)
        and isinstance(declared.get("currency"), str)
        and declared["currency"].strip()
        and declared.get("tax_basis") in {"includes_vat", "excludes_vat"}
    ):
        return {
            "status": "declared",
            "currency": declared["currency"],
            "tax_basis": declared["tax_basis"],
            "evidence_ref": {"kind": "existing_upload_source"},
        }
    return unavailable


def summarize_budget_line_sources(lines: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Describe budget provenance; unknown zero rows never acquire attestations."""
    verified_positive = unverified_positive = unverified_zero = verified_zero = 0
    known_bases: set[tuple[str, str]] = set()
    for line in lines:
        amount = _amount(line.get("budget_amount"))
        source = resolve_budget_line_source(line)
        known = source["status"] in {"verified", "declared"}
        if amount is not None and amount == 0:
            if known:
                verified_zero += 1
            else:
                unverified_zero += 1
            continue
        if known and amount is not None:
            verified_positive += 1
            known_bases.add((source["currency"], source["tax_basis"]))
        else:
            unverified_positive += 1
    consistent = bool(known_bases) and len(known_bases) == 1
    currency, basis = next(iter(known_bases)) if consistent else (None, None)
    complete = (
        bool(lines) and consistent and not (unverified_positive or unverified_zero)
    )
    return {
        "status": (
            "available" if complete else "partial" if known_bases else "unavailable"
        ),
        "currency": currency if consistent and not unverified_positive else None,
        "tax_basis": basis if consistent and not unverified_positive else None,
        "verified_positive_count": verified_positive,
        "unverified_positive_count": unverified_positive,
        "verified_zero_count": verified_zero,
        "unverified_zero_count": unverified_zero,
        "comparison_authorized": False,
    }
