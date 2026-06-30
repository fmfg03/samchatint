"""
Email new empleados their initial web password (SendGrid).
"""

from __future__ import annotations

import logging
from html import escape
from typing import Tuple

from devnous.utils.sendgrid_client import (
    build_sendgrid_payload,
    send_sendgrid_mail_async,
)

logger = logging.getLogger(__name__)

DEFAULT_LOGIN_URL = "https://sam.chat/login"


async def send_initial_password_email(
    *,
    to_email: str,
    nombre: str,
    plain_password: str,
    login_url: str = DEFAULT_LOGIN_URL,
) -> Tuple[bool, str]:
    """
    Send credential email. Returns (ok, admin_visible_note).

    If SendGrid is missing or the request fails, ok is False and the note
    explains that the admin must share the password manually.
    """
    to_email = (to_email or "").strip()
    if not to_email:
        return False, "Sin correo: no se envió email; comparte la contraseña manualmente."

    subject = "Bienvenida a sam.chat"
    safe_name = escape((nombre or "").strip())
    greeting = f"Hola, {safe_name}." if safe_name else "Hola,"
    safe_pw = escape(plain_password)
    safe_url = escape(login_url or DEFAULT_LOGIN_URL)
    html = f"""
    <div style="font-family:system-ui,-apple-system,sans-serif;max-width:520px;margin:0 auto;
      line-height:1.5;color:#171717;">
      <p>{greeting}</p>
      <p>Te damos la bienvenida a <strong>sam.chat</strong>. Tu contraseña temporal es:</p>
      <p style="font-size:17px;font-weight:600;font-family:ui-monospace,monospace;
        background:#f4f4f5;padding:12px 14px;border-radius:8px;word-break:break-all;">
        {safe_pw}
      </p>
      <p>Entra en <a href="{safe_url}">{safe_url}</a>. Por seguridad, cámbiala pronto desde tu cuenta.</p>
      <p style="color:#737373;font-size:12px;">Si no solicitaste este acceso, ignora este correo.</p>
    </div>
    """

    payload = build_sendgrid_payload(
        recipients=[{"email": to_email, "name": nombre.strip() if nombre else ""}],
        subject=subject,
        html_content=html,
    )
    ok, status, detail = await send_sendgrid_mail_async(payload)
    if ok:
        return True, f"Correo enviado correctamente (HTTP {status})."
    logger.warning(
        "Initial password email failed for %s: status=%s detail=%s",
        to_email,
        status,
        detail[:500] if detail else "",
    )
    return (
        False,
        "No se pudo enviar el correo automáticamente; comparte la contraseña manualmente. "
        f"(Detalle: {detail[:200] if detail else 'sin detalle'})",
    )
