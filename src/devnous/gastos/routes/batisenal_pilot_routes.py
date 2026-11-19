"""Explicit pilot mounting on the existing SamChat session; no startup provisioning.

The host supplies SupportPilot, stores, signer and canonical employee dependency.
No login, host identity translation, Events, DDL or credentials are created here.
"""

import json
import secrets
from dataclasses import asdict
from functools import wraps
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, text

from samchat.private_plugin.authorization import (
    resolve_session_principal,
    validate_authorization_request,
)
from samchat.private_plugin.contracts import Denied

from ..services import batisenal_service as owner
from ..services.batisenal_pilot_service import pilots
from .batisenal_routes import HEADERS, resolve_web_principal

PREFIX = "/soporte/batisenal/piloto"
AUTH_META = "/.well-known/oauth-authorization-server" + PREFIX
RESOURCE_META = "/.well-known/oauth-protected-resource" + PREFIX + "/mcp"
SAFE_HEADERS = {
    **HEADERS,
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; form-action 'self' https:; base-uri "
        "'none'; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
}


def html(body, status=200, *, form_page=False, panel=False):
    script = '<script src="' + PREFIX + '/widget.js" defer></script>' if panel else ""
    return HTMLResponse(
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Batiseñal · SamChat</title><link rel="stylesheet" href="'
        + PREFIX
        + '/style.css"></head><body><main class="bs-pilot">'
        '<header><a href="' + PREFIX + '/case">SamChat · Batiseñal</a>'
        "<span>Consulta privada</span></header>"
        + body
        + "</main>"
        + script
        + "</body></html>",
        status_code=status,
        # Preserve native Origin on same-origin form navigation. Do not change
        # the policy on errors, redirects, metadata or token responses.
        headers=(
            {**SAFE_HEADERS, "Referrer-Policy": "same-origin"}
            if form_page
            else SAFE_HEADERS
        ),
    )


def private_empty_message(permission):
    "Describe the visibility state without asserting whether a"
    "draft exists."
    if permission.startswith("Revocada;"):
        return (
            "El contenido privado no se muestra porque esta conexión está revocada. "
            "Esta vista no indica que las propuestas se hayan eliminado."
        )
    if permission.startswith("Vencida;"):
        return (
            "El contenido privado no se muestra porque esta conexión ha vencido. "
            "Esta vista no indica que las propuestas se hayan eliminado."
        )
    return (
        "Aún no hay una propuesta privada. "
        "Puedes continuar y volver a consultar aquí."
    )


def hidden(values):
    return "".join(
        '<input type="hidden" name="'
        + escape(k, quote=True)
        + '" value="'
        + escape(str(v), quote=True)
        + '">'
        for k, v in values.items()
    )


def form(values, label):
    return (
        '<form method="post" action="'
        + PREFIX
        + '/consent">'
        + hidden(values)
        + "<button>"
        + escape(label)
        + "</button></form>"
    )


def guarded(function):
    @wraps(function)
    async def call(*args, **kwargs):
        try:
            return await function(*args, **kwargs)
        except (Denied, owner.BatisenalError, HTTPException) as error:
            status = getattr(error, "status", getattr(error, "status_code", 403))
            code = (
                str(error)
                if isinstance(error, Denied)
                else getattr(error, "code", "UNAUTHENTICATED")
            )
            if code == "OAUTH_BUSY":
                return html(
                    "<h1>La conexión está ocupada</h1>"
                    "<p>No se confirmó esta operación. "
                    "Espera un momento y reintenta desde el mismo enlace. "
                    "Tu consulta y tu borrador se conservan.</p>",
                    503,
                )
            if code == "ACTIVATION_REQUIRED":
                status = 503
            if code == "UNAUTHENTICATED":
                status = 401
            return html(
                "<h1>No se confirmó la conexión</h1><p>Revisa tu sesión de "
                "SamChat y vuelve a abrir el enlace original. "
                "Tu caso y tu borrador de trabajo se conservan. No se publicó"
                " una respuesta.</p>"
                '<a href="/login">Ir al acceso de SamChat</a> · <a '
                'href="/panel">Volver a mis consultas</a>',
                status,
            )
        except Exception:
            return html(
                "<h1>No pudimos confirmar esta operación</h1><p>El servicio "
                "no está disponible. "
                "Vuelve a abrir el enlace original para verificar la "
                "conexión; no repitas cambios en tu operación.</p>"
                '<a href="/panel">Volver a mis consultas</a>',
                503,
            )

    return call


async def fields(request):
    if request.url.query:
        raise owner.BatisenalError("FIELDS_INVALID")
    if (
        request.headers.get("content-type", "").split(";")[0]
        != "application/x-www-form-urlencoded"
    ):
        raise owner.BatisenalError("CONTENT_TYPE_INVALID", 415)
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 8192:
            raise owner.BatisenalError("BODY_TOO_LARGE", 413)
    try:
        pairs = parse_qsl(
            raw.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=16,
        )
        if len(dict(pairs)) != len(pairs):
            raise ValueError()
        return dict(pairs)
    except (UnicodeError, ValueError) as exc:
        raise owner.BatisenalError("FIELDS_INVALID") from exc


def json_request(request, payload, csrf):
    "Translate HTML fields; keep actual Origin/session and"
    "supplied CSRF value."
    if not isinstance(csrf, str) or not csrf.isascii() or len(csrf) > 512:
        raise owner.BatisenalError("CSRF_DENIED", 403)
    scope = dict(request.scope)
    headers = [
        (k, v)
        for k, v in scope["headers"]
        if k.lower() not in (b"content-type", b"content-length", b"x-batisenal-csrf")
    ]
    headers += [
        (b"content-type", b"application/json"),
        (b"x-batisenal-csrf", csrf.encode("ascii")),
    ]
    scope["headers"] = headers

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(payload).encode(),
            "more_body": False,
        }

    return Request(scope, receive)


async def case_data(pilot, request):
    "Reuse the canonical business owners; no tenant/actor"
    "supplied by the page."
    pilot.gate()
    async with pilot.sessions() as session:
        await resolve_session_principal(
            request=request,
            session=session,
            get_current_empleado=pilot.employee_dependency,
        )
        employee = await pilot.employee_dependency(request, session)
        principal = await resolve_web_principal(session, employee, "GET")
        if pilot.case_service is None:
            raise owner.BatisenalError("RESOURCE_UNAVAILABLE", 403)
        drafts = await pilot.case_service.private_view(
            session, employee, pilot.pilot.case_id
        )
        detail = await owner.case_detail(session, principal, pilot.pilot.case_id)
        row = (
            (
                await session.execute(
                    select(pilots).where(
                        pilots.c.ticket_id == UUID(pilot.pilot.case_id),
                        pilots.c.employee == str(principal.employee),
                        pilots.c.issuer == pilot.resource.issuer,
                        pilots.c.installation_id == pilot.resource.installation_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        observed_at = await session.scalar(
            text("SELECT extract(epoch FROM clock_timestamp())")
        )
        detail["private_owner_view"] = pilot.case_service is not None
        detail["pilot_permission"] = (
            "Aún no autorizada"
            if row is None
            else (
                "Revocada; el agente ya no tiene acceso"
                if row.revoked
                else (
                    "Vencida; reconectar no amplía el plazo"
                    if observed_at >= row.expires_at
                    else "Vigente dentro de la ventana del piloto; esto no significa "
                    "que el agente esté analizando"
                )
            )
        )
        detail["viewer_name"] = (
            getattr(employee, "nombre", None) or "Cuenta de SamChat autenticada"
        )
        # Recheck after all render data I/O, before releasing any content.
        drafts = await pilot.case_service.private_view(
            session, employee, pilot.pilot.case_id
        )
        pilot.gate()
        return detail, drafts


class PilotResource:
    def __init__(self, pilot, origin):
        self.pilot, self.origin = pilot, origin

    async def __call__(self, scope, receive, send):
        if self.pilot.enabled is not True:
            await JSONResponse(
                {"error": "ACTIVATION_REQUIRED"}, status_code=503, headers=HEADERS
            )(scope, receive, send)
            return

        async def respond(message):
            if message["type"] == "http.response.start" and message["status"] == 401:
                message = dict(
                    message,
                    headers=list(message["headers"])
                    + [
                        (
                            b"www-authenticate",
                            (
                                'Bearer resource_metadata="'
                                + self.origin
                                + RESOURCE_META
                                + '"'
                            ).encode(),
                        )
                    ],
                )
            await send(message)

        await self.pilot.mcp(scope, receive, respond)


def mount_pilot(app, pilot):
    """Call once, before startup, under the host's existing SessionMiddleware.

    No env lookup or provisioning. Refuse collisions/config mismatch before mutation.
    """
    config = pilot.flow.config
    parsed = urlsplit(config.issuer)
    origin = parsed.scheme + "://" + parsed.netloc
    if config.issuer != origin + PREFIX or config.resource != origin + PREFIX + "/mcp":
        raise ValueError("PILOT_MOUNT_CONFIG_MISMATCH")
    paths = {PREFIX, AUTH_META, RESOURCE_META}
    for route in app.routes:
        path = getattr(route, "path", "")
        if path in paths or path.startswith(PREFIX + "/"):
            raise ValueError("PILOT_MOUNT_COLLISION")
    router = APIRouter()

    def gate(request):
        pilot.gate()
        if str(request.base_url).rstrip("/") != origin:
            raise owner.BatisenalError("ORIGIN_DENIED", 403)

    @router.get(AUTH_META)
    @guarded
    async def authorization_metadata(request: Request):
        gate(request)
        return JSONResponse(config.authorization_metadata(), headers=HEADERS)

    @router.get(RESOURCE_META)
    @guarded
    async def resource_metadata(request: Request):
        gate(request)
        return JSONResponse(config.resource_metadata(), headers=HEADERS)

    @router.get(PREFIX + "/style.css")
    @guarded
    async def style(request: Request):
        gate(request)
        from fastapi.responses import Response

        return Response(STYLE, media_type="text/css", headers=HEADERS)

    @router.get(PREFIX + "/widget.js")
    @guarded
    async def widget(request: Request):
        gate(request)
        from fastapi.responses import Response

        path = Path(__file__).resolve().parents[4] / "static" / "batisenal.js"
        return Response(
            path.read_text(), media_type="application/javascript", headers=HEADERS
        )

    @router.get(PREFIX + "/authorize")
    @guarded
    async def authorize(request: Request):
        gate(request)
        if len(request.url.query) > 4096:
            raise owner.BatisenalError("BODY_TOO_LARGE", 413)
        parameters = list(request.query_params.multi_items())
        validate_authorization_request(config, parameters)
        await pilot.browser_for_request(request)
        detail, _ = await case_data(pilot, request)
        csrf = request.session.setdefault("batisenal_csrf", secrets.token_urlsafe(32))
        return html(
            '<p class="eyebrow">Conexión individual · revisión '
            "humana</p><h1>Ayuda para " + escape(detail["title"]) + "</h1>"
            "<p>"
            + (
                "Stub sintético · no es Chatty"
                if pilot.pilot.source_kind == "synthetic_stub"
                else "Chatty · conexión configurada. Autorizar no inicia una consulta."
            )
            + "</p><p>Cuenta: "
            + escape(detail["viewer_name"])
            + "</p><p>El agente podrá consultar el contexto revisado de "
            "este único caso y guardar una propuesta privada. La revisión"
            " respeta los permisos vigentes de esta cuenta. No se "
            "comparten capturas. Usa sólo datos sintéticos y revisa el "
            "contexto antes de autorizar.</p>"
            "<p>La ventana dura como máximo 60 minutos desde la primera "
            "autorización; reconectar no la amplía.</p>"
            + form(
                {"stage": "begin", "csrf": csrf, "parameters": json.dumps(parameters)},
                "Revisar conexión",
            )
            + '<a href="'
            + PREFIX
            + '/case">Ahora no · conservar mi consulta</a>',
            form_page=True,
        )

    @router.post(PREFIX + "/consent")
    @guarded
    async def consent(request: Request):
        gate(request)
        data = await fields(request)
        stage = data.pop("stage", "")
        csrf = data.get("csrf", "")
        if stage == "begin" and set(data) == {"csrf", "parameters"}:
            try:
                payload = {"parameters": json.loads(data["parameters"])}
            except (ValueError, TypeError) as exc:
                raise owner.BatisenalError("FIELDS_INVALID") from exc
            preview = await pilot.browser_call(
                json_request(request, payload, csrf), "begin"
            )
            detail, _ = await case_data(pilot, request)
            values = {
                k: preview[k] for k in ("draft_id", "fingerprint", "csrf", "state")
            }
            values.update(stage="confirm", approved="true")
            scope = preview["pilot"]
            return html(
                '<p class="eyebrow">Último paso</p><h1>Autoriza sólo esta '
                "consulta</h1><section>"
                "<h2>"
                + escape(detail["title"])
                + "</h2><p>"
                + escape(detail["description"])
                + "</p>"
                "<p>"
                + escape(scope["organization"])
                + " / "
                + escape(scope["project"])
                + "</p>"
                "<p>Cuenta: "
                + escape(detail["viewer_name"])
                + "</p><p>"
                + (
                    "Stub sintético · no es Chatty"
                    if scope["source_kind"] == "synthetic_stub"
                    else "Chatty · conexión configurada"
                )
                + "</p><p>Máximo 60 minutos desde la primera autorización; "
                "reconectar no amplía el plazo.</p><p>Lectura del caso y "
                "borrador privado. Sin pagos, cambios de permisos o "
                "publicación.</p></section>"
                + form(values, "Autorizar lectura y borrador privado")
                + '<a href="'
                + PREFIX
                + '/case">Cancelar conexión · conservar consulta y trabajo</a>',
                form_page=True,
            )
        if stage == "confirm" and set(data) == {
            "draft_id",
            "fingerprint",
            "csrf",
            "state",
            "approved",
        }:
            if data["approved"] != "true":
                raise Denied("CONSENT_REJECTED")
            result = await pilot.browser_call(
                json_request(request, dict(data, approved=True), csrf), "confirm"
            )
            return RedirectResponse(
                result.redirect_uri
                + "?"
                + urlencode({"code": result.code, "state": result.state}),
                status_code=303,
                headers=SAFE_HEADERS,
            )
        raise owner.BatisenalError("FIELDS_INVALID")

    @router.post(PREFIX + "/token")
    async def token(request: Request):
        try:
            gate(request)
            if any(
                request.headers.get(h) for h in ("cookie", "origin", "authorization")
            ):
                raise Denied("INVALID_GRANT")
            data = await fields(request)
            value = asdict(await pilot.exchange(list(data.items())))
            return JSONResponse(
                {
                    k: value[k]
                    for k in ("access_token", "token_type", "expires_in", "scope")
                },
                headers=HEADERS,
            )
        except (Denied, owner.BatisenalError) as error:
            if isinstance(error, Denied) and str(error) == "OAUTH_BUSY":
                return JSONResponse(
                    {
                        "error": "temporarily_unavailable",
                        "error_description": (
                            "Retry the same code and PKCE verifier; "
                            "this attempt did not consume it."
                        ),
                    },
                    status_code=503,
                    headers=HEADERS,
                )
            return JSONResponse(
                {
                    "error": "invalid_grant",
                    "error_description": (
                        "Reconnect using the original authorization link "
                        "and PKCE verifier."
                    ),
                },
                status_code=400,
                headers=HEADERS,
            )
        except Exception:
            return JSONResponse(
                {"error": "temporarily_unavailable"}, status_code=503, headers=HEADERS
            )

    @router.get(PREFIX + "/case")
    @guarded
    async def case(request: Request):
        gate(request)
        detail, drafts = await case_data(pilot, request)
        csrf = request.session.setdefault("batisenal_csrf", secrets.token_urlsafe(32))
        ended = detail["pilot_permission"].startswith(("Revocada;", "Vencida;"))
        if ended:
            body = (
                "<h1>Acceso terminado</h1><p>"
                + escape(private_empty_message(detail["pilot_permission"]))
                + "</p>"
            )
            state = "ended"
        else:
            state = "stored" if drafts else "pending"
            if any(draft.get("stale") for draft in drafts):
                state = "stale"
            body = (
                "<h1>"
                + escape(detail["title"])
                + "</h1><p>"
                + escape(detail["description"])
                + "</p>"
                '<p class="permission">Autorización: '
                + escape(detail["pilot_permission"])
                + "</p>"
                "<details><summary>Contexto revisado</summary><dl>"
                + "".join(
                    "<dt>" + escape(k) + "</dt><dd>" + escape(str(v)) + "</dd>"
                    for k, v in detail["context"].items()
                )
                + "</dl></details>"
            )
            if not drafts:
                body += "<p>Pendiente de conexión o consulta explícita desde Chatty. "
                body += "Autorizar no significa que el agente esté analizando.</p>"
            for draft in drafts:
                label = (
                    "Chatty · conexión configurada"
                    if draft["source_kind"] == "ai_agent"
                    else "Stub sintético · no es Chatty"
                )
                body += (
                    '<section class="private"><h2>Borrador privado guardado</h2><p>'
                    + label
                    + "</p>"
                    + (
                        "<p>El contexto cambió; requiere nueva consulta explícita.</p>"
                        if draft.get("stale")
                        else ""
                    )
                    + "<pre>"
                    + escape(draft["body"])
                    + "</pre>"
                    + "<p>No publicado. Sólo tu cuenta puede revisar "
                    "esta propuesta.</p>"
                )
                reference = draft.get("operation_id")
                if reference:
                    body += (
                        "<p>Referencia: <code data-operation-reference>"
                        + escape(str(reference))
                        + "</code></p>"
                    )
                body += "</section>"
            body += (
                "<details><summary>Administrar esta conexión</summary>"
                '<form method="post" action="'
                + PREFIX
                + '/revoke">'
                + hidden({"csrf": csrf, "confirmed": "true"})
                + "<button>Revocar acceso del agente · conservar "
                "caso</button></form></details>"
            )
        return html(
            '<button type="button" id="bs-open" hidden aria-controls="bs-panel" '
            'aria-expanded="false">Abrir Batiseñal</button>'
            '<section id="bs-panel" aria-label="Revisión privada de Batiseñal">'
            '<button type="button" id="bs-minimize">Minimizar</button>'
            '<p id="bs-live" role="status" aria-live="polite"></p>'
            '<div id="bs-content" data-state="' + state + '">' + body + "</div>"
            '<label for="bs-notes">Notas privadas de trabajo · no se envían</label>'
            '<textarea id="bs-notes" maxlength="2000" rows="3"></textarea>'
            '<p id="bs-reference"></p>'
            '<button type="button" id="bs-refresh">Consultar actualización</button>'
            "<noscript><p>Para volver a validar el acceso, recarga esta "
            "página.</p></noscript>"
            "</section>",
            form_page=True,
            panel=True,
        )

    @router.post(PREFIX + "/revoke")
    @guarded
    async def revoke(request: Request):
        gate(request)
        data = await fields(request)
        if set(data) != {"csrf", "confirmed"} or data["confirmed"] != "true":
            raise Denied("CONSENT_REJECTED")
        await pilot.browser_call(
            json_request(request, {"confirmed": True}, data["csrf"]), "revoke"
        )
        return RedirectResponse(PREFIX + "/case", status_code=303, headers=SAFE_HEADERS)

    app.include_router(router)
    app.mount(PREFIX, PilotResource(pilot, origin), name="batisenal-pilot")
    app.state.batisenal_private_pilot_case = pilot.pilot.case_id
    return pilot


STYLE = """
:root{font-family:system-ui,sans-serif;
color:#182a3b;
background:#f2f6fa;
line-height:1.6}

*{box-sizing:border-box}
body{margin:0}
.bs-pilot{max-width:760px;
margin:auto;
padding:24px}

header,nav{display:flex;
gap:16px;
flex-wrap:wrap;
justify-content:space-between;
margin:16px 0 32px}

h1{font-size:clamp(1.7rem,5vw,2.4rem);
line-height:1.2}
h2{font-size:1.2rem}

section{background:white;
border:1px solid #cbd5e1;
padding:20px;
border-radius:16px;
margin:20px 0}

.private{border-left:5px solid #0f766e}
.eyebrow{color:#16665d;
font-weight:650}

button{background:#115e59;
color:white;
font:inherit;
border:0;
border-radius:10px;
padding:12px 18px;
cursor:pointer;
min-height:44px}

a{color:#115e59;
text-underline-offset:3px}
button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #d97706;
outline-offset:4px}

[hidden]{display:none!important}
textarea{display:block;
width:100%;
font:inherit;
margin:8px 0 16px;
padding:10px;
border:1px solid #64748b;
border-radius:8px}
label{font-weight:600}
textarea:focus-visible{outline:3px solid #d97706;
outline-offset:3px}
form{margin:24px 0}
pre{white-space:pre-wrap;
overflow-wrap:anywhere;
font:inherit}
details{margin:20px 0}
summary{cursor:pointer;
min-height:44px}

@media(max-width:480px){.bs-pilot{padding:16px}
header{margin-bottom:20px}
button{width:100%}
section{padding:16px}
}

"""
