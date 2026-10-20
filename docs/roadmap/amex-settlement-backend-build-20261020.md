# AMEX settlement backend implementation

Approved scope: technical specification §§5–7, 2026-10-20. This is an isolated
candidate; no production access, migration, commit, publication or deployment.

## Implementation

Three accounting routes share the existing direct-statement confirmation owner:
new expense recognition; association to a genuine prior report/direct receipt;
and historical expense-account credit settlement. A settlement debits the exact
original ledger account and credits the configured AMEX liability. It creates no
new expense, IVA, fiscal consumption or budget assignment. Mixed cuts preserve
complete membership and one new journal. Prior-only statements create an
append-only completion receipt and preserve links to original policies.

The scoped source picker uses SQL scope filtering before a stable R2/R3 union
and keyset LIMIT, plus session-bound opaque cursors. Selection is an audited,
versioned proposal; only final confirmation materializes allocations. Original
CFDI metadata and XML hash are verified, bound into the preview/source proof,
and frozen for COI export without treating a used CFDI as a new fiscal use.

Four owner-migrated tables and two evidence views retain real foreign keys,
append-only guards, physical source/destination ledger identities, exact numeric
residuals, complete membership, freeze and competitive ownership checks. The
new coverage guards retain every prior fiscal/budget branch. The schema receipt
preserves all previous expected trigger/function names and adds the new contract.
Startup continues to exclude all four owner tables.

The lock graph and narrow provenance-based source seal are documented separately
in `amex-settlement-lockgraph-20261020.md`. Unattributed advances/liquidations
remain exceptions rather than being silently partitioned. Final constraints are
forced inside the nested UOW, so an outer caller that catches a failure and
commits cannot retain a partial journal, cut, allocation or bank coverage.

The debtor auxiliary includes only real settlement debits; it does not add the
new expense or AMEX liability to its historical comprobación totals.

## Changed backend files

- `src/devnous/gastos/amex_settlement_models.py`, registration in `models.py`,
  and owner-table exclusions in `amex_schema_policy.py`.
- `services/amex_expense_account_settlement_service.py` and direct accounting.
- `services/employee_debtor_accounting_service.py` auxiliary read predicates.
- `services/amex_direct_schema_service.py` and static schema contract.
- Forward/rollback `20261020_amex_expense_account_settlements.sql` migrations.
- Shared direct PostgreSQL fixture migration chain, new settlement PG tests,
  and historical migration-replay test tail handling.

The orchestrator owns fiscal strict mode, common/result defaults, UI/routes,
canon amendments and route evidence; those are independently integrated changes.

## Verification

New PostgreSQL cases exercise real comprobación/report posting owners, explicit
source selection, sole R3 cut and all exports, sole R2 completion, mixed R1/R2/R3,
source exceptions, immutable evidence, late rollback with outer catch/commit,
concurrent duplicate confirmation, distinct-import credit competition, both
source mutation race winner directions, unrelated-writer nonblocking, revoked
prior-source scope, 27-source pagination and invalid cursors, invalid completion
membership, competitive cut rejection and rollback refusal after final evidence.

Final focal mypy: no issues in three source files. Focal runtime/new PG flake8
at 88 columns, Black and isort passed. Quality tools were reused from the
existing release environment; no dependencies were installed.

The earlier broad coverage run exposed two formatting-induced literal-call
errors, now corrected; that failed dataset is superseded. A subsequent baseline
retention fixture correctly failed the new strict fiscal validator because its
Concepto omitted the root withholding; the orchestrator owns that fixture repair.
No fiscal guard was weakened. Final measured PostgreSQL results and combined
coverage are appended by the final verification phase.

## Risks and limits

No historical opening balance, inferred debtor account, name-only selection,
permissive legacy DIRECT binder, automatic data repair or destructive rollback
was introduced. Ambiguous account-level liquidations remain blocked. Real
financial source correspondence and production Finance UAT remain independent.


## Final backend handoff

Before the last historical-origin extension, the final source passed 22 settlement
PostgreSQL cases and all 40 schema PostgreSQL cases, including the new-tail
rollback order. All old 53 trigger names and 31 function names remain required;
the installed final contract requires 76 triggers and 38 function hashes.
The orchestrator separately verified 27 backend and five suggestion SQL cases.

Independent review then required accepting genuine historical uploaded XML in
R3. The dedicated helper now verifies XML/material identity, UUID, RFCs, type I,
MXN and XML hash after proving the exact original ledger credit. It does not
require a new SAT origin label or loosen the R1 SAT validator. Four real-owner
PG tests cover uploaded origin and missing/malformed/contradictory evidence; the
orchestrator runs them in the final whole-suite measured run. The source fixture
accepts `origin="user_upload"` at ingestion, before original posting.
A nonposted legacy report_bridge without an explicit DIRECT selection now
returns a clear pending reason, rather than previewing coverage rejected by SQL;
its focused unit regression passed. No SQL authority was relaxed.

Focal mypy, flake8/88, Black and isort pass after the extension. Final source
hashes are in `/tmp/amex-settlement-backend-source-freeze-20261020.json`.
Forward migration SHA256:
`6cce7e4f62336837e78b2fa8a75b0ba716a114c6c9ebc43c8237b4cdad12816f`.
The final full PostgreSQL measurement and authenticated browser acceptance are
owned by the orchestrator; do not substitute earlier receipts for those results.

The same original-evidence rule also applies to R2: an already posted canonical
report receipt can carry uploaded XML. Both historical views now call the
historical identity helper; R1 still calls its SAT-specific owner. The fifth
real-owner origin test covers uploaded report -> prior-only completion with no
new policy. Eight focused historical/bridge unit cases passed, including the
negative assertion that the unchanged R1 validator still rejects `user_upload`.

## Final integrated verification receipt

The orchestrator's single measured run against the frozen final source passed
**99 PostgreSQL cases**, with **one intentional skip** for the explicitly invoked
metadata receipt regenerator (238.85 seconds). It includes all 27 settlement
cases, 40 schema cases, 27 backend cases and five suggestion SQL cases. Final
log: `/tmp/amex-suggestions-pg-final-20261020.log`; measured data:
`/tmp/amex-suggestions-pg-final-20261020.coverage`. The final unit suite passed
909 cases. Earlier failures described above have been corrected and superseded;
no known backend test failure remains. Independent final browser acceptance and
combined changed-line coverage remain separate orchestrator receipts.
