import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "release_manifest.py"
SPEC = importlib.util.spec_from_file_location("release_manifest_test", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_manifest_passes_for_single_current_release(tmp_path):
    unit_dir = tmp_path / "unit.d"
    unit_dir.mkdir()
    (unit_dir / "50-current-release.conf").write_text("[Service]\n")
    release = tmp_path / "srv" / "samchat" / "releases" / "gastos-prod-abc-test"
    release.mkdir(parents=True)
    current = tmp_path / "srv" / "samchat" / "current"
    current.symlink_to(release)

    with patch.object(MODULE, "RELEASE_PREFIX", str(tmp_path / "srv" / "samchat" / "releases" / "gastos-prod-")), patch.object(
        MODULE, "_systemctl_show"
    ) as show, patch.object(MODULE, "_git_head") as git_head:
        show.return_value = {
            "WorkingDirectory": str(release),
            "NRestarts": "0",
            "ActiveState": "active",
            "SubState": "running",
        }
        git_head.return_value = "abc123"
        for gate in MODULE.REQUIRED_GATES:
            gate_path = release / gate
            gate_path.parent.mkdir(parents=True, exist_ok=True)
            gate_path.write_text("# gate\n")

        manifest = MODULE.build_manifest(
            unit_dir=unit_dir,
            current_symlink=current,
            python_bin=tmp_path / "python",
            run_gates=False,
            check_http=False,
        )

    assert manifest["ok"] is True
    assert manifest["checks"]["single_dropin"] is True
    assert manifest["checks"]["current_symlink_matches_working_directory"] is True
    assert manifest["release"]["git_head"] == "abc123"


def test_manifest_fails_when_legacy_dropins_are_active(tmp_path):
    unit_dir = tmp_path / "unit.d"
    unit_dir.mkdir()
    (unit_dir / "50-current-release.conf").write_text("[Service]\n")
    (unit_dir / "zzzz-old.conf").write_text("[Service]\n")
    release = tmp_path / "srv" / "samchat" / "releases" / "gastos-prod-abc-test"
    release.mkdir(parents=True)
    current = tmp_path / "srv" / "samchat" / "current"
    current.symlink_to(release)

    with patch.object(MODULE, "RELEASE_PREFIX", str(tmp_path / "srv" / "samchat" / "releases" / "gastos-prod-")), patch.object(
        MODULE, "_systemctl_show"
    ) as show, patch.object(MODULE, "_git_head"):
        show.return_value = {
            "WorkingDirectory": str(release),
            "NRestarts": "0",
            "ActiveState": "active",
            "SubState": "running",
        }
        for gate in MODULE.REQUIRED_GATES:
            gate_path = release / gate
            gate_path.parent.mkdir(parents=True, exist_ok=True)
            gate_path.write_text("# gate\n")

        manifest = MODULE.build_manifest(
            unit_dir=unit_dir,
            current_symlink=current,
            python_bin=tmp_path / "python",
            run_gates=False,
            check_http=False,
        )

    assert manifest["ok"] is False
    assert manifest["checks"]["single_dropin"] is False
    assert manifest["dropins"]["active"] == ["50-current-release.conf", "zzzz-old.conf"]


def test_manifest_fails_when_required_gate_missing(tmp_path):
    unit_dir = tmp_path / "unit.d"
    unit_dir.mkdir()
    (unit_dir / "50-current-release.conf").write_text("[Service]\n")
    release = tmp_path / "srv" / "samchat" / "releases" / "gastos-prod-abc-test"
    release.mkdir(parents=True)
    current = tmp_path / "srv" / "samchat" / "current"
    current.symlink_to(release)

    with patch.object(MODULE, "RELEASE_PREFIX", str(tmp_path / "srv" / "samchat" / "releases" / "gastos-prod-")), patch.object(
        MODULE, "_systemctl_show"
    ) as show, patch.object(MODULE, "_git_head"):
        show.return_value = {
            "WorkingDirectory": str(release),
            "NRestarts": "0",
            "ActiveState": "active",
            "SubState": "running",
        }
        only_gate = MODULE.REQUIRED_GATES[0]
        gate_path = release / only_gate
        gate_path.parent.mkdir(parents=True, exist_ok=True)
        gate_path.write_text("# gate\n")

        manifest = MODULE.build_manifest(
            unit_dir=unit_dir,
            current_symlink=current,
            python_bin=tmp_path / "python",
            run_gates=False,
            check_http=False,
        )

    assert manifest["ok"] is False
    assert manifest["checks"]["required_gates_ok"] is False
    assert any(gate["reason"] == "missing" for gate in manifest["gates"])
