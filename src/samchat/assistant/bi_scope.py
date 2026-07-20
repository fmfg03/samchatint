from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Literal, Optional, Tuple

AssistantBIScope = Literal[
    "all",
    "beisbol",
    "copa-telmex",
    "copa-america",
]

ASSISTANT_BI_SCOPES: Tuple[AssistantBIScope, ...] = (
    "all",
    "beisbol",
    "copa-telmex",
    "copa-america",
)

_SCOPE_QUERY_TERMS: Dict[str, Tuple[str, ...]] = {
    "beisbol": ("beisbol", "béisbol", "liga telmex"),
    "copa-telmex": (
        "copa telmex",
        "copa-telmex",
        "telmex telcel",
    ),
    "copa-america": (
        "copa america",
        "copa américa",
        "copa-america",
        "club america",
        "club américa",
    ),
}


def normalize_bi_scope_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def bi_scope_terms(scope: Optional[str]) -> List[str]:
    normalized_scope = str(scope or "").strip().lower()
    return list(_SCOPE_QUERY_TERMS.get(normalized_scope, ()))


def text_matches_bi_scope(text: str, scope: Optional[str]) -> bool:
    normalized_scope = str(scope or "").strip().lower()
    if not normalized_scope or normalized_scope == "all":
        return True
    normalized_text = normalize_bi_scope_text(text)
    return any(
        normalize_bi_scope_text(term) in normalized_text
        for term in bi_scope_terms(normalized_scope)
    )
