"""Release attestation rejects self-asserted identity, stale code and policies."""

import copy
from pathlib import Path

import pytest

from samchat.batisenal_composition import (
    ConfigurationError,
    _git_object,
    _receipt_tree,
    policy_fingerprint,
    verify_build_receipt,
)


@pytest.fixture
def build(tmp_path):
    files = {
        "src/devnous/gastos/services/batisenal_case_service.py": (
            'RULE = "private only"\n'
        ),
        "src/devnous/gastos/services/batisenal_case_authority.py": (
            "# strict authority\n"
        ),
        "src/devnous/gastos/services/expense_coi_export_service.py": (
            "# canonical owner\n"
        ),
        "scripts/batisenal_real_pilot.py": "# owner operator\n",
        "requirements-runtime.txt": "# existing dependencies\n",
        "SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md": "# test canon\n",
    }
    entries = []
    for name, value in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)
        entries.append(
            {
                "path": name,
                "mode": "100644",
                "sha1": _git_object("blob", value.encode()),
            }
        )
    tree = _receipt_tree(entries)
    commit = (
        f"tree {tree}\n"
        "author Synthetic fixture <fixture@example.invalid> 0 +0000\n"
        "committer Synthetic fixture <fixture@example.invalid> 0 +0000\n"
        "\nTechnical fixture only\n"
    )
    receipt = {"commit": commit, "entries": entries}
    manifest = {
        "release": _git_object("commit", commit.encode()),
        "tree": tree,
        "policy_sha256": policy_fingerprint(tmp_path),
    }
    return tmp_path, manifest, receipt


def test_known_git_empty_tree_and_blob():
    # Public Git object-format vectors, independent of the fixture builder.
    assert _receipt_tree([]) == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    assert _git_object("blob", b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def test_verifies_real_local_bytes_and_rule(build):
    root, manifest, receipt = build
    verify_build_receipt(root, manifest, receipt)


@pytest.mark.parametrize("field", ["release", "tree", "policy_sha256"])
def test_manifest_identity_cannot_be_echoed(build, field):
    root, manifest, receipt = build
    manifest[field] = "f" * len(manifest[field])
    with pytest.raises(ConfigurationError, match="BUILD_"):
        verify_build_receipt(root, manifest, receipt)


@pytest.mark.parametrize("change", ["edit", "missing", "extra", "symlink"])
def test_changed_runtime_rejected(build, change):
    root, manifest, receipt = build
    p = root / "scripts/batisenal_real_pilot.py"
    if change == "edit":
        p.write_text("# changed source\n")
    elif change == "missing":
        p.unlink()
    elif change == "extra":
        (root / "src/new_runtime.py").write_text("pass\n")
    else:
        p.unlink()
        p.symlink_to(root / "requirements-runtime.txt")
    with pytest.raises(ConfigurationError, match="BUILD_SOURCE_"):
        verify_build_receipt(root, manifest, receipt)


@pytest.mark.parametrize(
    "receipt", [None, {}, {"commit": "", "entries": []}, {"commit": 1, "entries": []}]
)
def test_receipt_required_closed_and_nonempty(build, receipt):
    root, manifest, _ = build
    with pytest.raises(ConfigurationError, match="BUILD_"):
        verify_build_receipt(root, manifest, receipt)


@pytest.mark.parametrize(
    "mutation", ["entry_hash", "mode", "path", "duplicate", "extra_field"]
)
def test_receipt_entries_cannot_redirect_or_relabel(build, mutation):
    root, manifest, receipt = build
    receipt = copy.deepcopy(receipt)
    row = receipt["entries"][0]
    if mutation == "entry_hash":
        row["sha1"] = "f" * 40
    elif mutation == "mode":
        row["mode"] = []
    elif mutation == "path":
        row["path"] = "../outside"
    elif mutation == "duplicate":
        receipt["entries"].append(row)
    else:
        row["secret"] = "not allowed"
    with pytest.raises(ConfigurationError, match="BUILD_"):
        verify_build_receipt(root, manifest, receipt)


def test_policy_fingerprint_matches_case_service():
    from devnous.gastos.services.batisenal_case_service import rule_version

    root = Path(__file__).resolve().parents[3]
    assert policy_fingerprint(root) == rule_version()
