"""
Tocino.AI API client implementation for samchat expense management.

Provides a reusable client for sending ticket data for OCR and CFDI processing,
with structured error handling and logging.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# Tocino external API (see bacon.tocino.ai docs)
TICKETS_SUBMIT_PATH = "/api/external/tickets/"


TOCINO_ERROR_MISSING_CONFIG = "missing_config"
TOCINO_ERROR_AUTH = "auth_error"
TOCINO_ERROR_VALIDATION = "validation_error"
TOCINO_ERROR_RATE_LIMITED = "rate_limited"
TOCINO_ERROR_API_UNAVAILABLE = "api_unavailable"
TOCINO_ERROR_BAD_RESPONSE = "bad_response"
TOCINO_ERROR_UNKNOWN = "unknown_error"


class TocinoAPIError(Exception):
    """Raised when Tocino cannot accept or process a request.

    ``category`` is intentionally coarse and stable so routes/workers can tell
    credentials problems apart from upstream downtime without parsing messages.
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        response_text: Optional[str] = None,
        category: Optional[str] = None,
        user_message: Optional[str] = None,
        retryable: Optional[bool] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.category = category or classify_tocino_status(status_code)
        self.user_message = user_message or tocino_user_message(self.category, message)
        self.retryable = bool(retryable) if retryable is not None else self.category in {
            TOCINO_ERROR_API_UNAVAILABLE,
            TOCINO_ERROR_RATE_LIMITED,
        }


def classify_tocino_status(status_code: int) -> str:
    """Return a stable operational category for a Tocino HTTP/status failure."""

    status = int(status_code or 0)
    if status == 0:
        return TOCINO_ERROR_API_UNAVAILABLE
    if status in (401, 403):
        return TOCINO_ERROR_AUTH
    if status == 429:
        return TOCINO_ERROR_RATE_LIMITED
    if status in (400, 404, 409, 422):
        return TOCINO_ERROR_VALIDATION
    if 500 <= status <= 599:
        return TOCINO_ERROR_API_UNAVAILABLE
    return TOCINO_ERROR_UNKNOWN


def tocino_user_message(category: str, detail: str = "") -> str:
    """Spanish operator-facing message for Tocino failures without secrets."""

    clean_detail = str(detail or "").strip()
    suffix = f" Detalle: {clean_detail}" if clean_detail else ""
    if category == TOCINO_ERROR_MISSING_CONFIG:
        return "Facturaci?n Tocino no est? configurada en el servidor."
    if category == TOCINO_ERROR_AUTH:
        return "Tocino rechaz? las credenciales configuradas; revisa TOCINO_API_KEY."
    if category == TOCINO_ERROR_VALIDATION:
        return "Tocino recibi? la solicitud pero rechaz? los datos enviados." + suffix
    if category == TOCINO_ERROR_RATE_LIMITED:
        return "Tocino est? limitando temporalmente las solicitudes; intenta de nuevo en unos minutos."
    if category == TOCINO_ERROR_API_UNAVAILABLE:
        return "Tocino no respondi? correctamente; parece ca?da o intermitencia del servicio."
    if category == TOCINO_ERROR_BAD_RESPONSE:
        return "Tocino respondi? con un formato inesperado." + suffix
    return "No se pudo completar la solicitud con Tocino." + suffix


@dataclass
class TocinoClient:
    """Client for interacting with Tocino.AI's OCR/CFDI API."""

    api_key: str
    base_url: str

    def __init__(self, api_key: str = None, base_url: str = None) -> None:
        """Initialize Tocino client with API key and base URL.

        If not provided, will attempt to load from environment variables:
        - TOCINO_API_KEY
        - TOCINO_BASE_URL
        """
        self.api_key = api_key or os.getenv("TOCINO_API_KEY", "")
        self.base_url = (base_url or os.getenv("TOCINO_BASE_URL", "")).rstrip("/")

        if not self.api_key:
            raise ValueError("TOCINO_API_KEY must be provided or set in environment")
        if not self.base_url:
            raise ValueError("TOCINO_BASE_URL must be provided or set in environment")

    def _build_headers(self) -> Dict[str, str]:
        """Build required headers including API key and idempotency."""

        headers = {
            "X-API-KEY": self.api_key,
            "Idempotency-Key": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }
        return headers

    def submit_ticket(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Submit a ticket payload to Tocino's ticket-invoice endpoint.

        Returns parsed JSON for 200 responses; raises TocinoAPIError for 4xx/5xx.
        """

        endpoint = f"{self.base_url}{TICKETS_SUBMIT_PATH}"
        headers = self._build_headers()

        logger.info("Sending ticket to Tocino", extra={"endpoint": endpoint})
        logger.debug("Tocino request headers", extra={"headers": {k: v for k, v in headers.items() if k != "X-API-KEY"}})
        logger.debug("Tocino request payload", extra={"payload": {k: v for k, v in payload.items() if k != "file"}})

        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        except requests.RequestException as exc:
            logger.exception("Network error while calling Tocino API")
            raise TocinoAPIError(
                str(exc),
                status_code=0,
                category=TOCINO_ERROR_API_UNAVAILABLE,
            ) from exc

        logger.info(
            "Received response from Tocino",
            extra={"status_code": response.status_code, "request_id": response.headers.get("X-Request-ID")},
        )

        # Attempt to parse JSON regardless of status; fallback to text
        parsed: Optional[Dict[str, Any]] = None
        try:
            parsed = response.json()
        except ValueError:
            logger.warning("Tocino response is not valid JSON; falling back to text")

        if response.status_code == 200:
            return parsed if isinstance(parsed, dict) else {"raw": response.text}

        # Handle error statuses
        message = ""
        if isinstance(parsed, dict):
            # Common patterns: {"message": "..."} or field-level errors
            if "message" in parsed and isinstance(parsed["message"], str):
                message = parsed["message"]
            else:
                message = json.dumps(parsed)
        else:
            message = response.text

        logger.error(
            "Tocino API error",
            extra={"status_code": response.status_code, "error_message": message},
        )

        category = classify_tocino_status(response.status_code)
        raise TocinoAPIError(
            message=message,
            status_code=response.status_code,
            response_text=response.text,
            category=category,
        )

    def check_invoice_status(self, ticket_id: str) -> Dict[str, Any]:
        """Check the status of a ticket by ticket_id.

        Tocino API: GET https://bacon.tocino.ai/api/external/tickets/:TICKET_ID/

        Returns:
            {
                "status": "invoicing" | "No facturable" | "Finalizado" | ...,
                "invoice": {...} | null,
                "file_url": "...",
                "ticket_data": {...},
                "created_at": "...",
                "idempotency_key": "...",
                "ticket_id": "..."
            }
        """
        endpoint = f"{self.base_url}/api/external/tickets/{ticket_id}/"
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

        logger.info("Checking invoice status with Tocino", extra={"ticket_id": ticket_id, "endpoint": endpoint})

        try:
            response = requests.get(endpoint, headers=headers, timeout=60)

            # Parse response
            try:
                data = response.json()
            except ValueError:
                logger.warning("Tocino status response is not valid JSON")
                raise TocinoAPIError(
                    "Invalid JSON response",
                    status_code=response.status_code,
                    response_text=response.text,
                    category=TOCINO_ERROR_BAD_RESPONSE,
                    retryable=response.status_code >= 500,
                )

            logger.info("Received invoice status", extra={
                "ticket_id": ticket_id,
                "status": data.get("status"),
                "status_code": response.status_code
            })

            if response.status_code == 200:
                return data
            else:
                message = data.get("detail") or data.get("message") or data.get("error") or str(data)
                logger.error("Tocino status check failed", extra={"status_code": response.status_code, "error_message": message})
                raise TocinoAPIError(
                    message=message,
                    status_code=response.status_code,
                    response_text=str(data),
                    category=classify_tocino_status(response.status_code),
                )

        except requests.RequestException as exc:
            logger.error("Error checking invoice status", extra={"ticket_id": ticket_id, "error": str(exc)})
            raise TocinoAPIError(
                str(exc),
                status_code=0,
                category=TOCINO_ERROR_API_UNAVAILABLE,
            ) from exc


def get_tocino_client(api_key: str = None, base_url: str = None) -> TocinoClient:
    """Get a TocinoClient instance, using environment variables if not provided."""
    return TocinoClient(api_key=api_key, base_url=base_url)


def test_connection(sample_payload: Optional[Dict[str, Any]] = None) -> bool:
    """Helper for making a dry-run call to validate connectivity and credentials.

    If `sample_payload` is provided, it will be sent; otherwise a minimal payload
    with obviously invalid data is used to exercise request/response handling.

    Returns True if a 200 response is received, otherwise logs the error and returns False.
    """

    try:
        client = get_tocino_client()
    except (ValueError, Exception) as e:
        logger.warning(f"Failed to create Tocino client: {e}")
        return False

    # Build a minimal, likely-to-fail payload if none provided; purpose is reachability.
    payload: Dict[str, Any] = sample_payload or {
        "tax_id": "TEST123456789",
        "taxpayer": "Test Taxpayer",
        "taxpayer_name": "Test",
        "taxpayer_last_name": "User",
        "taxpayer_second_last_name": "Sample",
        "street_address_1": "Calle Falsa",
        "ext_num": "123",
        "int_num": "",
        "street_address_2": "Centro",
        "city": "CDMX",
        "state": "CDMX",
        "country": "México",
        "postal_code": "01234",
        "fiscal_regimen_code": "601",
        "cfdi_use_code": "G03",
        "csf_pdf": "",
        "filename": "ticket.jpg",
        "file": "",
    }

    try:
        result = client.submit_ticket(payload)
        logger.info("Tocino test connection succeeded", extra={"result": result})
        return True
    except TocinoAPIError as exc:
        logger.warning("Tocino test connection failed", extra={"status_code": exc.status_code, "error_message": str(exc)})
        return False
