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
document admission takes a transaction-scoped advisory lock on the canonical
batch identity, so sequential or concurrent retries
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

## Resumable staged-upload amendment

Date: 2026-10-07

The first authenticated pilot attempt proved that the seven exact PDFs total
133,003,456 bytes, above the 100 MB request-body limit on the public route.
No batch, binding, or batch review-session row was written by that attempt.

The approved correction keeps one immutable manifest and one governed batch,
but transports each PDF in its own request. Each source remains limited to 64
MB and is streamed to a private persistent staging root instead of being held
in memory. PostgreSQL binds the staging envelope and expected file hashes to
the authenticated actor. The browser then advances admission one dossier per
request, so reloads and network failures resume through the existing
deterministic batch, document, and review-session identities.

The operator UI owns the multipart sequence and renders upload and admission
progress. API failures remain structured JSON for clients, while the page
extracts the bounded `detail.message` and displays it in an accessible inline
alert; the operator is never navigated to a raw JSON response.

Before activating this amendment:

1. create `/srv/samchat/data/private/ctt_batch_uploads` with mode `0700`;
2. apply `20261007_copa_telmex_staged_batch_uploads.sql` as the database owner;
3. verify runtime grants and the new required schema objects;
4. verify `/readyz` reports both schema and batch-staging health;
5. run authenticated upload, interruption/resume, inline-error, and `11/11`
   operator acceptance.

Rollback to the previous release leaves the additive staging tables and private
files inert. The schema rollback refuses to drop non-empty staging tables.
Pilot expansion remains blocked until the 11 admitted drafts, zero committed
teams, zero eligibility grants, exact source bindings, and staff governance
blocker are verified.

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

The staged-upload amendment changes transport, progress, and recovery only. It
does not change the manifest, draft authority, team/eligibility boundary, or
staff-governance blocker. Canon unchanged for this corrective amendment; the
actual pilot admission remains a separate runtime and UAT fact.

## Runtime-role admission correction: 2026-10-27

The authenticated pilot uploaded all seven PDFs (133,003,456 bytes), then failed
on the first dossier with PostgreSQL 42501. The admission migration intentionally
withholds UPDATE/DELETE from the runtime role on batch and document bindings,
while SELECT FOR UPDATE also requires UPDATE privilege. The former isolated
migration test checked grants but executed its operations as administrator.

The human-approved correction replaces batch-row locking with transaction-scoped
PostgreSQL advisory locks. A fixed two-integer namespace is separate from the
single-bigint staging locks. Its key comes from the canonical batch UUID, so
all authorized actors and both intake paths share the same serialization domain.
Hash collisions only serialize unrelated batches; they cannot bypass a lock.
Each lock is taken on the connection performing the protected check, insertion,
and commit, and is released on commit or rollback. Staging row locks remain.
No runtime grants, owner migrations, permissions or eligibility rules change.

The same real-helper test exposed an implicit async relationship load while
preparing the first draft after flush. New batch sessions now initialize their
assets/drafts collections explicitly and bind rendered assets to that collection
before draft preparation; existing draft authority and validation are unchanged.

The new PostgreSQL runtime tests use SET ROLE devnous_user on every connection
and retain the session role across pooled-connection rollbacks. They exercise
the actual admission helper, PDF rendering, ORM writes, draft preparation and
version persistence. Only the external Zaubern authorization response is
simulated; this is not evidence of live external authorization or authenticated
operator UAT. Synthetic sources cover single and eleven-dossier admissions,
one-dossier advancement, retries, foreign edition denial, rollback recovery and
concurrent actors observed waiting on an actual PostgreSQL advisory lock.

Implementation and isolated verification do not constitute merge, deployment,
production data application or 11/11 authenticated pilot acceptance. The seven
stored production PDFs must be retained; the later operator retry should reuse
the same manifest and files. Canon Engineering section 20 and its register hash
are updated only with the exact human-approved concurrency amendment. Product
and sweep canon remain unchanged because draft/team/eligibility authority and
the historical inventory do not change.

The runtime-role schema also installs the actual REG-S02, REG-S04 and REG-S05
triggers from their versioned migrations. This exposed a further missing
REG-S04 binding: admitted page updates must carry the draft operation, decision
and receipt identifiers. Batch intake now copies those three already-verified
identifiers from the canonical draft, following the existing web-upload path.
No trigger or grant is relaxed. A negative case verifies that later page
rebinding and draft edits still fail closed. This is additional evidence
completion within the same admission helper, not a new authority surface.

## Native FK ownership correction: 2026-10-27

The authenticated pilot on release `7b91ab36c` reached draft preparation but
failed on the document-binding INSERT with PostgreSQL 42501. In production,
`devnous_user` owned both append-only tables with UPDATE revoked. PostgreSQL's
native composite-FK check takes a key-share lock using the referenced table's
owner. Revoking UPDATE from that owner therefore prevented even the permitted
INSERT. The prior fixture retained the superuser migration owner and missed
this production condition.

The human-approved correction is the administrator-run migration
`database/migrations/20261027_copa_telmex_batch_table_owner.sql`. It transfers
only the batch and document-binding tables to `samchat_ctt_admission_owner`, a
NOLOGIN, non-superuser role with no memberships. That owner has USAGE of the
target schema and its own table privileges. The runtime retains SELECT/INSERT
only; UPDATE, DELETE, TRUNCATE, REFERENCES and TRIGGER privileges are absent.
Constraints, native triggers, source identities, draft receipts and staff gates
are unchanged. No application restart or runtime code change is required.

Run as the database administrator with `search_path=public` in production. The
migration checks the expected owner, runtime ACL, role properties, lack of
memberships and validated batch-edition FK. It fails closed for unexpected
state, uses five-second lock and sixty-second statement timeouts, and commits
atomically. Reapplying the same migration is safe. It is never startup DDL.

Before application, privately back up DDL, ownership, ACL, memberships and
counts. Verify the same row and constraint fingerprints afterwards, effective
runtime privileges, login prohibition and lack of role membership. Do not admit
production dossiers as part of the administrative deployment. The rollback
script restores the previously observed owner and ACL and intentionally
restores the known FK failure; prefer roll-forward and preserve the NOLOGIN
role if it may have other dependencies.

The integrated fixture now reproduces the original owner and revoked UPDATE,
connects directly as the non-superuser runtime (current_user and session_user
both `devnous_user`), then applies the migration. Regression cases cover the
original failing INSERT, successful admission/retry, independent concurrent
connections, eleven dossiers, owner isolation, effective grants and actual
UPDATE/DELETE/TRUNCATE and SET ROLE denials. The native FK still rejects a
missing batch; migration reapplication and rollback preserve admitted rows;
an incompatible pre-existing role or a runtime attempt to migrate is rejected.
REG-S02/04/05 guards remain installed. External Zaubern authorization is the
only simulated component. Local evidence does not replace authenticated UAT.

Canon: engineering section 20 includes the explicitly human-approved ownership
amendment and its reason. Product and sweep are unchanged: no business authority,
team capture, eligibility or historical inventory changes.
