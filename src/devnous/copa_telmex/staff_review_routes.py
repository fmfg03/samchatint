"""Public adapters for the private review workflow; no authority is minted here.

The account comes from a current non-impersonated login. The private service
independently resolves principal, faculty, edition, source, plan and attempt.
Navigation, CSRF and a browser confirmation never replace those checks.
"""

from __future__ import annotations

import inspect
import json
import secrets
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

CSRF_KEY = "ctt_staff_review_csrf"
CSRF_HEADER = "X-CTT-CSRF"
MAX_BODY_BYTES = 256 * 1024
MAX_PNG_BYTES = 16 * 1024 * 1024
PRIVATE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
TEAM_FIELDS = frozenset(
    {
        "name",
        "state",
        "municipality",
        "gender",
        "category",
        "league",
        "representative_name",
        "contact_phone",
        "contact_email",
    }
)
PERSON_FIELDS = frozenset(
    {
        "slot",
        "first_name",
        "last_name",
        "birth_date",
        "curp",
        "source_page_index",
        "boxes",
        "photo_box",
    }
)
STAFF_FIELDS = PERSON_FIELDS | {"presence", "presence_confirmed", "absence_box"}
MESSAGES = {
    "STALE_PROPOSAL": (409, "La revisión cambió. Recarga el plan antes de confirmar."),
    "REVIEW_STALE": (409, "La revisión cambió. Recarga el plan antes de confirmar."),
    "ENROLLMENT_REQUIRED": (
        409,
        "Registra tu passkey y solicita su activación al responsable.",
    ),
    "PASSKEY_PENDING_ACTIVATION": (
        409,
        "La passkey está pendiente de activación por el responsable.",
    ),
    "SOURCE_UNAVAILABLE": (
        409,
        "La fuente no está disponible para verificar esta revisión.",
    ),
    "REVIEW_BLOCKED": (409, "La revisión tiene datos o fuentes pendientes."),
    "SCOPE_DENIED": (403, "La revisión no pertenece a tu facultad vigente."),
    "ACCOUNT_NOT_BOUND": (403, "Tu cuenta aún no está acreditada para esta edición."),
    "REVIEW_EXPIRED": (
        409,
        "La aprobación venció. Revisa el plan y confirma nuevamente.",
    ),
}


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"ok": False, "error": code, "message": message},
        headers=PRIVATE_HEADERS,
    )


def _uuid(value: Any) -> UUID:
    try:
        if type(value) is not str:
            raise ValueError
        parsed = UUID(value)
        if str(parsed) != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise _error(
            400, "INVALID_REFERENCE", "La referencia de revisión no es válida."
        ) from None


def _proposal_changes(value: Any) -> dict:
    """Only proposed business values and source rectangles cross this boundary."""
    if type(value) is not dict or set(value) - {
        "team",
        "players",
        "staff",
        "confirm_present_staff",
    }:
        raise _error(
            400, "INVALID_PROPOSAL", "La propuesta contiene campos no permitidos."
        )

    def box(rectangle):
        if rectangle is None:
            return
        if type(rectangle) is not dict or set(rectangle) != {
            "page_index",
            "x",
            "y",
            "width",
            "height",
        }:
            raise ValueError
        for key, number in rectangle.items():
            if type(number) is not int or not 0 <= number <= 1_000_000:
                raise ValueError
            if key in {"page_index", "width", "height"} and number < 1:
                raise ValueError

    try:
        if (
            "confirm_present_staff" in value
            and type(value["confirm_present_staff"]) is not bool
        ):
            raise ValueError
        team = value.get("team", {})
        if type(team) is not dict or set(team) - TEAM_FIELDS:
            raise ValueError
        if any(type(item) is not str or len(item) > 500 for item in team.values()):
            raise ValueError
        for kind, allowed, maximum in (
            ("players", PERSON_FIELDS, 35),
            ("staff", STAFF_FIELDS, 2),
        ):
            members = value.get(kind, [])
            if type(members) is not list or len(members) > maximum:
                raise ValueError
            slots = set()
            for member in members:
                if type(member) is not dict or set(member) - allowed:
                    raise ValueError
                slot = member.get("slot")
                if type(slot) is not int or not 1 <= slot <= maximum or slot in slots:
                    raise ValueError
                slots.add(slot)
                page_index = member.get("source_page_index")
                unresolved_staff = (
                    kind == "staff"
                    and member.get("presence") == "UNCERTAIN"
                    and member.get("presence_confirmed") is False
                    and member.get("boxes", {}) == {}
                    and member.get("photo_box") is None
                    and member.get("absence_box") is None
                )
                if type(page_index) is not int or not (
                    1 <= page_index <= 10000 or page_index == 0 and unresolved_staff
                ):
                    raise ValueError
                for name in ("first_name", "last_name", "birth_date", "curp"):
                    if (
                        type(member.get(name, "")) is not str
                        or len(member.get(name, "")) > 500
                    ):
                        raise ValueError
                boxes = member.get("boxes", {})
                if type(boxes) is not dict or set(boxes) - {
                    "full_name",
                    "birth_date",
                    "curp",
                }:
                    raise ValueError
                for rectangle in boxes.values():
                    box(rectangle)
                    if rectangle is not None and rectangle["page_index"] != page_index:
                        raise ValueError
                box(member.get("photo_box"))
                if (
                    member.get("photo_box") is not None
                    and member["photo_box"]["page_index"] != page_index
                ):
                    raise ValueError
                if kind == "staff":
                    if member.get("presence") not in {"PRESENT", "ABSENT", "UNCERTAIN"}:
                        raise ValueError
                    if type(member.get("presence_confirmed")) is not bool:
                        raise ValueError
                    box(member.get("absence_box"))
                    if (
                        member.get("absence_box") is not None
                        and member["absence_box"]["page_index"] != page_index
                    ):
                        raise ValueError
        return value
    except (ValueError, TypeError):
        raise _error(
            400, "INVALID_PROPOSAL", "Revisa los valores y las cajas de la propuesta."
        ) from None


class StaffReviewRoutes:
    """Session-bound HTTP presentation around an explicitly configured client."""

    def __init__(
        self,
        *,
        ensure_access: Callable,
        actor_from_request: Callable,
        refresh_actor: Callable,
        session_factory: Callable,
        client_resolver: Callable,
        expected_origin: str,
        allow_test_origin: bool = False,
    ):
        parsed = urlsplit(expected_origin)
        if (
            not all(
                callable(item)
                for item in (
                    ensure_access,
                    actor_from_request,
                    refresh_actor,
                    session_factory,
                    client_resolver,
                )
            )
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path
            or not parsed.hostname
            or (
                expected_origin != "https://sam.chat"
                and not (
                    allow_test_origin is True
                    and parsed.scheme in {"http", "https"}
                    and parsed.hostname in {"localhost", "127.0.0.1", "[::1]", "::1"}
                )
            )
        ):
            raise ValueError("invalid pinned review origin")
        self._access, self._actor, self._refresh = (
            ensure_access,
            actor_from_request,
            refresh_actor,
        )
        self._sessions, self._resolve = session_factory, client_resolver
        self._origin = expected_origin

    def _navigation(self, request: Request) -> None:
        try:
            if self._access(request) is not None:
                raise _error(
                    403,
                    "ACCESS_DENIED",
                    "La sesión no permite revisar este expediente.",
                )
        except HTTPException as error:
            error.headers = {**(error.headers or {}), **PRIVATE_HEADERS}
            raise
        if request.session.get("impersonator_empleado_id"):
            raise _error(
                403,
                "IMPERSONATION_DENIED",
                "Usa tu propia sesión para revisar y autorizar.",
            )

    def _mutation(self, request: Request) -> None:
        self._navigation(request)
        origins = request.headers.getlist("origin")
        if len(origins) != 1 or origins[0] != self._origin:
            raise _error(
                403, "ORIGIN_DENIED", "La acción debe realizarse desde esta aplicación."
            )
        tokens = request.headers.getlist(CSRF_HEADER)
        expected = request.session.get(CSRF_KEY)
        if (
            type(expected) is not str
            or len(tokens) != 1
            or not tokens[0]
            or not expected.isascii()
            or not tokens[0].isascii()
            or not secrets.compare_digest(expected, tokens[0])
        ):
            raise _error(403, "CSRF_DENIED", "Recarga la revisión antes de continuar.")

    async def _context(self, request: Request, session_id: str):
        self._navigation(request)
        session_uuid = _uuid(session_id)
        initial = self._actor(request)
        account = _uuid(str(initial.get("user_id") or ""))
        if _uuid(str(request.session.get("empleado_id") or "")) != account:
            raise _error(
                403, "ACTOR_CHANGED", "La cuenta de revisión no coincide con tu sesión."
            )
        try:
            async with self._sessions() as session:
                current = await self._refresh(session, initial)
        except HTTPException as error:
            error.headers = {**(error.headers or {}), **PRIVATE_HEADERS}
            raise
        except Exception:
            raise _error(
                503, "REVIEW_UNAVAILABLE", "No se pudo verificar tu sesión actual."
            ) from None
        if _uuid(str(current.get("user_id") or "")) != account:
            raise _error(
                403, "ACTOR_CHANGED", "La cuenta de revisión no coincide con tu sesión."
            )
        client = self._resolve(request)
        if inspect.isawaitable(client):
            client = await client
        if client is None:
            raise _error(
                503,
                "REVIEW_UNAVAILABLE",
                "La revisión protegida aún no está configurada.",
            )
        return client, account, session_uuid

    async def _body(self, request: Request, keys: set[str]) -> dict:
        if (
            request.headers.get("content-type", "").split(";", 1)[0]
            != "application/json"
        ):
            raise _error(
                415, "JSON_REQUIRED", "Esta acción requiere la revisión protegida."
            )
        chunks, total = [], 0
        async for part in request.stream():
            total += len(part)
            if total > MAX_BODY_BYTES:
                raise _error(
                    413,
                    "REQUEST_TOO_LARGE",
                    "La solicitud de revisión es demasiado grande.",
                )
            chunks.append(part)

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        try:
            raw = b"".join(chunks)
            # Bound nested input before JSON decoding or recursion can run.
            depth, quoted, escaped = 0, False, False
            for byte in raw:
                if quoted:
                    if escaped:
                        escaped = False
                    elif byte == 92:
                        escaped = True
                    elif byte == 34:
                        quoted = False
                elif byte == 34:
                    quoted = True
                elif byte in (91, 123):
                    depth += 1
                    if depth > 16:
                        raise ValueError
                elif byte in (93, 125):
                    depth -= 1
            body = json.loads(
                raw,
                object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if type(body) is not dict or set(body) != keys:
                raise ValueError
            return body
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise _error(
                400, "INVALID_REQUEST", "La solicitud de revisión no es válida."
            ) from None

    async def _call(self, client, method: str, *arguments):
        try:
            return await getattr(client, method)(*arguments)
        except Exception as error:
            code = getattr(error, "reason_code", getattr(error, "code", ""))
            if type(code) is str and code in MESSAGES:
                status, message = MESSAGES[code]
                raise _error(status, code, message) from None
            raise _error(
                503,
                "REVIEW_UNAVAILABLE",
                "No se pudo verificar la revisión. "
                "Consulta su estado antes de reintentar.",
            ) from None

    async def view_context(self, request: Request, session_id: str) -> dict:
        """HTML hook; an unavailable backend stays visibly closed."""
        self._navigation(request)
        token = request.session.get(CSRF_KEY)
        if type(token) is not str or not token:
            token = secrets.token_urlsafe(32)
            request.session[CSRF_KEY] = token
        client, account, session_uuid = await self._context(request, session_id)
        view = await self._call(client, "view", account, session_uuid)
        if type(view) is not dict or view.get("session_id") != str(session_uuid):
            raise _error(
                503,
                "REVIEW_UNAVAILABLE",
                "No se pudo verificar el alcance de la revisión.",
            )
        return {"staff_review_view": view, "staff_review_csrf": token}

    async def prepare_request(self, request: Request, session_id: str) -> JSONResponse:
        """Root dispatches staff JSON edits here before opening a write transaction."""
        self._mutation(request)
        body = await self._body(request, {"expected_proposal_id", "changes"})
        proposal = (
            _uuid(body["expected_proposal_id"])
            if body["expected_proposal_id"] is not None
            else None
        )
        changes = _proposal_changes(body["changes"])
        client, account, session_uuid = await self._context(request, session_id)
        result = await self._call(
            client, "prepare", account, session_uuid, proposal, changes
        )
        return JSONResponse(result, headers=PRIVATE_HEADERS)


def register_staff_review_routes(app, **dependencies) -> StaffReviewRoutes:
    """Register only adapters; no backend, pool, account or credential is created."""
    controller = StaffReviewRoutes(**dependencies)

    @app.get(
        "/api/registration-review/{session_id}/staff-approval", include_in_schema=False
    )
    async def view(request: Request, session_id: str):
        context = await controller.view_context(request, session_id)
        return JSONResponse(context, headers=PRIVATE_HEADERS)

    @app.get(
        "/api/registration-review/{session_id}/staff-approval/status",
        include_in_schema=False,
    )
    async def status(request: Request, session_id: str, attempt_id: str):
        attempt = _uuid(attempt_id)
        client, account, session_uuid = await controller._context(request, session_id)
        result = await controller._call(
            client, "status", account, session_uuid, attempt
        )
        return JSONResponse(result, headers=PRIVATE_HEADERS)

    @app.get(
        "/api/registration-review/{session_id}/staff-approval/source/"
        "{proposal_id}/{page_index}",
        include_in_schema=False,
    )
    async def source(
        request: Request, session_id: str, proposal_id: str, page_index: int
    ):
        proposal = _uuid(proposal_id)
        if not 1 <= page_index <= 10000:
            raise _error(400, "INVALID_REFERENCE", "La página no es válida.")
        client, account, session_uuid = await controller._context(request, session_id)
        raw = await controller._call(
            client, "source_page", account, session_uuid, proposal, page_index
        )
        if (
            type(raw) is not bytes
            or not raw.startswith(b"\x89PNG\r\n\x1a\n")
            or not 0 < len(raw) <= MAX_PNG_BYTES
        ):
            raise _error(
                503, "SOURCE_UNAVAILABLE", "La página no está disponible para revisar."
            )
        return Response(raw, media_type="image/png", headers=PRIVATE_HEADERS)

    @app.post(
        "/api/registration-review/{session_id}/staff-approval/challenge",
        include_in_schema=False,
    )
    async def challenge(request: Request, session_id: str):
        controller._mutation(request)
        body = await controller._body(request, {"proposal_id", "confirmed"})
        proposal = _uuid(body["proposal_id"])
        if body["confirmed"] is not True:
            raise _error(
                400,
                "CONFIRMATION_REQUIRED",
                "Confirma que revisaste el plan y sus fuentes.",
            )
        client, account, session_uuid = await controller._context(request, session_id)
        result = await controller._call(
            client, "challenge", account, session_uuid, proposal, True
        )
        return JSONResponse(result, headers=PRIVATE_HEADERS)

    @app.post(
        "/api/registration-review/{session_id}/staff-approval/assertion",
        include_in_schema=False,
    )
    async def assertion(request: Request, session_id: str):
        controller._mutation(request)
        body = await controller._body(request, {"attempt_id", "assertion"})
        attempt = _uuid(body["attempt_id"])
        if body["assertion"] is not None and type(body["assertion"]) is not dict:
            raise _error(
                400, "INVALID_REQUEST", "La respuesta de passkey no es válida."
            )
        encoded = (
            json.dumps(
                body["assertion"], separators=(",", ":"), ensure_ascii=True
            ).encode()
            if body["assertion"] is not None
            else None
        )
        if encoded is not None and len(encoded) > 65536:
            raise _error(
                413, "REQUEST_TOO_LARGE", "La respuesta de passkey es demasiado grande."
            )
        client, account, session_uuid = await controller._context(request, session_id)
        result = await controller._call(
            client, "assert_and_capture", account, session_uuid, attempt, encoded
        )
        return JSONResponse(result, headers=PRIVATE_HEADERS)

    @app.post(
        "/api/registration-review/{session_id}/staff-passkey/begin",
        include_in_schema=False,
    )
    async def enrollment_begin(request: Request, session_id: str):
        controller._mutation(request)
        await controller._body(request, set())
        client, account, session_uuid = await controller._context(request, session_id)
        result = await controller._call(
            client, "enrollment_begin", account, session_uuid
        )
        return JSONResponse(result, headers=PRIVATE_HEADERS)

    @app.post(
        "/api/registration-review/{session_id}/staff-passkey/complete",
        include_in_schema=False,
    )
    async def enrollment_complete(request: Request, session_id: str):
        controller._mutation(request)
        body = await controller._body(request, {"enrollment_id", "registration"})
        enrollment_id = _uuid(body["enrollment_id"])
        if type(body["registration"]) is not dict:
            raise _error(
                400, "INVALID_REQUEST", "La respuesta de registro no es válida."
            )
        client, account, session_uuid = await controller._context(request, session_id)
        result = await controller._call(
            client,
            "enrollment_complete",
            account,
            session_uuid,
            enrollment_id,
            body["registration"],
        )
        return JSONResponse(result, headers=PRIVATE_HEADERS)

    @app.post(
        "/api/registration-review/{session_id}/staff-passkey/status",
        include_in_schema=False,
    )
    async def enrollment_status(request: Request, session_id: str):
        controller._mutation(request)
        body = await controller._body(request, {"enrollment_id"})
        enrollment_id = _uuid(body["enrollment_id"])
        client, account, session_uuid = await controller._context(request, session_id)
        result = await controller._call(
            client, "enrollment_status", account, session_uuid, enrollment_id
        )
        return JSONResponse(result, headers=PRIVATE_HEADERS)

    return controller
