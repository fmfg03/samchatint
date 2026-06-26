"""
CFDI Verification Agent

This agent handles CFDI (Comprobante Fiscal Digital por Internet) verification
with the Mexican IRS (SAT) using the VerificaSolicitudDescarga Web Service.

Key Features:
- SOAP request builder for VerificaSolicitudDescarga
- XML response parser
- Estado solicitud tracking (1-6)
- Package download management
- Rate limiting and retry logic
- 72-hour expiration handling

Author: Copa Telmex Finance Integration Team
Date: 2025-10-10
"""

import asyncio
import base64
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin

import aiohttp
from lxml import etree

from .error_codes import (
    SATErrorCode,
    EstadoSolicitud,
    SATErrorHandler,
    SATErrorResponse,
    SATRateLimitError,
    SATRequestError,
    is_success
)
from .authentication_agent import SATAuthenticationAgent

logger = logging.getLogger(__name__)


class CFDIVerificationAgent:
    """
    Agent for CFDI verification with SAT Web Service.

    This agent manages:
    1. SOAP request building for VerificaSolicitudDescarga
    2. XML response parsing
    3. Estado solicitud tracking
    4. Package download coordination
    5. Rate limiting and retry logic

    Usage:
        agent = CFDIVerificationAgent(
            auth_agent=auth_agent,
            endpoint="https://pruebascfdiws.clouda.sat.gob.mx/..."
        )

        # Verify CFDI download request
        result = await agent.verify_solicitud(
            solicitud_id="4E80345D-917F-40BB-A98F-4A73939343C5",
            rfc="AXT940727FP8"
        )

        if result["estado"] == "Terminada":
            # Download packages
            packages = await agent.download_packages(result["paquetes"])
    """

    # SAT Endpoints
    PRODUCTION_ENDPOINT = "https://cfdidescargamasivasolicitud.clouda.sat.gob.mx/VerificaSolicitudDescargaService.svc"
    TESTING_ENDPOINT = "https://pruebascfdiws.clouda.sat.gob.mx/VerificaSolicitudDescargaService.svc"
    PRODUCTION_SOLICITUD_ENDPOINT = "https://cfdidescargamasivasolicitud.clouda.sat.gob.mx/SolicitaDescargaService.svc"
    TESTING_SOLICITUD_ENDPOINT = "https://pruebascfdiws.clouda.sat.gob.mx/SolicitaDescargaService.svc"
    PRODUCTION_DOWNLOAD_ENDPOINT = "https://cfdidescargamasiva.clouda.sat.gob.mx/DescargaMasivaService.svc"
    TESTING_DOWNLOAD_ENDPOINT = "https://pruebascfdiws.clouda.sat.gob.mx/DescargaMasivaService.svc"

    # Retry configuration
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 2  # seconds
    MAX_BACKOFF = 300    # 5 minutes

    # Estado solicitud polling
    POLL_INTERVAL = 5    # seconds
    MAX_POLL_TIME = 300  # 5 minutes

    def __init__(
        self,
        auth_agent: SATAuthenticationAgent,
        endpoint: Optional[str] = None,
        testing: bool = True,
        alert_callback: Optional[callable] = None
    ):
        """
        Initialize CFDI verification agent.

        Args:
            auth_agent: SAT authentication agent
            endpoint: Custom SAT endpoint (optional)
            testing: Use testing endpoint (default: True)
            alert_callback: Optional callback for critical alerts
        """

        self.auth_agent = auth_agent
        self.endpoint = endpoint or (self.TESTING_ENDPOINT if testing else self.PRODUCTION_ENDPOINT)
        self.error_handler = SATErrorHandler(alert_callback=alert_callback)

        # Rate limiting
        self.rate_limiter = SATRateLimiter()

        # Request tracking
        self.pending_requests: Dict[str, Dict[str, Any]] = {}

        logger.info(f"CFDI Verification Agent initialized with endpoint: {self.endpoint}")

    async def create_solicitud(
        self,
        *,
        rfc_solicitante: str,
        fecha_inicial: datetime,
        fecha_final: datetime,
        rfc_emisor: Optional[str] = None,
        rfc_receptor: Optional[str] = None,
        tipo_solicitud: str = "CFDI",
    ) -> Dict[str, Any]:
        """Create a SAT mass-download request."""

        await self.rate_limiter.check_rate_limit(rfc_solicitante)
        soap_request = self._build_solicita_descarga_request(
            rfc_solicitante=rfc_solicitante,
            fecha_inicial=fecha_inicial,
            fecha_final=fecha_final,
            rfc_emisor=rfc_emisor,
            rfc_receptor=rfc_receptor,
            tipo_solicitud=tipo_solicitud,
        )
        response = await self._make_soap_request_with_retry(
            soap_request,
            rfc_solicitante,
            endpoint=self._solicitud_endpoint(),
            soap_action=(
                "http://DescargaMasivaTerceros.sat.gob.mx/"
                "ISolicitaDescargaService/SolicitaDescarga"
            ),
        )
        return self._parse_solicita_descarga_response(response)

    async def verify_solicitud(
        self,
        solicitud_id: str,
        rfc: str,
        poll_until_complete: bool = False
    ) -> Dict[str, Any]:
        """
        Verify CFDI download request status.

        Args:
            solicitud_id: Download request ID (UUID)
            rfc: RFC (Registro Federal de Contribuyentes)
            poll_until_complete: If True, poll until estado is final

        Returns:
            Dictionary with verification results:
            - estado: Estado solicitud (Aceptada, En Proceso, Terminada, etc.)
            - codigo_estado: Estado code (1-6)
            - num_cfdis: Number of CFDIs found
            - paquetes: List of package IDs for download
            - mensaje: Message from SAT
            - cod_estatus: Status code from SAT
        """

        logger.info(f"Verifying CFDI solicitud: {solicitud_id} for RFC: {rfc}")

        # Check rate limit
        await self.rate_limiter.check_rate_limit(rfc)

        # Build SOAP request
        soap_request = self._build_verifica_solicitud_request(solicitud_id, rfc)

        # Make request with retry logic
        response = await self._make_soap_request_with_retry(soap_request, rfc)

        # Parse response
        result = self._parse_verifica_solicitud_response(response)

        # Track request
        self.pending_requests[solicitud_id] = {
            "rfc": rfc,
            "estado": result["estado"],
            "created_at": datetime.utcnow(),
            "last_checked": datetime.utcnow()
        }

        # Poll if requested and not final
        if poll_until_complete and not result["is_final"]:
            result = await self._poll_until_complete(solicitud_id, rfc)

        return result

    def _build_verifica_solicitud_request(
        self,
        solicitud_id: str,
        rfc: str
    ) -> str:
        """
        Build SOAP request for VerificaSolicitudDescarga.

        Args:
            solicitud_id: Download request ID
            rfc: RFC

        Returns:
            SOAP envelope XML string
        """

        # Build solicitud element
        solicitud_xml = f"""
        <VerificaSolicitudDescarga xmlns="http://DescargaMasivaTerceros.sat.gob.mx">
            <solicitud IdSolicitud="{solicitud_id}" RfcSolicitante="{rfc}">
            </solicitud>
        </VerificaSolicitudDescarga>
        """

        # Create authenticated envelope
        envelope = self.auth_agent.create_authenticated_envelope(
            body_xml=solicitud_xml,
            rfc=rfc
        )

        return envelope

    def _build_solicita_descarga_request(
        self,
        *,
        rfc_solicitante: str,
        fecha_inicial: datetime,
        fecha_final: datetime,
        rfc_emisor: Optional[str] = None,
        rfc_receptor: Optional[str] = None,
        tipo_solicitud: str = "CFDI",
    ) -> str:
        fecha_inicial_str = fecha_inicial.strftime("%Y-%m-%dT%H:%M:%S")
        fecha_final_str = fecha_final.strftime("%Y-%m-%dT%H:%M:%S")
        optional_attrs = []
        if rfc_emisor:
            optional_attrs.append(f'RfcEmisor="{rfc_emisor}"')
        if rfc_receptor:
            optional_attrs.append(f'RfcReceptor="{rfc_receptor}"')

        solicitud_xml = f"""
        <SolicitaDescarga xmlns="http://DescargaMasivaTerceros.sat.gob.mx">
            <solicitud
                RfcSolicitante="{rfc_solicitante}"
                FechaInicial="{fecha_inicial_str}"
                FechaFinal="{fecha_final_str}"
                TipoSolicitud="{tipo_solicitud}"
                {' '.join(optional_attrs)}>
            </solicitud>
        </SolicitaDescarga>
        """
        return self.auth_agent.create_authenticated_envelope(
            body_xml=solicitud_xml,
            rfc=rfc_solicitante,
        )

    def _build_download_package_request(
        self,
        *,
        package_id: str,
        rfc: str,
    ) -> str:
        body_xml = f"""
        <PeticionDescargaMasivaTercerosEntrada xmlns="http://DescargaMasivaTerceros.sat.gob.mx">
            <peticionDescarga IdPaquete="{package_id}" RfcSolicitante="{rfc}">
            </peticionDescarga>
        </PeticionDescargaMasivaTercerosEntrada>
        """
        return self.auth_agent.create_authenticated_envelope(body_xml=body_xml, rfc=rfc)

    def _solicitud_endpoint(self) -> str:
        if self.endpoint == self.TESTING_ENDPOINT:
            return self.TESTING_SOLICITUD_ENDPOINT
        if self.endpoint == self.PRODUCTION_ENDPOINT:
            return self.PRODUCTION_SOLICITUD_ENDPOINT
        return self.endpoint

    def _download_endpoint(self) -> str:
        if self.endpoint == self.TESTING_ENDPOINT:
            return self.TESTING_DOWNLOAD_ENDPOINT
        if self.endpoint == self.PRODUCTION_ENDPOINT:
            return self.PRODUCTION_DOWNLOAD_ENDPOINT
        return self.endpoint

    async def _make_soap_request_with_retry(
        self,
        soap_request: str,
        rfc: str,
        *,
        endpoint: Optional[str] = None,
        soap_action: Optional[str] = None,
    ) -> str:
        """
        Make SOAP request with retry logic.

        Args:
            soap_request: SOAP envelope XML
            rfc: RFC for rate limiting

        Returns:
            SOAP response XML string
        """

        last_error = None
        backoff = self.INITIAL_BACKOFF

        for attempt in range(self.MAX_RETRIES):
            try:
                return await self._make_soap_request(
                    soap_request,
                    endpoint=endpoint,
                    soap_action=soap_action,
                )

            except SATRateLimitError as e:
                last_error = e
                logger.warning(f"Rate limit hit (attempt {attempt + 1}/{self.MAX_RETRIES})")

                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.MAX_BACKOFF)

            except SATRequestError as e:
                last_error = e
                logger.error(f"SAT request error (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}")

                # Don't retry on non-retryable errors
                if not self._is_retryable_error(e):
                    raise

                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.MAX_BACKOFF)

        # All retries exhausted
        logger.error(f"All {self.MAX_RETRIES} retries exhausted")
        raise last_error

    async def _make_soap_request(
        self,
        soap_request: str,
        *,
        endpoint: Optional[str] = None,
        soap_action: Optional[str] = None,
    ) -> str:
        """
        Make SOAP request to SAT endpoint.

        Args:
            soap_request: SOAP envelope XML

        Returns:
            SOAP response XML string
        """

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": soap_action
            or "http://DescargaMasivaTerceros.sat.gob.mx/IVerificaSolicitudDescargaService/VerificaSolicitudDescarga"
        }
        target_endpoint = endpoint or self.endpoint

        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(
                    target_endpoint,
                    data=soap_request.encode("utf-8"),
                    headers=headers
                ) as response:

                    response_text = await response.text()

                    if response.status != 200:
                        logger.error(f"SAT HTTP error {response.status}: {response_text[:500]}")
                        raise SATRequestError(f"HTTP {response.status}: {response_text[:200]}")

                    return response_text

            except asyncio.TimeoutError:
                logger.error("SAT request timeout")
                raise SATRequestError("Request timeout (30s)")

            except aiohttp.ClientError as e:
                logger.error(f"SAT client error: {e}")
                raise SATRequestError(f"Client error: {e}")

    def _parse_verifica_solicitud_response(self, response_xml: str) -> Dict[str, Any]:
        """
        Parse VerificaSolicitudDescarga response.

        Args:
            response_xml: SOAP response XML

        Returns:
            Dictionary with parsed response data
        """

        try:
            root = etree.fromstring(response_xml.encode("utf-8"))

            # Find result element
            namespaces = {
                "s": "http://schemas.xmlsoap.org/soap/envelope/",
                "sat": "http://DescargaMasivaTerceros.sat.gob.mx"
            }

            result = root.find(
                ".//sat:VerificaSolicitudDescargaResult",
                namespaces
            )

            if result is None:
                raise SATRequestError("No result element in response")

            # Extract fields
            cod_estatus = int(result.get("CodEstatus", "0"))
            mensaje = result.get("Mensaje", "")
            codigo_estado = int(result.get("CodigoEstadoSolicitud", "0"))
            estado_solicitud = result.get("EstadoSolicitud", "")
            num_cfdis = int(result.get("NumeroCFDIs", "0"))

            # Handle error codes
            if not is_success(cod_estatus):
                error_response = self.error_handler.handle_error(cod_estatus, mensaje)

                if error_response.critical:
                    raise SATRequestError(f"Critical SAT error: {mensaje}")

                if cod_estatus == 5003:  # Rate limit
                    raise SATRateLimitError(mensaje)

            # Extract package IDs
            paquetes = []
            ids_paquetes = result.find(".//sat:IdsPaquetes", namespaces)
            if ids_paquetes is not None:
                for string_elem in ids_paquetes.findall(".//sat:string", namespaces):
                    if string_elem.text:
                        paquetes.append(string_elem.text)

            # Determine estado
            try:
                estado = EstadoSolicitud(codigo_estado)
            except ValueError:
                logger.warning(f"Unknown estado code: {codigo_estado}")
                estado = None

            return {
                "cod_estatus": cod_estatus,
                "mensaje": mensaje,
                "codigo_estado": codigo_estado,
                "estado": estado_solicitud,
                "estado_enum": estado,
                "num_cfdis": num_cfdis,
                "paquetes": paquetes,
                "is_final": estado.is_final if estado else False,
                "should_retry": estado.should_retry if estado else False
            }

        except etree.XMLSyntaxError as e:
            logger.error(f"XML parse error: {e}")
            raise SATRequestError(f"Invalid XML response: {e}")

    def _parse_solicita_descarga_response(self, response_xml: str) -> Dict[str, Any]:
        try:
            root = etree.fromstring(response_xml.encode("utf-8"))
            namespaces = {
                "sat": "http://DescargaMasivaTerceros.sat.gob.mx",
            }
            result = root.find(".//sat:SolicitaDescargaResult", namespaces)
            if result is None:
                raise SATRequestError("No result element in response")

            cod_estatus = int(result.get("CodEstatus", "0"))
            mensaje = result.get("Mensaje", "")
            solicitud_id = result.get("IdSolicitud", "")

            if not is_success(cod_estatus):
                error_response = self.error_handler.handle_error(cod_estatus, mensaje)
                if error_response.critical:
                    raise SATRequestError(f"Critical SAT error: {mensaje}")
                if cod_estatus == 5003:
                    raise SATRateLimitError(mensaje)

            return {
                "cod_estatus": cod_estatus,
                "mensaje": mensaje,
                "solicitud_id": solicitud_id,
                "accepted": bool(solicitud_id) and is_success(cod_estatus),
            }
        except etree.XMLSyntaxError as e:
            logger.error(f"XML parse error: {e}")
            raise SATRequestError(f"Invalid XML response: {e}")

    def _parse_download_package_response(self, response_xml: str) -> Dict[str, Any]:
        try:
            root = etree.fromstring(response_xml.encode("utf-8"))
            namespaces = {
                "sat": "http://DescargaMasivaTerceros.sat.gob.mx",
            }
            result = None
            for xpath in (
                ".//sat:PeticionDescargaMasivaTercerosEntradaResult",
                ".//sat:RespuestaDescargaMasivaTercerosSalida",
                ".//sat:DescargaMasivaTercerosResult",
            ):
                candidate = root.find(xpath, namespaces)
                if candidate is not None:
                    result = candidate
                    break
            if result is None:
                raise SATRequestError("No download result element in response")

            cod_estatus = int(result.get("CodEstatus", "0"))
            mensaje = result.get("Mensaje", "")
            paquete_b64 = result.get("Paquete", "") or (result.text or "").strip()

            if not is_success(cod_estatus):
                error_response = self.error_handler.handle_error(cod_estatus, mensaje)
                if error_response.critical:
                    raise SATRequestError(f"Critical SAT error: {mensaje}")
                if cod_estatus == 5003:
                    raise SATRateLimitError(mensaje)

            package_bytes = base64.b64decode(paquete_b64) if paquete_b64 else b""
            return {
                "cod_estatus": cod_estatus,
                "mensaje": mensaje,
                "package_b64": paquete_b64,
                "package_bytes": package_bytes,
                "empty": not bool(package_bytes),
            }
        except etree.XMLSyntaxError as e:
            logger.error(f"XML parse error: {e}")
            raise SATRequestError(f"Invalid XML response: {e}")

    async def _poll_until_complete(
        self,
        solicitud_id: str,
        rfc: str
    ) -> Dict[str, Any]:
        """
        Poll solicitud status until final state.

        Args:
            solicitud_id: Download request ID
            rfc: RFC

        Returns:
            Final verification result
        """

        logger.info(f"Polling solicitud {solicitud_id} until complete...")

        start_time = time.time()
        attempts = 0

        while True:
            attempts += 1
            elapsed = time.time() - start_time

            # Check timeout
            if elapsed > self.MAX_POLL_TIME:
                logger.warning(f"Polling timeout after {elapsed:.1f}s ({attempts} attempts)")
                raise SATRequestError(f"Polling timeout after {self.MAX_POLL_TIME}s")

            # Wait before checking
            await asyncio.sleep(self.POLL_INTERVAL)

            # Check status
            result = await self.verify_solicitud(solicitud_id, rfc, poll_until_complete=False)

            logger.info(f"Poll attempt {attempts}: Estado={result['estado']}, CFDIs={result['num_cfdis']}")

            # Check if final
            if result["is_final"]:
                logger.info(f"Solicitud complete after {elapsed:.1f}s ({attempts} attempts)")
                return result

    def _is_retryable_error(self, error: Exception) -> bool:
        """Check if error should trigger a retry."""

        if isinstance(error, SATRateLimitError):
            return True

        # Add more retryable conditions
        return False

    async def download_package(
        self,
        package_id: str,
        rfc: str
    ) -> Dict[str, Any]:
        """
        Download CFDI package.

        Args:
            package_id: Package ID from verification response
            rfc: RFC

        Returns:
            Dictionary with package data
        """

        logger.info(f"Downloading package: {package_id}")
        await self.rate_limiter.check_rate_limit(rfc)
        soap_request = self._build_download_package_request(package_id=package_id, rfc=rfc)
        response = await self._make_soap_request_with_retry(
            soap_request,
            rfc,
            endpoint=self._download_endpoint(),
            soap_action=(
                "http://DescargaMasivaTerceros.sat.gob.mx/"
                "IDescargaMasivaTercerosService/PeticionDescargaMasivaTercerosEntrada"
            ),
        )
        parsed = self._parse_download_package_response(response)
        parsed["package_id"] = package_id
        parsed["status"] = "success" if not parsed["empty"] else "empty"
        return parsed

    async def get_pending_requests(self) -> List[Dict[str, Any]]:
        """
        Get all pending verification requests.

        Returns:
            List of pending requests with status
        """

        pending = []

        for solicitud_id, data in self.pending_requests.items():
            # Check if expired (>72 hours)
            elapsed = datetime.utcnow() - data["created_at"]
            expired = elapsed.total_seconds() > (72 * 3600)

            pending.append({
                "solicitud_id": solicitud_id,
                "rfc": data["rfc"],
                "estado": data["estado"],
                "created_at": data["created_at"].isoformat(),
                "last_checked": data["last_checked"].isoformat(),
                "age_hours": elapsed.total_seconds() / 3600,
                "expired": expired
            })

        return pending

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get verification statistics.

        Returns:
            Dictionary with statistics
        """

        total_requests = len(self.pending_requests)
        expired = sum(
            1 for data in self.pending_requests.values()
            if (datetime.utcnow() - data["created_at"]).total_seconds() > (72 * 3600)
        )

        return {
            "total_requests": total_requests,
            "expired_requests": expired,
            "error_stats": self.error_handler.get_error_statistics(),
            "rate_limit_stats": self.rate_limiter.get_statistics()
        }


class SATRateLimiter:
    """
    Rate limiter for SAT API calls.

    Implements:
    - Minimum time between requests per RFC
    - Daily request limit per RFC
    - Conservative limits to avoid SAT errors 5003/5011
    """

    # Rate limits (conservative values)
    MIN_REQUEST_INTERVAL = 2.0  # seconds between requests
    DAILY_REQUEST_LIMIT = 100   # requests per RFC per day

    def __init__(self):
        """Initialize rate limiter."""

        self.last_request_time: Dict[str, float] = {}
        self.daily_requests: Dict[str, int] = defaultdict(int)
        self.daily_reset_time: Dict[str, datetime] = {}

    async def check_rate_limit(self, rfc: str) -> bool:
        """
        Check if request is allowed under rate limits.

        Args:
            rfc: RFC for rate limiting

        Returns:
            True if request allowed

        Raises:
            SATRateLimitError: If rate limit exceeded
        """

        # 1. Check minimum interval
        last_time = self.last_request_time.get(rfc, 0)
        elapsed = time.time() - last_time

        if elapsed < self.MIN_REQUEST_INTERVAL:
            wait_time = self.MIN_REQUEST_INTERVAL - elapsed
            logger.debug(f"Rate limit: waiting {wait_time:.2f}s for RFC {rfc}")
            await asyncio.sleep(wait_time)

        # 2. Check daily limit
        today = datetime.utcnow().date()
        reset_time = self.daily_reset_time.get(rfc)

        # Reset counter if new day
        if reset_time is None or reset_time.date() < today:
            self.daily_requests[rfc] = 0
            self.daily_reset_time[rfc] = datetime.utcnow()

        if self.daily_requests[rfc] >= self.DAILY_REQUEST_LIMIT:
            logger.error(f"Daily limit exceeded for RFC {rfc}: {self.daily_requests[rfc]}/{self.DAILY_REQUEST_LIMIT}")
            raise SATRateLimitError(f"Daily request limit exceeded for RFC {rfc}")

        # Update tracking
        self.last_request_time[rfc] = time.time()
        self.daily_requests[rfc] += 1

        return True

    def get_statistics(self) -> Dict[str, Any]:
        """Get rate limiting statistics."""

        return {
            "total_rfcs": len(self.daily_requests),
            "requests_by_rfc": dict(self.daily_requests),
            "max_requests_today": max(self.daily_requests.values()) if self.daily_requests else 0,
            "limit": self.DAILY_REQUEST_LIMIT
        }


# Example usage
if __name__ == "__main__":
    """
    Example usage of CFDI verification agent.
    """

    import os

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Example configuration
    CERT_PATH = os.getenv("SAT_CERT_PATH", "/path/to/efirma.cer")
    KEY_PATH = os.getenv("SAT_KEY_PATH", "/path/to/efirma.key")
    PASSPHRASE = os.getenv("SAT_PASSPHRASE", "secret")
    RFC = os.getenv("SAT_RFC", "AXT940727FP8")
    SOLICITUD_ID = os.getenv("SAT_SOLICITUD_ID", "4E80345D-917F-40BB-A98F-4A73939343C5")

    async def main():
        """Main example function."""

        print("=" * 60)
        print("CFDI Verification Agent - Example Usage")
        print("=" * 60)

        try:
            # Create authentication agent
            print("\n🔐 Creating authentication agent...")
            auth_agent = SATAuthenticationAgent(
                cert_path=CERT_PATH,
                key_path=KEY_PATH,
                passphrase=PASSPHRASE
            )

            # Create verification agent
            print("📋 Creating verification agent...")
            verification_agent = CFDIVerificationAgent(
                auth_agent=auth_agent,
                testing=True  # Use testing endpoint
            )

            # Verify solicitud
            print(f"\n🔍 Verifying solicitud: {SOLICITUD_ID}")
            result = await verification_agent.verify_solicitud(
                solicitud_id=SOLICITUD_ID,
                rfc=RFC,
                poll_until_complete=True  # Poll until final state
            )

            print("\n📊 Verification Result:")
            print(f"  Estado: {result['estado']}")
            print(f"  Código Estado: {result['codigo_estado']}")
            print(f"  Número CFDIs: {result['num_cfdis']}")
            print(f"  Paquetes: {len(result['paquetes'])}")

            if result['paquetes']:
                print(f"\n📦 Package IDs:")
                for i, package_id in enumerate(result['paquetes'], 1):
                    print(f"  {i}. {package_id}")

            # Get statistics
            print("\n📈 Statistics:")
            stats = verification_agent.get_statistics()
            print(f"  Total requests: {stats['total_requests']}")
            print(f"  Expired requests: {stats['expired_requests']}")

        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()

    # Run async main
    asyncio.run(main())
