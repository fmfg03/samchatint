"""Explicit private v2 application factory; not mounted by the live v1 app.

An authenticated channel proves the caller, never a reviewer's authority.
The configured issuer resolves and verifies that independently from PostgreSQL.
"""

import asyncio
import time

from fastapi import FastAPI, HTTPException, Request, Response

from devnous.copa_telmex.staff_review_channel import (
    MAC_HEADER,
    MAX_REQUEST_BYTES,
    NONCE_HEADER,
    PATH,
    RESPONSE_HEADER,
    TIME_HEADER,
    decode_review_request,
    encode_review_response,
    response_mac,
    verify_request,
)
from devnous.copa_telmex.staff_capture_transport_store import PostgresTransportNonces
from staff_review_issuer_v2 import PostgresReviewAssertionIssuer


def create_review_issuer_app(
    *,
    issuer: PostgresReviewAssertionIssuer,
    channel_key: bytes,
    consume_nonce,
    max_clock_skew=30,
    timeout_seconds=10,
    max_inflight=8,
) -> FastAPI:
    if (
        not isinstance(issuer, PostgresReviewAssertionIssuer)
        or type(channel_key) is not bytes
        or len(channel_key) < 32
        or not isinstance(consume_nonce, PostgresTransportNonces)
        or type(max_clock_skew) is not int
        or not 1 <= max_clock_skew <= 60
        or type(timeout_seconds) not in (int, float)
        or not 0 < timeout_seconds <= 60
        or type(max_inflight) is not int
        or not 1 <= max_inflight <= 128
    ):
        raise ValueError("invalid private review configuration")
    issuer.validate_channel_key(channel_key)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    semaphore = asyncio.Semaphore(max_inflight)

    @app.post(PATH)
    async def issue(request: Request):
        if semaphore.locked():
            raise HTTPException(status_code=503, detail="review issuer unavailable")
        async with semaphore:
            try:
                async with asyncio.timeout(timeout_seconds):
                    names = (TIME_HEADER, NONCE_HEADER, MAC_HEADER)
                    if any(len(request.headers.getlist(name)) != 1 for name in names):
                        raise HTTPException(
                            status_code=401, detail="review request denied"
                        )
                    if (
                        request.headers.get("content-type", "").split(";", 1)[0]
                        != "application/json"
                        or request.headers.get("content-encoding", "identity")
                        != "identity"
                    ):
                        raise HTTPException(
                            status_code=401, detail="review request denied"
                        )
                    chunks, size = [], 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_REQUEST_BYTES:
                            raise HTTPException(
                                status_code=413, detail="review request denied"
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    try:
                        nonce, expiry = verify_request(
                            channel_key,
                            request.headers[NONCE_HEADER],
                            request.headers[TIME_HEADER],
                            request.headers[MAC_HEADER],
                            body,
                            now=int(time.time()),
                            skew=max_clock_skew,
                        )
                        attempt, assertion = decode_review_request(body)
                    except Exception:
                        raise HTTPException(
                            status_code=401, detail="review request denied"
                        ) from None
                    # This callback commits in an independent channel transaction.
                    # It must be the protected durable nonce store in deployment.
                    if await consume_nonce(nonce, expiry) is not True:
                        raise HTTPException(
                            status_code=401, detail="review request denied"
                        )
                    proof = await issuer.issue(attempt, assertion)
                    result = encode_review_response(proof)
                    return Response(
                        result,
                        media_type="application/json",
                        headers={
                            RESPONSE_HEADER: response_mac(
                                channel_key, nonce, body, result
                            ),
                            "Cache-Control": "no-store",
                        },
                    )
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(
                    status_code=503, detail="review issuer unavailable"
                ) from None

    return app
