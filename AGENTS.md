# Repository Guidelines

## Mandatory SamChat Canon Bootstrap

Before investigating, planning, editing, testing, making a production claim, or
proposing a release, read these versioned canon files in this exact order:

1. `SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md`
2. `SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md`
3. `SAMCHAT_CODEBASE_SWEEP_REPORT_2026-09-10.md`

Verify their SHA-256 values against
`docs/roadmap/samchat-convergence-register.md`. If any canon is missing, its
hash differs, or integrity cannot be checked, stop and report the discrepancy.
Do not substitute memory, summaries, or assumptions for the canon.

For every relevant product, architecture, data, security, release, or backlog
change, evaluate whether the canon must change. The same PR must either update
the affected canon with evidence, date, and reason and refresh its register
hash, or state `Canon unchanged` with a concrete reason. Canon edits require
explicit human review; automation must never author or approve them silently.

## Project Structure & Module Organization
- Source: `src/samchat/`, `src/devnous/`, `src/ccpm/`
- Tests: `tests/` (unit, integration, e2e, performance)
- Docs: `docs/`
- Ops & Env: `deployment/`, `infrastructure/`, `terraform/`, `config/`, `database/`
- Utilities: `scripts/`, `tools/`
- Examples and API references: `examples/`, `api-documentation/`, `architecture/`

## Build, Test, and Development Commands
- Environment: `python -m venv venv && source venv/bin/activate`
- Install: `pip install -r requirements.txt`
- Test environment: `pip install -r requirements-test.txt`
- Docs environment: `pip install -r requirements-docs.txt`
- Development environment: `pip install -r requirements-dev.txt`
- Editable package install: `pip install -e .` (reads runtime dependencies from `requirements-runtime.txt`)
- Production web app: `systemctl restart samchat-gastos.service`
- Direct production-equivalent web app: `uvicorn copa_telmex_dashboard:app --host 127.0.0.1 --port 8000`
- Secondary DevNous API: `uvicorn devnous.api:app --host 0.0.0.0 --port 8000`
- CLI utility only: `python -m samchat.main`
- Format: `black src/ tests/ && isort src/ tests/`
- Lint: `flake8 src/ tests/`
- Types: `mypy src/`
- Tests: `pytest` or `pytest --cov=samchat --cov=devnous`
- Docker (optional): `docker-compose up -d` from `deployment/compose` configs

## Coding Style & Naming Conventions
- Python 3.8+ with type hints.
- Formatting via Black; import order via isort; keep lint clean (flake8).
- Indentation: 4 spaces; line length: 88.
- Naming: modules `snake_case.py`, classes `CamelCase`, functions/vars `snake_case`, constants `UPPER_SNAKE`.
- Keep public APIs documented with docstrings; prefer small, focused modules under `src/<package>/`.

## Testing Guidelines
- Framework: pytest; place tests mirroring package paths, e.g., `tests/unit/samchat/test_core.py`.
- Aim for ≥85% coverage on new/changed code; include negative and boundary cases.
- Use factories/fixtures from `tests/factories.py` and `tests/conftest.py`.
- Mark long-running/benchmarks under `tests/performance/`; don’t gate PRs on these by default.

## Commit & Pull Request Guidelines
- Use Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- Commit messages: imperative mood, concise summary + context in body if needed.
- PRs: clear description, linked issues (`Closes #123`), screenshots/logs for UX/ops changes, and notes on testing & rollout.
- Feature branches are temporary: after the PR is merged into `main`, delete the
  branch. The permanent evidence is the commit history and the PR, not the
  branch name.

## Security & Configuration Tips
- Never commit secrets. Use `.env` (copy from `.env.example`).
- Validate configs for DB/Redis before running integration tests.
- For migrations, coordinate changes in `database/` and document in PR.

## Agent-Specific Instructions
- Scope changes narrowly; follow structure above.
- Obey style tools; do not rewrite unrelated files.
- Prefer `rg`, `pytest -k <pattern>`, and path-scoped formatting/linting for speed.

## Session Workflow
For session-based work, especially anything under `building_sessions/*`:
- Read `/root/building_sessions/README.md` first.
- Read `/root/building_sessions/SOP.md` before editing.
- Use the session templates in `/root/building_sessions/templates/` for `plan.md`, `comms.md`, `closeout.md`, and `evidence.md`.
- If a session conflicts with any stronger repo SSOT, the SSOT wins.
