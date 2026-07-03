# RQF File Intelligence Pre-existing Status

Recorded before document-intelligence edits.

- Starting branch: `main`
- Starting HEAD: `fe405520eb49ea193457a2554c77884b7aae2763`
- Worktree state: already dirty with a large mix of modified, added, staged, and untracked files.

Representative pre-existing dirty areas observed:

- Root/runtime/config files, including `copa_telmex_dashboard.py`, `.gitignore`, requirements files, pytest config, and documentation.
- Forbidden or sensitive backend areas already dirty before this task, including `src/devnous/gastos/models.py`, `src/devnous/gastos/routes/auth_routes.py`, and `src/devnous/gastos/routes/webhook_handler.py`.
- Assistant runtime files already dirty before this task, including `src/samchat/assistant/agent_runtime.py`, `src/samchat/assistant/provider_execution.py`, and `src/samchat/assistant/router.py`.
- Frozen assistant UI redesign artifacts under `artifacts/ui/`.
- Untracked `goal-fest-page/` assistant UI source/dist tree. This task must not overwrite, delete, reformat, or regenerate the frozen assistant UI source or dist.

No product code had been edited by this document-intelligence task when this artifact was created.
