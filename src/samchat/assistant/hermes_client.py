from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


class SamchatAssistantAPIError(Exception):
    """Raised when the Samchat assistant API returns an error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        response_text: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


@dataclass
class HermesSamchatAssistantClient:
    """Thin client for Hermes -> Samchat assistant integration."""

    base_url: str
    service_token: str
    actor_email: Optional[str] = None
    actor_id: Optional[str] = None
    timeout_seconds: int = 120

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        service_token: Optional[str] = None,
        actor_email: Optional[str] = None,
        actor_id: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("SAMCHAT_ASSISTANT_BASE_URL")
            or "http://127.0.0.1:8000/api/assistant"
        ).rstrip("/")
        self.service_token = (
            service_token or os.getenv("HERMES_SERVICE_TOKEN") or ""
        ).strip()
        self.actor_email = (
            actor_email
            or os.getenv("HERMES_ACTOR_EMAIL")
            or os.getenv("HERMES_SERVICE_DEFAULT_EMAIL")
            or ""
        ).strip() or None
        self.actor_id = (actor_id or os.getenv("HERMES_ACTOR_ID") or "").strip() or None
        self.timeout_seconds = int(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("SAMCHAT_ASSISTANT_TIMEOUT_SECONDS", "120")
        )

        if not self.service_token:
            raise ValueError("HERMES_SERVICE_TOKEN must be configured.")
        if not self.actor_email and not self.actor_id:
            raise ValueError(
                "Set HERMES_ACTOR_EMAIL or HERMES_ACTOR_ID for Hermes assistant calls."
            )

    def _headers(self) -> Dict[str, str]:
        headers = {
            "X-Hermes-Service-Token": self.service_token,
            "Content-Type": "application/json",
        }
        if self.actor_email:
            headers["X-Hermes-Actor-Email"] = self.actor_email
        if self.actor_id:
            headers["X-Hermes-Actor-Id"] = self.actor_id
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Optional[Dict[str, Any]] = None,
        data_payload: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        query_params: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any] | List[Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)
        if files:
            headers.pop("Content-Type", None)
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=headers,
                json=json_payload,
                data=data_payload,
                files=files,
                params=query_params,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise SamchatAssistantAPIError(str(exc), status_code=0) from exc

        try:
            parsed = response.json()
        except ValueError:
            parsed = None

        if response.status_code >= 400:
            if isinstance(parsed, dict):
                message = str(parsed.get("detail") or parsed)
            else:
                message = response.text or f"HTTP {response.status_code}"
            raise SamchatAssistantAPIError(
                message,
                status_code=response.status_code,
                response_text=response.text,
            )

        if isinstance(parsed, (dict, list)):
            return parsed
        if response.text:
            return {"raw": response.text}
        return {}

    def create_conversation(
        self,
        *,
        title: Optional[str] = None,
        tournament_key: Optional[str] = None,
        module_key: str = "finance",
        module_label: str = "Hermes Finanzas",
        module_context: Optional[Dict[str, Any]] = None,
        external_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "title": title,
            "tournament_key": tournament_key,
            "module_key": module_key,
            "module_label": module_label,
            "module_context": module_context or {},
            "external_session_id": (external_session_id or "").strip() or None,
        }
        response = self._request("POST", "/conversations", json_payload=payload)
        return dict(response)

    def list_conversations(
        self,
        *,
        external_session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        external_session_id_clean = (external_session_id or "").strip()
        if external_session_id_clean:
            params["external_session_id"] = external_session_id_clean
        response = self._request(
            "GET",
            "/conversations",
            query_params=params or None,
        )
        return list(response) if isinstance(response, list) else []

    def ensure_conversation(
        self,
        *,
        title: str,
        tournament_key: Optional[str] = None,
        module_key: str = "finance",
        module_label: str = "Hermes Finanzas",
        module_context: Optional[Dict[str, Any]] = None,
        external_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        external_session_id_clean = (external_session_id or "").strip()
        if external_session_id_clean:
            rows = self.list_conversations(
                external_session_id=external_session_id_clean,
            )
            if rows:
                return dict(rows[0])
            return self.create_conversation(
                title=title,
                tournament_key=tournament_key,
                module_key=module_key,
                module_label=module_label,
                module_context=module_context,
                external_session_id=external_session_id_clean,
            )
        normalized_title = (title or "").strip()
        normalized_module = (module_key or "").strip().lower()
        normalized_tournament = (tournament_key or "").strip().lower()
        for row in self.list_conversations():
            row_title = str(row.get("title") or "").strip()
            row_module = str(row.get("module_key") or "").strip().lower()
            row_tournament = str(row.get("tournament_key") or "").strip().lower()
            if (
                row_title == normalized_title
                and row_module == normalized_module
                and row_tournament == normalized_tournament
            ):
                return row
        return self.create_conversation(
            title=title,
            tournament_key=tournament_key,
            module_key=module_key,
            module_label=module_label,
            module_context=module_context,
            external_session_id=external_session_id_clean or None,
        )

    def send_message(
        self,
        *,
        conversation_id: str,
        message: str,
        tournament_key: Optional[str] = None,
        module_key: Optional[str] = None,
        module_label: Optional[str] = None,
        module_context: Optional[Dict[str, Any]] = None,
        assistant_mode: str = "balanceado",
        bi_year: Optional[int] = None,
        bi_scope: Optional[str] = None,
        bi_segment: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "message": message,
            "tournament_key": tournament_key,
            "module_key": module_key,
            "module_label": module_label,
            "module_context": module_context,
            "assistant_mode": assistant_mode,
            "bi_year": bi_year,
            "bi_scope": bi_scope,
            "bi_segment": bi_segment,
        }
        response = self._request(
            "POST",
            f"/conversations/{conversation_id}/messages",
            json_payload=payload,
        )
        return dict(response)

    def send_media(
        self,
        *,
        conversation_id: str,
        file_path: str,
        kind: str,
        note: Optional[str] = None,
        tournament_key: Optional[str] = None,
        module_key: Optional[str] = None,
        module_label: Optional[str] = None,
        module_context: Optional[Dict[str, Any]] = None,
        assistant_mode: str = "balanceado",
        bi_year: Optional[int] = None,
        bi_scope: Optional[str] = None,
        bi_segment: Optional[str] = None,
    ) -> Dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(str(path))
        form_data = {
            "kind": kind,
            "note": note or "",
            "tournament_key": tournament_key or "",
            "module_key": module_key or "",
            "module_label": module_label or "",
            "module_context_json": json.dumps(module_context or {}, ensure_ascii=False),
            "assistant_mode": assistant_mode,
            "bi_year": str(bi_year or ""),
            "bi_scope": bi_scope or "",
            "bi_segment": bi_segment or "",
        }
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, "application/octet-stream")}
            response = self._request(
                "POST",
                f"/conversations/{conversation_id}/media",
                data_payload=form_data,
                files=files,
            )
        return dict(response)

    def confirm(
        self,
        *,
        conversation_id: str,
        run_id: str,
        approve: bool = True,
        assistant_mode: str = "balanceado",
    ) -> Dict[str, Any]:
        payload = {
            "run_id": run_id,
            "approve": approve,
            "assistant_mode": assistant_mode,
        }
        response = self._request(
            "POST",
            f"/conversations/{conversation_id}/confirm",
            json_payload=payload,
        )
        return dict(response)

    def send_message_with_confirmations(
        self,
        *,
        conversation_id: str,
        message: str,
        tournament_key: Optional[str] = None,
        module_key: Optional[str] = None,
        module_label: Optional[str] = None,
        module_context: Optional[Dict[str, Any]] = None,
        assistant_mode: str = "balanceado",
        auto_approve: bool = False,
        max_confirmation_rounds: int = 2,
    ) -> Dict[str, Any]:
        response = self.send_message(
            conversation_id=conversation_id,
            message=message,
            tournament_key=tournament_key,
            module_key=module_key,
            module_label=module_label,
            module_context=module_context,
            assistant_mode=assistant_mode,
        )
        if not auto_approve:
            return response
        current = dict(response)
        rounds = 0
        while current.get("pending_confirmation") and rounds < max_confirmation_rounds:
            pending = current.get("pending_confirmation") or {}
            run_id = str(pending.get("run_id") or "").strip()
            if not run_id:
                break
            current = self.confirm(
                conversation_id=conversation_id,
                run_id=run_id,
                approve=True,
                assistant_mode=assistant_mode,
            )
            rounds += 1
        return current
