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
import hashlib
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin

import aiohttp
from lxml import etree
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

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
from .timeout_config import (
    sat_http_timeout_seconds,
    sat_poll_interval_seconds,
    sat_poll_max_seconds,
)

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
    PRODUCTION_AUTH_ENDPOINT = "https://cfdidescargamasivasolicitud.clouda.sat.gob.mx/Autenticacion/Autenticacion.svc"
    TESTING_AUTH_ENDPOINT = "https://pruebascfdiws.clouda.sat.gob.mx/Autenticacion/Autenticacion.svc"
    PRODUCTION_SOLICITUD_ENDPOINT = "https://cfdidescargamasivasolicitud.clouda.sat.gob.mx/SolicitaDescargaService.svc"
    TESTING_SOLICITUD_ENDPOINT = "https://pruebascfdiws.clouda.sat.gob.mx/SolicitaDescargaService.svc"
    PRODUCTION_DOWNLOAD_ENDPOINT = "https://cfdidescargamasiva.clouda.sat.gob.mx/DescargaMasivaService.svc"
    TESTING_DOWNLOAD_ENDPOINT = "https://pruebascfdiws.clouda.sat.gob.mx/DescargaMasivaService.svc"

    # Retry configuration
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 2  # seconds
    MAX_BACKOFF = 300    # 5 minutes

    # Estado solicitud polling defaults (override via SAT_* env vars)
    POLL_INTERVAL = 5
    MAX_POLL_TIME = 1800

    def __init__(
        self,
        auth_agent: SATAuthenticationAgent,
        endpoint: Optional[str] = None,
        testing: bool = True,
        alert_callback: Optional[callable] = None,
        *,
        http_timeout_seconds: Optional[int] = None,
        poll_max_seconds: Optional[int] = None,
        poll_interval_seconds: Optional[int] = None,
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
        self.http_timeout_seconds = (
            http_timeout_seconds
            if http_timeout_seconds is not None
            else sat_http_timeout_seconds()
        )
        self.poll_max_seconds = (
            poll_max_seconds if poll_max_seconds is not None else sat_poll_max_seconds()
        )
        self.poll_interval_seconds = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else sat_poll_interval_seconds()
        )

        # Rate limiting
        self.rate_limiter = SATRateLimiter()

        # Request tracking
        self.pending_requests: Dict[str, Dict[str, Any]] = {}
        self._access_token: Optional[str] = None
        self._access_token_expires: Optional[datetime] = None

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
        operation = self._solicita_descarga_operation(
            rfc_solicitante=rfc_solicitante,
            rfc_emisor=rfc_emisor,
            rfc_receptor=rfc_receptor,
        )
        soap_request = self._build_solicita_descarga_request(
            rfc_solicitante=rfc_solicitante,
            fecha_inicial=fecha_inicial,
            fecha_final=fecha_final,
            rfc_emisor=rfc_emisor,
            rfc_receptor=rfc_receptor,
            tipo_solicitud=tipo_solicitud,
            operation=operation,
        )
        response = await self._make_soap_request_with_retry(
            soap_request,
            rfc_solicitante,
            endpoint=self._solicitud_endpoint(),
            soap_action=(
                "http://DescargaMasivaTerceros.sat.gob.mx/"
                f"ISolicitaDescargaService/{operation}"
            ),
            authorization_token=await self._get_access_token(),
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
        response = await self._make_soap_request_with_retry(
            soap_request,
            rfc,
            authorization_token=await self._get_access_token(),
        )

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

        return self._create_signed_envelope(
            operation="VerificaSolicitudDescarga",
            solicitud_tag="solicitud",
            attrs={
                "IdSolicitud": solicitud_id,
                "RfcSolicitante": rfc,
            },
        )

    def _create_signed_envelope(
        self,
        *,
        operation: str,
        solicitud_tag: str,
        attrs: Dict[str, Any],
    ) -> str:
        soap_ns = "http://schemas.xmlsoap.org/soap/envelope/"
        des_ns = "http://DescargaMasivaTerceros.sat.gob.mx"
        dsig_ns = "http://www.w3.org/2000/09/xmldsig#"
        envelope = etree.Element(
            f"{{{soap_ns}}}Envelope",
            nsmap={"des": des_ns, "s": soap_ns, "xd": dsig_ns},
        )
        etree.SubElement(envelope, f"{{{soap_ns}}}Header")
        body = etree.SubElement(envelope, f"{{{soap_ns}}}Body")
        operation_element = etree.SubElement(body, f"{{{des_ns}}}{operation}")
        solicitud = etree.SubElement(operation_element, f"{{{des_ns}}}{solicitud_tag}")
        for key, value in sorted(attrs.items()):
            if value is not None:
                solicitud.set(key, str(value))

        solicitud.append(self._create_payload_signature(operation_element))
        return etree.tostring(envelope, encoding="unicode", pretty_print=False)

    def _create_payload_signature(self, element: etree._Element) -> etree._Element:
        dsig_ns = "http://www.w3.org/2000/09/xmldsig#"
        digest = hashlib.sha1(
            etree.tostring(
                element,
                method="c14n",
                exclusive=False,
                with_comments=False,
            )
        ).digest()
        digest_b64 = base64.b64encode(digest).decode("utf-8")

        signature = etree.Element(f"{{{dsig_ns}}}Signature", nsmap={None: dsig_ns})
        signed_info = etree.SubElement(signature, f"{{{dsig_ns}}}SignedInfo")
        etree.SubElement(
            signed_info,
            f"{{{dsig_ns}}}CanonicalizationMethod",
            Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
        )
        etree.SubElement(
            signed_info,
            f"{{{dsig_ns}}}SignatureMethod",
            Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1",
        )
        reference = etree.SubElement(
            signed_info,
            f"{{{dsig_ns}}}Reference",
            URI="",
        )
        transforms = etree.SubElement(reference, f"{{{dsig_ns}}}Transforms")
        etree.SubElement(
            transforms,
            f"{{{dsig_ns}}}Transform",
            Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature",
        )
        etree.SubElement(
            reference,
            f"{{{dsig_ns}}}DigestMethod",
            Algorithm="http://www.w3.org/2000/09/xmldsig#sha1",
        )
        etree.SubElement(reference, f"{{{dsig_ns}}}DigestValue").text = digest_b64

        signature_value = self.auth_agent.private_key.sign(
            etree.tostring(
                signed_info,
                method="c14n",
                exclusive=False,
                with_comments=False,
            ),
            padding.PKCS1v15(),
            hashes.SHA1(),
        )
        etree.SubElement(signature, f"{{{dsig_ns}}}SignatureValue").text = (
            base64.b64encode(signature_value).decode("utf-8")
        )
        key_info = etree.SubElement(signature, f"{{{dsig_ns}}}KeyInfo")
        x509_data = etree.SubElement(key_info, f"{{{dsig_ns}}}X509Data")
        issuer_serial = etree.SubElement(x509_data, f"{{{dsig_ns}}}X509IssuerSerial")
        etree.SubElement(issuer_serial, f"{{{dsig_ns}}}X509IssuerName").text = (
            self.auth_agent.certificate.issuer.rfc4514_string()
        )
        etree.SubElement(issuer_serial, f"{{{dsig_ns}}}X509SerialNumber").text = str(
            self.auth_agent.certificate.serial_number
        )
        etree.SubElement(x509_data, f"{{{dsig_ns}}}X509Certificate").text = (
            self.auth_agent.certificate_b64
        )
        return signature

    def _solicita_descarga_operation(
        self,
        *,
        rfc_solicitante: str,
        rfc_emisor: Optional[str] = None,
        rfc_receptor: Optional[str] = None,
    ) -> str:
        solicitante = (rfc_solicitante or "").strip().upper()
        emisor = (rfc_emisor or "").strip().upper()
        receptor = (rfc_receptor or "").strip().upper()
        if receptor and receptor == solicitante:
            return "SolicitaDescargaRecibidos"
        if emisor and emisor == solicitante:
            return "SolicitaDescargaEmitidos"
        return "SolicitaDescarga"

    def _build_solicita_descarga_request(
        self,
        *,
        rfc_solicitante: str,
        fecha_inicial: datetime,
        fecha_final: datetime,
        rfc_emisor: Optional[str] = None,
        rfc_receptor: Optional[str] = None,
        tipo_solicitud: str = "CFDI",
        operation: str = "SolicitaDescarga",
    ) -> str:
        fecha_inicial_str = fecha_inicial.strftime("%Y-%m-%dT%H:%M:%S")
        fecha_final_str = fecha_final.strftime("%Y-%m-%dT%H:%M:%S")
        attrs: Dict[str, Any] = {
            "RfcSolicitante": rfc_solicitante,
            "FechaInicial": fecha_inicial_str,
            "FechaFinal": fecha_final_str,
            "TipoSolicitud": tipo_solicitud,
        }
        if operation == "SolicitaDescargaRecibidos":
            attrs["EstadoComprobante"] = "Vigente"
        elif operation == "SolicitaDescargaEmitidos":
            attrs["EstadoComprobante"] = "Todos"
        if rfc_emisor:
            attrs["RfcEmisor"] = rfc_emisor
        if rfc_receptor:
            attrs["RfcReceptor"] = rfc_receptor

        return self._create_signed_envelope(
            operation=operation,
            solicitud_tag="solicitud",
            attrs=attrs,
        )

    def _build_download_package_request(
        self,
        *,
        package_id: str,
        rfc: str,
    ) -> str:
        return self._create_signed_envelope(
            operation="PeticionDescargaMasivaTercerosEntrada",
            solicitud_tag="peticionDescarga",
            attrs={
                "IdPaquete": package_id,
                "RfcSolicitante": rfc,
            },
        )

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

    def _auth_endpoint(self) -> str:
        if self.endpoint == self.TESTING_ENDPOINT:
            return self.TESTING_AUTH_ENDPOINT
        if self.endpoint == self.PRODUCTION_ENDPOINT:
            return self.PRODUCTION_AUTH_ENDPOINT
        return self.PRODUCTION_AUTH_ENDPOINT

    async def _get_access_token(self) -> str:
        if (
            self._access_token
            and self._access_token_expires
            and datetime.utcnow() < self._access_token_expires - timedelta(seconds=30)
        ):
            return self._access_token

        response = await self._make_soap_request(
            self._build_authentication_request(),
            endpoint=self._auth_endpoint(),
            soap_action="http://DescargaMasivaTerceros.gob.mx/IAutenticacion/Autentica",
        )
        root = etree.fromstring(response.encode("utf-8"))
        token = root.find(".//{http://DescargaMasivaTerceros.gob.mx}AutenticaResult")
        if token is None or not token.text:
            raise SATRequestError("SAT authentication response did not include a token")

        expires = root.find(
            ".//{http://docs.oasis-open.org/wss/2004/01/"
            "oasis-200401-wss-wssecurity-utility-1.0.xsd}Expires"
        )
        self._access_token = token.text
        if expires is not None and expires.text:
            self._access_token_expires = datetime.fromisoformat(
                expires.text.rstrip("Z")
            )
        else:
            self._access_token_expires = datetime.utcnow() + timedelta(minutes=4)
        return self._access_token

    def _build_authentication_request(self) -> str:
        soap_ns = "http://schemas.xmlsoap.org/soap/envelope/"
        wsse_ns = (
            "http://docs.oasis-open.org/wss/2004/01/"
            "oasis-200401-wss-wssecurity-secext-1.0.xsd"
        )
        wsu_ns = (
            "http://docs.oasis-open.org/wss/2004/01/"
            "oasis-200401-wss-wssecurity-utility-1.0.xsd"
        )
        dsig_ns = "http://www.w3.org/2000/09/xmldsig#"
        sat_auth_ns = "http://DescargaMasivaTerceros.gob.mx"

        envelope = etree.Element(
            f"{{{soap_ns}}}Envelope",
            nsmap={"s": soap_ns, "o": wsse_ns, "u": wsu_ns},
        )
        header = etree.SubElement(envelope, f"{{{soap_ns}}}Header")
        security = etree.SubElement(
            header,
            f"{{{wsse_ns}}}Security",
            {f"{{{soap_ns}}}mustUnderstand": "1"},
        )
        timestamp = etree.SubElement(security, f"{{{wsu_ns}}}Timestamp")
        timestamp.set(f"{{{wsu_ns}}}Id", "_0")
        created_at = datetime.utcnow()
        expires_at = created_at + timedelta(minutes=5)
        etree.SubElement(timestamp, f"{{{wsu_ns}}}Created").text = (
            created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )
        etree.SubElement(timestamp, f"{{{wsu_ns}}}Expires").text = (
            expires_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )
        binary_token = etree.SubElement(
            security,
            f"{{{wsse_ns}}}BinarySecurityToken",
            ValueType=(
                "http://docs.oasis-open.org/wss/2004/01/"
                "oasis-200401-wss-x509-token-profile-1.0#X509v3"
            ),
            EncodingType=(
                "http://docs.oasis-open.org/wss/2004/01/"
                "oasis-200401-wss-soap-message-security-1.0#Base64Binary"
            ),
        )
        binary_token.set(f"{{{wsu_ns}}}Id", "BinarySecurityToken")
        binary_token.text = self.auth_agent.certificate_b64

        signature = etree.SubElement(
            security,
            f"{{{dsig_ns}}}Signature",
            nsmap={None: dsig_ns},
        )
        signed_info = etree.SubElement(signature, f"{{{dsig_ns}}}SignedInfo")
        etree.SubElement(
            signed_info,
            f"{{{dsig_ns}}}CanonicalizationMethod",
            Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
        )
        etree.SubElement(
            signed_info,
            f"{{{dsig_ns}}}SignatureMethod",
            Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1",
        )
        reference = etree.SubElement(
            signed_info,
            f"{{{dsig_ns}}}Reference",
            URI="#_0",
        )
        transforms = etree.SubElement(reference, f"{{{dsig_ns}}}Transforms")
        etree.SubElement(
            transforms,
            f"{{{dsig_ns}}}Transform",
            Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
        )
        etree.SubElement(
            reference,
            f"{{{dsig_ns}}}DigestMethod",
            Algorithm="http://www.w3.org/2000/09/xmldsig#sha1",
        )
        digest = hashlib.sha1(
            etree.tostring(
                timestamp,
                method="c14n",
                exclusive=True,
                with_comments=False,
            )
        ).digest()
        etree.SubElement(reference, f"{{{dsig_ns}}}DigestValue").text = (
            base64.b64encode(digest).decode("utf-8")
        )
        signature_value = self.auth_agent.private_key.sign(
            etree.tostring(
                signed_info,
                method="c14n",
                exclusive=True,
                with_comments=False,
            ),
            padding.PKCS1v15(),
            hashes.SHA1(),
        )
        etree.SubElement(signature, f"{{{dsig_ns}}}SignatureValue").text = (
            base64.b64encode(signature_value).decode("utf-8")
        )
        key_info = etree.SubElement(signature, f"{{{dsig_ns}}}KeyInfo")
        security_ref = etree.SubElement(key_info, f"{{{wsse_ns}}}SecurityTokenReference")
        etree.SubElement(
            security_ref,
            f"{{{wsse_ns}}}Reference",
            ValueType=(
                "http://docs.oasis-open.org/wss/2004/01/"
                "oasis-200401-wss-x509-token-profile-1.0#X509v3"
            ),
            URI="#BinarySecurityToken",
        )
        body = etree.SubElement(envelope, f"{{{soap_ns}}}Body")
        etree.SubElement(body, f"{{{sat_auth_ns}}}Autentica")
        return etree.tostring(envelope, encoding="unicode", pretty_print=False)

    async def _make_soap_request_with_retry(
        self,
        soap_request: str,
        rfc: str,
        *,
        endpoint: Optional[str] = None,
        soap_action: Optional[str] = None,
        authorization_token: Optional[str] = None,
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
                    authorization_token=authorization_token,
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
        authorization_token: Optional[str] = None,
    ) -> str:
        """
        Make SOAP request to SAT endpoint.

        Args:
            soap_request: SOAP envelope XML

        Returns:
            SOAP response XML string
        """

        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "Accept": "text/xml",
            "Cache-Control": "no-cache",
            "SOAPAction": soap_action
            or "http://DescargaMasivaTerceros.sat.gob.mx/IVerificaSolicitudDescargaService/VerificaSolicitudDescarga"
        }
        if authorization_token:
            headers["Authorization"] = f'WRAP access_token="{authorization_token}"'
        target_endpoint = endpoint or self.endpoint

        timeout = aiohttp.ClientTimeout(total=self.http_timeout_seconds)

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
                logger.error(
                    "SAT request timeout after %ss", self.http_timeout_seconds
                )
                raise SATRequestError(
                    f"Request timeout ({self.http_timeout_seconds}s)"
                )

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
                matches = root.xpath(
                    "//*[local-name()='VerificaSolicitudDescargaResult']"
                )
                result = matches[0] if matches else None

            if result is None:
                raise SATRequestError("No result element in response")

            # Extract fields
            cod_estatus = int(result.get("CodEstatus", "0"))
            mensaje = result.get("Mensaje", "")
            # SAT uses EstadoSolicitud (1-6) for processing state; CodigoEstadoSolicitud
            # often mirrors CodEstatus (5000) and must not be used as estado enum.
            estado_code_raw = result.get("EstadoSolicitud")
            if estado_code_raw is None or str(estado_code_raw).strip() == "":
                fallback = result.get("CodigoEstadoSolicitud", "0")
                # CodigoEstadoSolicitud often mirrors CodEstatus (5000) — not estado enum.
                if str(fallback).strip().isdigit() and int(fallback) >= 5000:
                    codigo_estado = 0
                else:
                    codigo_estado = int(fallback or "0")
            else:
                codigo_estado = int(estado_code_raw or "0")

            # Handle error codes
            if not is_success(cod_estatus):
                error_response = self.error_handler.handle_error(cod_estatus, mensaje)

                if error_response.critical:
                    raise SATRequestError(f"Critical SAT error: {mensaje}")

                if cod_estatus == 5003:  # Rate limit
                    raise SATRateLimitError(mensaje)

                return {
                    "cod_estatus": cod_estatus,
                    "mensaje": mensaje,
                    "codigo_estado": codigo_estado,
                    "estado": "Error verificación",
                    "estado_enum": None,
                    "num_cfdis": 0,
                    "paquetes": [],
                    "is_final": False,
                    "should_retry": bool(error_response.retry),
                    "verify_failed": True,
                }

            num_cfdis = int(result.get("NumeroCFDIs", "0") or "0")

            # Extract package IDs (text node and/or repeated IdsPaquetes elements)
            paquetes: List[str] = []
            for ids_paquetes in result.xpath(
                ".//*[local-name()='IdsPaquetes']"
            ):
                text = (ids_paquetes.text or "").strip()
                if text:
                    paquetes.append(text)
                for string_elem in ids_paquetes.xpath(".//*[local-name()='string']"):
                    if string_elem.text and string_elem.text.strip():
                        paquetes.append(string_elem.text.strip())

            # Determine estado
            try:
                estado = EstadoSolicitud(codigo_estado)
            except ValueError:
                logger.warning(f"Unknown estado code: {codigo_estado}")
                estado = None

            estado_solicitud = (
                estado.description_es if estado else str(estado_code_raw or "")
            )

            return {
                "cod_estatus": cod_estatus,
                "mensaje": mensaje,
                "codigo_estado": codigo_estado,
                "estado": estado_solicitud,
                "estado_enum": estado,
                "num_cfdis": num_cfdis,
                "paquetes": paquetes,
                "is_final": estado.is_final if estado else False,
                "should_retry": estado.should_retry if estado else False,
                "verify_failed": False,
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
                matches = root.xpath(
                    "//*[local-name()='SolicitaDescargaRecibidosResult' "
                    "or local-name()='SolicitaDescargaEmitidosResult' "
                    "or local-name()='SolicitaDescargaFolioResult']"
                )
                result = matches[0] if matches else None
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
                matches = root.xpath(
                    "//*[local-name()='RespuestaDescargaMasivaTercerosSalida' "
                    "or local-name()='DescargaMasivaTercerosResult']"
                )
                result = matches[0] if matches else None
            if result is None:
                raise SATRequestError("No download result element in response")

            header_nodes = root.xpath("//*[local-name()='respuesta']")
            if header_nodes:
                cod_estatus = int(header_nodes[0].get("CodEstatus", "0") or "0")
                mensaje = header_nodes[0].get("Mensaje", "") or result.get("Mensaje", "")
            else:
                cod_estatus = int(result.get("CodEstatus", "0") or "0")
                mensaje = result.get("Mensaje", "")

            paquete_nodes = result.xpath(".//*[local-name()='Paquete']")
            if paquete_nodes:
                paquete_b64 = (paquete_nodes[0].text or "").strip()
            else:
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
            if elapsed > self.poll_max_seconds:
                logger.warning(
                    "Polling timeout after %.1fs (%s attempts, max=%ss)",
                    elapsed,
                    attempts,
                    self.poll_max_seconds,
                )
                raise SATRequestError(
                    f"Polling timeout after {self.poll_max_seconds}s"
                )

            # Wait before checking
            await asyncio.sleep(self.poll_interval_seconds)

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
                "IDescargaMasivaTercerosService/Descargar"
            ),
            authorization_token=await self._get_access_token(),
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
