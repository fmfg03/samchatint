#!/usr/bin/env python3
"""Build a verifiable manifest for the active SamChat production release.

The manifest is intentionally read-only. It answers the operational question:
"what release is actually live, what systemd drop-in selected it, and which
release gates protect it?"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SERVICE_NAME = "samchat-gastos.service"
UNIT_DIR = Path("/etc/systemd/system/samchat-gastos.service.d")
CANONICAL_DROPIN = "50-current-release.conf"
CURRENT_SYMLINK = Path("/srv/samchat/current")
RELEASE_PREFIX = "/srv/samchat/releases/gastos-prod-"
HEALTHZ_URL = "http://127.0.0.1:8000/healthz"
READYZ_URL = "http://127.0.0.1:8000/readyz"
REQUIRED_GATES = (
    "scripts/ci/check-registration-operational-surface.py",
    "scripts/ci/check-accepted-regressions.py",
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run(argv: list[str], cwd: Path | None = None) -> CommandResult:
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandResult(proc.returncode, proc.stdout.strip(), proc.stderr.strip())


def _systemctl_show(service: str) -> dict[str, str]:
    result = _run(
        [
            "systemctl",
            "show",
            service,
            "-p",
            "WorkingDirectory",
            "-p",
            "NRestarts",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "--no-pager",
        ]
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    if not result.ok:
        values["error"] = result.stderr or result.stdout
    return values


def _active_dropins(unit_dir: Path) -> list[str]:
    if not unit_dir.is_dir():
        return []
    return sorted(path.name for path in unit_dir.glob("*.conf") if path.is_file())


def _readlink(path: Path) -> str | None:
    try:
        return str(path.resolve(strict=True))
    except OSError:
        return None


def _git_head(path: Path) -> str | None:
    if not path.exists():
        return None
    result = _run(["git", "rev-parse", "HEAD"], cwd=path)
    if result.ok and result.stdout:
        return result.stdout.splitlines()[0]
    return None


def _http_json(url: str, timeout: float) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            return {
                "ok": 200 <= int(response.status) < 300 and bool(payload.get("ok", True)),
                "status_code": int(response.status),
                "payload": payload,
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _gate_status(release_dir: Path, python_bin: Path, gate: str, run_gates: bool) -> dict[str, Any]:
    gate_path = release_dir / gate
    item: dict[str, Any] = {
        "path": gate,
        "exists": gate_path.is_file(),
        "executed": False,
        "ok": False,
    }
    if not gate_path.is_file():
        item["reason"] = "missing"
        return item
    if not run_gates:
        item["ok"] = True
        item["reason"] = "not_executed"
        return item
    if not python_bin.is_file():
        item["reason"] = "python_missing"
        return item
    result = _run([str(python_bin), str(gate_path), "--root", str(release_dir)])
    item.update(
        {
            "executed": True,
            "ok": result.ok,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-1000:],
            "stderr_tail": result.stderr[-1000:],
        }
    )
    return item


def build_manifest(
    *,
    service: str = SERVICE_NAME,
    unit_dir: Path = UNIT_DIR,
    current_symlink: Path = CURRENT_SYMLINK,
    python_bin: Path = Path("/srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python"),
    run_gates: bool = False,
    check_http: bool = True,
    timeout: float = 3.0,
) -> dict[str, Any]:
    systemd = _systemctl_show(service)
    working_directory = systemd.get("WorkingDirectory") or ""
    release_dir = Path(working_directory) if working_directory else Path("")
    dropins = _active_dropins(unit_dir)
    current_target = _readlink(current_symlink)
    gates = [_gate_status(release_dir, python_bin, gate, run_gates) for gate in REQUIRED_GATES]

    checks: dict[str, bool] = {
        "single_dropin": dropins == [CANONICAL_DROPIN],
        "working_directory_is_release": working_directory.startswith(RELEASE_PREFIX),
        "current_symlink_matches_working_directory": bool(current_target) and current_target == working_directory,
        "service_active": systemd.get("ActiveState") == "active",
        "no_restarts": systemd.get("NRestarts") in {"0", ""},
        "required_gates_ok": all(bool(gate.get("ok")) for gate in gates),
    }

    healthz: dict[str, Any] | None = None
    readyz: dict[str, Any] | None = None
    if check_http:
        healthz = _http_json(HEALTHZ_URL, timeout)
        readyz = _http_json(READYZ_URL, timeout)
        checks["healthz_ok"] = bool(healthz.get("ok"))
        checks["readyz_ok"] = bool(readyz.get("ok"))

    manifest = {
        "schema_version": "samchat.release_manifest.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ok": all(checks.values()),
        "checks": checks,
        "service": service,
        "systemd": systemd,
        "dropins": {
            "unit_dir": str(unit_dir),
            "active": dropins,
            "canonical": CANONICAL_DROPIN,
        },
        "release": {
            "working_directory": working_directory,
            "current_symlink": str(current_symlink),
            "current_target": current_target,
            "git_head": _git_head(release_dir) if working_directory else None,
        },
        "gates": gates,
    }
    if healthz is not None:
        manifest["healthz"] = healthz
    if readyz is not None:
        manifest["readyz"] = readyz
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-gates", action="store_true", help="Execute release gates against the active release")
    parser.add_argument("--no-http", action="store_true", help="Skip healthz/readyz HTTP checks")
    parser.add_argument("--timeout", type=float, default=3.0, help="HTTP timeout in seconds")
    parser.add_argument("--python-bin", default="/srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python")
    args = parser.parse_args(argv)

    manifest = build_manifest(
        python_bin=Path(args.python_bin),
        run_gates=args.run_gates,
        check_http=not args.no_http,
        timeout=args.timeout,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
