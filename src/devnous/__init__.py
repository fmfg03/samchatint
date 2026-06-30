"""DevNous package exports.

Keep heavyweight modules lazy so utility scripts can import subpackages without
pulling the full agent/runtime stack into memory at import time.
"""

from .models import *  # noqa: F401,F403

__version__ = "1.0.0"
__author__ = "DevNous Team"

__all__ = [
    "config",
    "DevNousConfig",
    "DevNousAgent",
]


def __getattr__(name: str):
    if name in {"config", "DevNousConfig"}:
        from .config import DevNousConfig, config

        exports = {"config": config, "DevNousConfig": DevNousConfig}
        return exports[name]
    if name == "DevNousAgent":
        from .devnous_agent import DevNousAgent

        return DevNousAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")