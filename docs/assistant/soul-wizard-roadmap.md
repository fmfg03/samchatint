# SOUL Wizard roadmap

Status: ACTIVE
Last updated: 2026-08-14
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

## Remaining cuts

### RQF-SOUL-WIZARD-003 - Clone from existing tournament

Objective:

Let Operations start a SOUL draft from an existing tournament or SOUL snapshot, then edit phases/dates/activities for the new edition.

Likely implementation:

- Add pure clone helper in `src/samchat/assistant/soul_wizard.py`.
- Accept source tournament snapshot / SOUL snapshot mapping.
- Produce `source_tournament_id`, `source_snapshot_id`, copied categories, branches, required documents, eligibility rules, finance baseline and phase/activity skeleton.
- Apply overrides for new name/year/dates.
- Add UI affordance on `/admin/sports/soul-wizard` for source ID/slug and maybe a sample/import path.

Acceptance:

- Clone output remains read-only and not executed.
- Clone preserves source metadata.
- New draft hash changes when overrides change.
- Tests prove source copy plus override behavior.
- No DB writes.

Do not overbuild:

- Do not implement full live DB search unless quick and already available.
- Do not create production tournament from clone.
- Do not wire assistant proposal yet; that is 004.

### RQF-SOUL-WIZARD-004 - Assistant proposal integration

Objective:

Allow assistant to propose a SOUL draft using the same contract, so a user can say: "create this tournament like last year but with these new dates/categories".

Likely implementation:

- Assistant read/preview tool or proposal artifact using `build_soul_wizard_payload`.
- Proposed action remains inert.
- Show missing information/questions.
- Output is a reviewable draft, not execution.

Acceptance:

- Assistant can produce a SOUL draft preview.
- It can ask for missing dates/categories/activities.
- It cites source tournament/snapshot when cloned.
- It never claims tournament was created.
- Approval/write path remains out of scope unless explicitly opened later.

## After cut 004

Pause and re-evaluate with the user.

Possible next routes:

1. Persistence for SOUL drafts.
2. Conversion from approved SOUL draft to real tournament objects.
3. Owner-pack folder generation from SOUL drafts.
4. Assistant live operations status using SOUL coverage.
5. UI polish after real operator feedback.

Do not automatically proceed into these without user confirmation.
