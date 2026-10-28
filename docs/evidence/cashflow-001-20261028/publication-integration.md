# Cashflow 001: reconciliation before publication

The approved diff was committed as30f8ce824 and reconciled with main b404e9849. This receipt supersedes the earlier baseline-only validation counts without rewriting that evidence.

- Acceptance:206passed (4 isolated PostgreSQL,2 browser);90.32% coverage of cashflow and executive template modules.
- CxC and executive/Direction regressions:165passed.
- Canonical weekly cashflow PostgreSQL regressions:6passed against disposable55479.
- Black/isort and scoped cashflow mypy pass; packaging and both release guards pass.

Integration preserves current-main AR individually dated accepted receipts, strict recognized accounting amount, conflict/unknown evidence, and calendar year distinct from budget edition. Existing tests calling retired private projections now exercise the shared report/source loader. Weekly budgets use canonical effective plans, week bounds and schedule completeness, with no DDL. Missing/partial/conflicting expense plans stay N/D; independently known income remains available. Account and currency filters invalidate any unaccredited direction.

Current legacy AR lacks accredited DTO currency and complete company-scoped status. This change deliberately does not infer those facts: detail remains available, totals/forecast remain N/D. Opening and closing require accredited bank balances. Finance authenticated UAT is pending. Repository-wide type/lint debt is separately attributed in original receipts.

Source PR contains cashflow only. Deployment must additionally preserve exact serving800848939 historical accounting delta (8files) as release-only cherry-pick over the merged cashflow commit, retain active venv release-598e7a286, static bundles/photos and canonical drop-in archive. No migration, financial import or production dependency change is authorized or needed by cashflow.

Canon unchanged: this implements existing finance authority and actual/forecast separation.
