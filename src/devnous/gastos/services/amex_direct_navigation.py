"""Bound opaque AMEX cursors inside the existing signed browser session.

Only navigation namespaces are pruned. Identity, authorization and CSRF fields
are untouched; an evicted cursor fails its owner's ordinary stale-cursor check.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

CURSOR_NAMESPACES = (
    "amex_direct_suggestion_cursors",
    "amex_direct_search_cursors",
    "amex_settlement_source_cursors",
)
MAX_CURSOR_BYTES = 1600
MAX_ENTRIES_PER_NAMESPACE = 4
_ORDER = "_navigation_order"


def navigation_cursor_bytes(navigation: dict) -> int:
    """Use default JSON spacing/escaping, matching SessionMiddleware encoding."""
    return len(
        json.dumps(
            {key: navigation[key] for key in CURSOR_NAMESPACES if key in navigation}
        ).encode("utf-8")
    )


def save_navigation_cursor(
    navigation: dict, namespace: str, payload: dict[str, Any]
) -> str:
    """Store a token, then evict oldest AMEX entries within a shared byte budget."""
    if namespace not in CURSOR_NAMESPACES:
        raise ValueError("unsupported_amex_cursor_namespace")
    groups = {key: dict(navigation.get(key) or {}) for key in CURSOR_NAMESPACES}
    sequence = 1 + max(
        (
            value.get(_ORDER, 0)
            for positions in groups.values()
            for value in positions.values()
            if isinstance(value, dict)
        ),
        default=0,
    )
    token = secrets.token_urlsafe(24)
    groups[namespace][token] = {**payload, _ORDER: sequence}
    for key, positions in groups.items():
        ordered = sorted(
            positions, key=lambda identifier: positions[identifier].get(_ORDER, 0)
        )
        for stale in ordered[:-MAX_ENTRIES_PER_NAMESPACE]:
            del positions[stale]
        if positions:
            navigation[key] = positions
        else:
            navigation.pop(key, None)
    while navigation_cursor_bytes(navigation) > MAX_CURSOR_BYTES:
        old = [
            (value.get(_ORDER, 0), key, identifier)
            for key in CURSOR_NAMESPACES
            for identifier, value in (navigation.get(key) or {}).items()
            if identifier != token
        ]
        if not old:
            navigation[namespace].pop(token)
            if not navigation[namespace]:
                navigation.pop(namespace)
            raise ValueError("amex_cursor_payload_too_large")
        _, key, stale = min(old)
        del navigation[key][stale]
        if not navigation[key]:
            navigation.pop(key)
    return token
