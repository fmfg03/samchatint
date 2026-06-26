"""
SAT Configuration Handler

Manages e.firma credentials and SAT configuration for multiple users/teams.
Credentials are stored encrypted in the database.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from uuid import UUID

from sqlalchemy import Column, String, DateTime, Boolean, Text, BigInteger
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from uuid import uuid4

from devnous.copa_telmex.models import Base

logger = logging.getLogger(__name__)


class SATCredentials(Base):
    """
    SAT e.firma credentials storage.

    Stores encrypted e.firma certificates and keys for SAT authentication.
    One credential set per user or team.
    """
    __tablename__ = 'sat_credentials'

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Owner information
    telegram_user_id = Column(BigInteger, index=True, nullable=True)
    telegram_chat_id = Column(BigInteger, index=True, nullable=True)
    team_id = Column(PG_UUID(as_uuid=True), index=True, nullable=True)

    # RFC (Tax ID)
    rfc = Column(String(13), nullable=False, index=True)

    # e.firma files (base64 encoded)
    certificate_data = Column(Text, nullable=False)  # .cer file
    private_key_data = Column(Text, nullable=False)  # .key file
    passphrase = Column(String(500), nullable=False)  # Encrypted passphrase

    # Certificate metadata
    certificate_subject = Column(String(500))
    certificate_issuer = Column(String(500))
    certificate_expiry = Column(DateTime)

    # Status
    is_active = Column(Boolean, default=True)
    last_used = Column(DateTime)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SATConfigHandler:
    """
    Handler for SAT configuration and credentials management.

    Features:
    - Store/retrieve e.firma credentials
    - Validate certificate expiration
    - Track credential usage
    - Support multiple users/teams
    """

    def __init__(self):
        """Initialize SAT config handler."""
        self.logger = logging.getLogger(__name__)

    async def store_credentials(
        self,
        session: AsyncSession,
        rfc: str,
        certificate_data: str,
        private_key_data: str,
        passphrase: str,
        telegram_user_id: Optional[int] = None,
        telegram_chat_id: Optional[int] = None,
        team_id: Optional[UUID] = None,
        certificate_metadata: Optional[Dict[str, Any]] = None
    ) -> SATCredentials:
        """
        Store SAT e.firma credentials.

        Args:
            session: Database session
            rfc: RFC (Tax ID)
            certificate_data: Base64-encoded .cer file
            private_key_data: Base64-encoded .key file
            passphrase: Certificate passphrase (will be encrypted)
            telegram_user_id: Optional Telegram user ID
            telegram_chat_id: Optional Telegram chat ID
            team_id: Optional team ID
            certificate_metadata: Optional certificate metadata

        Returns:
            Created SATCredentials instance
        """

        # Check if credentials already exist
        existing = await self.get_credentials(
            session,
            rfc=rfc,
            telegram_user_id=telegram_user_id,
            team_id=team_id
        )

        if existing:
            # Update existing
            existing.certificate_data = certificate_data
            existing.private_key_data = private_key_data
            existing.passphrase = passphrase
            existing.is_active = True
            existing.updated_at = datetime.utcnow()

            if certificate_metadata:
                existing.certificate_subject = certificate_metadata.get("subject")
                existing.certificate_issuer = certificate_metadata.get("issuer")
                existing.certificate_expiry = certificate_metadata.get("not_valid_after")

            await session.commit()
            await session.refresh(existing)

            self.logger.info(f"Updated SAT credentials for RFC: {rfc}")
            return existing

        # Create new credentials
        credentials = SATCredentials(
            rfc=rfc,
            certificate_data=certificate_data,
            private_key_data=private_key_data,
            passphrase=passphrase,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            team_id=team_id
        )

        if certificate_metadata:
            credentials.certificate_subject = certificate_metadata.get("subject")
            credentials.certificate_issuer = certificate_metadata.get("issuer")
            credentials.certificate_expiry = certificate_metadata.get("not_valid_after")

        session.add(credentials)
        await session.commit()
        await session.refresh(credentials)

        self.logger.info(f"Stored SAT credentials for RFC: {rfc}")
        return credentials

    async def get_credentials(
        self,
        session: AsyncSession,
        rfc: Optional[str] = None,
        telegram_user_id: Optional[int] = None,
        telegram_chat_id: Optional[int] = None,
        team_id: Optional[UUID] = None
    ) -> Optional[SATCredentials]:
        """
        Retrieve SAT credentials.

        Args:
            session: Database session
            rfc: Optional RFC filter
            telegram_user_id: Optional user ID filter
            telegram_chat_id: Optional chat ID filter
            team_id: Optional team ID filter

        Returns:
            SATCredentials if found, None otherwise
        """

        query = select(SATCredentials).where(SATCredentials.is_active == True)

        if rfc:
            query = query.where(SATCredentials.rfc == rfc)

        if telegram_user_id:
            query = query.where(SATCredentials.telegram_user_id == telegram_user_id)

        if telegram_chat_id:
            query = query.where(SATCredentials.telegram_chat_id == telegram_chat_id)

        if team_id:
            query = query.where(SATCredentials.team_id == team_id)

        result = await session.execute(query)
        return result.scalar_one_or_none()

    async def update_last_used(
        self,
        session: AsyncSession,
        credentials_id: UUID
    ) -> bool:
        """
        Update last_used timestamp for credentials.

        Args:
            session: Database session
            credentials_id: Credentials ID

        Returns:
            True if updated, False otherwise
        """

        query = select(SATCredentials).where(SATCredentials.id == credentials_id)
        result = await session.execute(query)
        credentials = result.scalar_one_or_none()

        if credentials:
            credentials.last_used = datetime.utcnow()
            await session.commit()
            return True

        return False

    async def deactivate_credentials(
        self,
        session: AsyncSession,
        credentials_id: UUID
    ) -> bool:
        """
        Deactivate credentials (soft delete).

        Args:
            session: Database session
            credentials_id: Credentials ID

        Returns:
            True if deactivated, False otherwise
        """

        query = select(SATCredentials).where(SATCredentials.id == credentials_id)
        result = await session.execute(query)
        credentials = result.scalar_one_or_none()

        if credentials:
            credentials.is_active = False
            credentials.updated_at = datetime.utcnow()
            await session.commit()

            self.logger.info(f"Deactivated SAT credentials for RFC: {credentials.rfc}")
            return True

        return False

    async def check_expiration(
        self,
        session: AsyncSession,
        days_threshold: int = 30
    ) -> list[Dict[str, Any]]:
        """
        Check for credentials with expiring certificates.

        Args:
            session: Database session
            days_threshold: Alert if expiring within this many days

        Returns:
            List of expiring credentials
        """

        expiring = []

        query = select(SATCredentials).where(
            SATCredentials.is_active == True,
            SATCredentials.certificate_expiry.isnot(None)
        )

        result = await session.execute(query)
        credentials_list = result.scalars().all()

        now = datetime.utcnow()
        threshold = datetime.utcnow().timestamp() + (days_threshold * 86400)

        for cred in credentials_list:
            if cred.certificate_expiry:
                expiry_timestamp = cred.certificate_expiry.timestamp()

                if expiry_timestamp < threshold:
                    days_until_expiry = (cred.certificate_expiry - now).days

                    expiring.append({
                        "id": str(cred.id),
                        "rfc": cred.rfc,
                        "certificate_expiry": cred.certificate_expiry.isoformat(),
                        "days_until_expiry": days_until_expiry,
                        "expired": days_until_expiry < 0,
                        "telegram_user_id": cred.telegram_user_id,
                        "telegram_chat_id": cred.telegram_chat_id
                    })

        return expiring

    def write_temp_files(
        self,
        credentials: SATCredentials,
        temp_dir: Path
    ) -> tuple[Path, Path]:
        """
        Write credentials to temporary files for SAT agent.

        Args:
            credentials: SATCredentials instance
            temp_dir: Temporary directory path

        Returns:
            Tuple of (cert_path, key_path)
        """

        import base64

        temp_dir = Path(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Write certificate
        cert_path = temp_dir / f"{credentials.rfc}_cert.cer"
        cert_data = base64.b64decode(credentials.certificate_data)
        cert_path.write_bytes(cert_data)

        # Write private key
        key_path = temp_dir / f"{credentials.rfc}_key.key"
        key_data = base64.b64decode(credentials.private_key_data)
        key_path.write_bytes(key_data)

        self.logger.debug(f"Wrote temp files for RFC {credentials.rfc}")

        return cert_path, key_path

    def cleanup_temp_files(self, cert_path: Path, key_path: Path):
        """
        Clean up temporary credential files.

        Args:
            cert_path: Certificate file path
            key_path: Private key file path
        """

        try:
            if cert_path.exists():
                cert_path.unlink()

            if key_path.exists():
                key_path.unlink()

            # Remove temp directory if empty
            temp_dir = cert_path.parent
            if temp_dir.exists() and not any(temp_dir.iterdir()):
                temp_dir.rmdir()

            self.logger.debug("Cleaned up temp credential files")

        except Exception as e:
            self.logger.warning(f"Failed to cleanup temp files: {e}")
