import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "monitor_samchat_health.py"
SPEC = importlib.util.spec_from_file_location(
    "monitor_samchat_health_test", SCRIPT_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_build_report_marks_unhealthy_when_any_check_fails():
    report = MODULE._build_report(
        service=MODULE.CheckResult(ok=True, detail="active"),
        nginx=MODULE.CheckResult(ok=True, status_code=200, detail="healthy"),
        ready=MODULE.CheckResult(ok=False, status_code=503, detail="degraded"),
        restarted=False,
    )

    assert report["ok"] is False
    assert report["restarted"] is False
    assert report["readiness"]["detail"] == "degraded"


def test_build_report_marks_healthy_when_all_checks_pass():
    report = MODULE._build_report(
        service=MODULE.CheckResult(ok=True, detail="active"),
        nginx=MODULE.CheckResult(ok=True, status_code=200, detail="healthy"),
        ready=MODULE.CheckResult(ok=True, status_code=200, detail="healthy"),
        restarted=True,
    )

    assert report["ok"] is True
    assert report["restarted"] is True


def _config() -> MODULE.HealthcheckConfig:
    return MODULE.HealthcheckConfig(
        failure_threshold=3,
        cooldown_seconds=300,
        timeout_seconds=5.0,
        lock_path=Path("/tmp/test-samchat-healthcheck.lock"),
        journal_lines=2,
    )


def test_restart_policy_does_not_restart_on_one_miss():
    decision = MODULE._evaluate_restart_decision(
        service=MODULE.CheckResult(ok=True, detail="active"),
        ready=MODULE.CheckResult(ok=False, status_code=503, detail="degraded"),
        state={},
        config=_config(),
        now=datetime(2026, 6, 30, tzinfo=timezone.utc),
        dry_run=False,
    )

    assert decision["restartable_failure"] is True
    assert decision["consecutive_failures"] == 1
    assert decision["allowed"] is False
    assert decision["reason"] == "below_threshold"


def test_restart_policy_allows_restart_after_consecutive_misses():
    decision = MODULE._evaluate_restart_decision(
        service=MODULE.CheckResult(ok=False, detail="inactive"),
        ready=MODULE.CheckResult(ok=False, detail="connection refused"),
        state={"consecutive_failures": 2},
        config=_config(),
        now=datetime(2026, 6, 30, tzinfo=timezone.utc),
        dry_run=False,
    )

    assert decision["consecutive_failures"] == 3
    assert decision["allowed"] is True
    assert decision["reason"] == "threshold_met"


def test_restart_policy_recovery_resets_counter():
    decision = MODULE._evaluate_restart_decision(
        service=MODULE.CheckResult(ok=True, detail="active"),
        ready=MODULE.CheckResult(ok=True, status_code=200, detail="healthy"),
        state={"consecutive_failures": 2},
        config=_config(),
        now=datetime(2026, 6, 30, tzinfo=timezone.utc),
        dry_run=False,
    )

    assert decision["restartable_failure"] is False
    assert decision["consecutive_failures"] == 0
    assert decision["allowed"] is False
    assert decision["reason"] == "healthy"


def test_restart_policy_cooldown_blocks_repeated_restart():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    decision = MODULE._evaluate_restart_decision(
        service=MODULE.CheckResult(ok=True, detail="active"),
        ready=MODULE.CheckResult(ok=False, status_code=503, detail="degraded"),
        state={
            "consecutive_failures": 3,
            "last_restart_utc": (now - timedelta(seconds=60)).isoformat(),
        },
        config=_config(),
        now=now,
        dry_run=False,
    )

    assert decision["consecutive_failures"] == 4
    assert decision["allowed"] is False
    assert decision["reason"] == "cooldown_active"
    assert decision["cooldown_remaining_seconds"] == 240


def test_restart_policy_dry_run_blocks_restart_after_threshold():
    decision = MODULE._evaluate_restart_decision(
        service=MODULE.CheckResult(ok=False, detail="inactive"),
        ready=MODULE.CheckResult(ok=False, detail="connection refused"),
        state={"consecutive_failures": 2},
        config=_config(),
        now=datetime(2026, 6, 30, tzinfo=timezone.utc),
        dry_run=True,
    )

    assert decision["consecutive_failures"] == 3
    assert decision["allowed"] is False
    assert decision["reason"] == "dry_run"


def test_pre_restart_snapshot_includes_service_status_and_recent_journal():
    with patch.object(MODULE, "_systemctl_show") as show, patch.object(
        MODULE, "_recent_journal_tail"
    ) as journal:
        show.return_value = {"MainPID": "123", "NRestarts": "0"}
        journal.return_value = ["line one", "line two"]

        snapshot = MODULE._build_pre_restart_snapshot(
            service=MODULE.CheckResult(ok=False, detail="inactive"),
            nginx=MODULE.CheckResult(
                ok=True,
                status_code=200,
                detail="healthy",
            ),
            ready=MODULE.CheckResult(ok=False, detail="timed out"),
            journal_lines=2,
        )

    assert snapshot["systemctl_is_active"] == "inactive"
    assert snapshot["healthz"]["status_code"] == 200
    assert snapshot["readyz"]["detail"] == "timed out"
    assert snapshot["systemctl_show"]["MainPID"] == "123"
    assert snapshot["systemctl_show"]["NRestarts"] == "0"
    assert snapshot["recent_journal"] == ["line one", "line two"]
