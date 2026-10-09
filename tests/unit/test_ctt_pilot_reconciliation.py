"""Fidelity, identity and authority checks for private pilot preparation."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/reconcile_ctt_reviewed_pilot.py"
spec = importlib.util.spec_from_file_location("pilot_reconciliation", SCRIPT)
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


@pytest.fixture
def evidence(tmp_path):
    pdf_root = tmp_path / "pdfs"
    pdf_root.mkdir()
    (pdf_root / "source.pdf").write_bytes(b"synthetic pdf source")
    digest = pilot.sha256(pdf_root / "source.pdf")
    source = {
        "pdf": "source.pdf",
        "pdf_sha256": digest,
        "pages": [1, 2],
        "team_document_id": "exact-document",
    }
    visual = {
        **source,
        "workflow_state": "generated",
        "human_reviewed": False,
        "canonical_import_ready": False,
        "people": [
            {"role": "director_tecnico", "fields": {"name": "Director"}},
            {"role": "auxiliar", "fields": {"name": "Auxiliary"}},
        ],
        "source_review_fields": {
            "team.name": {"value": "Visual", "pending_full_year": True},
            "auxiliar.curp": {"value": None, "resolution": "unresolved"},
        },
        "canonical_extraction_candidate": {"players": []},
        "source_reviewed_extraction_candidate": {"players": [{"name": "Player"}]},
        "alerts": [{"code": "TEST_INCIDENT", "blocks_registration": True}],
    }
    automatic_root = tmp_path / "auto"
    (automatic_root / "documents").mkdir(parents=True)
    prediction_path = automatic_root / "documents/doc.json"
    prediction = {
        "fields": {
            "team.name": {
                "value": "Automatic",
                "evidence": {"pdf": "source.pdf", "pdf_sha256": digest, "page": 1},
            },
            "auxiliar.curp": {"value": None},
            "director_tecnico.name": {"value": "Director"},
        },
        "people": [{"role": "director_tecnico"}, {"role": "auxiliar"}],
        "source_pages": [
            {"pdf": "source.pdf", "pdf_sha256": digest, "page": page} for page in [1, 2]
        ],
        "exceptions": [{"code": "MISSING_REQUIRED_FIELD"}],
    }
    reference = tmp_path / "visual.jsonl"
    reference.write_text(json.dumps(visual) + "\n")
    inventory = {
        "reference_sha256": pilot.sha256(reference),
        "reference_documents": 1,
        "files": [{"pdf": "source.pdf", "sha256": digest, "pages": 2}],
    }
    entry = {
        "document_id": "exact-document",
        "source": source,
        "prediction_path": str(prediction_path),
        "exceptions": [{"code": "REVIEW_REQUIRED"}],
    }
    report = {"documents": [entry], "method": "automatic"}

    def save():
        for path, value in [
            (prediction_path, prediction),
            (automatic_root / "inventory.json", inventory),
            (automatic_root / "report.json", report),
        ]:
            path.write_text(json.dumps(value))

    save()
    return locals()


def run(e):
    e["save"]()
    return pilot.reconcile(e["reference"], e["automatic_root"], e["pdf_root"])


def test_preserves_complete_evidence_roles_and_authority(evidence):
    e = evidence
    before = {p: p.read_bytes() for p in e["tmp_path"].rglob("*") if p.is_file()}
    result = run(e)
    doc = result["documents"][0]
    assert doc["visual_reference"] == e["visual"]
    assert doc["automatic_prediction"] == e["prediction"]
    assert doc["automatic_report_entry"] == e["entry"]
    assert len(doc["visual_reference"]["people"]) == 2
    assert doc["admission"]["authenticated_actor_id"] is None
    assert doc["admission"]["canonical_receipts"] is None
    assert result["canonical_import_ready"] is False
    assert result["grants_eligibility"] is False
    assert doc["pending_visual_field_paths"]
    assert len(doc["differences"]) == 2
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize(
    "fault",
    [
        "reference_hash",
        "count",
        "pdf_hash",
        "pdf_missing",
        "inventory_duplicate",
        "inventory_hash",
        "page_bounds",
        "entry_missing",
        "entry_duplicate",
        "source_name",
        "source_pages",
        "source_id",
        "prediction_pages",
        "prediction_hash",
        "field_source",
        "outside_prediction",
        "duplicate_visual",
    ],
)
def test_fails_closed_on_source_mismatch_or_ambiguity(evidence, fault):
    e = evidence
    if fault == "reference_hash":
        e["inventory"]["reference_sha256"] = "0" * 64
    elif fault == "count":
        e["inventory"]["reference_documents"] = 2
    elif fault == "pdf_hash":
        (e["pdf_root"] / "source.pdf").write_bytes(b"changed")
    elif fault == "pdf_missing":
        (e["pdf_root"] / "source.pdf").unlink()
    elif fault == "inventory_duplicate":
        e["inventory"]["files"] *= 2
    elif fault == "inventory_hash":
        e["inventory"]["files"][0]["sha256"] = "0" * 64
    elif fault == "page_bounds":
        e["inventory"]["files"][0]["pages"] = 1
    elif fault == "entry_missing":
        e["report"]["documents"].clear()
    elif fault == "entry_duplicate":
        e["report"]["documents"] *= 2
    elif fault in {"source_name", "source_pages", "source_id"}:
        e["entry"]["source"] = copy.deepcopy(e["source"])
        key, value = {
            "source_name": ("pdf", "source-other.pdf"),
            "source_pages": ("pages", [2, 1]),
            "source_id": ("team_document_id", "partial-document"),
        }[fault]
        e["entry"]["source"][key] = value
    elif fault == "prediction_pages":
        e["prediction"]["source_pages"].reverse()
    elif fault == "prediction_hash":
        e["prediction"]["source_pages"][0]["pdf_sha256"] = "0" * 64
    elif fault == "field_source":
        e["prediction"]["fields"]["team.name"]["evidence"]["page"] = 3
    elif fault == "outside_prediction":
        e["entry"]["prediction_path"] = str(e["reference"])
    elif fault == "duplicate_visual":
        e["reference"].write_text((json.dumps(e["visual"]) + "\n") * 2)
        e["inventory"]["reference_sha256"] = pilot.sha256(e["reference"])
        e["inventory"]["reference_documents"] = 2
    with pytest.raises(pilot.EvidenceError):
        run(e)


def test_missing_automatic_document(evidence):
    e = evidence
    e["save"]()
    e["prediction_path"].unlink()
    with pytest.raises(pilot.EvidenceError):
        pilot.reconcile(e["reference"], e["automatic_root"], e["pdf_root"])


@pytest.mark.parametrize("pages", [[], [0], [True], [1, 1], "1"])
def test_invalid_page_identity(pages):
    with pytest.raises(pilot.EvidenceError):
        pilot.source_identity(
            {"pdf": "source.pdf", "pdf_sha256": "a" * 64, "pages": pages}
        )


def test_private_output_cli_and_no_apply(evidence, capsys):
    e = evidence
    output = e["tmp_path"] / "private.json"
    args = [
        "--reference",
        str(e["reference"]),
        "--automatic-root",
        str(e["automatic_root"]),
        "--pdf-root",
        str(e["pdf_root"]),
        "--output",
        str(output),
    ]
    assert pilot.main(args) == 0
    assert output.stat().st_mode & 0o777 == 0o600
    assert "Visual" not in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        pilot.write_private_report(output, {})
    with pytest.raises(SystemExit) as error:
        pilot.main(args + ["--apply"])
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        pilot.main(args)
    assert error.value.code == 1


def test_missing_reference_and_invalid_json_are_safe(evidence):
    e = evidence
    e["reference"].write_text("invalid personal content")
    with pytest.raises(pilot.EvidenceError, match="invalid reference"):
        run(e)
    with pytest.raises(pilot.EvidenceError, match="invalid local"):
        pilot.load_json(e["tmp_path"] / "missing.json")
