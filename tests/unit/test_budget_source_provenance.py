"""Approved Copa receipt compatibility never invents source or expense IVA."""

import json
from copy import deepcopy
from decimal import Decimal

import pytest

import devnous.gastos  # noqa: F401 -- same initialization order as live app
from samchat.budgets import source_provenance as source


@pytest.fixture
def copa_lines():
    registry = json.loads(source.REGISTRY_PATH.read_text())
    return [
        {
            **{k: v for k, v in row.items() if k != "marker"},
            "metadata": {"copa_20261028": deepcopy(row["marker"])},
        }
        for row in registry["lines"]
    ]


def test_all_committed_copa_rows_preserve_exact_identity_and_amount(copa_lines):
    before = deepcopy(copa_lines)
    assert len(copa_lines) == 43
    assert sum(Decimal(r["budget_amount"]) for r in copa_lines) == Decimal(
        "17471291.92"
    )
    for line in copa_lines:
        resolved = source.resolve_budget_line_source(line)
        assert resolved["status"] == "verified"
        assert resolved["currency"] == "MXN"
        assert resolved["tax_basis"] == "includes_vat"
        assert (
            resolved["evidence_ref"]["row"] == line["metadata"]["copa_20261028"]["row"]
        )
    assert copa_lines == before


@pytest.mark.parametrize(
    "key,value",
    [
        ("id", "another-line"),
        ("budget_version_id", "another-version"),
        ("tournament_id", "another-tournament"),
        ("budget_concept_id", "another-concept"),
        ("phase", "another-phase"),
        ("budget_amount", "0.01"),
        ("budget_amount", True),
        ("budget_amount", "NaN"),
    ],
)
def test_changed_copa_identity_or_economy_cannot_reuse_receipt(copa_lines, key, value):
    line = copa_lines[0]
    line[key] = value
    assert source.resolve_budget_line_source(line)["status"] == "unavailable"


@pytest.mark.parametrize("key", ["row", "source_sha256", "plan_sha256", "amount"])
def test_changed_marker_cannot_be_overridden_by_forged_upload_source(copa_lines, key):
    line = copa_lines[0]
    line["metadata"]["copa_20261028"][key] = "different"
    line["metadata"]["upload_source"] = {"currency": "MXN", "tax_basis": "includes_vat"}
    assert source.resolve_budget_line_source(line)["status"] == "unavailable"


def test_missing_registry_and_changed_bytes_fail_closed(
    copa_lines, tmp_path, monkeypatch
):
    path = tmp_path / "registry.json"
    monkeypatch.setattr(source, "REGISTRY_PATH", path)
    assert source.resolve_budget_line_source(copa_lines[0])["status"] == "unavailable"
    path.write_text("{}")
    assert source.resolve_budget_line_source(copa_lines[0])["status"] == "unavailable"


def test_registry_drift_is_detected_even_after_success(
    copa_lines, tmp_path, monkeypatch
):
    path = tmp_path / "registry.json"
    path.write_bytes(source.REGISTRY_PATH.read_bytes())
    monkeypatch.setattr(source, "REGISTRY_PATH", path)
    assert source.resolve_budget_line_source(copa_lines[0])["status"] == "verified"
    registry = json.loads(path.read_text())
    registry["lines"][0]["marker"]["source_sha256"] = "0" * 64
    path.write_text(json.dumps(registry))
    assert source.resolve_budget_line_source(copa_lines[0])["status"] == "unavailable"


def test_legacy_declaration_stays_declared_not_new_attestation():
    line = {
        "metadata": {"upload_source": {"currency": "MXN", "tax_basis": "excludes_vat"}}
    }
    resolved = source.resolve_budget_line_source(line)
    assert resolved["status"] == "declared" and resolved["tax_basis"] == "excludes_vat"
    assert source.resolve_budget_line_source({"metadata": {}})["currency"] is None
    assert (
        source.resolve_budget_line_source({"metadata": "invalid"})["tax_basis"] is None
    )


def test_unknown_zero_rows_remain_unattested_and_do_not_authorize_comparison(
    copa_lines,
):
    unknown = {"id": "legacy-zero", "budget_amount": "0", "metadata": {}}
    result = source.summarize_budget_line_sources([*copa_lines, unknown])
    assert result["status"] == "partial"
    assert result["verified_positive_count"] == 43
    assert result["unverified_zero_count"] == 1
    assert result["comparison_authorized"] is False
    assert source.resolve_budget_line_source(unknown)["status"] == "unavailable"


def test_unverified_positive_amount_keeps_aggregate_basis_unknown(copa_lines):
    unknown = {"budget_amount": "99840.00", "metadata": {}}
    result = source.summarize_budget_line_sources([copa_lines[0], unknown])
    assert result["unverified_positive_count"] == 1
    assert result["currency"] is None and result["tax_basis"] is None


def test_mixed_basis_or_currency_never_becomes_comparable(copa_lines):
    declared = {
        "budget_amount": "1",
        "metadata": {"upload_source": {"currency": "USD", "tax_basis": "includes_vat"}},
    }
    result = source.summarize_budget_line_sources([copa_lines[0], declared])
    assert result["currency"] is None and result["tax_basis"] is None
    assert result["comparison_authorized"] is False


def test_empty_or_unknown_amount_cannot_become_measured_zero():
    assert source.summarize_budget_line_sources([])["status"] == "unavailable"
    result = source.summarize_budget_line_sources(
        [{"budget_amount": None, "metadata": {}}]
    )
    assert result["unverified_positive_count"] == 1
    assert result["unverified_zero_count"] == 0
