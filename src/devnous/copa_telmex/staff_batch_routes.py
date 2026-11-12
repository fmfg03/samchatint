"""Session-bound presentation adapters for private, explicitly admitted batches.

This module creates no pools, source admissions, authority, or capture rows. All
object/edition/capability decisions are repeated by the private workflow.
"""

from __future__ import annotations

import inspect
import json
import re
import secrets
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .staff_review_routes import PRIVATE_HEADERS, StaffReviewRoutes, _error, _uuid

CSRF_KEY = "ctt_batch_review_csrf"
CSRF_HEADER = "X-CTT-BATCH-CSRF"
MAX_BODY_BYTES = 1024 * 1024
MAX_PNG_BYTES = 16 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SENSITIVE_KEYS = frozenset(
    {
        "proof",
        "signature",
        "attestation",
        "child_authorization",
        "private_key",
        "signing_key",
        "channel_key",
        "signed_proof",
        "consent_proof",
        "consent_envelope",
        "signed_consent",
        "proof_bytes",
        "gate_proof",
        "verification_proof",
        "child_proof",
        "assertion_proof",
        "authorization_envelope",
        "attestation_signature",
        "parent_proof_bytes",
        "verification_proof_bytes",
        "root_bytes",
        "scope_bytes",
        "review_bytes",
        "evidence_bytes",
        "validation_bytes",
        "gate_envelope",
        "preauth_bytes",
        "proof_base64",
        "parent_consent",
        "signed_review",
        "signing_seed",
    }
)
MESSAGES = {
    "SCOPE_DENIED": (403, "El lote no pertenece a tu facultad vigente."),
    "ACCOUNT_NOT_BOUND": (403, "Tu cuenta no está acreditada para esta edición."),
    "BATCH_CAPABILITY_REQUIRED": (
        403,
        "Tu cuenta todavía no tiene habilitada la autorización por lotes.",
    ),
    "STALE_PROPOSAL": (409, "El plan cambió. Consulta el lote antes de continuar."),
    "REVIEW_STALE": (409, "El plan cambió. Consulta el lote antes de continuar."),
    "REVIEW_BLOCKED": (409, "El lote tiene fuentes o excepciones pendientes."),
    "REVIEW_EXPIRED": (
        409,
        "La autorización venció. Prepara otro bloque de los pendientes.",
    ),
    "REVIEW_REVOKED": (409, "Esta autorización fue revocada y no puede reanudarse."),
    "PASSKEY_PENDING_ACTIVATION": (
        409,
        "La passkey está pendiente de activación por el responsable.",
    ),
    "ENROLLMENT_REQUIRED": (
        409,
        "Necesitas una passkey activa para autorizar el lote.",
    ),
    "SOURCE_UNAVAILABLE": (409, "La fuente no está disponible para contrastarla."),
    "BUDGET_REQUIRED": (
        409,
        "La preparación necesita un límite de consumo autorizado.",
    ),
    "BATCH_BUSY": (409, "Hay otro bloque pendiente de terminar o consultar."),
    "QUALITY_FALSE_ADMISSION_REPORT_REQUIRED": (
        409,
        "Este equipo figuraba como apto. Informa la discrepancia crítica para "
        "suspender su grupo; no se puede corregir bajo la autorización anterior.",
    ),
}


def _sha(value: Any) -> str:
    if type(value) is not str or not SHA256.fullmatch(value):
        raise _error(400, "INVALID_REFERENCE", "La referencia de fuente no es válida.")
    return value


def _integer(value: Any, minimum: int = 0, maximum: int = 2**53 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _error(400, "INVALID_REQUEST", "La versión o el límite no es válido.")
    return value


def _references(values: Any, *, hashed: bool = False, maximum: int = 300) -> list:
    if type(values) is not list or not 0 < len(values) <= maximum:
        raise _error(400, "INVALID_REQUEST", "Selecciona un conjunto válido.")
    validated = [(_sha(item) if hashed else str(_uuid(item))) for item in values]
    if len(set(validated)) != len(validated):
        raise _error(
            400, "INVALID_REQUEST", "La selección contiene referencias repetidas."
        )
    return validated


def _safe_result(value: Any) -> dict:
    """Refuse accidentally returned private envelopes instead of publishing them."""

    def check(item, depth=0):
        if depth > 24:
            raise ValueError
        if type(item) is dict:
            if any(
                type(key) is not str
                or key.lower() in SENSITIVE_KEYS
                or key.lower().endswith(
                    (
                        "_bytes",
                        "_proof",
                        "_envelope",
                        "_signature",
                        "_private_key",
                        "_signing_seed",
                    )
                )
                for key in item
            ):
                raise ValueError
            for child in item.values():
                check(child, depth + 1)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError

    try:
        if type(value) is not dict:
            raise ValueError
        check(value)
        json.dumps(value, allow_nan=False)
        return value
    except (ValueError, TypeError, RecursionError):
        raise _error(
            503, "BATCH_UNAVAILABLE", "No se recibió un estado publicable del lote."
        ) from None


class StaffBatchRoutes:
    """Thin HTTP boundary; the configured private client owns each operation."""

    def __init__(self, *, templates=None, **dependencies):
        self._security = StaffReviewRoutes(**dependencies)
        self._templates = templates

    def _token(self, request: Request) -> str:
        token = request.session.get(CSRF_KEY)
        if type(token) is not str or not token:
            token = secrets.token_urlsafe(32)
            request.session[CSRF_KEY] = token
        return token

    def _mutation(self, request: Request) -> None:
        self._security._navigation(request)
        origins = request.headers.getlist("origin")
        if len(origins) != 1 or origins[0] != self._security._origin:
            raise _error(
                403, "ORIGIN_DENIED", "La acción debe realizarse desde esta aplicación."
            )
        tokens = request.headers.getlist(CSRF_HEADER)
        expected = request.session.get(CSRF_KEY)
        if (
            type(expected) is not str
            or len(tokens) != 1
            or not expected.isascii()
            or not tokens[0].isascii()
            or not secrets.compare_digest(expected, tokens[0])
        ):
            raise _error(403, "CSRF_DENIED", "Recarga el lote antes de continuar.")

    async def _context(self, request, object_id):
        return await self._security._context(request, object_id)

    async def _catalog_context(self, request):
        self._security._navigation(request)
        initial = self._security._actor(request)
        account = _uuid(str(initial.get("user_id") or ""))
        if _uuid(str(request.session.get("empleado_id") or "")) != account:
            raise _error(403, "ACTOR_CHANGED", "La cuenta no coincide con tu sesión.")
        try:
            async with self._security._sessions() as session:
                current = await self._security._refresh(session, initial)
        except HTTPException as error:
            error.headers = {**(error.headers or {}), **PRIVATE_HEADERS}
            raise
        except Exception:
            raise _error(
                503, "BATCH_UNAVAILABLE", "No se pudo verificar tu sesión."
            ) from None
        if _uuid(str(current.get("user_id") or "")) != account:
            raise _error(403, "ACTOR_CHANGED", "La cuenta no coincide con tu sesión.")
        client = self._security._resolve(request)
        if inspect.isawaitable(client):
            client = await client
        if client is None:
            raise _error(
                503,
                "BATCH_UNAVAILABLE",
                "La captura por lotes aún no está configurada.",
            )
        return client, account, None

    async def _call(self, client, method, *args):
        try:
            return await getattr(client, method)(*args)
        except Exception as error:
            code = getattr(error, "reason_code", getattr(error, "code", ""))
            if type(code) is str and code in MESSAGES:
                status, message = MESSAGES[code]
                raise _error(status, code, message) from None
            raise _error(
                503,
                "BATCH_UNAVAILABLE",
                "No se pudo verificar el lote. Consulta su estado antes de reintentar.",
            ) from None

    async def _body(self, request, keys):
        if (
            request.headers.get("content-type", "").split(";", 1)[0]
            != "application/json"
        ):
            raise _error(415, "JSON_REQUIRED", "Esta acción requiere datos JSON.")
        parts, total = [], 0
        async for part in request.stream():
            total += len(part)
            if total > MAX_BODY_BYTES:
                raise _error(
                    413, "REQUEST_TOO_LARGE", "La solicitud es demasiado grande."
                )
            parts.append(part)

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        try:
            raw = b"".join(parts)
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
            data = json.loads(
                raw,
                object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if type(data) is not dict or set(data) != set(keys):
                raise ValueError
            return data
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise _error(
                400, "INVALID_REQUEST", "La solicitud del lote no es válida."
            ) from None

    async def catalog_context(self, request):
        client, account, _ = await self._catalog_context(request)
        result = _safe_result(await self._call(client, "catalog", account))
        return {"batch_catalog": result, "batch_review_csrf": self._token(request)}

    async def view_context(self, request, run_id, offset=0, limit=50):
        _integer(offset, maximum=1_000_000)
        _integer(limit, minimum=1, maximum=100)
        client, account, run = await self._context(request, run_id)
        result = _safe_result(
            await self._call(client, "view", account, run, offset, limit)
        )
        if result.get("run_id") != str(run):
            raise _error(
                503, "BATCH_UNAVAILABLE", "El estado no corresponde a este lote."
            )
        return {"batch_review_view": result, "batch_review_csrf": self._token(request)}

    async def _result(self, client, method, account, object_id, body, scope_key):
        result = _safe_result(
            await self._call(client, method, account, object_id, body)
        )
        if result.get(scope_key) != str(object_id):
            raise _error(
                503,
                "BATCH_UNAVAILABLE",
                "La respuesta no corresponde a esta operación.",
            )
        return JSONResponse(result, headers=PRIVATE_HEADERS)


def register_staff_batch_routes(app, *, templates=None, **dependencies):
    """Mount public adapters only. No configured client means closed operations."""
    controller = StaffBatchRoutes(templates=templates, **dependencies)
    prefix = "/api/registration-review-batches"

    @app.get(prefix + "/catalog", include_in_schema=False)
    async def catalog(request: Request):
        return JSONResponse(
            await controller.catalog_context(request), headers=PRIVATE_HEADERS
        )

    @app.get(prefix + "/runs/{run_id}", include_in_schema=False)
    async def view(request: Request, run_id: str, offset: int = 0, limit: int = 50):
        return JSONResponse(
            await controller.view_context(request, run_id, offset, limit),
            headers=PRIVATE_HEADERS,
        )

    @app.post(prefix + "/runs", include_in_schema=False)
    async def create_run(request: Request):
        controller._mutation(request)
        data = await controller._body(
            request, {"edition_id", "source_manifest_hashes", "idempotency_key"}
        )
        _uuid(data["edition_id"])
        _uuid(data["idempotency_key"])
        _references(data["source_manifest_hashes"], hashed=True, maximum=16)
        client, account, _ = await controller._catalog_context(request)
        result = _safe_result(
            await controller._call(client, "create_run", account, data)
        )
        _uuid(result.get("run_id"))
        return JSONResponse(result, headers=PRIVATE_HEADERS)

    @app.post(prefix + "/runs/{run_id}/sample", include_in_schema=False)
    async def sample(request: Request, run_id: str):
        controller._mutation(request)
        data = await controller._body(request, {"expected_run_version", "observations"})
        _integer(data["expected_run_version"])
        observations = data["observations"]
        if type(observations) is not list or not 1 <= len(observations) <= 300:
            raise _error(400, "INVALID_REQUEST", "La muestra no es válida.")
        documents = set()
        for observation in observations:
            if type(observation) is not dict or set(observation) != {
                "document_id",
                "outcome",
                "notes",
            }:
                raise _error(
                    400,
                    "INVALID_REQUEST",
                    "El contraste contiene campos no permitidos.",
                )
            document = _sha(observation["document_id"])
            if document in documents:
                raise _error(
                    400, "INVALID_REQUEST", "La muestra contiene una cédula repetida."
                )
            documents.add(document)
            if type(observation["outcome"]) is not str or observation[
                "outcome"
            ] not in {"matches", "critical_error", "exception_confirmed"}:
                raise _error(
                    400, "INVALID_REQUEST", "Indica el resultado del contraste."
                )
            if (
                type(observation["notes"]) is not str
                or len(observation["notes"]) > 2000
            ):
                raise _error(
                    400, "INVALID_REQUEST", "La observación es demasiado larga."
                )
        client, account, run = await controller._context(request, run_id)
        return await controller._result(client, "sample", account, run, data, "run_id")

    @app.post(prefix + "/runs/{run_id}/prepare", include_in_schema=False)
    async def prepare(request: Request, run_id: str):
        controller._mutation(request)
        data = await controller._body(
            request, {"expected_run_version", "member_ids", "idempotency_key"}
        )
        _integer(data["expected_run_version"])
        _references(data["member_ids"], maximum=50)
        _uuid(data["idempotency_key"])
        client, account, run = await controller._context(request, run_id)
        return await controller._result(client, "prepare", account, run, data, "run_id")

    @app.post(prefix + "/blocks/{block_id}/challenge", include_in_schema=False)
    async def challenge(request: Request, block_id: str):
        controller._mutation(request)
        data = await controller._body(request, {"expected_block_version", "confirmed"})
        _integer(data["expected_block_version"])
        if data["confirmed"] is not True:
            raise _error(
                400,
                "CONFIRMATION_REQUIRED",
                "Confirma el conjunto exacto antes de autorizar.",
            )
        client, account, block = await controller._context(request, block_id)
        return await controller._result(
            client, "challenge", account, block, data, "block_id"
        )

    @app.post(prefix + "/blocks/{block_id}/assertion", include_in_schema=False)
    async def assertion(request: Request, block_id: str):
        controller._mutation(request)
        data = await controller._body(request, {"attempt_id", "assertion"})
        _uuid(data["attempt_id"])
        if (
            type(data["assertion"]) is not dict
            or len(json.dumps(data["assertion"])) > 65536
        ):
            raise _error(
                400, "INVALID_REQUEST", "La respuesta de passkey no es válida."
            )
        client, account, block = await controller._context(request, block_id)
        return await controller._result(
            client, "assertion", account, block, data, "block_id"
        )

    def transition(name):
        async def action(request: Request, block_id: str):
            controller._mutation(request)
            data = await controller._body(
                request, {"expected_version", "idempotency_key"}
            )
            _integer(data["expected_version"])
            _uuid(data["idempotency_key"])
            client, account, block = await controller._context(request, block_id)
            return await controller._result(
                client, name, account, block, data, "block_id"
            )

        action.__name__ = "batch_" + name
        app.post(prefix + "/blocks/{block_id}/" + name, include_in_schema=False)(action)

    for operation in ("pause", "resume", "revoke"):
        transition(operation)

    @app.get(
        prefix + "/blocks/{block_id}/attempts/{attempt_id}", include_in_schema=False
    )
    async def attempt(request: Request, block_id: str, attempt_id: str):
        selected = _uuid(attempt_id)
        client, account, block = await controller._context(request, block_id)
        result = _safe_result(
            await controller._call(client, "attempt", account, block, selected)
        )
        if result.get("block_id") != str(block) or result.get("attempt_id") != str(
            selected
        ):
            raise _error(
                503,
                "BATCH_UNAVAILABLE",
                "El estado no corresponde al intento consultado.",
            )
        return JSONResponse(result, headers=PRIVATE_HEADERS)

    @app.get(prefix + "/runs/{run_id}/members/{member_id}", include_in_schema=False)
    async def member(request: Request, run_id: str, member_id: str):
        selected = _uuid(member_id)
        client, account, run = await controller._context(request, run_id)
        result = _safe_result(
            await controller._call(client, "member", account, run, selected)
        )
        if result.get("run_id") != str(run) or result.get("member_id") != str(selected):
            raise _error(
                503, "BATCH_UNAVAILABLE", "La cédula no corresponde al lote consultado."
            )
        return JSONResponse(result, headers=PRIVATE_HEADERS)

    @app.post(
        prefix + "/runs/{run_id}/members/{member_id}/corrections",
        include_in_schema=False,
    )
    async def corrections(request: Request, run_id: str, member_id: str):
        # Parse the closed pending value union before the private call.
        controller._mutation(request)
        data = await controller._body(request, {"expected_version", "edits"})
        _integer(data["expected_version"])
        edits = data["edits"]
        if type(edits) is not list or not 1 <= len(edits) <= 200:
            raise _error(
                400, "INVALID_REQUEST", "Propón al menos una corrección de datos."
            )
        fields = set()
        for edit in edits:
            if type(edit) is not dict or set(edit) != {"field_id", "value"}:
                raise _error(
                    400,
                    "INVALID_REQUEST",
                    "La corrección contiene campos no permitidos.",
                )
            field = edit["field_id"]
            value = edit["value"]
            if (
                type(field) is not str
                or not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", field)
                or field in fields
            ):
                raise _error(
                    400, "INVALID_REQUEST", "La referencia del dato no es válida."
                )
            fields.add(field)
            from .staff_batch_corrections import validate_edit_value
            from .staff_batch_store import BatchDenied

            try:
                validate_edit_value(field, value)
            except BatchDenied:
                raise _error(
                    400, "INVALID_REQUEST", "El valor propuesto no es válido."
                ) from None
        selected = _uuid(member_id)
        client, account, run = await controller._context(request, run_id)
        result = _safe_result(
            await controller._call(
                client, "propose_corrections", account, run, selected, data
            )
        )
        if result.get("run_id") != str(run) or result.get("member_id") != str(selected):
            raise _error(
                503, "BATCH_UNAVAILABLE", "La respuesta no corresponde a esta cédula."
            )
        return JSONResponse(result, headers=PRIVATE_HEADERS)

    @app.post(
        prefix + "/runs/{run_id}/members/{member_id}/quality-report",
        include_in_schema=False,
    )
    async def quality_report(request: Request, run_id: str, member_id: str):
        controller._mutation(request)
        data = await controller._body(request, {"expected_version", "reason"})
        _integer(data["expected_version"])
        if (
            type(data["reason"]) is not str
            or not data["reason"].strip()
            or len(data["reason"]) > 500
        ):
            raise _error(
                400,
                "INVALID_REQUEST",
                "Describe la discrepancia en un máximo de 500 caracteres.",
            )
        selected = _uuid(member_id)
        client, account, run = await controller._context(request, run_id)
        result = _safe_result(
            await controller._call(
                client, "report_quality_issue", account, run, selected, data
            )
        )
        if result.get("run_id") != str(run) or result.get("member_id") != str(selected):
            raise _error(
                503, "BATCH_UNAVAILABLE", "La respuesta no corresponde a esta cédula."
            )
        return JSONResponse(result, headers=PRIVATE_HEADERS)

    @app.get(
        prefix + "/runs/{run_id}/sources/{document_id}/pages/{page_index}",
        include_in_schema=False,
    )
    async def source(request: Request, run_id: str, document_id: str, page_index: int):
        _sha(document_id)
        _integer(page_index, minimum=1, maximum=4)
        client, account, run = await controller._context(request, run_id)
        raw = await controller._call(
            client, "source_page", account, run, document_id, page_index
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

    if templates is not None:

        @app.get("/registration-review-batches/new", include_in_schema=False)
        async def new_page(request: Request):
            context = await controller.catalog_context(request)
            return templates.TemplateResponse(
                request=request,
                name="registration_review_batch.html",
                context=context,
                headers=PRIVATE_HEADERS,
            )

        @app.get("/registration-review-batches/{run_id}", include_in_schema=False)
        async def run_page(request: Request, run_id: str):
            context = await controller.view_context(request, run_id)
            return templates.TemplateResponse(
                request=request,
                name="registration_review_batch.html",
                context=context,
                headers=PRIVATE_HEADERS,
            )

    return controller
