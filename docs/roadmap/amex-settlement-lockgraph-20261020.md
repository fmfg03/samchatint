# Settlement lock graph (approved spec §§5–7)

Inspected 2026-10-20 before connecting settlement. Existing DIRECT writes call
load_statement: actor -> import -> direct_state -> global AMEX advisory lock
(amex_recognition_service.lock_recognition) -> direct items -> card/accounts ->
load_expenses (ordered expense rows) -> budget assignment/line/version -> ledger.
Report-bank legacy binding takes global AMEX first, then owning documents,
then ordered expenses/accounts. It rejects DIRECT sources and remains unchanged.

The debtor owner creates comprobación ledger only after an approved document,
and currently does not acquire the global AMEX lock. Settlement never takes a
Documento row lock while holding the global AMEX lock. Instead it reads immutable
approval/credit identity and takes historical ledger line locks in UUID order,
then an advisory transaction lock per cuenta/account before residual verification.
Database provenance guards serialize competing debtor-ledger writes on this same
cuenta/account lock; no path from that lock back to import/global AMEX is added.

Selection acquires import/global AMEX and item only; no ledger mutation or final
allocation. Preview uses read-only queries, no FOR UPDATE/advisory locks/autoflush.
Confirm locks historical source lines in sorted order before constructing the
journal; append-only allocation guards recheck exact numeric ledger residual under
that lock. Source expense/document/header/line changes after a final settlement
are rejected. Ambiguous account-level liquidations are fail-closed, rather than
inventing attribution to individual expense credits.

The nested UOW forces deferred checks before leaving the savepoint. A late guard
failure rolls back journal, allocations, bank coverage and cut even if an outer
caller catches the exception and commits. No SAT or network call under locks.

## Race closure reviewed with orchestrator

After sorted original line locks, allocation takes transaction advisory
`amex-settlement-source-seal-v1`, then reads identity and residual again. Every
UPDATE/DELETE trigger on original expense, documento, CFDI, cuenta, header and
ledger takes this seal before checking final allocations. A source writer can
already hold its own row; it never requests global AMEX or another original
line after the seal. Ledger INSERT takes the seal but writes only its new rows,
without original-line locks. No seal -> original-row edge is introduced.
Source mutation first changes the re-read proof; allocation first makes the
waiting mutation see final append-only evidence and reject its change. No new
Documento/expense/CFDI FOR UPDATE is acquired by settlement.

### Narrow scope of the seal

The final SQL takes the seal only for source mutations whose stable OLD IDs are
referenced by a real `deudores_comprobacion` credit ledger: original expense,
CFDI through its expense, owning documento/cuenta, original poliza and lines.
This predicate is based on original ledger provenance, not on final allocations,
so it also covers a source writer racing the very first allocation. Unrelated
employees, bank/expense journal lines and DIRECT projection updates do not take
this seal. A new ledger line takes it only on the settlement path or when its
account/cuenta matches an existing original debtor credit. Ordinary advances
created before any original credit remain unattributed and later block matching
rather than being silently deducted. The PostgreSQL tests exercise both source
mutation winner orders and prove unrelated ledger insertion succeeds while the
seal is held by another transaction.
