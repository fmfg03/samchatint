import json
import sys
from uuid import uuid4

import pytest
from scripts.reconcile_ctt_batch_capture import main, reconcile

from devnous.copa_telmex.staff_batch_contracts import (
    stable_document_id,
    validate_source_manifest,
)


@pytest.fixture
def exported():
    edition = {
        "tenant_id": "synthetic",
        "edition_id": str(uuid4()),
        "tournament_id": str(uuid4()),
        "edition_year": 2026,
        "roster_slug": "ctt-test",
        "phase": "national",
    }
    document = stable_document_id(edition["edition_id"], [("a" * 64, 1), ("a" * 64, 2)])
    manifest = validate_source_manifest(
        {
            "schema_version": "ctt.registration.source-manifest.v2",
            "edition": edition,
            "pdfs": [
                {"filename": "synthetic.pdf", "sha256": "a" * 64, "page_count": 2}
            ],
            "documents": [
                {
                    "document_id": document,
                    "source_slices": [{"pdf_sha256": "a" * 64, "pages": [1, 2]}],
                    "extraction": {},
                }
            ],
        }
    )
    attempt = str(uuid4())
    run = {
        "run_id": str(uuid4()),
        "edition_id": edition["edition_id"],
        "manifest_hashes": [manifest["manifest_sha256"]],
        "members": [
            {
                "document_id": document,
                "manifest_sha256": manifest["manifest_sha256"],
                "status": "committed",
                "attempt_id": attempt,
            }
        ],
    }
    results = [
        {"attempt_id": attempt, "result_id": str(uuid4()), "team_id": str(uuid4())}
    ]
    return run, [manifest], results


def test_complete_matching_exports_report_only_offline_consistency(exported):
    report = reconcile(*exported)
    assert report["consistent"] is True and report["counts"]["committed"] == 1
    assert report["database_verified"] is False
    assert report["database_writes"] == 0


def test_committed_label_without_canonical_result_is_inconsistent(exported):
    run, manifests, _ = exported
    report = reconcile(run, manifests, [])
    assert report["consistent"] is False
    assert report["counts"]["committed"] == 0 and report["counts"]["inconsistent"] == 1


@pytest.mark.parametrize(
    "fault", ["pagination", "edition", "duplicate_result", "manifest"]
)
def test_missing_or_mixed_export_cannot_claim_completeness(exported, fault):
    run, manifests, results = exported
    if fault == "pagination":
        run["members"] = []
    elif fault == "edition":
        run["edition_id"] = str(uuid4())
    elif fault == "duplicate_result":
        results.append(dict(results[0]))
    else:
        run["members"][0]["manifest_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        reconcile(run, manifests, results)


@pytest.mark.parametrize("status", ["failed", "uncertain", "exception", "ready"])
def test_unfinished_statuses_are_kept_distinct_and_never_count_as_committed(
    exported, status
):
    run, manifests, _ = exported
    run["members"][0]["status"] = status
    result = reconcile(run, manifests, [])
    assert result["counts"]["committed"] == 0
    assert result["counts"]["pending" if status == "ready" else status] == 1


def test_cli_writes_only_private_report_and_refuses_overwrite(
    exported, tmp_path, monkeypatch
):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    paths = []
    for name, value in zip(
        ("run", "manifest", "results"), (exported[0], exported[1][0], exported[2])
    ):
        path = root / (name + ".json")
        path.write_text(json.dumps(value))
        paths.append(path)
    output = root / "receipt.json"
    arguments = [
        "reconcile",
        "--run-export",
        str(paths[0]),
        "--manifest",
        str(paths[1]),
        "--results-export",
        str(paths[2]),
        "--output",
        str(output),
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    assert main() == 0
    assert output.stat().st_mode & 0o077 == 0
    assert json.loads(output.read_text())["database_verified"] is False
    with pytest.raises(FileExistsError):
        main()
    output.unlink()
    root.chmod(0o755)
    with pytest.raises(ValueError, match="Private output directory"):
        main()
    assert not output.exists()
