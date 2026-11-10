"""Shared session and request helpers for the private Batiseñal pilot.

General creation, attachment and publication routes are deliberately not mounted.
"""

import json
import secrets

from ..services import batisenal_service as service

HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


async def read_payload(request, policy):
    origin = request.headers.get("origin", "")
    if origin != str(request.base_url).rstrip("/"):
        raise service.BatisenalError("ORIGIN_DENIED", 403)
    expected = request.session.get("batisenal_csrf")
    supplied = request.headers.get("x-batisenal-csrf", "")
    if not expected or not secrets.compare_digest(expected, supplied):
        raise service.BatisenalError("CSRF_DENIED", 403)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise service.BatisenalError("CONTENT_TYPE_INVALID", 415)
    maximum = 4 * ((policy.max_image_bytes + 2) // 3) + 64000
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > maximum:
            raise service.BatisenalError("BODY_TOO_LARGE", 413)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise service.BatisenalError("JSON_INVALID") from exc
    if not isinstance(data, dict):
        raise service.BatisenalError("JSON_INVALID")
    return data


async def resolve_web_principal(session, employee, method):
    from ..services.access_control_service import (
        AccessControlLookupError,
        can_access_tool,
        explicit_tool_decision,
    )
    from .dependencies import _load_effective_permissions

    async def allowed(tool, write_action):
        action = "ver" if method == "GET" else write_action
        try:
            for check in {"ver", action}:
                if (
                    await explicit_tool_decision(session, employee, tool, check)
                    is False
                ):
                    return False
        except AccessControlLookupError as exc:
            raise service.BatisenalError("AUTHORITY_UNAVAILABLE", 503) from exc
        return await can_access_tool(session, employee, tool, action)

    tokens = await _load_effective_permissions(session, employee.id)
    return service.principal_from_tokens(
        employee,
        tokens,
        can_request=await allowed("soporte.self", "crear"),
        can_support=await allowed("admin.soporte", "editar"),
    )
