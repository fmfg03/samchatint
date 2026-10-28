# Explicit historical accounting source admission

The historical CSV importer previously selected fixed year manifests, including the original2026Q1balance-only source. It could return an already-persisted snapshot during preview and initialized schema. This change adds an explicit, hash-pinned normalized-manifest path with fresh read-only previews, existing-schema preflight, scoped company/year locks and versioned append-only imports. Legacy source selection retains its default behavior. No new dependency or migration is introduced.

The approved operation uses the new files as the current2026source for each evidenced company while preserving all prior imports. Sources contain policy movements through September and trial balances through August. September is not represented as an official trial balance. Source workbooks, original row numbers and subcent precision remain traceable. Material source discrepancies block application; missing project/cost-center data remain missing.

The user subsequently authorized loading the undisputed remainder. Complete policy periods are withheld together across all three normalized families: company01 March,April,June,July; GOAT August; company02 none. Every withheld period requires a reason and hash-pinned evidence. The operator enforces this exact approved admission profile and expected-preview/source-commit hashes. Unexplained missing periods, excluded rows still present, mixed identity/year, empty packages, changed evidence, duplicate keys and material reconciliation differences fail closed. These are partial current sources; readers never fill omitted periods using superseded runs or fabricated zeroes.

Current company/coverage inventories now select the same latest completed apply per company/year as the canonical snapshot. Their counts and period sets exclude superseded records; historical records remain addressable by import-run ID. This prevents the partial replacement from displaying old March as current or counting multiple versions as duplicate facts.

## Operator sequence

1. Normalize the pinned source configuration with scripts/normalize_historical_xlsx.py into a private output directory. Review its independent validation report and full-source discrepancy evidence. Keep complete original reports distinct from admitted manifests.
2. Run scripts/import_historical_explicit.py with explicit --manifest arguments and --output-dir. Preview is a new READ ONLY transaction, with active authorized actor and fresh source validation. It does not perform DDL or reuse the old snapshot as candidate evidence.
3. Rehearse on a uniquely named restored database using the pinned --restore target. Prove drift rejection, material blocking, transaction rollback across internal commits, old-data preservation, concurrency and repeat-run idempotence. Retain private before/after evidence keyed by reviewed plan hash.
4. Deploy only the reviewed change on the exact serving base after required checks. Apply with --apply --expected-hash and --expected-commit from a new preview of that serving release. The outer transaction preserves atomicity across companies and independent postcommit reads verify source selection, every period/count and monetary sum. Existing operational/budget/reference records are fingerprinted for preservation.
5. Retain the commit marker and receipt. If confirmation is interrupted, inspect the database and receipt before retrying. Do not restore a stale global backup over later financial activity. Authenticated Finance acceptance remains distinct.

## Validation and boundaries

Unit and real PostgreSQL suites cover owner validation, normalization and operator boundaries, including partial selection over an old full source and read-only inventories. Independent review verifies source/evidence pinning, SQL write allowlists, authority, concurrency and scope. Actual restored-source receipts document exact counts and coverage; production receipts are separate evidence. No code or data acceptance is inferred from a screen or source filename alone.

The operation does not approve proposed tournament-account bindings. Those still need company/account/period/attribution and tax-basis evidence; mixed-tournament transactions cannot be allocated by guesswork. Account codes are never used as cross-company identity.

Canon unchanged: this concretizes existing governed-source, company identity, financial history, audit and no-DDL boundaries. It introduces no new financial state, payment authority, company reassignment or inferred attribution. The admission profile is an operator restriction, not a universal business rule in the historical owner.

Final pre-PR validation:96focused tests passed together, including4real disposable PostgreSQL cases. Independent read-only validation found no remaining critical, important or minor findings. The actual restored-source operator applied all3admitted packages, independently checked counts and all monetary sums/periods/current inventories, rolled back an injected failure identically, rejected stale evidence and repeated without new imports. Admitted movements:01=6953,GOAT=1021,02=893; admitted balance rows2972/2562/6240. Sources and quarantine evidence are rehashed through commit; immutable receipts retain before/after state. The production preview is read-only; application remains a separately guarded operation.
