"""Fresh, hashed, company-scoped accounting source admission."""

import csv
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from samchat.accounting_historical import service


@pytest.fixture
def bundle(tmp_path):
    identity = {
        "fiscal_year": "2026",
        "company_code": "01",
        "company_label": "PSP1705058S4",
    }
    balances = [
        {
            **identity,
            "month_num": str(m),
            "account_code": "1120-001-001",
            "description": "Bank",
            "level": "3",
            "is_detail": "True",
            "saldo_inicial": "0",
            "cargos": "10",
            "abonos": "10",
            "saldo_final": "0",
            "row_number": str(m),
        }
        for m in range(1, 9)
    ]
    headers, lines = [], []
    for m in range(1, 10):
        pid = f"2026-{m:02d}:Dr:1"
        headers.append(
            {
                **identity,
                "month_num": str(m),
                "policy_id": pid,
                "policy_type": "Dr",
                "policy_number": "1",
                "policy_date": f"2026-{m:02d}-01",
                "description": "Original policy",
                "line_count": "2",
                "total_debe": "10.00",
                "total_haber": "10.00",
                "has_summary": "False",
                "summary_debe": "0",
                "summary_haber": "0",
            }
        )
        for k in range(2):
            lines.append(
                {
                    **identity,
                    "month_num": str(m),
                    "policy_id": pid,
                    "row_number": str(m * 2 + k),
                    "account_code": "1120-001-001",
                    "account_name": "Bank",
                    "debe": "10.00" if k == 0 else "0.00",
                    "haber": "0.00" if k == 0 else "10.00",
                    "concept": "Original entry",
                }
            )
    document = {
        "schema_version": "samchat.historical.explicit.v1",
        "fiscal_year": 2026,
        "company_code": "01",
        "company_label": "PSP1705058S4",
        "derived": {},
        "originals": [],
    }
    datasets = {
        "trial_balance": balances,
        "policy_headers": headers,
        "policy_lines": lines,
    }

    def write():
        for name, rows in datasets.items():
            p = tmp_path / (name + ".csv")
            with p.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            document["derived"][name] = {
                "path": p.name,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
        if not document["originals"]:
            for name in ["trial_balance", "policy_lines"]:
                p = tmp_path / (name + ".xlsx")
                p.write_bytes(b"original evidence " + name.encode())
                document["originals"].append(
                    {
                        "path": p.name,
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                        "source_family": "original_" + name,
                    }
                )
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(document))
        return path

    return document, datasets, write


def load(bundle):
    return service._load_explicit_historical_manifest(
        fiscal_year=2026,
        source_path=str(bundle[2]()),
        company_code="01",
        company_label="PSP1705058S4",
    )


def test_complete_bundle_preserves_scope_provenance_and_missing_analytical_fields(
    bundle,
):
    manifest, balances, headers, lines, sources = load(bundle)
    assert len(balances) == 8 and len(headers) == 9 and len(lines) == 18
    assert not any(
        row.get("project_raw") or row.get("cost_center_raw") for row in lines
    )
    assert len(sources) == 6
    assert {x["source_family"] for x in sources} == {
        "original_trial_balance",
        "original_policy_lines",
        "balanza",
        "poliza_header",
        "poliza_line",
        "explicit_manifest",
    }
    validation = sources[-1]["metadata"]
    assert (
        validation["policy_months"][-1] == 9 and validation["balance_months"][-1] == 8
    )
    assert validation["september_balance_available"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "year",
        "company",
        "label",
        "source_hash",
        "row_company",
        "row_year",
        "duplicate_balance",
        "duplicate_header",
        "duplicate_line",
        "orphan",
        "date",
        "partial_balance",
        "partial_lines",
        "negative",
        "nan",
        "subcent",
        "unbalanced",
        "header_total",
        "count",
        "identity",
        "account",
    ],
)
def test_invalid_bundles_fail_closed(bundle, mutation):
    doc, data, write = bundle
    if mutation == "year":
        doc["fiscal_year"] = 2025
    elif mutation == "company":
        doc["company_code"] = "02"
    elif mutation == "label":
        doc["company_label"] = "wrong"
    elif mutation == "source_hash":
        path = write()
        p = path.parent / "trial_balance.csv"
        p.write_text(p.read_text() + "\n")
        with pytest.raises(ValueError, match="hash changed"):
            service._load_explicit_historical_manifest(
                fiscal_year=2026,
                source_path=str(path),
                company_code="01",
                company_label="PSP1705058S4",
            )
        return
    elif mutation == "row_company":
        data["policy_lines"][0]["company_code"] = "02"
    elif mutation == "row_year":
        data["trial_balance"][0]["fiscal_year"] = "2025"
    elif mutation == "duplicate_balance":
        data["trial_balance"].append(dict(data["trial_balance"][0], month_num="01"))
    elif mutation == "duplicate_header":
        data["policy_headers"].append(dict(data["policy_headers"][0]))
    elif mutation == "duplicate_line":
        data["policy_lines"][1]["row_number"] = data["policy_lines"][0]["row_number"]
    elif mutation == "orphan":
        data["policy_lines"][0]["policy_id"] = "missing"
    elif mutation == "date":
        data["policy_headers"][0]["policy_date"] = "2025-01-01"
    elif mutation == "partial_balance":
        data["trial_balance"].pop()
    elif mutation == "partial_lines":
        data["policy_lines"][:] = data["policy_lines"][:-2]
    elif mutation == "negative":
        data["policy_lines"][0]["debe"] = "-10"
    elif mutation == "nan":
        data["policy_lines"][0]["debe"] = "NaN"
    elif mutation == "subcent":
        data["policy_lines"][0]["debe"] = "10.001"
    elif mutation == "unbalanced":
        data["policy_lines"][0]["debe"] = "10.02"
        data["policy_headers"][0]["total_debe"] = "10.02"
    elif mutation == "header_total":
        data["policy_headers"][0]["total_debe"] = "11"
    elif mutation == "count":
        data["policy_headers"][0]["line_count"] = "3"
    elif mutation == "identity":
        data["policy_headers"][0]["policy_id"] = "arbitrary"
    elif mutation == "account":
        data["trial_balance"][0]["account_code"] = "unknown"
    with pytest.raises(ValueError):
        load(bundle)


def test_one_cent_source_difference_is_visible_and_not_balanced_by_invention(bundle):
    doc, data, write = bundle
    data["policy_lines"][0]["debe"] = "10.01"
    data["policy_headers"][0]["total_debe"] = "10.01"
    _, _, headers, lines, sources = load(bundle)
    assert (
        headers[0]["_explicit_rounding_difference"] == ".01"
        or headers[0]["_explicit_rounding_difference"] == "0.01"
    )
    assert lines[0]["debe"] == "10.01"
    assert sources[-1]["metadata"]["rounding_incidents"] == [
        {"policy_id": "2026-01:Dr:1", "difference": "0.01"}
    ]


@pytest.mark.asyncio
async def test_explicit_preview_is_fresh_and_never_initializes_schema(
    bundle, monkeypatch
):
    path = bundle[2]()
    schema = AsyncMock()
    snapshot = AsyncMock(return_value={"stale": True})
    monkeypatch.setattr(service, "ensure_historical_accounting_schema", schema)
    monkeypatch.setattr(service, "load_historical_accounting_snapshot", snapshot)
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [
        "accounting_import_runs",
        "historical_accounting_source_files",
        "historical_trial_balance_rows",
        "historical_policy_headers",
        "historical_policy_lines",
    ]
    session.execute.return_value = result
    value = await service.import_historical_accounting_pilot(
        session,
        fiscal_year=2026,
        source_kind="normalized_manifest",
        source_path=str(path),
        company_code="01",
        company_label="PSP1705058S4",
        ensure_schema=False,
    )
    assert value["created"] is False and value["summary"]["policy_lines"] == 18
    schema.assert_not_awaited()
    snapshot.assert_not_awaited()
    session.commit.assert_not_awaited()
    assert all(
        str(c.args[0]).lstrip().startswith("SELECT")
        for c in session.execute.await_args_list
    )


@pytest.mark.asyncio
async def test_explicit_requires_no_schema_and_complete_provisioned_owner_schema(
    bundle, monkeypatch
):
    path = bundle[2]()
    session = AsyncMock()
    with pytest.raises(ValueError, match="ensure_schema=False"):
        await service.import_historical_accounting_pilot(
            session,
            fiscal_year=2026,
            source_kind="normalized_manifest",
            source_path=str(path),
        )
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute.return_value = result
    with pytest.raises(ValueError, match="migration"):
        await service.import_historical_accounting_pilot(
            session,
            fiscal_year=2026,
            source_kind="normalized_manifest",
            source_path=str(path),
            ensure_schema=False,
        )


def test_material_cross_family_difference_is_reported_not_validated_ok(bundle):
    doc, data, write = bundle
    data["trial_balance"][0]["cargos"] = "11.00"
    _, _, _, _, sources = load(bundle)
    validation = sources[-1]["metadata"]
    assert validation["reconciliation_status"] == "review_required"
    issue = validation["material_reconciliation_issues"][0]
    assert issue["period"] == "2026-01" and issue["debit_difference"] == "-1.00"
    assert all(
        x["validation_status"] == "REVIEW_REQUIRED"
        for x in sources
        if x["source_family"]
        in {"balanza", "poliza_header", "poliza_line", "explicit_manifest"}
    )


@pytest.mark.asyncio
async def test_material_reconciliation_blocks_apply_before_any_write(bundle):
    doc, data, write = bundle
    data["trial_balance"][0]["cargos"] = "11.00"
    path = write()
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [
        "accounting_import_runs",
        "historical_accounting_source_files",
        "historical_trial_balance_rows",
        "historical_policy_headers",
        "historical_policy_lines",
    ]
    session.execute.return_value = result
    with pytest.raises(ValueError, match="reconciliation requires review"):
        await service.import_historical_accounting_pilot(
            session,
            fiscal_year=2026,
            source_kind="normalized_manifest",
            source_path=str(path),
            company_code="01",
            company_label="PSP1705058S4",
            ensure_schema=False,
            apply_changes=True,
            started_by_empleado_id="actor",
        )
    assert all(
        str(c.args[0]).lstrip().startswith("SELECT")
        for c in session.execute.await_args_list
    )
    session.commit.assert_not_awaited()


def test_balance_parent_leaf_flag_cannot_hide_reconciliation_difference(bundle):
    doc, data, write = bundle
    data["trial_balance"][0]["is_detail"] = "False"
    with pytest.raises(ValueError, match="leafness"):
        load(bundle)


@pytest.mark.asyncio
async def test_superseded_source_is_not_silently_reported_as_current(
    bundle, monkeypatch
):
    path = bundle[2]()
    session = AsyncMock()
    schema = AsyncMock()
    monkeypatch.setattr(service, "_require_explicit_historical_schema", schema)
    monkeypatch.setattr(
        service, "_find_existing_import_run_id", AsyncMock(return_value="old")
    )
    monkeypatch.setattr(
        service,
        "_find_latest_applied_import_run",
        AsyncMock(return_value={"id": "current"}),
    )
    with pytest.raises(ValueError, match="superseded"):
        await service.import_historical_accounting_pilot(
            session,
            fiscal_year=2026,
            source_kind="normalized_manifest",
            source_path=str(path),
            company_code="01",
            company_label="PSP1705058S4",
            ensure_schema=False,
            apply_changes=True,
            started_by_empleado_id="actor",
        )
    session.commit.assert_not_awaited()
    assert all("INSERT" not in str(c.args[0]) for c in session.execute.await_args_list)


def withhold_approved_months(bundle):
    doc, data, write = bundle
    path = write()
    months = {3, 4, 6, 7}
    evidence_path = path.parent / "full-validation.json"
    evidence_path.write_text(
        json.dumps(
            {
                "identity": {
                    "fiscal_year": 2026,
                    "company_code": "01",
                    "company_label": "PSP1705058S4",
                },
                "status": "REVIEW_REQUIRED",
            }
        )
    )
    evidence = {
        "path": str(evidence_path),
        "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
    }
    doc["full_validation_report"] = evidence
    doc["withheld_months"] = [
        {
            "month_num": m,
            "reason": "User-authorized whole-month quarantine",
            "evidence": dict(evidence),
        }
        for m in sorted(months)
    ]
    for name in data:
        data[name][:] = [r for r in data[name] if int(r["month_num"]) not in months]
    return doc, data, write


def test_evidenced_whole_month_quarantine_reports_partial_without_false_zero(bundle):
    withhold_approved_months(bundle)
    _, balances, headers, lines, sources = load(bundle)
    assert {int(r["month_num"]) for r in balances} == {1, 2, 5, 8}
    assert {int(r["month_num"]) for r in headers} == {1, 2, 5, 8, 9}
    assert {int(r["month_num"]) for r in lines} == {1, 2, 5, 8, 9}
    validation = sources[-1]["metadata"]
    assert validation["coverage_status"] == "partial_withheld_periods"
    assert [x["month_num"] for x in validation["withheld_months"]] == [3, 4, 6, 7]
    assert validation["balance_months"] == [1, 2, 5, 8]
    assert validation["material_reconciliation_issues"] == []
    assert any(x["source_family"] == "quarantine_evidence" for x in sources)


@pytest.mark.parametrize(
    "case",
    [
        "undocumented",
        "remaining_line",
        "remaining_header",
        "remaining_balance",
        "duplicate",
        "out_of_range",
        "no_reason",
        "no_evidence",
        "wrong_hash",
        "wrong_company",
        "different_family_coverage",
        "unapproved_extra_month",
        "all_withheld",
    ],
)
def test_quarantine_never_admits_partial_or_undocumented_scope(bundle, case):
    doc, data, write = bundle
    old = {k: [dict(r) for r in v] for k, v in data.items()}
    withhold_approved_months(bundle)
    if case == "undocumented":
        doc.pop("withheld_months")
    elif case == "remaining_line":
        data["policy_lines"].extend(
            r for r in old["policy_lines"] if r["month_num"] == "3"
        )
    elif case == "remaining_header":
        data["policy_headers"].extend(
            r for r in old["policy_headers"] if r["month_num"] == "3"
        )
    elif case == "remaining_balance":
        data["trial_balance"].extend(
            r for r in old["trial_balance"] if r["month_num"] == "3"
        )
    elif case == "duplicate":
        doc["withheld_months"].append(doc["withheld_months"][0])
    elif case == "out_of_range":
        doc["withheld_months"][0]["month_num"] = 13
    elif case == "no_reason":
        doc["withheld_months"][0]["reason"] = " "
    elif case == "no_evidence":
        doc["withheld_months"][0].pop("evidence")
    elif case == "wrong_hash":
        doc["withheld_months"][0]["evidence"]["sha256"] = "0" * 64
    elif case == "wrong_company":
        evidence = doc["full_validation_report"]
        p = __import__("pathlib").Path(evidence["path"])
        p.write_text(
            json.dumps(
                {
                    "identity": {
                        "fiscal_year": 2026,
                        "company_code": "02",
                        "company_label": "PMD0608162M2",
                    }
                }
            )
        )
        evidence["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
        for item in doc["withheld_months"]:
            item["evidence"] = dict(evidence)
    elif case == "different_family_coverage":
        doc["expected_coverage"] = {
            "trial_balance": [1, 2, 5, 8],
            "policy_headers": list(range(1, 10)),
            "policy_lines": list(range(1, 10)),
        }
    elif case == "unapproved_extra_month":
        doc["withheld_months"].append(dict(doc["withheld_months"][0], month_num=1))
    elif case == "all_withheld":
        doc["withheld_months"] = [
            dict(doc["withheld_months"][0], month_num=m) for m in range(1, 10)
        ]
    with pytest.raises(ValueError):
        load(bundle)


def test_optional_full_report_is_hashed_even_without_withheld_months(bundle):
    doc, _, write = bundle
    path = write()
    proof = path.parent / "review.json"
    proof.write_text("{}")
    doc["full_validation_report"] = {"path": str(proof), "sha256": "0" * 64}
    with pytest.raises(ValueError, match="hash changed"):
        load(bundle)
