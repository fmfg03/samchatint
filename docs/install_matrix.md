# Install Matrix

Use the smallest dependency profile that matches the task.

## Profiles

### Production / Runtime

```bash
pip install -r requirements.txt
```

Use this for the current operational deployment in this repository.

Notes:

- This is the install path currently used for the live `sam.chat` deployment.
- The main production web runtime is `copa_telmex_dashboard.py` under `samchat-gastos.service`.

### Editable Package Runtime

```bash
pip install -r requirements-runtime.txt
pip install -e .
```

Use this when you need the `samchat` package/entrypoint installed locally without the full development toolchain.

### Test Environment

```bash
pip install -r requirements-test.txt
```

Includes runtime plus pytest-related tooling.

### Docs Environment

```bash
pip install -r requirements-docs.txt
```

Includes runtime plus Sphinx documentation tooling.

### Full Development Environment

```bash
pip install -r requirements-dev.txt
```

Includes:

- runtime
- test
- docs
- lint/type-check
- performance/developer tooling

## Runtime Surfaces

These are different entrypoints. They are not interchangeable.

### Current Production Web App

```bash
systemctl restart samchat-gastos.service
```

Equivalent process:

```bash
uvicorn copa_telmex_dashboard:app --host 127.0.0.1 --port 8000
```

### Secondary DevNous API

```bash
uvicorn devnous.api:app --host 0.0.0.0 --port 8000
```

This is a valid FastAPI surface, but it is not the main production `sam.chat` runtime in this deployment.

### CLI Utility

```bash
samchat info
samchat health
```

This requires the package to be installed, for example via `pip install -e .`.

### MCP Launcher

```bash
python3 mcp_platform_launcher.py
```

Separate MCP/demo/orchestration surface. Not the current production web bootstrap.

## Practical Rule

Before changing runtime behavior, identify which surface you are actually changing:

1. `copa_telmex_dashboard.py` for live `sam.chat`
2. `src/devnous/api.py` for the standalone DevNous API
3. `src/samchat/main.py` for CLI-only work
4. `mcp_platform_launcher.py` for MCP/demo flows
