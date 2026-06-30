from __future__ import annotations

from .hermes_client import HermesSamchatAssistantClient, SamchatAssistantAPIError

__all__ = [
    "assistant_router",
    "HermesSamchatAssistantClient",
    "SamchatAssistantAPIError",
]


def __getattr__(name: str):
    if name == "assistant_router":
        from .router import router as assistant_router

        return assistant_router
    raise AttributeError(name)
