# SOUL Wizard roadmap

Status: READ_ONLY_MINI_ROADMAP_CLOSED
Last updated: 2026-08-17
Parent branch: `codex/rqf-054-owner-pack-institutional-boards`

## Why this exists

Operations needs a guided way to create the institutional SOUL for each tournament. The owner pack, Sports Platform, assistant memory, and operational dashboards all depend on the same basic tournament context:

- tournament identity;
- categories and branches/genders;
- phases and dates;
- activities per phase;
- responsibilities;
- expected entities and teams;
- documents and eligibility rules;
- finance baseline;
- later: clone from prior tournament and assistant proposal workflow.

The user explicitly warned not to disappear into this rabbit hole. Treat this as a four-cut mini-roadmap and return to the broader assistant/owner roadmap after each cut.

## Non-negotiable scope boundaries

- Do not create operational tournaments yet.
- Do not create teams, calendars, communications, or payments.
- Do not claim complete owner-pack readiness from the wizard alone.
- Wizard output is a draft/review artifact until explicit authority paths exist.
- Keep the UI useful but simple. Operations should not need a perfect ontology to start.

## Completed cuts

### RQF-SOUL-WIZARD-001 - Wizard contract and draft model

Commit: `a487e2c assistant: add soul wizard draft contract`

Implemented:

- `src/samchat/assistant/soul_wizard.py`
- `tests/unit/test_assistant_soul_wizard.py`
- registry artifact `assistant.soul_wizard_contract`

Contract includes:

- `SoulWizardDraft`
- `SoulWizardPhase`
- `SoulWizardActivity`
- `SoulWizardReadinessReport`
- validation for missing identity, phases, dates, activities, invalid dates, and warnings.

Read-only guarantees:

- `execution_status = not_executed`
- `operational_writes_allowed = False`
- writes attempted = 0
- side effects detected = 0

### RQF-SOUL-WIZARD-002 - Admin UI stepper

Commit: `8d67f91 assistant: add soul wizard admin stepper`

Implemented:

- UI route: `/admin/sports/soul-wizard`
- Link from `/admin/torneos`
- GET renders empty wizard.
- POST reviews the draft and returns readiness preview.
- CSRF uses tournament-admin CSRF guard.
- Permission uses `require_tournament_admin`.

UX pattern:

- Identity fields.
- Category/branch/entity textareas.
- Six phase cards.
- Activities entered one per line:

```text
activity name | owner | YYYY-MM-DD
```

- Preview shows status, score, errors, warnings, tournament info and phase summaries.
- Page explicitly says it does not create tournaments/equipo/calendar/communications.

Verification at close:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/test_assistant_soul_wizard.py \
  tests/unit/test_assistant_institutional_artifact_registry.py \
  -q
# 13 passed

PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/test_assistant_soul_wizard.py \
  tests/unit/test_assistant_institutional_artifact_registry.py \
  tests/unit/gastos/test_tournament_admin_authority_closure.py \
  tests/unit/test_assistant_tournament_goal_shadow.py \
  tests/unit/test_assistant_tournament_draft_workbench.py \
  -q
# 78 passed
```

## Completed cuts continued

### RQF-SOUL-WIZARD-003 - Clone from existing tournament

Commit: `27113be assistant: add soul wizard clone draft`

Implemented:

- Pure clone helper in `src/samchat/assistant/soul_wizard.py`.
- Accepts source tournament snapshot / SOUL snapshot mapping.
- Preserves source tournament id, source snapshot id and source tournament name.
- Copies categories, branches, expected entities, expected teams, required documents, eligibility rules, finance baseline and phase/activity skeleton.
- Applies non-empty form overrides for new name/year/dates/fields.
- UI affordance on `/admin/sports/soul-wizard` for JSON source snapshot import.

Acceptance met:

- Clone output remains read-only and not executed.
- Clone preserves source metadata.
- Tests prove source copy plus override behavior.
- No DB writes.

### RQF-SOUL-WIZARD-004 - Activation preview/diff

Commit: `583f09e assistant: add soul wizard activation preview`

Implemented:

- Backend `preview` contract for manual drafts and clone diffs.
- Field-level status: captured, inherited, overridden, added, missing, removed_or_missing, changed.
- Summary counts for inherited, overridden, missing, blockers and warnings.
- UI section: `Diff de activacion propuesta`.
- Explicit non-claims: does not activate tournament, create records, or send notifications.

Acceptance met:

- SOUL draft preview is visible and machine-readable.
- Clone sources are distinguishable from manual captured fields.
- Missing dates/categories/activities remain visible as blockers/warnings.
- It never claims tournament was created.
- Approval/write path remains out of scope.

## After cut 004

Pause and return to the principal Assistant/Owner Pack roadmap. SOUL is the context substrate, not the main roadmap.

Possible next routes:

1. Persistence for SOUL drafts.
2. Conversion from approved SOUL draft to real tournament objects.
3. Owner-pack folder generation from SOUL drafts.
4. Assistant live operations status using SOUL coverage.
5. UI polish after real operator feedback.

Do not automatically proceed into these without user confirmation.
