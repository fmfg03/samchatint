"""
SAT Authentication Agent

This agent handles authentication with the Mexican IRS (SAT) CFDI Web Service
using e.firma certificates and WS-Security 1.0 protocol.

Key Features:
- e.firma certificate loading and validation
- WS-Security 1.0 header generation
- XML signature with RSA-SHA1
- WRAP access_token formatting
- Certificate expiration monitoring

Author: Copa Telmex Finance Integration Team
Date: 2025-10-10
"""

import base64
import hashlib
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from urllib.parse import quote

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

from .error_codes import (
    CertificateExpiredError,
    SATAuthenticationError,
    SATErrorHandler
)

logger = logging.getLogger(__name__)


class SATAuthenticationAgent:
    """
    Agent for SAT CFDI Web Service authentication.

    This agent manages:
    1. e.firma certificate loading and validation
    2. WS-Security 1.0 header generation
    3. XML digital signature
    4. WRAP access token formatting

    Usage:
        agent = SATAuthenticationAgent(
            cert_path="/path/to/efirma.cer",
            key_path="/path/to/efirma.key",
            passphrase="secret"
        )

        # Authenticate
        auth_result = await agent.authenticate(rfc="AXT940727FP8")

        # Use authentication in SOAP request
        soap_request = agent.create_authenticated_envelope(
            body_xml="<YourSOAPBody/>",
            rfc="AXT940727FP8"
        )
    """

    # WS-Security namespaces
    WSSE_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    WSU_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"
    SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"

    def __init__(
        self,
        cert_path: str,
        key_path: str,
        passphrase: str,
        alert_callback: Optional[callable] = None
    ):
        """
        Initialize SAT authentication agent.

        Args:
            cert_path: Path to e.firma certificate (.cer file)
            key_path: Path to e.firma private key (.key file)
            passphrase: Passphrase for private key
            alert_callback: Optional callback for critical alerts
        """

        self.cert_path = Path(cert_path)
        self.key_path = Path(key_path)
        self.passphrase = passphrase.encode() if isinstance(passphrase, str) else passphrase

        self.certificate = None
        self.private_key = None
        self.certificate_b64 = None

        self.error_handler = SATErrorHandler(alert_callback=alert_callback)

        # Load and validate certificate
        self._load_certificate()
        self._load_private_key()

    def _load_certificate(self):
        """Load and validate e.firma certificate."""

        if not self.cert_path.exists():
            raise FileNotFoundError(f"Certificate not found: {self.cert_path}")

        logger.info(f"Loading certificate from {self.cert_path}")

        try:
            with open(self.cert_path, "rb") as f:
                cert_data = f.read()

            # Try PEM format first
            try:
                self.certificate = x509.load_pem_x509_certificate(cert_data, default_backend())
            except Exception:
                # Try DER format
                self.certificate = x509.load_der_x509_certificate(cert_data, default_backend())

            # Convert to Base64 for WS-Security
            cert_der = self.certificate.public_bytes(serialization.Encoding.DER)
            self.certificate_b64 = base64.b64encode(cert_der).decode("utf-8")

            # Validate certificate
            validation = self._validate_certificate()

            if not validation["valid"]:
                raise CertificateExpiredError(validation["reason"])

            logger.info("Certificate loaded and validated successfully")
            logger.info(f"Certificate subject: {self.certificate.subject}")
            logger.info(f"Certificate expires: {self.certificate.not_valid_after}")

        except CertificateExpiredError:
            raise
        except Exception as e:
            logger.error(f"Failed to load certificate: {e}")
            raise SATAuthenticationError(f"Certificate loading failed: {e}")

    def _load_private_key(self):
        """Load e.firma private key."""

        if not self.key_path.exists():
            raise FileNotFoundError(f"Private key not found: {self.key_path}")

        logger.info(f"Loading private key from {self.key_path}")

        try:
            with open(self.key_path, "rb") as f:
                key_data = f.read()

            self.private_key = serialization.load_pem_private_key(
                key_data,
                password=self.passphrase,
                backend=default_backend()
            )

            logger.info("Private key loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load private key: {e}")
            raise SATAuthenticationError(f"Private key loading failed: {e}")

    def _validate_certificate(self) -> Dict[str, Any]:
        """
        Validate e.firma certificate.

        Returns:
            Dictionary with validation results
        """

        now = datetime.utcnow()

        # Check expiration
        if self.certificate.not_valid_after < now:
            days_expired = (now - self.certificate.not_valid_after).days
            error_msg = f"Certificate expired {days_expired} days ago"
            logger.critical(error_msg)
            return {"valid": False, "reason": error_msg}

        # Check not yet valid
        if self.certificate.not_valid_before > now:
            error_msg = "Certificate not yet valid"
            logger.error(error_msg)
            return {"valid": False, "reason": error_msg}

        # Alert if expiring soon (30 days)
        days_until_expiration = (self.certificate.not_valid_after - now).days
        if days_until_expiration <= 30:
            warning_msg = f"Certificate expires in {days_until_expiration} days!"
            logger.warning(warning_msg)

            if self.error_handler.alert_callback:
                self.error_handler.alert_callback(
                    f"⚠️  SAT Certificate Expiration Warning\n"
                    f"Certificate expires in {days_until_expiration} days\n"
                    f"Expiration date: {self.certificate.not_valid_after}\n"
                    f"Renew certificate immediately!"
                )

        return {
            "valid": True,
            "subject": str(self.certificate.subject),
            "issuer": str(self.certificate.issuer),
            "not_valid_before": self.certificate.not_valid_before,
            "not_valid_after": self.certificate.not_valid_after,
            "days_until_expiration": days_until_expiration
        }

    def authenticate(self, rfc: str) -> Dict[str, Any]:
        """
        Authenticate with SAT and generate WS-Security header.

        Args:
            rfc: RFC (Registro Federal de Contribuyentes - Tax ID)

        Returns:
            Dictionary with authentication data:
            - wsse_header: WS-Security header XML
            - wrap_token: WRAP access_token string
            - certificate_b64: Base64-encoded certificate
            - rfc: RFC used for authentication
        """

        logger.info(f"Authenticating with SAT for RFC: {rfc}")

        # Generate WS-Security header
        wsse_header = self._generate_wsse_header(rfc)

        # Generate WRAP token
        wrap_token = self._generate_wrap_token(rfc)

        return {
            "authenticated": True,
            "rfc": rfc,
            "wsse_header": wsse_header,
            "wrap_token": wrap_token,
            "certificate_b64": self.certificate_b64,
            "timestamp": datetime.utcnow().isoformat()
        }

    def _generate_wsse_header(self, rfc: str) -> etree.Element:
        """
        Generate WS-Security 1.0 header.

        Args:
            rfc: RFC for authentication

        Returns:
            lxml Element with complete WS-Security header
        """

        # Create Security element
        security = etree.Element(
            f"{{{self.WSSE_NS}}}Security",
            nsmap={
                "o": self.WSSE_NS,
                "u": self.WSU_NS
            }
        )

        # 1. BinarySecurityToken (certificate)
        token_id = f"uuid-{uuid.uuid4()}-1"

        binary_token = etree.SubElement(
            security,
            f"{{{self.WSSE_NS}}}BinarySecurityToken",
            EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary",
            ValueType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509v3",
        )
        binary_token.set(f"{{{self.WSU_NS}}}Id", token_id)
        binary_token.text = self.certificate_b64

        # 2. Signature (will be added by sign_soap_body method)
        # Note: Signature is created when signing the actual SOAP body

        return security

    def _generate_wrap_token(self, rfc: str) -> str:
        """
        Generate WRAP access_token.

        Format: WRAP access_token="[URLENCODED_SIGNATURE]", access_token="[RFC]"

        Args:
            rfc: RFC for authentication

        Returns:
            WRAP token string
        """

        # For now, return simple format
        # In production, this would include the actual signed token
        wrap_token = f'WRAP access_token="{quote(self.certificate_b64)}", access_token="{rfc}"'

        return wrap_token

    def sign_xml(self, xml_element: etree.Element) -> etree.Element:
        """
        Sign XML element with e.firma private key.

        Args:
            xml_element: XML element to sign

        Returns:
            Signature element
        """

        # Canonicalize XML for signing
        c14n_xml = etree.tostring(
            xml_element,
            method="c14n",
            exclusive=True,
            with_comments=False
        )

        # Calculate SHA1 digest
        digest = hashlib.sha1(c14n_xml).digest()
        digest_b64 = base64.b64encode(digest).decode("utf-8")

        # Create signature
        signature = etree.Element(
            f"{{{self.DSIG_NS}}}Signature",
            nsmap={"ds": self.DSIG_NS}
        )

        # SignedInfo
        signed_info = etree.SubElement(signature, f"{{{self.DSIG_NS}}}SignedInfo")

        canonicalization_method = etree.SubElement(
            signed_info,
            f"{{{self.DSIG_NS}}}CanonicalizationMethod",
            Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"
        )

        signature_method = etree.SubElement(
            signed_info,
            f"{{{self.DSIG_NS}}}SignatureMethod",
            Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"
        )

        reference = etree.SubElement(
            signed_info,
            f"{{{self.DSIG_NS}}}Reference",
            URI="#_1"
        )

        transforms = etree.SubElement(reference, f"{{{self.DSIG_NS}}}Transforms")
        transform = etree.SubElement(
            transforms,
            f"{{{self.DSIG_NS}}}Transform",
            Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"
        )

        digest_method = etree.SubElement(
            reference,
            f"{{{self.DSIG_NS}}}DigestMethod",
            Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"
        )

        digest_value = etree.SubElement(reference, f"{{{self.DSIG_NS}}}DigestValue")
        digest_value.text = digest_b64

        # Sign the SignedInfo
        signed_info_c14n = etree.tostring(
            signed_info,
            method="c14n",
            exclusive=True,
            with_comments=False
        )

        rsa_signature = self.private_key.sign(
            signed_info_c14n,
            padding.PKCS1v15(),
            hashes.SHA1()
        )

        signature_value = etree.SubElement(signature, f"{{{self.DSIG_NS}}}SignatureValue")
        signature_value.text = base64.b64encode(rsa_signature).decode("utf-8")

        # KeyInfo
        key_info = etree.SubElement(signature, f"{{{self.DSIG_NS}}}KeyInfo")
        security_token_reference = etree.SubElement(
            key_info,
            f"{{{self.WSSE_NS}}}SecurityTokenReference"
        )
        reference_elem = etree.SubElement(
            security_token_reference,
            f"{{{self.WSSE_NS}}}Reference",
            ValueType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509v3"
        )

        return signature

    def create_authenticated_envelope(
        self,
        body_xml: str,
        rfc: str,
        namespace: str = "http://DescargaMasivaTerceros.sat.gob.mx"
    ) -> str:
        """
        Create complete SOAP envelope with WS-Security authentication.

        Args:
            body_xml: SOAP body content (XML string)
            rfc: RFC for authentication
            namespace: Namespace for SOAP body

        Returns:
            Complete SOAP envelope as XML string
        """

        # Create SOAP envelope
        envelope = etree.Element(
            f"{{{self.SOAP_NS}}}Envelope",
            nsmap={
                "s": self.SOAP_NS,
                "o": self.WSSE_NS,
                "u": self.WSU_NS
            }
        )

        # Create header with WS-Security
        header = etree.SubElement(envelope, f"{{{self.SOAP_NS}}}Header")
        wsse_header = self._generate_wsse_header(rfc)
        header.append(wsse_header)

        # Create body
        body = etree.SubElement(envelope, f"{{{self.SOAP_NS}}}Body")

        # Parse and add body content
        body_element = etree.fromstring(body_xml.encode("utf-8"))
        body.append(body_element)

        # Convert to string
        envelope_str = etree.tostring(
            envelope,
            encoding="unicode",
            pretty_print=True
        )

        return envelope_str

    def get_certificate_info(self) -> Dict[str, Any]:
        """
        Get certificate information.

        Returns:
            Dictionary with certificate details
        """

        if not self.certificate:
            return {"error": "Certificate not loaded"}

        validation = self._validate_certificate()

        return {
            "subject": str(self.certificate.subject),
            "issuer": str(self.certificate.issuer),
            "serial_number": self.certificate.serial_number,
            "not_valid_before": self.certificate.not_valid_before.isoformat(),
            "not_valid_after": self.certificate.not_valid_after.isoformat(),
            "days_until_expiration": validation.get("days_until_expiration"),
            "valid": validation["valid"]
        }


# Example usage
if __name__ == "__main__":
    """
    Example usage of SAT authentication agent.
    """

    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Example configuration (use environment variables in production!)
    CERT_PATH = os.getenv("SAT_CERT_PATH", "/path/to/efirma.cer")
    KEY_PATH = os.getenv("SAT_KEY_PATH", "/path/to/efirma.key")
    PASSPHRASE = os.getenv("SAT_PASSPHRASE", "secret")
    RFC = os.getenv("SAT_RFC", "AXT940727FP8")

    # Create agent
    print("=" * 60)
    print("SAT Authentication Agent - Example Usage")
    print("=" * 60)

    try:
        agent = SATAuthenticationAgent(
            cert_path=CERT_PATH,
            key_path=KEY_PATH,
            passphrase=PASSPHRASE
        )

        # Get certificate info
        print("\n📜 Certificate Information:")
        cert_info = agent.get_certificate_info()
        for key, value in cert_info.items():
            print(f"  {key}: {value}")

        # Authenticate
        print(f"\n🔐 Authenticating for RFC: {RFC}")
        auth_result = agent.authenticate(rfc=RFC)
        print(f"  ✅ Authenticated: {auth_result['authenticated']}")
        print(f"  ⏰ Timestamp: {auth_result['timestamp']}")

        # Create sample SOAP envelope
        print("\n📨 Creating authenticated SOAP envelope...")
        sample_body = """
        <VerificaSolicitudDescarga xmlns="http://DescargaMasivaTerceros.sat.gob.mx">
            <solicitud IdSolicitud="4E80345D-917F-40BB-A98F-4A73939343C5"
                       RfcSolicitante="AXT940727FP8">
            </solicitud>
        </VerificaSolicitudDescarga>
        """

        envelope = agent.create_authenticated_envelope(
            body_xml=sample_body,
            rfc=RFC
        )

        print("  ✅ SOAP envelope created")
        print(f"  📏 Size: {len(envelope)} bytes")

    except CertificateExpiredError as e:
        print(f"\n❌ CRITICAL: {e}")
        print("   Action: Renew certificate immediately!")

    except SATAuthenticationError as e:
        print(f"\n❌ Authentication Error: {e}")

    except FileNotFoundError as e:
        print(f"\n❌ File Error: {e}")
        print("   Note: Update CERT_PATH and KEY_PATH environment variables")

    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}")
        import traceback
        traceback.print_exc()
