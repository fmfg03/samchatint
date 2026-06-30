"""
SAT CFDI Error Codes and Handling

This module defines all error codes from the SAT (Servicio de Administración Tributaria)
CFDI Web Service and provides handling strategies for each.

Source: Official SAT Documentation - "Servicio de Verificación de Descarga Masiva 2023"
Page 13: Error Code Table

Author: Copa Telmex Finance Integration Team
Date: 2025-10-10
"""

from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


class SATErrorCode(Enum):
    """
    SAT CFDI Web Service error codes.

    Categories:
    - 300-305: Authentication and validation errors
    - 5000: Success code
    - 5002-5011: Request and processing errors
    """

    # Authentication Errors (300-305) - NO RETRY
    USUARIO_NO_VALIDO = 300
    XML_MAL_FORMADO = 301
    SELLO_MAL_FORMADO = 302
    SELLO_NO_CORRESPONDE = 303
    CERTIFICADO_REVOCADO_CADUCO = 304  # CRITICAL!
    CERTIFICADO_NO_VALIDO = 305

    # Success Code
    SOLICITUD_RECIBIDA = 5000

    # Processing Errors (5002-5011)
    SIN_PRIVILEGIOS = 5002
    TOPE_MAXIMO = 5003
    NO_SE_ENCONTRO_INFO = 5004
    SOLICITUD_DUPLICADA = 5005
    SOLICITUD_VENCIDA = 5006
    NUMERO_SOLICITUDES_EXEDIDO = 5011

    @property
    def description_es(self) -> str:
        """Error description in Spanish."""
        return ERROR_DESCRIPTIONS_ES[self]

    @property
    def description_en(self) -> str:
        """Error description in English."""
        return ERROR_DESCRIPTIONS_EN[self]

    @property
    def is_retryable(self) -> bool:
        """Whether this error should trigger a retry."""
        return self in RETRYABLE_ERRORS

    @property
    def is_critical(self) -> bool:
        """Whether this error is critical (requires immediate admin attention)."""
        return self in CRITICAL_ERRORS

    @property
    def severity(self) -> str:
        """Error severity level."""
        return ERROR_SEVERITY[self]


class EstadoSolicitud(Enum):
    """
    Estado de solicitud values from SAT.

    These represent the processing state of a CFDI download request.
    """

    ACEPTADA = 1        # Accepted, not yet processed
    EN_PROCESO = 2      # Processing
    TERMINADA = 3       # Finished successfully
    ERROR = 4           # Processing error
    RECHAZADA = 5       # Rejected
    VENCIDA = 6         # Expired (>72 hours)

    @property
    def description_es(self) -> str:
        """Estado description in Spanish."""
        return ESTADO_DESCRIPTIONS_ES[self]

    @property
    def description_en(self) -> str:
        """Estado description in English."""
        return ESTADO_DESCRIPTIONS_EN[self]

    @property
    def should_retry(self) -> bool:
        """Whether to retry checking this estado."""
        return self in [EstadoSolicitud.ACEPTADA, EstadoSolicitud.EN_PROCESO]

    @property
    def is_final(self) -> bool:
        """Whether this is a final state (no more checking needed)."""
        return self in [EstadoSolicitud.TERMINADA, EstadoSolicitud.ERROR,
                       EstadoSolicitud.RECHAZADA, EstadoSolicitud.VENCIDA]


# Error Descriptions (Spanish)
ERROR_DESCRIPTIONS_ES = {
    SATErrorCode.USUARIO_NO_VALIDO: "Usuario inválido para realizar la petición",
    SATErrorCode.XML_MAL_FORMADO: "XML mal formado",
    SATErrorCode.SELLO_MAL_FORMADO: "Sello mal formado o inválido",
    SATErrorCode.SELLO_NO_CORRESPONDE: "Sello no corresponde al certificado",
    SATErrorCode.CERTIFICADO_REVOCADO_CADUCO: "Certificado revocado o caduco",
    SATErrorCode.CERTIFICADO_NO_VALIDO: "Certificado no vigente",
    SATErrorCode.SOLICITUD_RECIBIDA: "Solicitud recibida con éxito",
    SATErrorCode.SIN_PRIVILEGIOS: "Sin privilegios para realizar la descarga",
    SATErrorCode.TOPE_MAXIMO: "Se superó el tope máximo",
    SATErrorCode.NO_SE_ENCONTRO_INFO: "No se encontró la información",
    SATErrorCode.SOLICITUD_DUPLICADA: "Solicitud duplicada",
    SATErrorCode.SOLICITUD_VENCIDA: "La solicitud ha vencido (>72 horas)",
    SATErrorCode.NUMERO_SOLICITUDES_EXEDIDO: "Se ha excedido el número de descargas por folio por día",
}

# Error Descriptions (English)
ERROR_DESCRIPTIONS_EN = {
    SATErrorCode.USUARIO_NO_VALIDO: "Invalid user for this request",
    SATErrorCode.XML_MAL_FORMADO: "Malformed XML",
    SATErrorCode.SELLO_MAL_FORMADO: "Malformed or invalid seal",
    SATErrorCode.SELLO_NO_CORRESPONDE: "Seal doesn't match certificate",
    SATErrorCode.CERTIFICADO_REVOCADO_CADUCO: "Certificate revoked or expired",
    SATErrorCode.CERTIFICADO_NO_VALIDO: "Certificate not valid",
    SATErrorCode.SOLICITUD_RECIBIDA: "Request received successfully",
    SATErrorCode.SIN_PRIVILEGIOS: "No download privileges",
    SATErrorCode.TOPE_MAXIMO: "Maximum limit exceeded",
    SATErrorCode.NO_SE_ENCONTRO_INFO: "Information not found",
    SATErrorCode.SOLICITUD_DUPLICADA: "Duplicate request",
    SATErrorCode.SOLICITUD_VENCIDA: "Request expired (>72 hours)",
    SATErrorCode.NUMERO_SOLICITUDES_EXEDIDO: "Daily download limit per folio exceeded",
}

# Estado Descriptions (Spanish)
ESTADO_DESCRIPTIONS_ES = {
    EstadoSolicitud.ACEPTADA: "Aceptada",
    EstadoSolicitud.EN_PROCESO: "En Proceso",
    EstadoSolicitud.TERMINADA: "Terminada",
    EstadoSolicitud.ERROR: "Error",
    EstadoSolicitud.RECHAZADA: "Rechazada",
    EstadoSolicitud.VENCIDA: "Vencida",
}

# Estado Descriptions (English)
ESTADO_DESCRIPTIONS_EN = {
    EstadoSolicitud.ACEPTADA: "Accepted",
    EstadoSolicitud.EN_PROCESO: "Processing",
    EstadoSolicitud.TERMINADA: "Finished",
    EstadoSolicitud.ERROR: "Error",
    EstadoSolicitud.RECHAZADA: "Rejected",
    EstadoSolicitud.VENCIDA: "Expired",
}

# Retryable errors (errors that may succeed on retry)
RETRYABLE_ERRORS = {
    SATErrorCode.TOPE_MAXIMO,            # Rate limit - retry with backoff
    SATErrorCode.NUMERO_SOLICITUDES_EXEDIDO,  # Daily limit - retry next day
}

# Critical errors (require immediate admin attention)
CRITICAL_ERRORS = {
    SATErrorCode.CERTIFICADO_REVOCADO_CADUCO,  # Certificate invalid!
    SATErrorCode.CERTIFICADO_NO_VALIDO,        # Certificate not valid
}

# Error severity levels
ERROR_SEVERITY = {
    SATErrorCode.USUARIO_NO_VALIDO: "ERROR",
    SATErrorCode.XML_MAL_FORMADO: "ERROR",
    SATErrorCode.SELLO_MAL_FORMADO: "ERROR",
    SATErrorCode.SELLO_NO_CORRESPONDE: "ERROR",
    SATErrorCode.CERTIFICADO_REVOCADO_CADUCO: "CRITICAL",
    SATErrorCode.CERTIFICADO_NO_VALIDO: "CRITICAL",
    SATErrorCode.SOLICITUD_RECIBIDA: "INFO",
    SATErrorCode.SIN_PRIVILEGIOS: "ERROR",
    SATErrorCode.TOPE_MAXIMO: "WARNING",
    SATErrorCode.NO_SE_ENCONTRO_INFO: "INFO",
    SATErrorCode.SOLICITUD_DUPLICADA: "WARNING",
    SATErrorCode.SOLICITUD_VENCIDA: "WARNING",
    SATErrorCode.NUMERO_SOLICITUDES_EXEDIDO: "WARNING",
}


@dataclass
class SATErrorResponse:
    """
    Structured SAT error response.

    Attributes:
        code: SAT error code
        message: Error message from SAT
        retry: Whether to retry this request
        backoff: Whether to use exponential backoff
        delay_seconds: Recommended delay before retry
        critical: Whether this error is critical
        action: Recommended action for this error
    """

    code: int
    message: str
    retry: bool
    backoff: bool = False
    delay_seconds: int = 0
    critical: bool = False
    action: str = ""

    @classmethod
    def from_sat_response(cls, code: int, message: str) -> "SATErrorResponse":
        """Create error response from SAT code and message."""

        try:
            error_code = SATErrorCode(code)
        except ValueError:
            # Unknown error code
            logger.error(f"Unknown SAT error code: {code}")
            return cls(
                code=code,
                message=message,
                retry=False,
                action="Unknown error - manual review required"
            )

        return cls(
            code=code,
            message=message,
            retry=error_code.is_retryable,
            backoff=error_code in RETRYABLE_ERRORS,
            delay_seconds=_get_retry_delay(error_code),
            critical=error_code.is_critical,
            action=_get_recommended_action(error_code)
        )


def _get_retry_delay(error_code: SATErrorCode) -> int:
    """
    Get recommended retry delay for error code.

    Returns delay in seconds.
    """

    if error_code == SATErrorCode.TOPE_MAXIMO:
        # Rate limit - wait 5 minutes
        return 300

    elif error_code == SATErrorCode.NUMERO_SOLICITUDES_EXEDIDO:
        # Daily limit - wait until next day (12 hours conservatively)
        return 43200

    else:
        # No retry
        return 0


def _get_recommended_action(error_code: SATErrorCode) -> str:
    """Get recommended action for error code."""

    actions = {
        SATErrorCode.USUARIO_NO_VALIDO: "Verify RFC and certificate match",
        SATErrorCode.XML_MAL_FORMADO: "Fix XML structure and resend",
        SATErrorCode.SELLO_MAL_FORMADO: "Re-sign request with valid certificate",
        SATErrorCode.SELLO_NO_CORRESPONDE: "Verify certificate and private key match",
        SATErrorCode.CERTIFICADO_REVOCADO_CADUCO: "CRITICAL: Renew certificate immediately!",
        SATErrorCode.CERTIFICADO_NO_VALIDO: "CRITICAL: Check certificate validity period",
        SATErrorCode.SOLICITUD_RECIBIDA: "Continue processing",
        SATErrorCode.SIN_PRIVILEGIOS: "Request download authorization from SAT",
        SATErrorCode.TOPE_MAXIMO: "Reduce date range and retry after 5 minutes",
        SATErrorCode.NO_SE_ENCONTRO_INFO: "Verify search parameters - no CFDIs match criteria",
        SATErrorCode.SOLICITUD_DUPLICADA: "Use existing request ID",
        SATErrorCode.SOLICITUD_VENCIDA: "Create new request (original expired after 72h)",
        SATErrorCode.NUMERO_SOLICITUDES_EXEDIDO: "Retry tomorrow - daily limit reached",
    }

    return actions.get(error_code, "Unknown error")


class SATErrorHandler:
    """
    Handler for SAT error codes with retry logic and alerting.

    Usage:
        handler = SATErrorHandler()

        # Handle error from SAT response
        response = handler.handle_error(code=304, message="Certificado revocado")

        if response.critical:
            send_critical_alert(response.message)

        if response.retry:
            await asyncio.sleep(response.delay_seconds)
            # Retry request...
    """

    def __init__(self, alert_callback: Optional[callable] = None):
        """
        Initialize error handler.

        Args:
            alert_callback: Optional callback for critical errors
        """
        self.alert_callback = alert_callback
        self.error_counts: Dict[int, int] = {}

    def handle_error(self, code: int, message: str) -> SATErrorResponse:
        """
        Handle SAT error code.

        Args:
            code: SAT error code
            message: Error message from SAT

        Returns:
            SATErrorResponse with recommended actions
        """

        # Track error frequency
        self.error_counts[code] = self.error_counts.get(code, 0) + 1

        # Create structured response
        response = SATErrorResponse.from_sat_response(code, message)

        # Log error
        self._log_error(response)

        # Alert if critical
        if response.critical:
            self._alert_critical(response)

        return response

    def _log_error(self, response: SATErrorResponse):
        """Log error with appropriate severity."""

        log_message = (
            f"SAT Error {response.code}: {response.message} | "
            f"Action: {response.action}"
        )

        if response.critical:
            logger.critical(log_message)
        elif response.retry:
            logger.warning(log_message)
        else:
            logger.error(log_message)

    def _alert_critical(self, response: SATErrorResponse):
        """Send critical alert."""

        alert_message = (
            f"🚨 CRITICAL SAT ERROR 🚨\n"
            f"Code: {response.code}\n"
            f"Message: {response.message}\n"
            f"Action: {response.action}\n"
            f"Occurrences: {self.error_counts[response.code]}"
        )

        logger.critical(alert_message)

        if self.alert_callback:
            try:
                self.alert_callback(alert_message)
            except Exception as e:
                logger.error(f"Failed to send critical alert: {e}")

    def get_error_statistics(self) -> Dict[str, Any]:
        """
        Get error statistics.

        Returns:
            Dictionary with error counts and patterns
        """

        total_errors = sum(self.error_counts.values())

        return {
            "total_errors": total_errors,
            "error_counts": self.error_counts.copy(),
            "most_frequent": max(self.error_counts.items(), key=lambda x: x[1])[0] if self.error_counts else None,
            "critical_errors": sum(
                count for code, count in self.error_counts.items()
                if code in [304, 305]
            )
        }


class CertificateExpiredError(Exception):
    """Raised when SAT certificate is expired or revoked."""
    pass


class SATAuthenticationError(Exception):
    """Raised when SAT authentication fails."""
    pass


class SATRateLimitError(Exception):
    """Raised when SAT rate limit is exceeded."""
    pass


class SATRequestError(Exception):
    """Raised when SAT request fails."""
    pass


def raise_on_error(code: int, message: str):
    """
    Raise appropriate exception for SAT error code.

    Args:
        code: SAT error code
        message: Error message from SAT

    Raises:
        CertificateExpiredError: If certificate is invalid (304, 305)
        SATAuthenticationError: If authentication fails (300-303)
        SATRateLimitError: If rate limit exceeded (5003, 5011)
        SATRequestError: For other errors
    """

    if code in [304, 305]:
        raise CertificateExpiredError(f"SAT Error {code}: {message}")

    elif code in [300, 301, 302, 303]:
        raise SATAuthenticationError(f"SAT Error {code}: {message}")

    elif code in [5003, 5011]:
        raise SATRateLimitError(f"SAT Error {code}: {message}")

    elif code != 5000:  # 5000 = success
        raise SATRequestError(f"SAT Error {code}: {message}")


# Convenience function for quick error checking
def is_success(code: int) -> bool:
    """Check if SAT response code indicates success."""
    return code == 5000


def is_error(code: int) -> bool:
    """Check if SAT response code indicates an error."""
    return code != 5000


def is_critical_error(code: int) -> bool:
    """Check if SAT response code indicates a critical error."""
    try:
        error_code = SATErrorCode(code)
        return error_code.is_critical
    except ValueError:
        return False


# Example usage
if __name__ == "__main__":
    """
    Example usage of SAT error handling.
    """

    # Set up logging
    logging.basicConfig(level=logging.INFO)

    # Create error handler
    def critical_alert(message: str):
        print(f"\n📧 SENDING ALERT TO ADMIN:\n{message}\n")

    handler = SATErrorHandler(alert_callback=critical_alert)

    # Example 1: Certificate expired (CRITICAL)
    print("=" * 60)
    print("Example 1: Certificate Expired (CRITICAL)")
    print("=" * 60)
    response = handler.handle_error(304, "Certificado revocado o caduco")
    print(f"Retry: {response.retry}")
    print(f"Critical: {response.critical}")
    print(f"Action: {response.action}")

    # Example 2: Rate limit (WARNING, RETRYABLE)
    print("\n" + "=" * 60)
    print("Example 2: Rate Limit Exceeded (RETRYABLE)")
    print("=" * 60)
    response = handler.handle_error(5003, "Se superó el tope máximo")
    print(f"Retry: {response.retry}")
    print(f"Backoff: {response.backoff}")
    print(f"Delay: {response.delay_seconds} seconds")
    print(f"Action: {response.action}")

    # Example 3: No info found (INFO, NO RETRY)
    print("\n" + "=" * 60)
    print("Example 3: No Information Found (NO RETRY)")
    print("=" * 60)
    response = handler.handle_error(5004, "No se encontró la información")
    print(f"Retry: {response.retry}")
    print(f"Action: {response.action}")

    # Example 4: Success
    print("\n" + "=" * 60)
    print("Example 4: Success")
    print("=" * 60)
    response = handler.handle_error(5000, "Solicitud recibida con éxito")
    print(f"Action: {response.action}")

    # Get statistics
    print("\n" + "=" * 60)
    print("Error Statistics")
    print("=" * 60)
    stats = handler.get_error_statistics()
    print(f"Total errors: {stats['total_errors']}")
    print(f"Error counts: {stats['error_counts']}")
    print(f"Critical errors: {stats['critical_errors']}")
