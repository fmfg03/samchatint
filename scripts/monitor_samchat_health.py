#!/usr/bin/env python3
"""Local health watcher for sam.chat.

Checks:
- systemd service state for samchat-gastos
- local app readiness on 127.0.0.1:8000/readyz
- nginx -> app path on https://sam.chat/healthz via loopback

If the service or app readiness is unhealthy, it can restart the Samchat web
service and re-check before exiting.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPSConnection
from pathlib import Path
from typing import Any

SERVICE_NAME = "samchat-gastos.service"
NGINX_SERVICE_NAME = "nginx"
APP_READY_URL = "http://127.0.0.1:8000/readyz"
HOST_HEADER = "sam.chat"
NGINX_HEALTH_PATH = "/healthz"
STATE_FILE = Path("/tmp/samchat-healthcheck-state.json")


@dataclass
class CheckResult:
    ok: bool
    status_code: int | None = None
    detail: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class HealthcheckConfig:
    failure_threshold: int
    cooldown_seconds: int
    timeout_seconds: float
    lock_path: Path
    journal_lines: int


def _run_systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_journalctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["journalctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _service_is_active(name: str) -> CheckResult:
    proc = _run_systemctl("is-active", name)
    detail = (proc.stdout or proc.stderr or "").strip()
    return CheckResult(ok=proc.returncode == 0, detail=detail or "unknown")


def _restart_service(name: str) -> CheckResult:
    proc = _run_systemctl("restart", name)
    detail = (proc.stdout or proc.stderr or "").strip()
    return CheckResult(ok=proc.returncode == 0, detail=detail or "restarted")


def _systemctl_show(name: str, *properties: str) -> dict[str, str]:
    args: list[str] = ["show", name]
    for prop in properties:
        args.extend(["-p", prop])
    proc = _run_systemctl(*args)
    values: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    if proc.returncode != 0:
        values["error"] = (proc.stderr or proc.stdout or "").strip()
    return values


def _recent_journal_tail(name: str, lines: int) -> list[str]:
    proc = _run_journalctl(
        "-u", name, "-n", str(max(1, lines)), "--no-pager", "-o", "short-iso"
    )
    raw = proc.stdout if proc.returncode == 0 else proc.stderr
    return (raw or "").splitlines()[-max(1, lines):]


def _http_json_check(url: str, timeout: float) -> CheckResult:
    try:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            return CheckResult(
                ok=(
                    200 <= int(response.status) < 300
                    and bool(payload.get("ok", True))
                ),
                status_code=int(response.status),
                detail=payload.get("status") or "ok",
                payload=payload,
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        payload = None
        try:
            payload = json.loads(raw) if raw else None
        except Exception:
            payload = None
        return CheckResult(
            ok=False,
            status_code=int(exc.code),
            detail=(payload or {}).get("status") or raw[:300] or str(exc),
            payload=payload,
        )
    except Exception as exc:
        return CheckResult(ok=False, detail=str(exc))


def _nginx_https_check(timeout: float) -> CheckResult:
    conn = HTTPSConnection(
        "127.0.0.1",
        443,
        timeout=timeout,
        context=ssl._create_unverified_context(),
    )
    try:
        conn.request(
            "GET",
            NGINX_HEALTH_PATH,
            headers={"Host": HOST_HEADER, "Accept": "application/json"},
        )
        response = conn.getresponse()
        raw = response.read().decode("utf-8")
        payload = json.loads(raw) if raw else {}
        return CheckResult(
            ok=(
                200 <= int(response.status) < 300
                and bool(payload.get("ok", True))
            ),
            status_code=int(response.status),
            detail=payload.get("status") or "ok",
            payload=payload,
        )
    except Exception as exc:
        return CheckResult(ok=False, detail=str(exc))
    finally:
        conn.close()


def _build_report(
    *,
    service: CheckResult,
    nginx: CheckResult,
    ready: CheckResult,
    restarted: bool,
    restart_decision: dict[str, Any] | None = None,
    pre_restart_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ok = service.ok and nginx.ok and ready.ok
    report: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "restarted": restarted,
        "restart_decision": restart_decision
        or {
            "restartable_failure": False,
            "consecutive_failures": 0,
            "threshold": 0,
            "allowed": False,
            "reason": "not_evaluated",
        },
        "service": {
            "name": SERVICE_NAME,
            "ok": service.ok,
            "detail": service.detail,
        },
        "nginx": {
            "name": NGINX_SERVICE_NAME,
            "ok": nginx.ok,
            "status_code": nginx.status_code,
            "detail": nginx.detail,
        },
        "readiness": {
            "url": APP_READY_URL,
            "ok": ready.ok,
            "status_code": ready.status_code,
            "detail": ready.detail,
        },
    }
    if pre_restart_snapshot is not None:
        report["pre_restart_snapshot"] = pre_restart_snapshot
    return report


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_path(name: str, default: str) -> Path:
    raw = os.environ.get(name)
    return Path(raw) if raw else Path(default)


def _load_config(args: argparse.Namespace) -> HealthcheckConfig:
    threshold = args.failure_threshold
    if threshold is None:
        threshold = _env_int("SAMCHAT_HEALTHCHECK_FAILURE_THRESHOLD", 3)
    cooldown = args.cooldown_seconds
    if cooldown is None:
        cooldown = _env_int("SAMCHAT_HEALTHCHECK_COOLDOWN_SECONDS", 300)
    timeout = args.timeout
    if timeout is None:
        timeout = _env_float("SAMCHAT_HEALTHCHECK_TIMEOUT_SECONDS", 5.0)
    lock_path = args.lock_path
    if lock_path is None:
        lock_path = _env_path(
            "SAMCHAT_HEALTHCHECK_LOCK_PATH", "/tmp/samchat-healthcheck.lock"
        )
    return HealthcheckConfig(
        failure_threshold=max(1, int(threshold)),
        cooldown_seconds=max(0, int(cooldown)),
        timeout_seconds=max(0.1, float(timeout)),
        lock_path=Path(lock_path),
        journal_lines=max(1, int(args.journal_lines)),
    )


def _read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=True),
        encoding="utf-8",
    )


def _failure_signature(
    *,
    service: CheckResult,
    nginx: CheckResult,
    ready: CheckResult,
) -> dict[str, Any]:
    return {
        "service": {
            "ok": service.ok,
            "detail": service.detail,
        },
        "nginx": {
            "ok": nginx.ok,
            "status_code": nginx.status_code,
            "detail": nginx.detail,
        },
        "ready": {
            "ok": ready.ok,
            "status_code": ready.status_code,
            "detail": ready.detail,
        },
    }


def _parse_timestamp(raw: Any) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def _evaluate_restart_decision(
    *,
    service: CheckResult,
    ready: CheckResult,
    state: dict[str, Any],
    config: HealthcheckConfig,
    now: datetime,
    dry_run: bool,
) -> dict[str, Any]:
    restartable_failure = (not service.ok) or (not ready.ok)
    previous_failures = int(state.get("consecutive_failures") or 0)
    consecutive_failures = previous_failures + 1 if restartable_failure else 0
    last_restart_ts = _parse_timestamp(state.get("last_restart_utc"))
    cooldown_remaining = 0
    if last_restart_ts is not None:
        elapsed = max(0, int(now.timestamp() - last_restart_ts))
        cooldown_remaining = max(0, config.cooldown_seconds - elapsed)

    allowed = (
        restartable_failure
        and consecutive_failures >= config.failure_threshold
        and cooldown_remaining == 0
        and not dry_run
    )
    if not restartable_failure:
        reason = "healthy"
    elif consecutive_failures < config.failure_threshold:
        reason = "below_threshold"
    elif cooldown_remaining > 0:
        reason = "cooldown_active"
    elif dry_run:
        reason = "dry_run"
    else:
        reason = "threshold_met"
    return {
        "restartable_failure": restartable_failure,
        "consecutive_failures": consecutive_failures,
        "threshold": config.failure_threshold,
        "allowed": allowed,
        "reason": reason,
        "cooldown_seconds": config.cooldown_seconds,
        "cooldown_remaining_seconds": cooldown_remaining,
        "dry_run": dry_run,
    }


def _build_pre_restart_snapshot(
    *,
    service: CheckResult,
    nginx: CheckResult,
    ready: CheckResult,
    journal_lines: int,
) -> dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "systemctl_is_active": service.detail,
        "healthz": {
            "ok": nginx.ok,
            "status_code": nginx.status_code,
            "detail": nginx.detail,
        },
        "readyz": {
            "ok": ready.ok,
            "status_code": ready.status_code,
            "detail": ready.detail,
        },
        "service": {
            "ok": service.ok,
            "detail": service.detail,
        },
        "systemctl_show": _systemctl_show(
            SERVICE_NAME,
            "MainPID",
            "NRestarts",
            "ActiveState",
            "SubState",
            "Result",
            "ExecMainStatus",
            "ExecMainCode",
        ),
        "recent_journal": _recent_journal_tail(SERVICE_NAME, journal_lines),
    }


def _update_state(
    *,
    report: dict[str, Any],
    failure_signature: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    state = {
        "ok": bool(report.get("ok")),
        "timestamp_utc": report.get("timestamp_utc"),
        "restarted": bool(report.get("restarted")),
        "consecutive_failures": int(decision.get("consecutive_failures") or 0),
        "failure_signature": failure_signature,
        "restart_decision": decision,
    }
    existing = _read_state()
    if existing.get("last_restart_utc"):
        state["last_restart_utc"] = existing["last_restart_utc"]
    if report.get("restarted"):
        state["last_restart_utc"] = report.get("timestamp_utc")
    _save_state(state)


def _acquire_lock(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _release_lock(handle: Any) -> None:
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Monitor and optionally self-heal sam.chat"
    )
    parser.add_argument(
        "--timeout", type=float, default=None, help="HTTP timeout seconds"
    )
    parser.add_argument(
        "--restart-on-failure",
        action="store_true",
        help=(
            "Restart samchat-gastos.service when the service or readiness "
            "check fails"
        ),
    )
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help=(
            "Evaluate restart policy and emit snapshots without executing "
            "systemctl restart"
        ),
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=4.0,
        help="Seconds to wait after restart before re-checking",
    )
    parser.add_argument(
        "--failure-threshold",
        type=int,
        default=None,
        help="Consecutive failures required before restart",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=int,
        default=None,
        help="Minimum seconds between restarts",
    )
    parser.add_argument(
        "--lock-path", type=Path, default=None, help="Non-blocking flock path"
    )
    parser.add_argument(
        "--journal-lines",
        type=int,
        default=40,
        help="Recent service journal lines to include before restart",
    )
    args = parser.parse_args()
    config = _load_config(args)

    lock_handle = _acquire_lock(config.lock_path)
    if lock_handle is None:
        print(
            json.dumps(
                {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "ok": True,
                    "skipped": True,
                    "reason": "lock_held",
                    "lock_path": str(config.lock_path),
                },
                ensure_ascii=True,
            )
        )
        return 0

    try:
        service = _service_is_active(SERVICE_NAME)
        ready = _http_json_check(APP_READY_URL, timeout=config.timeout_seconds)
        nginx = _nginx_https_check(timeout=config.timeout_seconds)
        restarted = False
        pre_restart_snapshot = None
        decision = _evaluate_restart_decision(
            service=service,
            ready=ready,
            state=_read_state(),
            config=config,
            now=datetime.now(timezone.utc),
            dry_run=args.no_restart,
        )

        if args.restart_on_failure and decision["restartable_failure"]:
            pre_restart_snapshot = _build_pre_restart_snapshot(
                service=service,
                nginx=nginx,
                ready=ready,
                journal_lines=config.journal_lines,
            )

        if args.restart_on_failure and decision["allowed"]:
            restart = _restart_service(SERVICE_NAME)
            restarted = restart.ok
            if restarted:
                time.sleep(max(1.0, float(args.settle_seconds)))
                service = _service_is_active(SERVICE_NAME)
                ready = _http_json_check(
                    APP_READY_URL,
                    timeout=config.timeout_seconds,
                )
                nginx = _nginx_https_check(timeout=config.timeout_seconds)
            else:
                ready = CheckResult(
                    ok=False, detail=f"restart failed: {restart.detail}"
                )

        report = _build_report(
            service=service,
            nginx=nginx,
            ready=ready,
            restarted=restarted,
            restart_decision=decision,
            pre_restart_snapshot=pre_restart_snapshot,
        )
        _update_state(
            report=report,
            failure_signature=_failure_signature(
                service=service, nginx=nginx, ready=ready
            ),
            decision=decision,
        )
        print(json.dumps(report, ensure_ascii=True))
        return 0 if report["ok"] else 1
    finally:
        _release_lock(lock_handle)


if __name__ == "__main__":
    sys.exit(main())
