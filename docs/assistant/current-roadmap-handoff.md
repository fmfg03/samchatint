# SamChat current roadmap handoff

Status: ACTIVE_CONTEXT
Last updated: 2026-08-14
Branch at time of writing: `codex/rqf-054-owner-pack-institutional-boards`

This file exists so the roadmap survives conversation compaction. Read this before continuing assistant/owner-pack/SOUL work.

## Product north star

SamChat is an operational assistant built with the Claude Code paradigm: it understands a business request, inspects available context, uses governed business tools, generates artifacts, proposes actions, and executes only with explicit authority.

It is not a dashboard with chat. Dashboards are auxiliary surfaces. The assistant is the primary operational interface.

## Current architectural guardrails

- Do not wire artifacts just to wire them.
- For each candidate artifact: inspect implementation, usage, tests, overlap, and fit against real user questions before exposing it.
- Read-only evidence and previews may move quickly.
- Writes remain behind preview, explicit human authority, idempotency, audit, and receipt boundaries.
- Memory/precedent informs decisions; it never grants authority.
- Do not expand scope into hardening or infrastructure unless it protects the current slice.

## Main roadmap lanes

### Lane A - Assistant institutional intelligence

Goal: make the assistant answer operational questions using institutional artifacts and evidence-backed context.

Recently completed on this branch:

- `finance.closeout_diagnostics` tool and tests.
- `assistant_institutional_artifacts` registry tool and tests.
- `assistant.owner_entity_dossier_audit` artifact audit.
- `assistant.sports_platform_audit` artifact audit.

Important decisions:

- `sports.director_general_entity_dossier` is useful but must not be exposed raw.
- `sports.platform_snapshot` is useful but too broad; expose narrowed operational views later.
- `tournament.soul_snapshot` is wired, but coverage is incomplete because there should be one SOUL per tournament and currently coverage appears thin.

### Lane B - Owner pack / Director General requests

Goal: support the owner request for tournament/entity folders with operations, finance, national phase, marketing evidence, and missing-information tracking.

Current state:

- Owner pack live snapshot/status/inventory exist.
- Entity dossier audit exists.
- Platform snapshot audit exists.
- Still needs reliable SOUL coverage per tournament before promising complete owner folders.

Do not claim full owner-pack readiness until the SOUL data exists for each tournament.

### Lane C - SOUL Wizard

Goal: let Operations create a tournament SOUL step by step, so each tournament can have the context needed by assistant, owner pack, and operations dashboards.

See `docs/assistant/soul-wizard-roadmap.md` for the active four-cut plan.

Completed:

- `RQF-SOUL-WIZARD-001` - draft contract and validation.
- `RQF-SOUL-WIZARD-002` - admin UI stepper.

Next:

- `RQF-SOUL-WIZARD-003` - clone from existing tournament.
- `RQF-SOUL-WIZARD-004` - assistant proposal integration.

### Lane D - Buzz / multiagent collaboration spike

Tracked separately as `RQF-SAMCHAT-BUZZ-001` and GitHub issue #148.

Status: roadmap-added experimental spike only.

Rules:

- Do not let Buzz block ordinary assistant roadmap work.
- No production writes.
- No real fiscal documents in the spike.
- Raw documents remain in deterministic evidence enclave.
- Buzz rooms are collaborative, not authoritative.

See:

- `docs/assistant/rqf-samchat-buzz-001-phase-0.md`
- `docs/assistant/rqf-assistant-009-quality-roadmap.md`

### Lane E - Finance/accounting/operations product backlog

This lane contains customer-facing operational modules already worked in prior sprint branches:

- AMEX reconciliation.
- Bank reconciliation and cash flow.
- CxC/CxP.
- SAT massive download and CFDI matching.
- Accounting cleanup and prepolizas.
- Beneficiary/third-party requests.
- Authorization profiles/strategy.
- Materialities, CFDI totals, tips, ISH, no-deductible rules.

Do not lose this lane, but the current thread focus returned to assistant/owner/SOUL after several operational bug pauses.

## Current branch commits to remember

Recent commits on this branch include:

- `8d67f91 assistant: add soul wizard admin stepper`
- `a487e2c assistant: add soul wizard draft contract`
- `0faa904 assistant: audit sports platform artifact`
- `585e449 assistant: audit owner entity dossier artifact`
- `d4311cb assistant: expose institutional artifact registry tool`
- `5afebd2 assistant: add institutional artifact registry`
- `caf4cb8 assistant: add finance closeout diagnostics`

## Where to resume if compacted

1. Check `git status --short --branch`.
2. Read this file.
3. Read `docs/assistant/soul-wizard-roadmap.md`.
4. Continue with `RQF-SOUL-WIZARD-003` unless the user explicitly changes focus.
5. Keep the SOUL work scoped: capture/clone/review first, no production writes before preview and approval boundary.

## Remote working notes

The active repo has been worked on remotely at:

`/tmp/samchat-propina-edit-field`

Typical verification command:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest <tests> -q
```

Use ASCII in scripted remote file writes when possible; prior PowerShell-to-SSH sessions can corrupt accented characters.
