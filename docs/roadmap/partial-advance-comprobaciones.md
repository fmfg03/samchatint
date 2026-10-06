# Partial paid-advance comprobaciones

Date: 2026-10-06. Proposed implementation and canon amendment for human PR review.
Source: Francisco's request for I-575016, Carlos Lozano. The supplied amounts
are test fixtures; no live report or financial data has been changed.

## Behavior

The applicant reviews the new expense count/amount, confirms a required motive
and submits a partial lot. The approver sees that motive and must record comments.
Control Presupuestal and existing authorization gates still apply. Telegram
approval notifications link to the document review form so comments can be entered.

The original report remains open. Each child INFORME owns its expense membership
and one atomic COI policy, using its own approval month and debtor posting identity.
Neither repeat submission nor repeat accounting recognizes the same expenses twice.
Approved lot evidence and original case identity are protected by database triggers.
Later corrections require accounting reversal, which this feature does not create.

Actual returns require a transfer receipt and an idempotent submission key. A
promised return has no accounting effect. Available capture balance limits further
returns; the persisted debtor auxiliary shows the recognized balance separately.
Closing requires every child approved, no active unassigned expenses, and an actual
zero debtor balance. Closing the original creates no additional expense policy.

| Event | Debit | Credit | Recognized collaborator balance |
| --- | --- | --- | --- |
| Paid advance | Collaborator debtor 32,370 | Bank 32,370 | 32,370 |
| Approve first lot | Expense/tax accounts 13,912.50 | Collaborator debtor 13,912.50 | 18,457.50 |
| Register actual return | Bank 9,200 | Collaborator debtor 9,200 | 9,257.50 |
| Approve another lot | Expense/tax accounts 4,000 | Collaborator debtor 4,000 | 5,257.50 |
| Actual final return | Bank 5,257.50 | Collaborator debtor 5,257.50 | 0 |

The existing accounting preview determines expense/tax splits; the table expresses
aggregate movements. Company-AMEX cuts and ordinary reimbursements retain their
existing rules. A previously approved original cannot be converted. Missing paid
advance postings require Accounting reconciliation before opting in.

## Verification

- Focused workflow, ledger, reimbursement, budget-control, approval visibility,
  COI and Telegram regression suite: 278 tests passed, including requester/approver web controls and zero-only closure.
- New SQLite-backed business suite: actual workflow transitions, postings, lines,
  approval history and receipt persistence; external notifications/audit and project
  route hooks stubbed, no-tax accounting preview fixture, JSON auxiliary discovery
  adapted for SQLite. Changed-statement coverage: 87.55%; new partial service: 93.8%.
- Isolated PostgreSQL/WASM migration test applies the DDL twice and verifies
  expense immutability, original identity, membership, COI status updates, multiple
  returns, duplicate keys and the one-active-reimbursement constraint.
  Run with `node tests/integration/partial_advance_migration.mjs
  /path/to/@electric-sql/pglite/dist/index.js` after installing PGlite locally.

These checks do not establish native PostgreSQL concurrency behavior, complete
tax scenarios, authenticated browser UAT, production migration or deployment.

## Release and rollback

1. Review this proposal and the product/engineering canon amendment explicitly.
2. The authorized database owner inventories existing settlement indexes and
   takes a verified backup. Apply
   `database/migrations/20261006_partial_advance_comprobaciones.sql` transactionally
   before releasing code that references the new columns. No startup DDL.
3. Release through existing immutable release/gate procedures. Finance UAT should
   exercise requester reason, approver comments, budget assignment, receipt uploads,
   each lot's COI export, later-period recognition, duplicate retries and final close.
4. Verify live auxiliary balances, expense membership, policy identifiers and
   authenticated access before accepting the feature.

Once partial lots or multiple returns exist, rolling back only application code is
unsafe: older code lacks the ownership rules and may collect child expenses through
the original account. Disable affected writes/exports until reconciliation and an
approved forward fix or coordinated data/schema restoration. Do not recreate the
old unique active-settlement index while multiple active returns exist.
