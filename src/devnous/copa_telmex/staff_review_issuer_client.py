"""Client for the explicitly configured private loopback review issuer."""

import ipaddress
import time
from urllib.parse import urlsplit
from uuid import uuid4

from .staff_review_channel import (
    MAC_HEADER,
    MAX_RESPONSE_BYTES,
    NONCE_HEADER,
    PATH,
    RESPONSE_HEADER,
    TIME_HEADER,
    decode_review_response,
    encode_review_request,
    request_mac,
)


class ReviewIssuerUnavailable(Exception):
    """No authenticated identity proof was received; issuance may have committed."""


class ReviewIssuerClient:
    """Never obtain URL, channel key or HTTP client from browser parameters."""

    def __init__(self, *, url, channel_key, http_client, timeout_seconds=10):
        try:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "http"
                or not ipaddress.ip_address(parsed.hostname).is_loopback
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is None
                or type(channel_key) is not bytes
                or len(channel_key) < 32
                or getattr(http_client, "trust_env", None) is not False
                or type(timeout_seconds) not in (int, float)
                or not 0 < timeout_seconds <= 60
            ):
                raise ValueError("invalid private configuration")
            self._url = url.rstrip("/") + PATH
            self._key, self._client = channel_key, http_client
            self._timeout = timeout_seconds
        except Exception:
            raise ReviewIssuerUnavailable("review issuer unavailable") from None

    async def issue(self, attempt_id, assertion: bytes) -> bytes:
        try:
            body = encode_review_request(attempt_id, assertion)
            nonce, timestamp = uuid4(), int(time.time())
            headers = {
                "Content-Type": "application/json",
                TIME_HEADER: str(timestamp),
                NONCE_HEADER: str(nonce),
                MAC_HEADER: request_mac(self._key, nonce, timestamp, body),
            }
            async with self._client.stream(
                "POST",
                self._url,
                content=body,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("content-encoding", "identity")
                    != "identity"
                    or len(response.headers.get_list(RESPONSE_HEADER)) != 1
                ):
                    raise ValueError("unauthenticated response")
                parts, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ValueError("response limit")
                    parts.append(chunk)
                return decode_review_response(
                    self._key,
                    nonce,
                    body,
                    b"".join(parts),
                    response.headers[RESPONSE_HEADER],
                )
        except Exception:
            raise ReviewIssuerUnavailable("review issuer unavailable") from None
