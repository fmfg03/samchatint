"""
SAT Handler for Expense Management Integration

Coordinates SAT CFDI downloads and links them to expense reports.
"""

import base64
import io
import logging
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .authentication_agent import SATAuthenticationAgent
from .verification_agent import CFDIVerificationAgent
from .config_handler import SATConfigHandler, SATCredentials
from .cfdi_status_service import SATCFDIStatusService
from .error_codes import (
    CertificateExpiredError,
    SATAuthenticationError,
    SATRateLimitError,
    SATRequestError
)

from devnous.gastos.models import ExpenseReport
from devnous.gastos.services.cfdi_ingestion_service import ingest_cfdi_xml

logger = logging.getLogger(__name__)


class SATExpenseHandler:
    """
    Handler for SAT integration with expense management.

    Features:
    - Download CFDIs from SAT
    - Link CFDIs to expense reports
    - Validate CFDI authenticity
    - Track download status
    - Certificate management
    """

    def __init__(self, use_testing_endpoint: bool = True):
        """
        Initialize SAT expense handler.

        Args:
            use_testing_endpoint: Use SAT testing endpoint (default: True)
        """
        self.config_handler = SATConfigHandler()
        self.cfdi_status_service = SATCFDIStatusService()
        self.use_testing_endpoint = use_testing_endpoint
        self.logger = logging.getLogger(__name__)

    def _default_expense_date_range(self, expense: ExpenseReport) -> tuple[datetime, datetime]:
        anchor = expense.fecha or datetime.utcnow()
        start = anchor - timedelta(days=7)
        end = anchor + timedelta(days=7)
        return start, end

    def _extract_xml_payloads(self, package_bytes: bytes) -> List[str]:
        if not package_bytes:
            return []
        try:
            with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
                payloads: List[str] = []
                for name in archive.namelist():
                    if name.lower().endswith(".xml"):
                        payloads.append(archive.read(name).decode("utf-8"))
                return payloads
        except zipfile.BadZipFile:
            text = package_bytes.decode("utf-8", errors="ignore").strip()
            return [text] if text else []

    async def _get_active_credentials(
        self,
        session: AsyncSession,
        *,
        telegram_user_id: Optional[int] = None,
        rfc: Optional[str] = None,
    ) -> Optional[SATCredentials]:
        return await self.config_handler.get_credentials(
            session=session,
            telegram_user_id=telegram_user_id,
            rfc=rfc,
        )

    async def create_download_request(
        self,
        session: AsyncSession,
        *,
        fecha_inicial: datetime,
        fecha_final: datetime,
        telegram_user_id: Optional[int] = None,
        rfc: Optional[str] = None,
        rfc_emisor: Optional[str] = None,
        rfc_receptor: Optional[str] = None,
    ) -> Dict[str, Any]:
        credentials = await self._get_active_credentials(
            session,
            telegram_user_id=telegram_user_id,
            rfc=rfc,
        )
        if not credentials:
            return {
                "status": "error",
                "message": (
                    "❌ No hay credenciales SAT configuradas\n\n"
                    "Carga la e.firma antes de crear solicitudes SAT."
                ),
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cert_path, key_path = self.config_handler.write_temp_files(credentials, temp_path)
            try:
                auth_agent = SATAuthenticationAgent(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    passphrase=credentials.passphrase,
                )
                verification_agent = CFDIVerificationAgent(
                    auth_agent=auth_agent,
                    testing=self.use_testing_endpoint,
                )
                sat_result = await verification_agent.create_solicitud(
                    rfc_solicitante=credentials.rfc,
                    fecha_inicial=fecha_inicial,
                    fecha_final=fecha_final,
                    rfc_emisor=rfc_emisor,
                    rfc_receptor=rfc_receptor,
                )
                return {
                    "status": "success" if sat_result.get("accepted") else "error",
                    "message": sat_result.get("mensaje") or "Solicitud SAT procesada",
                    "result": sat_result,
                }
            finally:
                self.config_handler.cleanup_temp_files(cert_path, key_path)
                await self.config_handler.update_last_used(session, credentials.id)

    async def consult_cfdi_status(
        self,
        *,
        uuid: str,
        rfc_emisor: str,
        rfc_receptor: str,
        total: Any,
    ) -> Dict[str, Any]:
        """Consult SAT status for a CFDI without requiring e.firma."""

        try:
            return await self.cfdi_status_service.consult_status(
                uuid=uuid,
                rfc_emisor=rfc_emisor,
                rfc_receptor=rfc_receptor,
                total=total,
            )
        except ValueError as exc:
            return {
                "status": "error",
                "message": str(exc),
                "request": {
                    "uuid": (uuid or "").strip().upper(),
                    "rfc_emisor": (rfc_emisor or "").strip().upper(),
                    "rfc_receptor": (rfc_receptor or "").strip().upper(),
                    "total": str(total or "").strip(),
                },
            }

    async def check_download_request(
        self,
        session: AsyncSession,
        *,
        solicitud_id: str,
        telegram_user_id: Optional[int] = None,
        rfc: Optional[str] = None,
        poll_until_complete: bool = False,
    ) -> Dict[str, Any]:
        credentials = await self._get_active_credentials(
            session,
            telegram_user_id=telegram_user_id,
            rfc=rfc,
        )
        if not credentials:
            return {
                "status": "error",
                "message": (
                    "❌ No hay credenciales SAT configuradas\n\n"
                    "Carga la e.firma antes de consultar solicitudes SAT."
                ),
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cert_path, key_path = self.config_handler.write_temp_files(credentials, temp_path)
            try:
                auth_agent = SATAuthenticationAgent(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    passphrase=credentials.passphrase,
                )
                verification_agent = CFDIVerificationAgent(
                    auth_agent=auth_agent,
                    testing=self.use_testing_endpoint,
                )
                sat_result = await verification_agent.verify_solicitud(
                    solicitud_id=solicitud_id,
                    rfc=credentials.rfc,
                    poll_until_complete=poll_until_complete,
                )
                return {
                    "status": "success",
                    "message": sat_result.get("mensaje") or "Solicitud consultada",
                    "result": sat_result,
                }
            finally:
                self.config_handler.cleanup_temp_files(cert_path, key_path)
                await self.config_handler.update_last_used(session, credentials.id)

    async def process_download_request(
        self,
        session: AsyncSession,
        *,
        solicitud_id: str,
        telegram_user_id: Optional[int] = None,
        rfc: Optional[str] = None,
        expense: Optional[ExpenseReport] = None,
    ) -> Dict[str, Any]:
        credentials = await self._get_active_credentials(
            session,
            telegram_user_id=telegram_user_id,
            rfc=rfc,
        )
        if not credentials:
            return {
                "status": "error",
                "message": (
                    "❌ No hay credenciales SAT configuradas\n\n"
                    "Carga la e.firma antes de procesar paquetes SAT."
                ),
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cert_path, key_path = self.config_handler.write_temp_files(credentials, temp_path)
            try:
                auth_agent = SATAuthenticationAgent(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    passphrase=credentials.passphrase,
                )
                verification_agent = CFDIVerificationAgent(
                    auth_agent=auth_agent,
                    testing=self.use_testing_endpoint,
                )
                verification = await verification_agent.verify_solicitud(
                    solicitud_id=solicitud_id,
                    rfc=credentials.rfc,
                    poll_until_complete=True,
                )
                if verification.get("estado") != "Terminada":
                    return {
                        "status": "processing" if not verification.get("is_final") else "error",
                        "message": verification.get("mensaje") or verification.get("estado") or "Solicitud no lista",
                        "result": {"verification": verification},
                    }

                downloaded_packages = []
                ingested_cfdis = 0
                warnings: List[str] = []
                for package_id in verification.get("paquetes", []):
                    package_result = await verification_agent.download_package(
                        package_id=package_id,
                        rfc=credentials.rfc,
                    )
                    downloaded_packages.append(package_result)
                    for xml_payload in self._extract_xml_payloads(package_result.get("package_bytes", b"")):
                        await ingest_cfdi_xml(
                            session,
                            xml_payload,
                            source="sat",
                            entity=expense,
                            nova_request_id=solicitud_id,
                            numero_referencia=getattr(expense, "numero_referencia", None),
                            allow_shared=True,
                        )
                        ingested_cfdis += 1

                if ingested_cfdis == 0 and verification.get("num_cfdis", 0) > 0:
                    warnings.append(
                        "SAT reportó CFDIs, pero no se logró ingerir ningún XML del paquete."
                    )

                if expense is not None:
                    expense.nova_request_id = solicitud_id
                    expense.estado_factura = "completada" if ingested_cfdis > 0 else "error"
                    expense.mensaje_error = warnings[0] if warnings else None
                    await session.commit()

                return {
                    "status": "success" if not warnings else "warning",
                    "message": "Paquetes SAT procesados",
                    "result": {
                        "verification": verification,
                        "packages": downloaded_packages,
                        "ingested_cfdis": ingested_cfdis,
                        "warnings": warnings,
                    },
                }
            finally:
                self.config_handler.cleanup_temp_files(cert_path, key_path)
                await self.config_handler.update_last_used(session, credentials.id)

    async def create_download_request_for_expense(
        self,
        session: AsyncSession,
        expense_id: UUID,
        *,
        telegram_user_id: Optional[int] = None,
        fecha_inicial: Optional[datetime] = None,
        fecha_final: Optional[datetime] = None,
        rfc_emisor: Optional[str] = None,
        rfc_receptor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a SAT download request for an expense using stored e.firma."""

        query = select(ExpenseReport).where(ExpenseReport.id == expense_id)
        result = await session.execute(query)
        expense = result.scalar_one_or_none()
        if not expense:
            return {"status": "error", "message": "❌ Gasto no encontrado"}

        credentials = await self.config_handler.get_credentials(
            session=session,
            telegram_user_id=telegram_user_id,
        )
        if not credentials:
            return {
                "status": "error",
                "message": (
                    "❌ No hay credenciales SAT configuradas\n\n"
                    "Usa /sat_setup para configurar tus credenciales e.firma"
                ),
            }

        start_date, end_date = self._default_expense_date_range(expense)
        if fecha_inicial is not None:
            start_date = fecha_inicial
        if fecha_final is not None:
            end_date = fecha_final

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            cert_path, key_path = self.config_handler.write_temp_files(credentials, temp_path)
            try:
                auth_agent = SATAuthenticationAgent(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    passphrase=credentials.passphrase,
                )
                verification_agent = CFDIVerificationAgent(
                    auth_agent=auth_agent,
                    testing=self.use_testing_endpoint,
                )
                sat_result = await verification_agent.create_solicitud(
                    rfc_solicitante=credentials.rfc,
                    fecha_inicial=start_date,
                    fecha_final=end_date,
                    rfc_emisor=rfc_emisor,
                    rfc_receptor=rfc_receptor,
                )
                if not sat_result.get("accepted"):
                    return {
                        "status": "error",
                        "message": sat_result.get("mensaje") or "No se pudo crear la solicitud SAT",
                        "result": sat_result,
                    }

                expense.nova_request_id = sat_result["solicitud_id"]
                expense.estado_factura = "en_proceso"
                expense.mensaje_error = None
                await session.commit()
                return {
                    "status": "success",
                    "message": (
                        "✅ Solicitud SAT creada\n\n"
                        f"Solicitud: {sat_result['solicitud_id']}"
                    ),
                    "result": sat_result,
                }
            finally:
                self.config_handler.cleanup_temp_files(cert_path, key_path)
                await self.config_handler.update_last_used(session, credentials.id)

    async def setup_credentials(
        self,
        session: AsyncSession,
        rfc: str,
        certificate_file_data: bytes,
        private_key_file_data: bytes,
        passphrase: str,
        telegram_user_id: Optional[int] = None,
        telegram_chat_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Setup SAT e.firma credentials for a user.

        Args:
            session: Database session
            rfc: RFC (Tax ID)
            certificate_file_data: .cer file bytes
            private_key_file_data: .key file bytes
            passphrase: Certificate passphrase
            telegram_user_id: Optional Telegram user ID
            telegram_chat_id: Optional Telegram chat ID

        Returns:
            Result dictionary with status and message
        """

        try:
            # Encode files to base64
            cert_b64 = base64.b64encode(certificate_file_data).decode('utf-8')
            key_b64 = base64.b64encode(private_key_file_data).decode('utf-8')

            # Validate credentials by creating agent
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                cert_path = temp_path / "temp_cert.cer"
                key_path = temp_path / "temp_key.key"

                cert_path.write_bytes(certificate_file_data)
                key_path.write_bytes(private_key_file_data)

                # Test authentication
                auth_agent = SATAuthenticationAgent(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    passphrase=passphrase
                )

                # Get certificate info
                cert_info = auth_agent.get_certificate_info()

                if not cert_info.get("valid"):
                    return {
                        "status": "error",
                        "message": f"❌ Certificado inválido: {cert_info.get('subject', 'Unknown')}"
                    }

            # Store credentials
            credentials = await self.config_handler.store_credentials(
                session=session,
                rfc=rfc,
                certificate_data=cert_b64,
                private_key_data=key_b64,
                passphrase=passphrase,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                certificate_metadata=cert_info
            )

            days_until_expiry = cert_info.get("days_until_expiration", 0)
            expiry_warning = ""

            if days_until_expiry < 30:
                expiry_warning = f"\n\n⚠️  Certificado expira en {days_until_expiry} días"

            return {
                "status": "success",
                "credentials_id": str(credentials.id),
                "message": (
                    f"✅ Credenciales SAT configuradas exitosamente\n\n"
                    f"📋 RFC: {rfc}\n"
                    f"🔐 Certificado: {cert_info.get('subject', 'N/A')}\n"
                    f"📅 Válido hasta: {cert_info.get('not_valid_after', 'N/A')}"
                    f"{expiry_warning}"
                )
            }

        except CertificateExpiredError as e:
            self.logger.error(f"Certificate expired: {e}")
            return {
                "status": "error",
                "message": f"❌ Certificado expirado o revocado\n\n{str(e)}"
            }

        except Exception as e:
            self.logger.error(f"Failed to setup credentials: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"❌ Error al configurar credenciales: {str(e)}"
            }

    async def download_cfdi_for_expense(
        self,
        session: AsyncSession,
        expense_id: UUID,
        solicitud_id: Optional[str] = None,
        telegram_user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Download CFDI for an expense report.

        Args:
            session: Database session
            expense_id: Expense report ID
            solicitud_id: SAT download request ID
            telegram_user_id: Optional Telegram user ID

        Returns:
            Result dictionary with status and message
        """

        try:
            # Get expense
            query = select(ExpenseReport).where(ExpenseReport.id == expense_id)
            result = await session.execute(query)
            expense = result.scalar_one_or_none()

            if not expense:
                return {
                    "status": "error",
                    "message": "❌ Gasto no encontrado"
                }

            # Get credentials
            credentials = await self.config_handler.get_credentials(
                session=session,
                telegram_user_id=telegram_user_id
            )

            if not credentials:
                return {
                    "status": "error",
                    "message": (
                        "❌ No hay credenciales SAT configuradas\n\n"
                        "Usa /sat_setup para configurar tus credenciales e.firma"
                    )
                }

            # Create agents with temp files
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                cert_path, key_path = self.config_handler.write_temp_files(
                    credentials, temp_path
                )

                try:
                    # Create authentication agent
                    auth_agent = SATAuthenticationAgent(
                        cert_path=str(cert_path),
                        key_path=str(key_path),
                        passphrase=credentials.passphrase
                    )

                    # Create verification agent
                    verification_agent = CFDIVerificationAgent(
                        auth_agent=auth_agent,
                        testing=self.use_testing_endpoint
                    )

                    if not solicitud_id:
                        create_result = await verification_agent.create_solicitud(
                            rfc_solicitante=credentials.rfc,
                            fecha_inicial=self._default_expense_date_range(expense)[0],
                            fecha_final=self._default_expense_date_range(expense)[1],
                        )
                        if not create_result.get("accepted"):
                            expense.estado_factura = "error"
                            expense.mensaje_error = create_result.get("mensaje")
                            await session.commit()
                            return {
                                "status": "error",
                                "message": (
                                    "❌ No se pudo crear la solicitud SAT\n\n"
                                    f"{create_result.get('mensaje', 'Error desconocido')}"
                                ),
                                "result": create_result,
                            }
                        solicitud_id = create_result["solicitud_id"]

                    # Verify solicitud
                    result = await verification_agent.verify_solicitud(
                        solicitud_id=solicitud_id,
                        rfc=credentials.rfc,
                        poll_until_complete=True
                    )

                    # Update expense with results
                    expense.nova_request_id = solicitud_id
                    expense.estado_factura = result['estado']

                    if result['is_final']:
                        if result['estado'] == 'Terminada':
                            downloaded_packages = []
                            ingested_cfdis = 0
                            for package_id in result.get("paquetes", []):
                                package_result = await verification_agent.download_package(
                                    package_id=package_id,
                                    rfc=credentials.rfc,
                                )
                                downloaded_packages.append(package_result)
                                for xml_payload in self._extract_xml_payloads(
                                    package_result.get("package_bytes", b"")
                                ):
                                    await ingest_cfdi_xml(
                                        session,
                                        xml_payload,
                                        source="sat",
                                        entity=expense,
                                        nova_request_id=solicitud_id,
                                        numero_referencia=expense.numero_referencia,
                                        allow_shared=True,
                                    )
                                    ingested_cfdis += 1

                            expense.estado_factura = 'completada'
                            if ingested_cfdis == 0 and result.get("num_cfdis", 0) > 0:
                                expense.mensaje_error = (
                                    "SAT confirmó CFDIs, pero no se pudieron ingerir archivos XML."
                                )
                            else:
                                expense.mensaje_error = None

                            await session.commit()

                            return {
                                "status": "success",
                                "message": (
                                    f"✅ Descarga completada\n\n"
                                    f"📦 CFDIs encontrados: {result['num_cfdis']}\n"
                                    f"📥 Paquetes procesados: {len(downloaded_packages)}\n"
                                    f"🧾 CFDIs ingeridos: {ingested_cfdis}\n\n"
                                    f"Los CFDIs quedaron listos para conciliación."
                                ),
                                "result": {
                                    "verification": result,
                                    "packages": downloaded_packages,
                                    "ingested_cfdis": ingested_cfdis,
                                },
                            }

                        else:
                            # Error or rejected
                            expense.estado_factura = 'error'
                            expense.mensaje_error = result.get('mensaje', 'Error desconocido')

                            await session.commit()

                            return {
                                "status": "error",
                                "message": (
                                    f"❌ Error en descarga SAT\n\n"
                                    f"Estado: {result['estado']}\n"
                                    f"Mensaje: {result.get('mensaje', 'N/A')}"
                                )
                            }

                    else:
                        # Still processing
                        expense.estado_factura = 'en_proceso'
                        await session.commit()

                        return {
                            "status": "processing",
                            "message": (
                                f"⏳ Descarga en proceso\n\n"
                                f"Estado: {result['estado']}\n"
                                f"El SAT está procesando tu solicitud. "
                                f"Intenta nuevamente en unos minutos."
                            )
                        }

                finally:
                    # Cleanup temp files
                    self.config_handler.cleanup_temp_files(cert_path, key_path)

                    # Update last used
                    await self.config_handler.update_last_used(
                        session, credentials.id
                    )

        except SATRateLimitError as e:
            self.logger.warning(f"SAT rate limit: {e}")
            return {
                "status": "error",
                "message": (
                    "⚠️  Límite de solicitudes SAT excedido\n\n"
                    "El SAT tiene límites en el número de solicitudes.\n"
                    "Intenta nuevamente más tarde."
                )
            }

        except SATAuthenticationError as e:
            self.logger.error(f"SAT authentication error: {e}")
            return {
                "status": "error",
                "message": (
                    f"❌ Error de autenticación SAT\n\n"
                    f"{str(e)}\n\n"
                    "Verifica tus credenciales e.firma."
                )
            }

        except Exception as e:
            self.logger.error(f"Failed to download CFDI: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"❌ Error al descargar CFDI: {str(e)}"
            }

    async def get_credentials_status(
        self,
        session: AsyncSession,
        telegram_user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get SAT credentials status for a user.

        Args:
            session: Database session
            telegram_user_id: Optional Telegram user ID

        Returns:
            Status dictionary
        """

        credentials = await self.config_handler.get_credentials(
            session=session,
            telegram_user_id=telegram_user_id
        )

        if not credentials:
            return {
                "status": "not_configured",
                "message": (
                    "❌ No hay credenciales SAT configuradas\n\n"
                    "Para descargar CFDIs del SAT necesitas configurar "
                    "tus credenciales e.firma.\n\n"
                    "Usa /sat_setup para configurar."
                )
            }

        # Check expiration
        days_until_expiry = None
        expired = False

        if credentials.certificate_expiry:
            delta = credentials.certificate_expiry - datetime.utcnow()
            days_until_expiry = delta.days
            expired = days_until_expiry < 0

        status_emoji = "✅" if not expired else "❌"
        expiry_text = ""

        if expired:
            expiry_text = f"\n⚠️  CERTIFICADO EXPIRADO (hace {abs(days_until_expiry)} días)"
        elif days_until_expiry is not None and days_until_expiry < 30:
            expiry_text = f"\n⚠️  Certificado expira en {days_until_expiry} días"

        return {
            "status": "configured",
            "expired": expired,
            "days_until_expiry": days_until_expiry,
            "credentials": credentials,
            "message": (
                f"{status_emoji} Credenciales SAT configuradas\n\n"
                f"📋 RFC: {credentials.rfc}\n"
                f"📅 Expira: {credentials.certificate_expiry.strftime('%Y-%m-%d') if credentials.certificate_expiry else 'N/A'}\n"
                f"🕐 Último uso: {credentials.last_used.strftime('%Y-%m-%d %H:%M') if credentials.last_used else 'Nunca'}"
                f"{expiry_text}"
            )
        }

    async def check_expiring_certificates(
        self,
        session: AsyncSession,
        days_threshold: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Check for expiring certificates.

        Args:
            session: Database session
            days_threshold: Alert if expiring within this many days

        Returns:
            List of expiring credentials
        """

        return await self.config_handler.check_expiration(
            session=session,
            days_threshold=days_threshold
        )

    async def remove_credentials(
        self,
        session: AsyncSession,
        telegram_user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Remove SAT credentials for a user.

        Args:
            session: Database session
            telegram_user_id: Optional Telegram user ID

        Returns:
            Result dictionary
        """

        credentials = await self.config_handler.get_credentials(
            session=session,
            telegram_user_id=telegram_user_id
        )

        if not credentials:
            return {
                "status": "error",
                "message": "❌ No hay credenciales configuradas"
            }

        success = await self.config_handler.deactivate_credentials(
            session=session,
            credentials_id=credentials.id
        )

        if success:
            return {
                "status": "success",
                "message": (
                    "✅ Credenciales SAT eliminadas\n\n"
                    f"RFC: {credentials.rfc}"
                )
            }
        else:
            return {
                "status": "error",
                "message": "❌ Error al eliminar credenciales"
            }
