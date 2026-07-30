# RQF-056 Beneficiary/Ops Sprint Ledger

Branch: `sprint-rqf-056-beneficiary-ops`
Base: `18a8e2e617c92056ec3f4f82cc8538cafd8c2a63` (`origin/main`, after RQF-056F)
Workflow: commit per phase; push/PR/merge only at sprint close.

## Sprint guardrails

- Do not merge this branch into `main` until the sprint close gate.
- Preserve existing authorization ownership:
  - Solicitudes and informes belong to the requester/solicitante.
  - Approval routes to the requester?s approver, not the beneficiary?s approver.
  - Bank account selection must belong to the selected beneficiary.
- Third-party employee beneficiary capability is limited to explicit allowlist or explicit profile permission.
- Budget visibility/mutation must remain restricted:
  - mutate: superadmin only.
  - view: superadmin, directors, Alicia.
  - Bibiana, Carlos, Roberto and other operations users do not see budgets unless policy changes explicitly.
- Employee reimbursements must not render the employee as a provider in approval messages.
- Keep generated/customer artifacts untracked unless deliberately promoted.

## Committed phases on sprint branch

| Phase | Commit | Status | Notes |
| --- | --- | --- | --- |
| RQF-056G/H | `4a0a970b5` | done | Hardened third-party beneficiary selectors and name matching. |
| RQF-056I | `34c239a80` | done | Exposed draft solicitud cancellation from the solicitudes list. |
| RQF-056K | `6e4112f48` | done | Preserved authoritative CFDI XML total in quick expense autofill (`128` vs computed `124`). |
| RQF-056L | `a1185e079` | done | Tightened budget visibility and mutation policy. |
| RQF-056O | `6bb02b772` | done | Classified Tocino failures: auth, validation, rate limit, upstream unavailable, bad response. |
| RQF-056P | `38bfdeca6` | done | Added explicit `finance.employee_beneficiary.request` profile capability for employee-beneficiary requests. |
| RQF-056R | `21c1d6d25` | done | Clarified employee beneficiary selection UI with Step 1 employee and Step 2 bank account separation. |

## Verified but not changed in this sprint branch

- RQF-056J: Materialidades preview before submit already exists.
- RQF-056M: Telegram approval messages already include proyecto and etapa/subproyecto.
- RQF-056N: Employee reimbursement semantics already omit provider and separate requester/beneficiary.
- Empty draft informe cancellation already exists in list/detail views and uses soft cancellation + audit.

## Current untracked artifacts intentionally left untracked

- `2.1. Cat?logo Contable.xls`
- `Estrategia de Autorizaci?n.xlsx`
- `branch-prune-manifest-20260730T015022Z.tsv`

## Recent focal validation

- `tests/unit/gastos/test_tocino_client_errors.py`: 6 passed.
- Beneficiary/budget focal set: 35 passed.
- Sprint regression focal set: 53 passed.

## Open sprint backlog / next likely cuts

1. Confirm live profile assignments for Alicia, Bibiana, Carlos, Roberto, Benjam?n and Juan Pablo include either:
   - matching allowlist identity, or
   - `finance.employee_beneficiary.request`.
3. Continue customer Excel plan items without losing QA/release plan context.
4. At sprint close:
   - run broader automated suite;
   - push branch;
   - open PR;
   - merge only after review gate.

## QA/release plan context to preserve

Before labeling SamChat as deliverable, freeze a release candidate and run formal QA over the exact production commit:

- repository sanity;
- reproducible install;
- complete tests/lint/type/security where available;
- migration tests from fresh and existing DB;
- E2E by role for requests, approvals, expenses, CFDI, OCR, operations, reports, assistant;
- failure tests for AI/OCR/external services/retries/restarts/backups;
- Plataforma Sports UAT with PASS / PASS with observation / blocking FAIL / change request / out-of-scope classification;
- soak period before tag/deploy acceptance.
