"""CI must run exact private-source acceptance rather than silently skip it."""

import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "ctt_ci_fixture_bootstrap", ROOT / "scripts/ci/check-ctt-test-fixtures.py"
)
assert SPEC is not None and SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_original_fixture_is_schema_only_and_exact_approved_runtime():
    manifest = bootstrap.verify()
    assert manifest["source_repository"] == "https://github.com/fmfg03/zaubern"
    assert manifest["files"][
        "services/samchat-registration-gate/staff_review_issuer_v2.py"
    ] == ("617b2c022c8ca4d72e16bb0b8fdccf903e59f848eef41fddc835070fe843a17a")
    assert set(manifest["test_helper_files"]) == bootstrap.TEST_HELPERS
    assert {
        path for path in manifest["files"] if "/tests/" in path
    } == bootstrap.TEST_HELPERS
    assert not any(path.endswith((".key", ".env")) for path in manifest["files"])


@pytest.fixture
def fixtures(tmp_path):
    copied = tmp_path / "fixtures"
    shutil.copytree(bootstrap.FIXTURES, copied)
    return copied


@pytest.mark.parametrize(
    "attack",
    [
        "changed",
        "missing",
        "extra",
        "symlink",
        "pin",
        "schemaflag",
        "function_count",
        "path",
        "digest",
    ],
)
def test_fixture_drift_or_unknown_source_fails_closed(fixtures, attack):
    runtime = fixtures / "governance-runtime"
    file = runtime / "services/samchat-registration-gate/staff_review_issuer_v2.py"
    path = runtime / "manifest.json"
    manifest = json.loads(path.read_text())
    if attack == "changed":
        file.write_bytes(file.read_bytes() + b"\n# changed\n")
    elif attack == "missing":
        file.unlink()
    elif attack == "extra":
        (runtime / "extra.py").write_text("raise RuntimeError")
    elif attack == "symlink":
        file.unlink()
        file.symlink_to(ROOT / "requirements.txt")
    elif attack == "pin":
        manifest["source_base_commit"] = "main"
    elif attack in {"schemaflag", "function_count"}:
        receipt_path = fixtures / "ctt-schema-only-v1/receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["schema_only" if attack == "schemaflag" else "function_count"] = False
        receipt_path.write_text(json.dumps(receipt))
    elif attack == "path":
        manifest["files"] = {"../outside": "a" * 64}
    elif attack == "digest":
        manifest["files"]["README.md"] = "bad-hash"
    path.write_text(json.dumps(manifest))
    with pytest.raises((ValueError, FileNotFoundError)):
        bootstrap.verify(fixtures)


def test_dump_with_table_rows_is_rejected(fixtures, monkeypatch):
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="TABLE DATA"),
    )
    with pytest.raises(ValueError, match="business rows"):
        bootstrap.verify(fixtures)


def test_prepare_copies_private_fixture_and_real_test_fido2_under_tmp(tmp_path):
    destination = tmp_path / "isolated"
    environment = bootstrap.prepare(destination)
    assert (destination / "dependencies/fido2/server.py").is_file()
    assert all(
        Path(environment[key]).resolve().is_relative_to(Path("/tmp"))
        for key in ("CTT_SCHEMA_ONLY_DIRECTORY", "CTT_GATE_SOURCE", "CTT_FIDO2_PATH")
    )
    assert (
        str(destination / "governance-runtime/services/evidence-bus")
        in environment["PYTHONPATH"]
    )
    assert (destination.stat().st_mode & 0o777) == 0o700
    with pytest.raises(ValueError, match="fresh isolated"):
        bootstrap.prepare(destination)


@pytest.mark.parametrize(
    "path", ["/tmp", "/etc/ctt-test-only", "/tmp/../etc/ctt-test-only"]
)
def test_prepare_cannot_write_outside_fresh_tmp(path):
    with pytest.raises(ValueError, match="fresh isolated"):
        bootstrap.prepare(Path(path))


def test_wrong_fido2_or_copy_failure_leaves_no_prepared_fixture(tmp_path, monkeypatch):
    destination = tmp_path / "isolated"
    monkeypatch.setattr(bootstrap.importlib.metadata, "version", lambda name: "0.0.0")
    with pytest.raises(ValueError, match="approved FIDO2"):
        bootstrap.prepare(destination)
    monkeypatch.setattr(bootstrap.importlib.metadata, "version", lambda name: "2.2.1")
    original = bootstrap.shutil.copytree

    def fail(source, target, **kwargs):
        if target.name == "dependencies":
            return original(source, target, **kwargs)
        raise OSError("synthetic copy failure")

    monkeypatch.setattr(bootstrap.shutil, "copytree", fail)
    with pytest.raises(OSError):
        bootstrap.prepare(destination)
    assert not destination.exists()


def junit(path, *, attack=None):
    root = ElementTree.Element("testsuites")
    suite = ElementTree.SubElement(root, "testsuite")
    for cohort, count in bootstrap.REQUIRED_COHORTS.items():
        for i in range(count):
            case = ElementTree.SubElement(
                suite,
                "testcase",
                classname="tests.integration." + cohort,
                name="case" + str(i),
            )
            if cohort == "test_ctt_review_workflow_postgres" and i == 0:
                case.set(
                    "name",
                    "test_private_prepare_to_official_committed_session["
                    "operational_rpc]",
                )
            if attack == "missing_operational" and i == 0:
                case.set("name", "different_scenario")
            if attack in {"skipped", "failure", "error"} and i == 0:
                ElementTree.SubElement(case, attack)
    if attack == "missing":
        suite.remove(suite[0])
    ElementTree.ElementTree(root).write(path)


@pytest.mark.parametrize(
    "attack", ["missing", "skipped", "failure", "error", "missing_operational"]
)
def test_required_acceptance_cannot_be_absent_skipped_or_failed(tmp_path, attack):
    path = tmp_path / "report.xml"
    junit(path, attack=attack)
    with pytest.raises(ValueError, match="required CTT acceptance"):
        bootstrap.check_junit(path)


def test_complete_required_acceptance_cohorts_pass(tmp_path):
    path = tmp_path / "report.xml"
    junit(path)
    bootstrap.check_junit(path)


def test_cli_verify_prepare_and_junit(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["fixture-check"])
    bootstrap.main()
    env = tmp_path / "github.env"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fixture-check",
            "--prepare",
            str(tmp_path / "staged"),
            "--github-env",
            str(env),
        ],
    )
    bootstrap.main()
    assert "CTT_GATE_SOURCE=" in env.read_text()
    report = tmp_path / "report.xml"
    junit(report)
    monkeypatch.setattr(sys, "argv", ["fixture-check", "--check-junit", str(report)])
    bootstrap.main()


def test_normal_and_privileged_junit_are_both_mandatory(tmp_path):
    normal, privileged = tmp_path / "normal.xml", tmp_path / "privileged.xml"
    junit(normal)
    root = ElementTree.parse(normal).getroot()
    suite = root.find("testsuite")
    assert suite is not None
    operational = next(
        case for case in suite if case.get("name", "").endswith("[operational_rpc]")
    )
    suite.remove(operational)
    ElementTree.ElementTree(root).write(normal)
    separate = ElementTree.Element("testsuites")
    ElementTree.SubElement(separate, "testsuite").append(operational)
    ElementTree.ElementTree(separate).write(privileged)
    with pytest.raises(ValueError):
        bootstrap.check_junit(normal)
    bootstrap.check_junit(normal, privileged)
    operational.set("classname", "wrong_cohort")
    ElementTree.ElementTree(separate).write(privileged)
    with pytest.raises(ValueError):
        bootstrap.check_junit(normal, privileged)


def test_generated_bytecode_is_not_copied_into_staged_runtime(fixtures, tmp_path):
    runtime = fixtures / "governance-runtime"
    cache = runtime / "__pycache__"
    cache.mkdir()
    (cache / "stale.pyc").write_bytes(b"not executable fixture input")
    (runtime / "stale.pyc").write_bytes(b"not staged")
    bootstrap.verify(fixtures)
    destination = tmp_path / "fresh"
    bootstrap.prepare(destination, fixtures)
    assert not list((destination / "governance-runtime").rglob("*.pyc"))
