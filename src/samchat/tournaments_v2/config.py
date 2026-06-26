from __future__ import annotations

import os
from dataclasses import dataclass


def _env_truthy(value: str | None, *, default: bool) -> bool:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class TournamentsV2Config:
    supabase_url: str
    service_role_key: str
    anon_key: str
    reads_enabled: bool
    writes_enabled: bool
    fallback_to_legacy: bool
    request_timeout_sec: int
    page_size: int
    max_rows: int


def load_tournaments_v2_config() -> TournamentsV2Config:
    return TournamentsV2Config(
        supabase_url=(os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL") or "").rstrip("/"),
        service_role_key=(os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip(),
        anon_key=(
            os.getenv("SUPABASE_ANON_KEY")
            or os.getenv("VITE_SUPABASE_ANON_KEY")
            or ""
        ).strip(),
        reads_enabled=_env_truthy(os.getenv("TOURNAMENTS_V2_READS_ENABLED"), default=True),
        writes_enabled=_env_truthy(os.getenv("TOURNAMENTS_V2_WRITES_ENABLED"), default=False),
        fallback_to_legacy=_env_truthy(
            os.getenv("TOURNAMENTS_V2_FALLBACK_TO_LEGACY"),
            default=True,
        ),
        request_timeout_sec=max(5, int(os.getenv("TOURNAMENTS_V2_TIMEOUT_SEC", "18"))),
        page_size=max(100, min(int(os.getenv("TOURNAMENTS_V2_PAGE_SIZE", "1000")), 5000)),
        max_rows=max(1000, min(int(os.getenv("TOURNAMENTS_V2_MAX_ROWS", "20000")), 100000)),
    )
