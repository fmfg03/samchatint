"""Environment-driven SAT HTTP and polling timeout settings."""

from __future__ import annotations

import os


def _read_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def sat_http_timeout_seconds() -> int:
    """Per-request SOAP HTTP timeout (seconds)."""
    return _read_int_env("SAT_HTTP_TIMEOUT_SECONDS", 120, minimum=10, maximum=600)


def sat_poll_max_seconds() -> int:
    """Maximum time to poll a solicitud until a final SAT estado (seconds)."""
    return _read_int_env("SAT_POLL_MAX_SECONDS", 1800, minimum=30, maximum=7200)


def sat_poll_interval_seconds() -> int:
    """Delay between SAT estado polls when poll_until_complete is enabled."""
    return _read_int_env("SAT_POLL_INTERVAL_SECONDS", 5, minimum=1, maximum=120)
