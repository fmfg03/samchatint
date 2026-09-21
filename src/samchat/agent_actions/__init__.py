"""Fail-closed internal contracts for the SamChat Agent Action API.

This package intentionally contains no HTTP route, natural-language router, or
business handler.  Domain actions remain disabled until their owners provide a
verified principal-and-scope binding.
"""

from .registry import (
    ACTION_NOT_REGISTERED,
    CANONICAL_SCOPE_UNPROVEN,
    get_action,
)
from .service import AgentActionService

__all__ = [
    "ACTION_NOT_REGISTERED",
    "CANONICAL_SCOPE_UNPROVEN",
    "AgentActionService",
    "get_action",
]
