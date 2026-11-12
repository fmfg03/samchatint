#!/usr/bin/env python3
"""Verify and stage exact, test-only governance sources and schema under /tmp.

No production DSN, role, key or source dossier is read. This does not install
packages, start services or turn test policies into business authority.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures"
SHA = re.compile(r"[0-9a-f]{64}\Z")
TEST_HELPERS = frozenset(
    {
        "services/evidence-bus/tests/v2_fixtures.py",
        "services/samchat-registration-gate/tests/test_staff_capture_gate_v2.py",
        "services/samchat-registration-gate/tests/test_staff_capture_gate_v3.py",
        "services/samchat-registration-gate/tests/test_staff_capture_finality_v2.py",
        "services/samchat-registration-gate/tests/"
        "test_staff_capture_national_curp_v2.py",
    }
)
REQUIRED_COHORTS = {
    "test_ctt_review_workflow_postgres": 12,
    "test_ctt_passkey_enrollment_postgres": 19,
    "test_ctt_full_runtime_postgres": 28,
    "test_ctt_assertion_proof_recovery_postgres": 11,
}


def _files(directory: Path, expected: dict[str, str], metadata: str):
    if type(expected) is not dict or not expected:
        raise ValueError("fixture manifest missing")
    actual = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("fixture symlink denied")
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    if actual != {*expected, metadata}:
        raise ValueError("fixture inventory differs")
    for name, digest in expected.items():
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or type(digest) is not str
            or not SHA.fullmatch(digest)
        ):
            raise ValueError("fixture binding invalid")
        if hashlib.sha256((directory / relative).read_bytes()).hexdigest() != digest:
            raise ValueError("fixture bytes differ")


def verify(fixtures: Path = FIXTURES):
    runtime, schema = fixtures / "governance-runtime", fixtures / "ctt-schema-only-v1"
    manifest = json.loads((runtime / "manifest.json").read_text())
    if (
        manifest.get("schema") != "ctt.ci.governance-runtime-fixture.v1"
        or manifest.get("source_repository") != "https://github.com/fmfg03/zaubern"
        or any(
            not re.fullmatch(r"[0-9a-f]{40}", manifest.get(key, ""))
            for key in ("source_base_commit", "source_main_observed")
        )
    ):
        raise ValueError("fixture source pin invalid")
    if set(manifest.get("test_helper_files", [])) != TEST_HELPERS:
        raise ValueError("exact original test helper inventory required")
    _files(runtime, manifest["files"], "manifest.json")
    receipt = json.loads((schema / "receipt.json").read_text())
    if receipt.get("schema_only") is not True or receipt.get("function_count") != 17:
        raise ValueError("schema-only receipt required")
    _files(schema, receipt["hashes"], "receipt.json")
    pg_restore = Path("/usr/lib/postgresql/16/bin/pg_restore")
    listing = subprocess.run(
        [str(pg_restore), "--list", str(schema / "ctt.dump")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if any(marker in listing for marker in ("TABLE DATA", "SEQUENCE SET", "BLOB DATA")):
        raise ValueError("fixture must not contain business rows")
    return manifest


def prepare(destination: Path, fixtures: Path = FIXTURES):
    verify(fixtures)
    destination = destination.absolute()
    if (
        not destination.is_relative_to(Path("/tmp"))
        or destination == Path("/tmp")
        or ".." in destination.parts
        or destination.exists()
        or destination.resolve() != destination
    ):
        raise ValueError("fresh isolated /tmp destination required")
    if importlib.metadata.version("fido2") != "2.2.1":
        raise ValueError("approved FIDO2 test dependency required")
    spec = importlib.util.find_spec("fido2")
    if spec is None or not spec.submodule_search_locations:
        raise ValueError("FIDO2 test package unavailable")
    fido_source = Path(next(iter(spec.submodule_search_locations)))
    destination.mkdir(mode=0o700)
    try:
        shutil.copytree(
            fixtures / "governance-runtime",
            destination / "governance-runtime",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        shutil.copytree(fixtures / "ctt-schema-only-v1", destination / "schema-only")
        deps = destination / "dependencies"
        deps.mkdir()
        shutil.copytree(
            fido_source,
            deps / "fido2",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        metadata = importlib.metadata.distribution("fido2").read_text("METADATA")
        if not metadata:
            raise ValueError("FIDO2 test package metadata unavailable")
        distribution = deps / "fido2-2.2.1.dist-info"
        distribution.mkdir()
        (distribution / "METADATA").write_text(metadata)
        gate = destination / "governance-runtime/services/samchat-registration-gate"
        bus = destination / "governance-runtime/services/evidence-bus"
        return {
            "CTT_SCHEMA_ONLY_DIRECTORY": str(destination / "schema-only"),
            "CTT_GATE_SOURCE": str(gate),
            "CTT_FIDO2_PATH": str(deps),
            "PYTHONPATH": os.pathsep.join(
                (
                    str(ROOT),
                    str(ROOT / "src"),
                    str(gate),
                    str(bus),
                    str(gate / "tests"),
                    str(bus / "tests"),
                    str(deps),
                )
            ),
        }
    except Exception:
        shutil.rmtree(destination)
        raise


def check_junit(path: Path, additional: Path | None = None):
    cases = list(ElementTree.parse(path).getroot().iter("testcase"))
    if additional is not None:
        cases.extend(ElementTree.parse(additional).getroot().iter("testcase"))
    for cohort, minimum in REQUIRED_COHORTS.items():
        selected = [c for c in cases if c.get("classname", "").split(".")[-1] == cohort]
        if len(selected) < minimum or any(
            case.find(tag) is not None
            for case in selected
            for tag in ("skipped", "failure", "error")
        ):
            raise ValueError(
                "required CTT acceptance cohort missing, skipped or failed: " + cohort
            )
    for scenario in (
        "operational_rpc",
        "operational_rpc_national",
        "operational_rpc_national_optional_photo",
    ):
        operational = [
            case
            for case in cases
            if case.get("classname", "").split(".")[-1]
            == "test_ctt_review_workflow_postgres"
            and case.get("name", "")
            == f"test_private_prepare_to_official_committed_session[{scenario}]"
        ]
        if len(operational) != 1:
            raise ValueError(
                "required CTT acceptance: operational RPC scenario must run once: "
                + scenario
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", type=Path)
    parser.add_argument("--github-env", type=Path)
    parser.add_argument("--check-junit", type=Path)
    parser.add_argument("--additional-junit", type=Path)
    args = parser.parse_args()
    if args.check_junit:
        check_junit(args.check_junit, args.additional_junit)
        print("Required CTT acceptance cohorts passed without skips")
    elif args.prepare:
        environment = prepare(args.prepare)
        if args.github_env:
            with args.github_env.open("a") as stream:
                for name, value in environment.items():
                    stream.write(name + "=" + value + "\n")
        else:
            print(json.dumps(environment, sort_keys=True))
        print("Exact CTT test-only fixtures prepared")
    else:
        verify()
        print("Exact CTT test-only fixture hashes and schema-only inventory verified")


if __name__ == "__main__":
    main()
