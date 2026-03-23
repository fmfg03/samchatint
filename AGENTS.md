# Repository Guidelines

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
