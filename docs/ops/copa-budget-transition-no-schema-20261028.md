# Copa budget transition without schema initialization

The approved one-time Copa load needs to call canonical status transitions inside an outer transaction while prohibiting DDL. `transition_budget_version(..., ensure_schema=False)` now propagates that explicit setting to version reads as well as its initial schema initializer. Default behavior remains unchanged. Financial history guards, row locks, legal status transitions and audit remain mandatory.

The load itself must first verify the deployed schema and authority, revalidate its exact preview and source hashes, and rehearse using a restored database. Internal commits must remain savepoints inside the operator's transaction. The approved workbook is CTT 2026 (2).xlsx: 43 source rows, MXN 17,471,291.92 including VAT, Copa week 52. Economic load and source provenance use the existing services. No data migration is introduced by this code change. Production data application and authenticated financial acceptance are separate evidence.

Validation: 48 focused unit tests passed, including both schema modes, no-op status requests, legal transitions, audit and historical/invalid-transition rejection. Initial test collection exposed the existing application initialization-order dependency; the new test now initializes the model registry before importing budgets. Existing SQLAlchemy deprecation warning remains. Full required CI and operator PostgreSQL acceptance are required before production application.

Canon unchanged: this is an explicit schema-initialization option, following existing budget-service patterns, to preserve the canon's owner-run DDL boundary. It changes no financial state definition, authority or product contract.
