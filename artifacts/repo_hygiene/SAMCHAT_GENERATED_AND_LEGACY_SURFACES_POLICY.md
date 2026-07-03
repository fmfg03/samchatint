# SamChat Generated And Legacy Surfaces Policy

Created for `RQF_HARDENING_008_REAPPLY_ON_RUNTIME_CLEAN` on 2026-07-03.

## Normal Scan Exclusions

Exclude generated or environment-owned directories from routine code-quality scans unless the scan specifically targets runtime packaging:

- `node_modules`
- `dist`, unless the directory is a proven runtime artifact
- `__pycache__`
- Python virtualenv directories such as `.venv`, `venv`, `.venv-*`, and `env`
- Graphify outputs and cache directories

## Legacy Surfaces Requiring Quarantine Or Runbook

Do not delete or execute these categories casually. First map process, cron, systemd, deployment, and DB dependencies:

- Root Telegram/OCR bot scripts
- Migration, seed, purge, and backfill scripts
- Old deployment trees and deployment helper copies

## Must Not Delete Without Further Proof

- `goal-fest-page/dist` until live frontend mapping is proven
- `copatelmex/dist` until live frontend mapping is proven
- OCR scripts until cron, systemd, and process references are checked
- Migration and backfill scripts until the DB runbook is mapped

Generated-artifact cleanup should be handled as an explicit repository hygiene task with its own evidence log.
