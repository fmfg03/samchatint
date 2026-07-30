# RQF-056 / RQF-057A Client Excel Sprint Ledger

Branch: `sprint-rqf-056-beneficiary-ops`
Base: `18a8e2e617c92056ec3f4f82cc8538cafd8c2a63` (`origin/main`, after RQF-056F)
Workflow: commit per phase; push/PR/merge only at sprint close.
Plan master: customer Excel + ordered stages G through O, then RQF-057A operations audit.

## Sprint guardrails

- Do not merge this branch into `main` until the sprint close gate.
- Preserve existing authorization ownership:
  - Solicitudes and informes belong to the requester/solicitante.
  - Approval routes to the requester approver, not the beneficiary approver.
  - Bank account selection must belong to the selected beneficiary.
- Third-party employee beneficiary capability is limited to explicit allowlist or explicit profile permission.
- Budget visibility/mutation must remain restricted:
  - mutate: superadmin only.
  - view: superadmin, directors, Alicia.
  - Bibiana, Carlos, Roberto and other operations users do not see budgets unless policy changes explicitly.
- Employee reimbursements must not render the employee as a provider.
- Keep generated/customer artifacts untracked unless deliberately promoted.

## Ordered sprint stages and real status

| Stage | Status | Evidence / commits | Remaining closure gap |
| --- | --- | --- | --- |
| RQF-056G ? Selector de beneficiario en Informes de Gastos | PARTIAL / needs E2E verification | `4a0a970b5`, `38bfdeca6`, `21c1d6d25`, profile visibility commits | Need verify actual form route and live authorized users; confirm close still goes to requester approver with focused route/service test. |
| RQF-056H ? Selector de beneficiario en Anticipos with full authorized list | PARTIAL / likely implemented, needs live/data verification | `4a0a970b5`, `38bfdeca6`, `21c1d6d25`, `b8328dcf5` | Need verify authorized users see all active employees and bank accounts refresh to selected beneficiary in route/E2E tests. |
| RQF-056I ? Cancelar / eliminar borradores incompletos | PARTIAL | `34c239a80`; existing empty informe cancellation verified in code | Need complete policy tests for finanzas/superadmin cleanup and ensure no sent/approved/paid/movement document can be removed. |
| RQF-056J ? Materialidades verificables antes de guardar | NOT CLOSED | Existing code appeared present; no dedicated close commit | Need inspect UI/tests against requirements: preview/thumbnail, filename, remove, multiple files, no CFDI breakage. |
| RQF-056K ? Correcci?n definitiva de totales CFDI XML/PDF | PARTIAL | `6e4112f48` | Need test with real problematic CFDI fixture, robust Total/SubTotal/Descuento/Impuestos/Retenciones validation, and clear inconsistency message. |
| RQF-056L ? Presupuestos visibility/editing | PARTIAL / strong unit coverage | `a1185e079` | Need verify directors definition and UI hiding/POST 403/frozen versions across full routes. |
| RQF-056M ? Telegram proyecto y etapa | NOT CLOSED | Existing implementation observed; no close commit | Need tests proving Telegram includes proyecto/fase and buttons still work. |
| RQF-056N ? Reimbursement semantics employee != provider | NOT CLOSED | Existing implementation/test observed | Need align documents + screens + Telegram, not just one helper/test. |
| RQF-056O ? Tocino/facturacion status/errors | PARTIAL | `6bb02b772` | Need safe retry, status visible, Telegram relevant status notifications, minimal non-secret payload logging. |
| RQF-057A ? Operaciones end-to-end wiring audit | NOT STARTED | none | Need map bot -> teams -> players -> documents -> tournaments -> calendars -> incidents; identify Supabase/local/dead/duplicate/partial; prioritized migration stages. |

## Commits currently on sprint branch

| Commit | Note |
| --- | --- |
| `4a0a970b5` | RQF-056G-H harden third-party beneficiary selectors |
| `34c239a80` | RQF-056I expose draft solicitud cancellation |
| `6e4112f48` | RQF-056K preserve CFDI XML total in quick expense autofill |
| `a1185e079` | RQF-056L tighten budget visibility and mutation policy |
| `6bb02b772` | RQF-056O classify Tocino submission failures |
| `38bfdeca6` | RQF-056P add permissioned employee beneficiary requests |
| `467a5e947` | RQF-056Q preserve sprint ledger |
| `21c1d6d25` | RQF-056R clarify employee beneficiary selection UI |
| `1223cb74f` | RQF-056S update sprint ledger after beneficiary UI cut |
| `afbc9b421` | RQF-056T surface employee beneficiary profile access |
| `1b5e279ba` | RQF-056U update ledger for profile access visibility |
| `b8328dcf5` | RQF-056V expose employee beneficiary access summary |
| `1d32f9206` | RQF-056W update ledger for access summary |

## Current untracked artifacts intentionally left untracked

- `2.1. Cat?logo Contable.xls`
- `Estrategia de Autorizaci?n.xlsx`
- `branch-prune-manifest-20260730T015022Z.tsv`

## Next work order

1. Close RQF-056G with concrete tests for Informe beneficiary selector and requester-approver preservation.
2. Close RQF-056H with concrete tests for Anticipo authorized full employee list and beneficiary bank-account refresh.
3. Continue through I, J, K, L, M, N, O in order.
4. Start RQF-057A only after RQF-056O is actually closed.
5. Sprint close only after broader tests + push + PR + review gate + final merge.

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
