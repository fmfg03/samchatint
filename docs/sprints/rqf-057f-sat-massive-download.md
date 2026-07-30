# RQF-057F — Descarga masiva SAT y conciliación

Status: CLOSED_LOCAL_PENDING_EXTERNAL_CREDENTIALS
Date: 2026-07-30
Branch: sprint-rqf-056-beneficiary-ops

## Objective

Confirm and operationalize the SAT connector for extracting CFDIs emitidos and recibidos, scheduled at 09:00 and 23:00 America/Mexico_City, while leaving the real SAT witness pending until the customer provides/loads the e.firma/FIEL credentials.

## What is already implemented in SamChat

- Admin console: `/admin/gastos/sat` for e.firma credential status, manual sync/backfill, job history, health by direction, SLA coverage, open SAT requests, and CFDI lookup.
- Protected ingress endpoints:
  - `POST /ingress/sat-cfdi-sync?mode=auto` for the full scheduled sync.
  - `POST /ingress/sat-cfdi-open-jobs` for the lightweight hourly worker.
- Production guardrails: both ingress endpoints require `X-SAT-Sync-Secret`; live SAT calls require `SAT_USE_PRODUCTION=true`.
- Direction separation: `SATSyncService.DIRECTIONS == ("received", "issued")`; state and download requests are persisted per `(rfc, direction)`.
- Package/blob persistence and ingestion into the CFDI report store.
- Existing matching surface: `/admin/gastos/cfdis/matching`, plus automatic linking helpers for SAT CFDIs against expense/report documents when UUIDs are available.

## New in this cut

- Added deployable cron wrappers:
  - `scripts/run_sat_cfdi_sync.sh` for the 09:00 and 23:00 full sync.
  - `scripts/run_sat_open_jobs.sh` for hourly open-job verification/reclaim.
- Updated the SAT ingress docstrings to the customer-requested schedule.
- Updated the SAT admin console copy so Finanzas sees the actual runner scripts and the 09:00/23:00 CDMX cadence.
- Added unit coverage for:
  - received/issued direction contract;
  - full-sync script endpoint/header/schedule;
  - open-jobs script endpoint/header/schedule;
  - admin/docstring schedule visibility;
  - no committed API keys/private keys in the runner scripts.

## Operational boundary

This cut deliberately does not commit or invent SAT credentials. Real production extraction remains pending until the customer loads the e.firma/FIEL certificate, key and password for the active RFC.

Recommended crontab once `SAT_SYNC_SECRET`, `SAT_USE_PRODUCTION=true` and FIEL are configured on the server:

```cron
0 9,23 * * * /root/samchat/scripts/run_sat_cfdi_sync.sh
15 * * * * /root/samchat/scripts/run_sat_open_jobs.sh
```

## Non-claims

- No successful live SAT download is claimed in this cut.
- No FIEL material, passwords, private keys or SAT secrets were committed.
- No automatic deployment/crontab mutation was performed in this commit.
- The existing reconciliation/matching surface is confirmed present, but broader SAT-vs-Solicitudes UAT remains part of sprint QA.
