# RQF-057G9 — Authorization enforcement readiness

Status: CLOSED_COMMITTED_LOCAL
Date: 2026-07-30
Branch: sprint-rqf-056-beneficiary-ops

## Objective

Move the authorization strategy matrix from pure advisory evidence toward enforceable workflow control without changing production behavior by accident.

## Implementation

- Added explicit environment switch: `SAMCHAT_AUTHORIZATION_STRATEGY_ENFORCEMENT`.
- Default remains off/advisory.
- When enabled, document approval evaluates the configured authorization profile matrix before mutating a document to `aprobado`.
- If the document resolves to a matrix rule and active matching profiles exist, the approving actor must match at least one required configured profile.
- Missing/fallback rules remain non-blocking to avoid false positives while the customer completes UAT of the matrix.
- Matching uses both role key and copied profile employee matcher, so the UI profile model remains the source of operational control.

## Closure boundary

This cut does not require two-step approval state transitions yet. It prevents an outside-matrix approver from approving when enforcement is deliberately enabled, but it does not yet model sequential first/second approval gates for rules requiring DG/second approval.

## Verification

- Unit coverage confirms enforcement is off by default.
- Unit coverage confirms explicit env switch enables enforcement.
- Unit coverage confirms copied profile matcher can identify Odilon even when his app role is generic.
- Source coverage confirms workflow checks hard enforcement before setting `documento.estado = "aprobado"`.
