# Copa Telmex pilot batch admission

Date: 2026-10-06

Evidence state: isolated implementation under review. This document does not
claim merge, deployment, migration application, data admission, authenticated
UAT, eligibility, or business acceptance.

## Purpose

Admit the 11 manually reconciled Copa Telmex 2026 dossiers into authenticated
registration-review drafts with immutable source identity. Admission preserves
the director tecnico and auxiliar evidence. It creates no team, player, staff,
or eligibility record.

## Admission contract

The private manifest uses `ctt.registration.batch-admission.v1` and binds:

- the exact tournament UUID, edition year, and roster slug;
- a deterministic document ID;
- the source PDF name and SHA-256;
- the ordered, one-based source pages and their SHA-256;
- the complete extraction payload and its SHA-256;
- exactly two technical-staff slots: `director_tecnico` and `auxiliar`;
- each staff slot to its role, source reference, and evidence through a
  recomputed stable SHA-256 identifier.

The server rejects missing, reordered, duplicated, ambiguous, or mismatched
evidence before opening a database transaction. PDF input is read with a 64 MB
per-file limit. Only declared source pages are rendered into review assets.

The preparer is local and private. It has no database or apply option. Its
manifest and reconciliation inputs remain outside the repository with mode
`0600` and are not included in logs or receipts.

## Persistence and retry behavior

The owner migration creates:

- `copa_telmex_registration_batches`;
- `copa_telmex_registration_batch_documents`;
- `copa_telmex_team_staff`.

Runtime startup DDL excludes these tables. The migration owns their creation.
Edition, manifest, document, source-page, review-session, and team/staff-slot
constraints fail closed. Rollback refuses to drop the schema after an admitted
document or staff record exists.

Batch and review-session UUIDs are deterministic. Batch creation is atomic and
document admission locks the batch row, so sequential or concurrent retries
recover the existing binding instead of creating a second review session. A
failed later document does not erase earlier admitted documents; rerunning the
same exact manifest resumes through the recovered bindings.

## Authority boundary

The endpoint requires the existing internal registration-review session and
role gate. Each receipt binds the authenticated actor, manifest, source,
payload, and review session without copying personal field values into the
receipt.

Every admitted draft records `human_reviewed=false` and
`canonical_import_ready=false`. The response explicitly reports zero committed
teams and no eligibility grant.

The current REG-S05/Zaubern roster contract authorizes ordered player slots but
does not authorize technical-staff slots. A draft containing staff therefore
receives `STAFF_GOVERNANCE_CONTRACT_REQUIRED` and cannot be committed. Team and
staff creation remain blocked until the external governance contract is
separately approved, implemented, and verified end to end.

## Rollout order

1. Approve the final diff and required canon amendment.
2. Commit, push, and review a protected PR as separately authorized effects.
3. Merge and prepare the release only after required checks and substantive
   review pass; keep the candidate inactive.
4. Take a fresh database backup, apply the backward-compatible owner migration,
   and verify all three tables and application grants before release activation.
5. Activate the prepared release and verify health, readiness, protected-route
   denial, restart count, journal, and rollback target.
6. Submit the exact private 11-document manifest and PDFs through an
   authenticated operator session.
7. Verify 11 review drafts, zero new teams, zero eligibility grants, exact
   source bindings, and the staff governance blocker.
8. Run authenticated operator UAT before expanding to the remaining source
   batch.

The release must not be activated before the owner migration: commit and
reprocess query the batch-binding table to enforce the immutable staff blocker.
The previous release remains compatible with the additive tables during this
migration-first window.

## Verification in the isolated candidate

- focused admission, template, incident, operational-surface, and schema-policy
  tests;
- owner migration, constraints, idempotent installation, and guarded rollback
  against an isolated Unix-socket-only PostgreSQL;
- source manifest regeneration from the original PDFs with no database writes;
- diff hygiene, scoped formatting, import ordering, lint, type, and regression
  checks recorded in the final build report.

## Canon impact

The current canon says that the 11 reviewed dossiers remain pending canonical
admission. This implementation changes repository capability by adding a
review-only admission path, while actual admission remains pending until the
deployed endpoint is invoked. Product and Engineering canon amendments and
their refreshed register hashes therefore require explicit human review in the
same PR.
