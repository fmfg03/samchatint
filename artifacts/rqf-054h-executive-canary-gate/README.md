# RQF-054H Executive Canary Gate

Status: FIXTURE_GATE_CAPTURED
Date: 2026-08-31

## Fixture Gate

Command:

```bash
./.venv/bin/python scripts/run_assistant_executive_canary.py --fixture
```

Captured result:

- Schema: `samchat.assistant_executive_canary.v1`
- Mode: `fixture`
- Status: `pass`
- Total: 7
- Passed: 7
- Failed: 0
- Timeouts: 0

The fixture gate is deterministic and local. It does not call HTTP, providers,
credentials, or business write paths.

Fixture evidence is stored in:

- `artifacts/rqf-054h-executive-canary-gate/fixture-result.json`

## Live Gate Boundary

The live canary remains pending. It requires explicit authenticated operator
approval and either a cookie file or bearer token passed to
`scripts/run_assistant_executive_canary.py --live`.

Live mode targets `/api/assistant`, must not print secrets, and must preserve
the read-only authority boundary:

- no pending confirmations
- no writes detected
- provider/model recorded
- latency and timeout evidence recorded
- pass/fail captured per executive regression case

No live canary, deployment, restart, frontend asset copy, or runtime mutation
was performed in this stage.
