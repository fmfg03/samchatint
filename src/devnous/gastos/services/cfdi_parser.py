"""
CFDI XML Parser Service

Extracts all fields from CFDI XML files and returns structured data.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from ipaddress import ip_address, ip_network
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse
import requests

logger = logging.getLogger(__name__)

MAX_CFDI_XML_DOWNLOAD_BYTES = 15 * 1024 * 1024
_CFDI_XML_CHUNK_BYTES = 1024 * 1024
_PRIVATE_NETS = (
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),
    ip_network("::1/128"),
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
)


def _cfdi_xml_url_allowed(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host == "localhost":
        return False
    try:
        address = ip_address(host)
    except ValueError:
        return True
    return not any(address in net for net in _PRIVATE_NETS)


def download_xml(url: str) -> Optional[str]:
    """Download XML file from URL."""
    if not _cfdi_xml_url_allowed(url):
        logger.warning("Blocked CFDI XML download from disallowed URL")
        return None
    try:
        response = requests.get(
            url,
            timeout=30,
            stream=True,
            allow_redirects=False,
        )
        if response.status_code == 200:
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=_CFDI_XML_CHUNK_BYTES):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_CFDI_XML_DOWNLOAD_BYTES:
                    logger.warning("CFDI XML download exceeded max size")
                    return None
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace")
        logger.error(f"Failed to download XML: {response.status_code}")
        return None
    except Exception as e:
        logger.error(f"Error downloading XML: {e}")
        return None


def parse_datetime(date_str: str) -> Optional[datetime]:
    """Parse CFDI datetime string."""
    if not date_str:
        return None
    try:
        # CFDI format: 2025-11-03T19:54:52
        return datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')
    except:
        try:
            # With timezone: 2025-11-03T19:54:52Z
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except:
            logger.warning(f"Could not parse datetime: {date_str}")
            return None


def parse_cfdi_xml(xml_content: str) -> Dict[str, Any]:
    """
    Parse CFDI XML and extract all fields.
    
    Returns dict with all extracted CFDI data.
    """
    try:
        root = ET.fromstring(xml_content)
        
        # Check if root itself is Comprobante (most common case)
        comprobante = None
        if root.tag == '{http://www.sat.gob.mx/cfd/4}Comprobante' or root.tag == 'Comprobante':
            comprobante = root
        else:
            # Find Comprobante as child element (try with and without namespace)
            comprobante = root.find('.//{http://www.sat.gob.mx/cfd/4}Comprobante')
            if comprobante is None:
                comprobante = root.find('.//Comprobante')
        
        if comprobante is None:
            logger.error(f"Could not find Comprobante element. Root tag: {root.tag}")
            return {}
        
        # Extract Emisor
        emisor = comprobante.find('.//{http://www.sat.gob.mx/cfd/4}Emisor')
        if emisor is None:
            emisor = comprobante.find('.//Emisor')
        
        # Extract Receptor
        receptor = comprobante.find('.//{http://www.sat.gob.mx/cfd/4}Receptor')
        if receptor is None:
            receptor = comprobante.find('.//Receptor')
        
        # Extract TimbreFiscalDigital
        timbre = root.find('.//{http://www.sat.gob.mx/TimbreFiscalDigital}TimbreFiscalDigital')
        if timbre is None:
            timbre = root.find('.//TimbreFiscalDigital')
        
        # Extract Conceptos
        conceptos_elem = comprobante.find('.//{http://www.sat.gob.mx/cfd/4}Conceptos')
        if conceptos_elem is None:
            conceptos_elem = comprobante.find('.//Conceptos')
        
        conceptos_list = []
        if conceptos_elem is not None:
            for concepto in conceptos_elem.findall('.//{http://www.sat.gob.mx/cfd/4}Concepto') or \
                          conceptos_elem.findall('.//Concepto'):
                concepto_data = {
                    'clave_prod_serv': concepto.get('ClaveProdServ', ''),
                    'objeto_imp': concepto.get('ObjetoImp', ''),
                    'cantidad': float(concepto.get('Cantidad', 0) or 0),
                    'clave_unidad': concepto.get('ClaveUnidad', ''),
                    'unidad': concepto.get('Unidad', ''),
                    'descripcion': concepto.get('Descripcion', ''),
                    'valor_unitario': float(concepto.get('ValorUnitario', 0) or 0),
                    'importe': float(concepto.get('Importe', 0) or 0),
                    'descuento': float(concepto.get('Descuento', 0) or 0),
                }
                
                # Extract Impuestos from Concepto
                impuestos_elem = concepto.find('.//{http://www.sat.gob.mx/cfd/4}Impuestos')
                if impuestos_elem is None:
                    impuestos_elem = concepto.find('.//Impuestos')
                
                if impuestos_elem is not None:
                    traslados = []
                    for traslado in impuestos_elem.findall('.//{http://www.sat.gob.mx/cfd/4}Traslado') or \
                                  impuestos_elem.findall('.//Traslado'):
                        traslados.append({
                            'base': float(traslado.get('Base', 0) or 0),
                            'impuesto': traslado.get('Impuesto', ''),
                            'tipo_factor': traslado.get('TipoFactor', ''),
                            'tasa_o_cuota': float(traslado.get('TasaOCuota', 0) or 0),
                            'importe': float(traslado.get('Importe', 0) or 0),
                        })
                    concepto_data['impuestos'] = traslados
                
                conceptos_list.append(concepto_data)
        
        # Extract Impuestos (comprobante level)
        impuestos_elem = comprobante.find('.//{http://www.sat.gob.mx/cfd/4}Impuestos')
        if impuestos_elem is None:
            impuestos_elem = comprobante.find('.//Impuestos')
        
        # Extract Traslados (VAT)
        traslados_list = []
        if impuestos_elem is not None:
            traslados_elem = impuestos_elem.find('.//{http://www.sat.gob.mx/cfd/4}Traslados')
            if traslados_elem is None:
                traslados_elem = impuestos_elem.find('.//Traslados')
            
            if traslados_elem is not None:
                for traslado in traslados_elem.findall('.//{http://www.sat.gob.mx/cfd/4}Traslado') or \
                              traslados_elem.findall('.//Traslado'):
                    traslados_list.append({
                        'impuesto': traslado.get('Impuesto', ''),
                        'tipo_factor': traslado.get('TipoFactor', ''),
                        'tasa_o_cuota': float(traslado.get('TasaOCuota', 0) or 0),
                        'importe': float(traslado.get('Importe', 0) or 0),
                        'base': float(traslado.get('Base', 0) or 0),
                    })
        
        # Extract Retenciones (Withholdings)
        retenciones_list = []
        if impuestos_elem is not None:
            retenciones_elem = impuestos_elem.find('.//{http://www.sat.gob.mx/cfd/4}Retenciones')
            if retenciones_elem is None:
                retenciones_elem = impuestos_elem.find('.//Retenciones')
            
            if retenciones_elem is not None:
                for retencion in retenciones_elem.findall('.//{http://www.sat.gob.mx/cfd/4}Retencion') or \
                              retenciones_elem.findall('.//Retencion'):
                    retenciones_list.append({
                        'impuesto': retencion.get('Impuesto', ''),
                        'importe': float(retencion.get('Importe', 0) or 0),
                    })
        
        # Combine traslados and retenciones in impuestos_detalle
        impuestos_detalle = {
            'traslados': traslados_list,
            'retenciones': retenciones_list
        }
        
        # Extract main concepto descripcion (first concepto's descripcion)
        descripcion_concepto_principal = ''
        if conceptos_list and len(conceptos_list) > 0:
            descripcion_concepto_principal = conceptos_list[0].get('descripcion', '')
        
        # Build result dict
        result = {
            # Comprobante
            'version': comprobante.get('Version', ''),
            'serie': comprobante.get('Serie', ''),
            'folio': comprobante.get('Folio', ''),
            'fecha': parse_datetime(comprobante.get('Fecha', '')),
            'sello': comprobante.get('Sello', ''),
            'forma_pago': comprobante.get('FormaPago', ''),
            'no_certificado': comprobante.get('NoCertificado', ''),
            'certificado': comprobante.get('Certificado', ''),
            'subtotal': float(comprobante.get('SubTotal', 0) or 0),
            'descuento': float(comprobante.get('Descuento', 0) or 0),
            'moneda': comprobante.get('Moneda', ''),
            'tipo_cambio': float(comprobante.get('TipoCambio', 0) or 0),
            'total': float(comprobante.get('Total', 0) or 0),
            'tipo_de_comprobante': comprobante.get('TipoDeComprobante', ''),
            'metodo_pago': comprobante.get('MetodoPago', ''),
            'lugar_expedicion': comprobante.get('LugarExpedicion', ''),
            'exportacion': comprobante.get('Exportacion', ''),
            
            # Emisor
            'emisor_rfc': emisor.get('Rfc', '') if emisor is not None else '',
            'emisor_nombre': emisor.get('Nombre', '') if emisor is not None else '',
            'emisor_regimen_fiscal': emisor.get('RegimenFiscal', '') if emisor is not None else '',
            
            # Receptor
            'receptor_rfc': receptor.get('Rfc', '') if receptor is not None else '',
            'receptor_nombre': receptor.get('Nombre', '') if receptor is not None else '',
            'receptor_uso_cfdi': receptor.get('UsoCFDI', '') if receptor is not None else '',
            'receptor_domicilio_fiscal': receptor.get('DomicilioFiscalReceptor', '') if receptor is not None else '',
            'receptor_regimen_fiscal': receptor.get('RegimenFiscalReceptor', '') if receptor is not None else '',
            
            # TimbreFiscalDigital
            'timbre_version': timbre.get('Version', '') if timbre is not None else '',
            'cfdi_uuid': timbre.get('UUID', '') if timbre is not None else '',
            'fecha_timbrado': parse_datetime(timbre.get('FechaTimbrado', '')) if timbre is not None else None,
            'rfc_prov_certif': timbre.get('RfcProvCertif', '') if timbre is not None else '',
            'sello_cfd': timbre.get('SelloCFD', '') if timbre is not None else '',
            'no_certificado_sat': timbre.get('NoCertificadoSAT', '') if timbre is not None else '',
            'sello_sat': timbre.get('SelloSAT', '') if timbre is not None else '',
            
            # Impuestos
            'total_impuestos_trasladados': float(impuestos_elem.get('TotalImpuestosTrasladados', 0) or 0) if impuestos_elem is not None else 0.0,
            'conceptos': conceptos_list,
            'descripcion_concepto_principal': descripcion_concepto_principal,
            'impuestos_detalle': impuestos_detalle,
            
            # Raw XML
            'xml_raw': xml_content,
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Error parsing CFDI XML: {e}", exc_info=True)
        return {}


def extract_cfdi_data(xml_url: str) -> Dict[str, Any]:
    """
    Download and parse CFDI XML from URL.
    
    Returns dict with extracted data or empty dict on error.
    """
    xml_content = download_xml(xml_url)
    if not xml_content:
        return {}
    
    return parse_cfdi_xml(xml_content)





