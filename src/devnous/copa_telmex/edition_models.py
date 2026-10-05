"""Explicit edition scope; installation belongs to owner-run migrations."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from .models import Base


class CopaTelmexTournamentEdition(Base):
    """Bind one local project edition to an exact operational roster slug.

    The cross-domain tournaments foreign key is enforced in the SQL migration.
    It is intentionally not resolved through Copa's independent ORM metadata.
    """

    __tablename__ = "copa_telmex_tournament_editions"
    __table_args__ = (
        UniqueConstraint(
            "tournament_id", "edition_year", name="uq_copa_telmex_project_edition"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tournament_id = Column(UUID(as_uuid=True), nullable=False)
    edition_year = Column(Integer, nullable=False)
    roster_slug = Column(String(80), nullable=False, unique=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
