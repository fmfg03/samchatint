"""
Supabase sync helpers for tournament bots.

This module provides a minimal async client that uses Supabase REST (PostgREST)
and Auth Admin API via the service role key.

It is intentionally small and dependency-free (aiohttp only).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from devnous.copa_telmex.supabase_authority import (
    SupabaseWritePermit,
    require_supabase_write_permit,
)


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    service_role_key: str
    import_user_email: str = "telegram-import@sam.chat"
    import_user_password: str = "telegram-import-not-a-login"


class SupabaseSyncError(RuntimeError):
    pass


class SupabaseAdminClient:
    def __init__(
        self,
        cfg: SupabaseConfig,
        cache_dir: str = "data",
        *,
        write_permit: Optional[SupabaseWritePermit] = None,
    ):
        self.cfg = cfg
        self._write_permit = write_permit
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._import_user_cache = self._cache_dir / "supabase_import_user.json"

    def _headers(self) -> Dict[str, str]:
        # service_role key works as both "apikey" and bearer for admin endpoints.
        return {
            "Authorization": f"Bearer {self.cfg.service_role_key}",
            "apikey": self.cfg.service_role_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _rest_url(self, table: str) -> str:
        return f"{self.cfg.url.rstrip('/')}/rest/v1/{table}"

    def _auth_admin_url(self, path: str) -> str:
        return f"{self.cfg.url.rstrip('/')}/auth/v1/admin/{path.lstrip('/')}"

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        json_body: Any = None,
    ) -> Any:
        if method.upper() not in {"GET", "HEAD"}:
            require_supabase_write_permit(self._write_permit)
        hdrs = dict(self._headers())
        if headers:
            hdrs.update(headers)
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method,
                url,
                params=params,
                headers=hdrs,
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise SupabaseSyncError(
                        f"{method} {url} -> {resp.status}: {text[:500]}"
                    )
                if not text:
                    return None
                try:
                    return json.loads(text)
                except Exception:
                    return text

    async def ensure_import_user(self) -> str:
        """
        Ensure the special "telegram import" user exists and return its UUID.

        We cache the UUID locally to avoid scanning the user list repeatedly.
        """
        if self._import_user_cache.exists():
            try:
                data = json.loads(self._import_user_cache.read_text(encoding="utf-8"))
                uid = str(data.get("id") or "").strip()
                if uid:
                    return uid
            except Exception:
                pass

        # Preferred path: reuse an existing admin user from user_roles.
        # This avoids hard dependency on Auth Admin create/list semantics.
        try:
            admins = await self._request(
                "GET",
                self._rest_url("user_roles"),
                params={"select": "user_id", "role": "eq.admin", "limit": "1"},
            )
            if admins:
                uid = str(admins[0].get("user_id") or "").strip()
                if uid:
                    self._import_user_cache.write_text(
                        json.dumps({"id": uid}, indent=2), encoding="utf-8"
                    )
                    return uid
        except Exception:
            pass

        # Fallback: try create a dedicated import user in auth.
        body = {
            "email": self.cfg.import_user_email,
            "password": self.cfg.import_user_password,
            "email_confirm": True,
            "user_metadata": {"full_name": "Telegram Import"},
        }
        try:
            created = await self._request(
                "POST", self._auth_admin_url("users"), json_body=body
            )
            uid = str((created or {}).get("user", {}).get("id") or "")
            if uid:
                self._import_user_cache.write_text(
                    json.dumps({"id": uid}, indent=2), encoding="utf-8"
                )
                return uid
        except SupabaseSyncError:
            # Likely already exists. Fall back to list users and match by email.
            pass

        page = 1
        while page <= 50:
            users = await self._request(
                "GET",
                self._auth_admin_url("users"),
                params={"page": str(page), "per_page": "200"},
            )
            for u in (users or {}).get("users", []) or []:
                if (u.get("email") or "").lower() == self.cfg.import_user_email.lower():
                    uid = str(u.get("id") or "")
                    if uid:
                        self._import_user_cache.write_text(
                            json.dumps({"id": uid}, indent=2), encoding="utf-8"
                        )
                        return uid
            if not (users or {}).get("users"):
                break
            page += 1

        raise SupabaseSyncError(
            "Could not ensure telegram import user in Supabase auth"
        )

    async def get_tournament_id_by_slug(self, slug: str) -> str:
        rows = await self._request(
            "GET",
            self._rest_url("tournaments"),
            params={"select": "id", "slug": f"eq.{slug}", "limit": "1"},
        )
        if not rows:
            raise SupabaseSyncError(f"Supabase tournament not found for slug={slug}")
        return str(rows[0]["id"])

    async def get_category_id(self, tournament_id: str, category_name: str) -> str:
        rows = await self._request(
            "GET",
            self._rest_url("categories"),
            params={
                "select": "id",
                "tournament_id": f"eq.{tournament_id}",
                "name": f"eq.{category_name}",
                "limit": "1",
            },
        )
        if rows:
            return str(rows[0]["id"])

        # Fallback: auto-create category when missing to avoid OCR import failures.
        normalized = (category_name or "").strip()
        if not normalized:
            raise SupabaseSyncError(
                f"Supabase category not found and empty name provided: tournament_id={tournament_id}"
            )

        year_born = None
        if normalized.isdigit() and len(normalized) == 4:
            year_born = normalized

        created = await self._request(
            "POST",
            self._rest_url("categories"),
            headers={"Prefer": "return=representation"},
            json_body={
                "tournament_id": tournament_id,
                "name": normalized,
                "year_born": year_born,
                "description": f"Categoria auto-creada desde OCR Telegram: {normalized}",
                "max_players_per_team": 18,
                "registration_closed": False,
            },
        )
        if not created:
            raise SupabaseSyncError(
                f"Supabase category auto-create failed: tournament_id={tournament_id} name={normalized}"
            )
        return str(created[0]["id"])

    async def find_team(
        self,
        *,
        tournament_id: str,
        user_id: str,
        team_name: str,
    ) -> Optional[Dict[str, Any]]:
        rows = await self._request(
            "GET",
            self._rest_url("teams"),
            params={
                "select": "id,team_name",
                "tournament_id": f"eq.{tournament_id}",
                "user_id": f"eq.{user_id}",
                "team_name": f"eq.{team_name}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    async def create_team(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        rows = await self._request(
            "POST",
            self._rest_url("teams"),
            headers={"Prefer": "return=representation"},
            json_body=payload,
        )
        if not rows:
            raise SupabaseSyncError("Supabase teams insert returned empty response")
        return rows[0]

    async def upsert_registration(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # registrations has UNIQUE(team_id, category_id)
        rows = await self._request(
            "POST",
            self._rest_url("registrations"),
            params={"on_conflict": "team_id,category_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json_body=payload,
        )
        if not rows:
            raise SupabaseSyncError(
                "Supabase registrations upsert returned empty response"
            )
        return rows[0]

    async def get_player_by_curp(self, curp: str) -> Optional[Dict[str, Any]]:
        rows = await self._request(
            "GET",
            self._rest_url("players"),
            params={"select": "id,curp", "curp": f"eq.{curp}", "limit": "1"},
        )
        return rows[0] if rows else None

    async def insert_players(self, payloads: List[Dict[str, Any]]) -> int:
        if not payloads:
            return 0
        rows = await self._request(
            "POST",
            self._rest_url("players"),
            headers={"Prefer": "return=representation"},
            json_body=payloads,
        )
        return len(rows or [])


def load_supabase_config_from_env() -> Optional[SupabaseConfig]:
    url = (os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        return None
    return SupabaseConfig(url=url, service_role_key=key)
