# RQF Accounting Laminar — AMEX event hooks

The AMEX posting service is deliberately transaction-neutral: it never commits
and every entry is keyed by a stable event identity. The workflow caller owns
the state transition and journal entry in the same database transaction.

## Rule 9 — approved AMEX expense report

Call `ensure_amex_report_approval_posting()` from the `INFORME` approval path,
before the approved state is persisted. A `pending` result blocks approval. The
existing employee-debtor posting must not also process company-AMEX expenses.

## Rule 10 — validated AMEX reconciliation

Call `ensure_amex_reconciliation_posting()` after loading the selected active
`AmexCardAccount`, and before publishing the validation notification. A
`pending` result blocks validation. The selected card is the only trusted source
for the liability account; labels and last-four digits are not account bindings.

## Rule 11 — confirmed AMEX payment

AMEX payment requests embed `SAMCHAT_AMEX_CARD_ACCOUNT_ID=<uuid>` when created.
Call `ensure_amex_payment_posting()` before the generic provider/beneficiary
payment branch and before setting `pagado_en`. A `pending` result blocks payment.
The card UUID, not free text, resolves the liability account.

No historical rows are backfilled by these hooks. Existing records without the
structured card marker remain pending for explicit review.
