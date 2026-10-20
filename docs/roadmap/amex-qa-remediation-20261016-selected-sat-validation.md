# Independent selected-SAT correction validation — 2026-10-16

Reviewer: implementation-validator `/root/amex_ci_followup_validation`. Target: three-file correction after `7ea82f2e1` for PR #491.

Critical: None. Important: None. Minor: None. **CLEAN_WITH_NOTES**; no correction cycle or new approval checkpoint required.

The correction implements the already-approved specification's bounded SAT consultation and concurrent-use requirements (source lines97 and128). No schema, dependency, authority or product workflow added. Canon unchanged: canonical authority, external-request limits and evidence boundaries preserved; all three register hashes match.

Reviewed canonical owner locks before committing the tokenized lease; selected/batch mutual exclusion; SOAP outside database transactions with30s timeout; completion authority/source/fiscal-identity/ownership checks; atomic observation insertion; token-only cleanup with import-before-state locking; finally cleanup on rejection, failure and cancellation without masking a propagating original error. Superseded attempts cannot publish receipts or release newer leases.

Independent verification: affected units25PASS,1inheritedwarning,2.19s; integration collection49cases (46bridged+3HTTP), including all seven added parameterizations; focused PostgreSQL/HTTP log inspected15PASS,427warnings,27.85s; git diff --check PASS. No PostgreSQL run repeated. Business diff exactly three assigned files. Historical40-file manifest:37unchanged, three expected differences (status service, authority acceptance and finder unit tests).

Residual risks: fresh aggregate coverage and GitHub checks must pass on the revised head; earlier checks apply only to the previous head. Failed cleanup after process/database interruption leaves a lease that expires after35s. External SAT acceptance remains synthetic; production migration/recovery/deployment and authenticated Finance UAT remain separate.

Reviewer made no edits, live requests, migration or publication actions.
