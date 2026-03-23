# Runtime Map

This repository contains several runnable surfaces. Treat them as separate entrypoints.

## Current Production

- Service: `samchat-gastos.service`
- ExecStart: `uvicorn copa_telmex_dashboard:app --host 127.0.0.1 --port 8000`
- Purpose:
  - `sam.chat` web app
  - gastos/contabilidad/admin
  - assistant UI and assistant backend
  - tournament admin integration

Primary code paths:

- `copa_telmex_dashboard.py`
- `src/devnous/gastos/`
- `src/samchat/assistant/`
- `src/devnous/tournaments/`

## Secondary API Surface

- Command: `uvicorn devnous.api:app`
- Purpose:
  - generic DevNous API
  - standalone tool API
- Status:
  - valid FastAPI app
  - not the main production `sam.chat` web runtime

Primary code paths:

- `src/devnous/api.py`
- `src/devnous/devnous_agent.py`

## CLI Utility

- Command: `python -m samchat.main`
- Purpose:
  - `health`
  - `info`
- Status:
  - package utility only
  - not a web server

Primary code path:

- `src/samchat/main.py`

## MCP Launcher

- Command: `python3 mcp_platform_launcher.py`
- Purpose:
  - MCP/demo/orchestrator surface
- Status:
  - separate runtime
  - not the current production `sam.chat` web entrypoint

## Practical Rule

Before changing runtime behavior, confirm which surface you are editing:

1. `copa_telmex_dashboard.py` for live `sam.chat`
2. `src/devnous/api.py` for the standalone DevNous API
3. `src/samchat/main.py` for CLI-only work
4. `mcp_platform_launcher.py` for MCP/demo flows

See also:

- `docs/install_matrix.md`
