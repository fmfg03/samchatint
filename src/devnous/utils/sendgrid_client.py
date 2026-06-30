"""
Minimal SendGrid v3 mail/send client for transactional email.

Uses the same environment variables as other SamChat backends:
SENDGRID_API_KEY, SENDGRID_FROM_EMAIL, SENDGRID_FROM_NAME.
"""

from __future__ import annotations

import asyncio
import html as html_module
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def email_plain_text_from_html(html_content: str) -> str:
    """Strip tags to a readable plain-text fallback."""
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html_content or "")
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</p\s*>", "\n\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html_module.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def build_sendgrid_payload(
    *,
    recipients: List[Dict[str, str]],
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
) -> Dict[str, Any]:
    """Build JSON body for POST https://api.sendgrid.com/v3/mail/send."""
    from_email = (os.getenv("SENDGRID_FROM_EMAIL") or "noreply@sam.chat").strip()
    from_name = (os.getenv("SENDGRID_FROM_NAME") or "Plataforma Sports").strip()
    to_list: List[Dict[str, Any]] = []
    for r in recipients:
        entry: Dict[str, Any] = {"email": r["email"].strip()}
        name = (r.get("name") or "").strip()
        if name:
            entry["name"] = name
        to_list.append(entry)
    return {
        "personalizations": [{"to": to_list}],
        "from": {"email": from_email, "name": from_name},
        "subject": (subject or "").strip(),
        "content": [
            {
                "type": "text/plain",
                "value": (
                    text_content or email_plain_text_from_html(html_content)
                ).strip(),
            },
            {"type": "text/html", "value": html_content},
        ],
    }


def send_sendgrid_mail_sync(
    payload: Dict[str, Any],
    *,
    timeout: int = 20,
) -> Tuple[bool, int, str]:
    """
    POST to SendGrid. Returns (success, http_status, detail_message).

    On success, status is typically 202 and the body may be empty.
    """
    api_key = (os.getenv("SENDGRID_API_KEY") or "").strip()
    if not api_key:
        return False, 0, "SENDGRID_API_KEY is not configured"

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            response_body = res.read().decode("utf-8", errors="replace")
            status = int(getattr(res, "status", 202) or 202)
            if status >= 400:
                return False, status, response_body or "SendGrid error"
            return True, status, response_body
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return False, int(exc.code or 502), detail or str(exc.reason)
    except urllib.error.URLError as exc:
        logger.warning("SendGrid URLError: %s", exc)
        return False, 0, str(exc)


async def send_sendgrid_mail_async(
    payload: Dict[str, Any],
    *,
    timeout: int = 20,
) -> Tuple[bool, int, str]:
    return await asyncio.to_thread(send_sendgrid_mail_sync, payload, timeout=timeout)
