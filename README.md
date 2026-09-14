# SamChat

SamChat is Plataforma Sports' governed operational system for tournament, gastos, finance, evidence, and assistant workflows. Its primary interface is a controlled business assistant and operational web application; it is not a general MCP platform or a promise of autonomous financial execution.

## Runtime Status

The primary live-web runtime is `samchat-gastos.service`, launching `copa_telmex_dashboard:app`. Repository code, deployed runtime behavior, and customer acceptance are distinct evidence levels. A feature in this repository is not automatically deployed or accepted.

Connected repository domains include gastos and approvals, Payment Run, budgets, accounts receivable, cashflow, a read-mostly governed assistant, tournament/OCR operations, Sam Inbox, runtime artifacts, and executive views. Read the protected [product canon](SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md), [engineering canon](SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md), and [codebase sweep](SAMCHAT_CODEBASE_SWEEP_REPORT_2026-09-10.md).

## Repository map

- `copa_telmex_dashboard.py`: composite FastAPI live-web entrypoint.
- `src/devnous/gastos/`: gastos, finance, operational, and web surfaces.
- `src/samchat/`: assistant, finance, budgets, AR, cashflow, inbox, and related services.
- `tests/`: automated checks.
- `docs/`: curated documentation map and historical evidence.

## Local development

```bash
pip install -r requirements.txt
pip install -r requirements-test.txt
pytest -q
```

See [the installation matrix](docs/install_matrix.md). Do not treat secondary APIs, CLIs, demos, or nested applications as the production `sam.chat` runtime without verified release evidence.

## Release, authority, and documentation

Production verification requires the exact active release, its systemd drop-in, health/readiness checks, and a focused workflow smoke. Financial and authorization writes require the canonical owner, explicit authority, confirmation, idempotency, and audit evidence. See [the runtime map](docs/runtime_map.md), [release guidance](docs/release/current-release-dropin.md), and [the documentation map](docs/README.md). Keep changes scoped and follow [AGENTS.md](AGENTS.md).
