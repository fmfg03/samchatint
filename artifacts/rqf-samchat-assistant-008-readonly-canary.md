# RQF-SAMCHAT-ASSISTANT-008 — Multi-Employee Read-Only Canary

Status: CANARY_RAN_HOTFIX_READY
Date: 2026-07-30
Runtime: `samchat-gastos.service`
Release observed: `/srv/samchat/releases/gastos-prod-678301566-sprint-close`
Mode: read-only canary

## Configuration observed

```text
ASSISTANT_AGENT_RUNTIME_ENABLED=true
ASSISTANT_AGENT_RUNTIME_READONLY_ONLY=true
ASSISTANT_AGENT_WRITES_ENABLED=false
ASSISTANT_AGENT_SHADOW_ENABLED=false
ASSISTANT_AGENT_PROVIDER_TIMEOUT_SECONDS=15
ASSISTANT_AGENT_RUNTIME_TOTAL_BUDGET_SECONDS=25
ASSISTANT_AGENT_PROVIDER_MAX_CONCURRENCY=2
```

Allowlist observed:

- Alberto Corona (`73847177-aca1-4348-8f1b-709a8cd8b432`)
- Francisco Fernandez (`b8816679-ad77-4590-83d5-50ffce335854`)

## Pre-canary status

- Service active: yes.
- `/healthz`: healthy.
- `/readyz`: healthy, schema health ok.
- Assistant tables before fresh traffic check showed no runs in the prior 48 hours.
- Historical assistant runs: 153 total; 141 completed, 10 provider_timeout, 2 failed.

## Canary execution

Actor: Francisco Fernandez via Hermes service auth.
Conversation: `539983ad-0d81-42f7-81bf-7e2494630317`
Prompts: 8 short read-only finance/operations prompts.

Results:

```text
total=8
ok_http=6
http_500=2
pending_confirmations=0
provider_timeout_responses=1
write_confirmations=0
```

Representative safe result:

- The assistant responded with read-only capabilities and did not request confirmation.
- Tool traces were persisted for successful runs.

Product/runtime issues found:

1. Two calls failed with provider HTTP 400 because Anthropic rejected tool schema `tools.7.custom.input_schema`: top-level `oneOf/allOf/anyOf` is unsupported.
2. One call returned a controlled provider-timeout response.
3. One read-only route produced a deterministic validation miss: `view must be one of: pendiente, vinculado, sin_gasto`.

## Immediate hotfix

Provider schema compatibility issue traced to `tournament_goal_shadow` tool definition. The tool used top-level `oneOf` to express source id/name alternatives. That is useful JSON Schema, but incompatible with Anthropic tools API.

Hotfix direction:

- Remove top-level `oneOf` from provider-facing schema.
- Keep `goal` as the only provider-required field.
- Preserve backend validation/resolution of `source_tournament_id` or `source_tournament_name`.
- Add/adjust unit coverage to assert no top-level `oneOf`, `allOf`, or `anyOf` in the provider tool schema.

## Safety conclusion

Canary safety posture held: no writes, no confirmations, no intentional mutations.

Product readiness is not yet good enough to expand allowlist. Fix provider schema compatibility first, then rerun Canary 008.

## Hotfix verification

Local hotfix tests on `/root/samchat`:

```text
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/test_assistant_tournament_goal_shadow_contract.py \
  tests/unit/test_assistant_agent_runtime.py \
  tests/unit/test_assistant_agent_runtime_contract.py \
  -q

43 passed, 7 warnings
```

The provider-facing `tournament_goal_shadow` schema no longer contains top-level `oneOf`, `allOf`, or `anyOf`.
