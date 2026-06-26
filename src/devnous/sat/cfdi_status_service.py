"""SAT CFDI status consultation service.

This module wraps the SAT ConsultaCFDI SOAP endpoint behind a small,
testable API. It does not require e.firma credentials.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional
from urllib.parse import quote

import aiohttp
from lxml import etree

logger = logging.getLogger(__name__)


SAT_CFDI_STATUS_ENDPOINT = (
    "https://consultaqr.facturaelectronica.sat.gob.mx/"
    "ConsultaCFDIService.svc"
)
SAT_CFDI_STATUS_SOAP_ACTION = "http://tempuri.org/IConsultaCFDIService/Consulta"


@dataclass(frozen=True)
class CFDIStatusRequest:
    uuid: str
    rfc_emisor: str
    rfc_receptor: str
    total: str


def normalize_cfdi_status(value: Optional[str]) -> str:
    status = (value or "").strip().lower()
    if "vigente" in status:
        return "vigente"
    if "cancelad" in status:
        return "cancelado"
    if "no encontrado" in status or "no se encontr" in status:
        return "no_encontrado"
    if not status:
        return "error"
    return "desconocido"


def normalize_total_for_sat(total: Any) -> str:
    try:
        value = Decimal(str(total).strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        raise ValueError("Total CFDI inválido.")
    if value < 0:
        raise ValueError("Total CFDI inválido.")
    return f"{value:.6f}"


def validate_cfdi_status_request(
    *,
    uuid: str,
    rfc_emisor: str,
    rfc_receptor: str,
    total: Any,
) -> CFDIStatusRequest:
    uuid_clean = (uuid or "").strip().upper()
    emisor_clean = (rfc_emisor or "").strip().upper()
    receptor_clean = (rfc_receptor or "").strip().upper()
    if not uuid_clean:
        raise ValueError("UUID requerido.")
    if not emisor_clean:
        raise ValueError("RFC emisor requerido.")
    if not receptor_clean:
        raise ValueError("RFC receptor requerido.")
    return CFDIStatusRequest(
        uuid=uuid_clean,
        rfc_emisor=emisor_clean,
        rfc_receptor=receptor_clean,
        total=normalize_total_for_sat(total),
    )


def build_cfdi_status_query(request: CFDIStatusRequest) -> str:
    return (
        f"?re={quote(request.rfc_emisor)}"
        f"&rr={quote(request.rfc_receptor)}"
        f"&tt={quote(request.total)}"
        f"&id={quote(request.uuid)}"
    )


def build_cfdi_status_soap_request(request: CFDIStatusRequest) -> str:
    expresion = build_cfdi_status_query(request)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:tem="http://tempuri.org/">
  <soapenv:Header/>
  <soapenv:Body>
    <tem:Consulta>
      <tem:expresionImpresa><![CDATA[{expresion}]]></tem:expresionImpresa>
    </tem:Consulta>
  </soapenv:Body>
</soapenv:Envelope>"""


def _first_text(root: etree._Element, local_name: str) -> str:
    result = root.xpath(f"//*[local-name()='{local_name}']")
    if not result:
        return ""
    value = result[0]
    return (value.text or "").strip()


def parse_cfdi_status_response(response_xml: str) -> Dict[str, Any]:
    try:
        root = etree.fromstring(response_xml.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Respuesta SAT inválida: {exc}") from exc

    codigo_estatus = _first_text(root, "CodigoEstatus")
    estado = _first_text(root, "Estado")
    es_cancelable = _first_text(root, "EsCancelable")
    estatus_cancelacion = _first_text(root, "EstatusCancelacion")
    normalized = normalize_cfdi_status(estado or codigo_estatus)
    return {
        "status": normalized,
        "codigo_estatus": codigo_estatus,
        "estado": estado,
        "es_cancelable": es_cancelable,
        "estatus_cancelacion": estatus_cancelacion,
        "raw": {
            "codigo_estatus": codigo_estatus,
            "estado": estado,
            "es_cancelable": es_cancelable,
            "estatus_cancelacion": estatus_cancelacion,
        },
    }


class SATCFDIStatusService:
    def __init__(self, endpoint: str = SAT_CFDI_STATUS_ENDPOINT) -> None:
        self.endpoint = endpoint

    async def consult_status(
        self,
        *,
        uuid: str,
        rfc_emisor: str,
        rfc_receptor: str,
        total: Any,
    ) -> Dict[str, Any]:
        request = validate_cfdi_status_request(
            uuid=uuid,
            rfc_emisor=rfc_emisor,
            rfc_receptor=rfc_receptor,
            total=total,
        )
        soap_request = build_cfdi_status_soap_request(request)
        response_xml = await self._post_soap(soap_request)
        parsed = parse_cfdi_status_response(response_xml)
        parsed["request"] = {
            "uuid": request.uuid,
            "rfc_emisor": request.rfc_emisor,
            "rfc_receptor": request.rfc_receptor,
            "total": request.total,
        }
        return parsed

    async def _post_soap(self, soap_request: str) -> str:
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": SAT_CFDI_STATUS_SOAP_ACTION,
        }
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.post(
                    self.endpoint,
                    data=soap_request.encode("utf-8"),
                    headers=headers,
                ) as response:
                    text = await response.text()
                    if response.status != 200:
                        logger.warning(
                            "SAT CFDI status HTTP error",
                            extra={"status_code": response.status},
                        )
                        return _error_payload(f"HTTP {response.status}")
                    return text
            except aiohttp.ClientError as exc:
                logger.warning("SAT CFDI status request failed: %s", exc)
                return _error_payload("No se pudo consultar el SAT")


def _error_payload(message: str) -> str:
    return f"""<Envelope><Body><ConsultaResponse><ConsultaResult>
        <CodigoEstatus>{message}</CodigoEstatus>
        <Estado>Error</Estado>
    </ConsultaResult></ConsultaResponse></Body></Envelope>"""
