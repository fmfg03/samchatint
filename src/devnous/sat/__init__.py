"""
SAT (Servicio de Administración Tributaria) Integration Module.

Handles CFDI download and verification with Mexican IRS.
"""

from .authentication_agent import SATAuthenticationAgent
from .verification_agent import CFDIVerificationAgent
from .config_handler import SATConfigHandler, SATCredentials
from .sat_handler import SATExpenseHandler
from .error_codes import (
    SATErrorCode,
    EstadoSolicitud,
    SATErrorHandler,
    SATErrorResponse,
    CertificateExpiredError,
    SATAuthenticationError,
    SATRateLimitError,
    SATRequestError
)

__all__ = [
    "SATAuthenticationAgent",
    "CFDIVerificationAgent",
    "SATConfigHandler",
    "SATCredentials",
    "SATExpenseHandler",
    "SATErrorCode",
    "EstadoSolicitud",
    "SATErrorHandler",
    "SATErrorResponse",
    "CertificateExpiredError",
    "SATAuthenticationError",
    "SATRateLimitError",
    "SATRequestError"
]
