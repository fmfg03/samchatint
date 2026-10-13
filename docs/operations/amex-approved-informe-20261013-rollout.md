# AMEX approved INFORME — release and rollback procedure

Status: approved implementation candidate, not merged or deployed. Technical-spec and canon approval does not authorize production DDL, data repair or release activation.

## Admission and migration

1. Review the final diff, required tests, independent validation, canon hashes and route inventory. Pin the reviewed commit and exact release bytes.
2. Obtain explicit release/migration authorization. Confirm the current release selector and preserve its receipt; identify the live schema and approved owner connection without exposing credentials.
3. Run the existing metadata-only inspection with the owner-approved environment variable: `PYTHONPATH=src python scripts/check_amex_approved_informe_schema.py --database-url-env AMEX_SCHEMA_DATABASE_URL`. Missing new schema on the old release is expected; the command neither creates schema nor reads business rows. Inspect the old recognition constraints, source ownership and integrity read-only before applying DDL.
4. Create and verify a custom-format backup; retain its SHA-256 and successful `pg_restore --list` receipt. Schedule a controlled write pause for the migration/release window so old code cannot write a new origin it cannot read.
5. Apply `database/migrations/20261013_amex_approved_informe_recognition.sql` with the separately authorized owner connection, error-stop and its transaction. The migration preserves legacy IDs/receipts and does not repair or backfill historical expenses.
6. Run metadata-only inspection again; require ready=true, with all runtime-required columns, types, nullability, validated anchor constraints and enabled guard triggers on their expected tables. Prepare the reviewed runtime using the existing release process. No helpers or startup paths provision these tables.
7. Activate the reviewed release, verify health/readiness/schema guards and require authenticated Finance acceptance separately. Approval alone must create neither policy nor financial decision. An explicit consolidated Finance confirmation creates one complete cut; a subsequent bank association must preserve that cut and policy.

## Compatible rollback

After any report-first recognition exists, a historical release assuming imported_expense_id is always nonnull is incompatible. Keep the additive schema and immutable financial records. Build a rollback release from the same reviewed candidate with `amex-approved-informe-20261013-disable-writes.patch`: reads, frozen exports and exact successful replays remain available; new report-first confirmations, cuts/adjustments and new bank associations return pending. Real approval evidence may still be captured. Validate the compatible release before activation; preserve the original candidate as a separate receipt. The patch is a reviewed artifact, not a production mutation.

Down-DDL in `database/migrations/20261013_amex_approved_informe_recognition_rollback.sql` is only admissible before dependent report-first rows or immutable evidences/associations exist. Its preflight must reject populated state without deleting financial records. Do not rewrite policies, snapshots, actors or cuts to permit a schema downgrade.

## Acceptance and residual business work

Confirm the operational approver and approval date shown in Finance, declare accounting treatments explicitly without per-item comprobacion checkboxes, and generate the full report policy once. Repeat the same confirmation and verify the same policy. Associate one bank charge to its existing report receipt(s) and verify no additional expense, CFDI application, budget actual or export policy. Test a mismatched bank charge as an exception.

Historical missing snapshots receive only a current Finance attestation with a reason. The 14 preactivation historical expenses stay separately blocked; no automatic repair, fake approval or bank matching is included. Production migration, release health, authenticated Finance UAT and business acceptance each require their own evidence.

Artifact admission from repository root: first require `sha256sum -c docs/operations/amex-approved-informe-20261013-rollback-source.sha256`, then `git apply --unidiff-zero --check docs/operations/amex-approved-informe-20261013-disable-writes.patch`. The zero-context patch avoids trailing whitespace in a versioned patch document; never apply it to source bytes differing from the pinned manifest. Applying/building/activating a rollback release still requires the separately authorized release procedure.
