# RQF-SAMCHAT-SERVICE-STABILITY-002 - Healthcheck Restart Policy Hardening

Date: 2026-06-30

## Status

IMPLEMENTED_VALIDATED_RUNTIME_OFF

## Scope

Harden `scripts/monitor_samchat_health.py` so `samchat-gastos.service` is not restarted on one transient miss, and so restart decisions emit structured evidence before a restart.

No assistant runtime behavior was changed.
No assistant runtime was activated.
No writes were enabled.

## Implementation

The healthcheck monitor now supports:

- consecutive failure threshold before restart
- cooldown between restarts
- non-blocking flock lockfile to avoid overlapping timer runs
- `--no-restart` dry-run mode
- separate service, `healthz`, and `readyz` reporting
- pre-restart snapshot before `systemctl restart`
- env/argument configuration:
  - `SAMCHAT_HEALTHCHECK_FAILURE_THRESHOLD`
  - `SAMCHAT_HEALTHCHECK_COOLDOWN_SECONDS`
  - `SAMCHAT_HEALTHCHECK_TIMEOUT_SECONDS`
  - `SAMCHAT_HEALTHCHECK_LOCK_PATH`

Default effective policy:

```text
failure_threshold=3
cooldown_seconds=300
timeout_seconds=5
lock_path=/tmp/samchat-healthcheck.lock
```

Restart eligibility remains intentionally narrow:

- `systemctl is-active samchat-gastos.service` failure can count toward restart.
- `readyz` failure can count toward restart.
- `healthz` is recorded in the report and pre-restart snapshot, but does not by itself trigger restart. This preserves the previous restart semantics.

## Pre-Restart Snapshot Fields

When `--restart-on-failure` is set and a restartable failure exists, the report includes:

```text
timestamp_utc
systemctl_is_active
healthz.ok/status_code/detail
readyz.ok/status_code/detail
service.ok/detail
systemctl_show.MainPID
systemctl_show.NRestarts
systemctl_show.ActiveState
systemctl_show.SubState
systemctl_show.Result
systemctl_show.ExecMainStatus
systemctl_show.ExecMainCode
recent_journal
```

## Tests

Unit tests added/updated in `tests/unit/test_monitor_samchat_health.py`:

- 1 miss does not restart
- N consecutive misses allow restart
- recovery before threshold resets counter
- cooldown blocks repeated restart
- dry-run blocks restart after threshold
- pre-restart snapshot includes service status, healthz, readyz, MainPID, NRestarts, and recent journal

## Validation

Commands:

```text
python3 -m py_compile scripts/monitor_samchat_health.py tests/unit/test_monitor_samchat_health.py
./scripts/pytestw tests/unit/test_monitor_samchat_health.py
/root/samchat/.venv/bin/flake8 scripts/monitor_samchat_health.py tests/unit/test_monitor_samchat_health.py
python3 scripts/monitor_samchat_health.py --restart-on-failure --no-restart --failure-threshold 3 --cooldown-seconds 300 --journal-lines 5
```

Results:

```text
py_compile: PASS
pytest monitor: 8 passed
flake8 scoped: PASS
dry-run monitor: ok=true, restarted=false, restart_decision.reason=healthy
```

Dry-run monitor result:

```text
restartable_failure=false
consecutive_failures=0
threshold=3
allowed=false
reason=healthy
cooldown_seconds=300
dry_run=true
service=active
healthz=200
readyz=200
```

## Baseline

Runtime posture during baseline:

```text
ASSISTANT_AGENT_RUNTIME_ENABLED=false
ASSISTANT_AGENT_SHADOW_ENABLED=false
ASSISTANT_AGENT_WRITES_ENABLED=false
```

Probe window:

```text
start_utc=2026-06-30T03:28:58.161899+00:00
end_utc=2026-06-30T03:33:59.937400+00:00
probe_count=60
interval_seconds=5
healthz_failures=0
readyz_failures=0
http_5xx=0
```

Systemd/journal cross-check during baseline:

```text
samchat-healthcheck.service ran at 05:29:42, 05:30:44, 05:31:44, 05:32:46, 05:33:49 CEST
all healthcheck reports: ok=true, restarted=false, reason=healthy
samchat-gastos.service stop/start events during baseline: 0
NRestarts=0
```

Final service state:

```text
samchat-gastos.service active/running
MainPID=3069059
Result=success
NRestarts=0
ASSISTANT_AGENT_RUNTIME_ENABLED=false
ASSISTANT_AGENT_SHADOW_ENABLED=false
ASSISTANT_AGENT_WRITES_ENABLED=false
```

## Caveat

The working tree is globally dirty and contains many unrelated staged/untracked files. This artifact validates only the scoped files for this RQF.

## Decision

SERVICE_STABILITY_002_IMPLEMENTED_BASELINE_PASS

ALLOW_HEALTHCHECK_HARDENING_REVIEW

KEEP_ASSISTANT_RUNTIME_OFF

DO_NOT_ENABLE_WRITES

DO_NOT_EXPAND_ASSISTANT_RUNTIME

Next stage:

`RQF-SAMCHAT-ASSISTANT-007C - Health-Stable Read-Only Runtime Soak After Healthcheck Hardening`
