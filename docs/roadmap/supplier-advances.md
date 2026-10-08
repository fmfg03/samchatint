# Supplier advances in third-party requests

Date: 2026-10-08. Evidence level: isolated repository candidate; no deployment,
production migration, data repair or Finance acceptance is claimed.

Runtime: `copa_telmex_dashboard.py`; domain: gastos third-party documents,
authorization, Payment Run, accounting and budget projections; data owner:
existing SQLAlchemy/PostgreSQL document services. No parallel database or
assistant write path is introduced.

Francisco authorized the supplier-advance implementation after reviewing the
workflow corrections. The initial payment and later invoice use separate
linked documents. The paid original request is retained with its operational
reference, support, payment evidence and responsible requester.

## Behavior

1. The requester selects **Anticipo a proveedor** on the existing third-party
   form and enters the expected invoice/comprobación date. Initial PDF is
   supporting evidence rather than fiscal intake. Initial XML/shared-CFDI
   capture is disallowed; fiscal invoices enter through the later controlled
   comprobación action.
2. The advance goes directly to the existing authorization route, then Payment
   Run. Approval and operational cutoff do not create a bank journal. Accounting
   payment confirmation posts 1215-001-001 against configured Santander
   1120-001-001, without creating a non-deductible expense.
3. The original remains `pagado`; its operational label and detail show
   **Pendiente de comprobar**, available/reserved/applied balances and history.
   Support uploads remain enabled. The owner uses **Comprobar anticipo** to
   attach a full stamped invoice XML and optional PDF.
4. Each invoice becomes one linked SOLICITUD in `control_presupuestal`. Its
   invoice total, advance allocation and remaining bank payable are separate
   persisted amounts. The issuer must match the active provider RFC; currency
   must match; the recipient must be an active configured RFC. A full invoice
   cannot be shared across requests. This checks uploaded evidence and existing
   duplicate reservations; it does not claim live SAT cancellation verification.
5. Budget assignment uses the full fiscal base before taxes, not the cash
   remainder. Final approval uses existing authorization checks and the existing
   canonical tax/account preview. The full invoice accrual and advance
   application are atomic, with a single full-invoice expense. Liability is
   2120-002-099. Missing accounts or inconsistent fiscal amounts block approval.
   **Recognition is at final approval**, preserving the existing third-party
   accounting hook, rather than at budget assignment. Rejected invoices therefore
   cannot leave an unapproved expense accrual.
6. A zero remainder closes the child as `cerrado`, without a bank transfer or
   payment timestamp. A positive remainder remains approved for Payment Run;
   confirmation posts only that remainder against the liability. The original
   advance is never paid again.
7. Partial/multiple invoices reserve the remaining advance under a parent row
   lock. Rejected/cancelled unapproved invoice children release reservations.
   A smaller invoice applies only its total, leaving the excess open. The system
   does not invent a supplier refund or silently move surplus to another request.

## Invariants

- Initial advance, invoice recognition, advance application and remainder payment
  have separate deterministic event identities and balanced journals.
- Duplicate browser submission keys return the same invoice child. Existing CFDI
  reservations are reused. PostgreSQL enforces unique active invoice/event keys,
  party identity, amount equality, a paid-parent prerequisite, allocation caps
  and immutable paid/approved evidence.
- Paid originals and approved invoice children cannot be reset, edited or
  deleted to undo accounting. Substitution/cancellation after approval requires
  a separately authorized accounting reversal; no reversal bypass is added.
- No generated expense is treated as payment evidence. A positive-remainder
  expense exists from invoice approval, while `gasto_generado_id` is populated
  only at payment confirmation; zero-remainder children close without payment.
- Same-currency allocation only. Conversion and cross-entity allocation are not
  added. This is not the planned three-company installation split.

## Fiscal scope and Finance validation

This change implements the operational advance and full-invoice settlement.
It reuses configured invoice tax mappings and does not automate the SAT fiscal
advance procedure, related advance/egreso CFDIs, or payment-complement issuance.
Contabilidad must verify those documents and the IVA/retention account treatment
for its actual transaction before go-live. Configured RFC acceptance does not
claim multi-company accounting segregation. A supplier refund, transfer of
surplus to another advance, and post-approval CFDI substitution retain explicit
manual/accounting ownership.

## Migration and rollout

Owner-run migration: `database/migrations/20261008_supplier_advances.sql`.
Apply after a backup and before deploying application code; the ORM expects the
new columns. No startup/runtime DDL is introduced. The migration is idempotent.

For rollback, restore the previous application release while retaining additive
columns, constraints and financial records. Do not drop this schema or delete
advance/invoice/journal rows once business records exist. No historical records
are reclassified or backfilled.

Before promotion verify the exact commit, release guards, health/readiness and
the authenticated UAT cases below. Migration authority, deployment and UAT are
separate from this implementation request.

## Validation

Focused Python tests cover initial web capture/submission, authorization/payment
ownership, equal/higher/lower invoices, multiple invoices and reservation release,
duplicate submissions/UUIDs, altered fiscal totals, missing configuration,
retention journals, zero-transfer closure, full-invoice expense creation and
budget SQL/Python parity. SQLite is used for isolated persistence/journals and
its advisory-lock functions are explicit no-op test stubs; these tests do not
claim real PostgreSQL concurrency verification.

`tests/integration/supplier_advance_migration.mjs` executes the owner migration
on isolated PostgreSQL/WASM, including rerun, payment evidence, identity,
overallocation, protected amounts/CFDI links, rejection release, prohibited
reopening/deletion, unique posting and ordinary-request compatibility. It does
not connect to production.

Local checks: runtime packaging, registration operational surface, accepted
regressions, PR policy contract, compilation and diff hygiene. Repository-wide
CI remains separate from the focused local suite.

## Finance UAT

| Case | Expected evidence |
| --- | --- |
| Initial advance | Direct approval, bank proof, 1215 debit/1120 credit, no expense |
| Equal invoice | Full budget classification and accrual; advance cleared; no bank payment |
| Higher invoice | Full fiscal expense/taxes; original advance applied; only difference scheduled/paid |
| Smaller invoice | Original excess stays visible and open; no automatic refund |
| Multiple invoices | Each linked invoice applied once; no allocation above original paid amount |
| Retentions/IVA | Existing configured tax preview validated against actual CFDI and payment evidence |
| Retry/rejection | No duplicate document/poliza; unapproved rejected reservation released |
| Evidence edits | Paid original and approved invoice reject unauthorized mutation |
| Reports/COI | Source/reference/approver/payment proof/journals and relevant exports agree |

Canon unchanged: the existing protected canons remain the integrity baseline;
this candidate preserves canonical ownership, human authority, idempotency,
source traceability and the operational-cutoff/payment-evidence distinction.
This dated feature specification records the additional behavior without
rewriting production/deployment/acceptance claims. No canon amendment is authored
or approved silently.


PR review corrections (2026-10-08): supplier advances are MXN only; creation, edits, invoice approval and bank postings fail closed without verified FX conversion. Supplier flags select supplier-transfer authorization thresholds independently of optional invoice number, urgency or free-form text. Invoice expenses are explicitly excluded from generic transfer COI exports because that path cannot represent the separate accrual/application/payment journals; those accounting events remain persisted, and a dedicated combined export is not included. One PDF upload creates one supporting attachment. New invoice submissions schedule the canonical Budget Control notification only after commit; browser replays do not schedule another notification.
