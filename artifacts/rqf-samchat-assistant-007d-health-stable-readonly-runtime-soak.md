# RQF-SAMCHAT-ASSISTANT-007D — Health-Stable Read-Only Runtime Soak After Provider Isolation

Status: PASSED_HEALTH_STABLE_READONLY_RUNTIME_SOAK

Final decision:

- ALLOW_MULTI_EMPLOYEE_READ_ONLY_CANARY_PLANNING
- RUNTIME_ROLLED_BACK_OFF_AFTER_SOAK
- WRITES_OFF
- DO_NOT_ENABLE_WRITES
- DO_NOT_ENABLE_GENERAL_RUNTIME
- DO_NOT_EXPAND_AUTOMATICALLY

## Scope

Run a health-stable read-only runtime soak after provider isolation. No code changes, no write enablement, and no allowlist expansion were performed during the soak.

## Config

Formal soak config:

```text
ASSISTANT_AGENT_RUNTIME_ENABLED=true
ASSISTANT_AGENT_RUNTIME_READONLY_ONLY=true
ASSISTANT_AGENT_RUNTIME_EMPLOYEE_IDS=d21a5407-aa5f-42d5-a90e-f3293b4eb907
ASSISTANT_AGENT_WRITES_ENABLED=false
ASSISTANT_AGENT_SHADOW_ENABLED=false
ASSISTANT_AGENT_PROVIDER_TIMEOUT_SECONDS=15
ASSISTANT_AGENT_RUNTIME_TOTAL_BUDGET_SECONDS=25
ASSISTANT_AGENT_PROVIDER_MAX_CONCURRENCY=2
```

Runtime surface:

- systemd unit: `samchat-gastos.service`
- live runtime: `copa_telmex_dashboard.py`
- allowlisted employee: `d21a5407-aa5f-42d5-a90e-f3293b4eb907`

## Window

- date: `2026-07-02`
- start: `18:54:30+02:00`
- end: `18:57:38+02:00`
- requested interactions: 10
- completed interactions: 10
- failed client interactions: 0

## Runtime Results

```text
interactions=10
ok=10
runtime_allowed=10
tool_trace_persisted=10
pending_confirmations=0
controlled_timeout_responses=4
provider_timeout_traces=4
```

Observed tools:

```text
finance_ops_query=8
finance_alerts_scan=8
finance_realtime_report=4
finance_strategy_snapshot=2
assistant_canonical_query=2
```

All runtime decisions were `RUNTIME_ALLOWED_EMPLOYEE_ID`.

## Health Results

Concurrent probes during the formal window:

```text
probe_count=165
healthz_200=165
readyz_200=165
healthz_max_s=1.145925
readyz_max_s=0.183204
```

Healthcheck during the formal window:

```text
healthcheck restarted=true count=0
healthcheck runs observed=3
all healthcheck runs ok=true
all healthcheck restart decisions reason=healthy
```

Service stability during the formal window:

```text
samchat-gastos stop/start/failure count=0
```

## Side Effect Evidence

Baseline counts:

```json
{
  "assistant_conversations": 363,
  "assistant_messages": 214,
  "assistant_runs": 98,
  "cuentas_de_gastos": 0,
  "documentos": 21,
  "expense_reports": 18,
  "telegram_notification_outbox": 328
}
```

Post-soak counts:

```json
{
  "assistant_conversations": 364,
  "assistant_messages": 234,
  "assistant_runs": 108,
  "cuentas_de_gastos": 0,
  "documentos": 21,
  "expense_reports": 18,
  "telegram_notification_outbox": 328
}
```

Deltas:

```text
assistant_conversations=+1
assistant_messages=+20
assistant_runs=+10
cuentas_de_gastos=0
documentos=0
expense_reports=0
telegram_notification_outbox=0
```

Interpretation: only expected assistant telemetry changed. Operational tables did not change.

## Rollback

Runtime was rolled back off after the soak:

```text
ASSISTANT_AGENT_RUNTIME_ENABLED=false
ASSISTANT_AGENT_RUNTIME_READONLY_ONLY=true
ASSISTANT_AGENT_RUNTIME_EMPLOYEE_IDS=
ASSISTANT_AGENT_WRITES_ENABLED=false
ASSISTANT_AGENT_SHADOW_ENABLED=false
```

Post-rollback health:

```text
ActiveState=active
SubState=running
NRestarts=0
healthz=200
readyz=200
monitor dry-run ok=true restarted=false reason=healthy
```

## Acceptance Mapping

| Criterion | Result | Evidence |
| --- | --- | --- |
| 10-20 real allowlisted interactions | PASS | 10 completed |
| 100% tool_trace persisted | PASS | 10/10 |
| 100% runtime decision recorded | PASS | 10/10 `RUNTIME_ALLOWED_EMPLOYEE_ID` |
| healthz=200 in all probes | PASS | 165/165 |
| readyz=200 in all probes | PASS | 165/165 |
| HTTP 5xx=0 | PASS | 10/10 client interactions OK |
| healthcheck restarts=0 | PASS | restarted=true count 0 |
| samchat-gastos stop/start=0 | PASS | stop/start/failure count 0 |
| write_handlers_invoked=0 | PASS | pending confirmations 0; writes disabled |
| side_effects_detected=0 | PASS | operational deltas all 0 |
| rollback after window | PASS | runtime off and health healthy |

## Caveats

Four interactions returned controlled provider timeout responses. This is acceptable for the health-stability gate because the provider isolation patch prevented event-loop blockage, preserved health/readiness, and returned controlled responses without side effects. It should still be tracked as product-quality evidence for future runtime tuning.

The global working tree remains dirty. Do not release from `/root/samchat`; use a clean branch containing the approved provider isolation patch and this artifact if artifacts are included in release scope.

## Next Stage

RQF-SAMCHAT-ASSISTANT-008 — Multi-Employee Internal Read-Only Canary Planning

Allowed:

- plan 2-3 internal employees
- keep runtime read-only
- keep writes disabled
- keep provider timeout/concurrency controls

Still blocked:

- writes
- general runtime
- external users
- authority closure claims
