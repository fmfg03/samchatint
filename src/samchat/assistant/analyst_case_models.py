from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from devnous.copa_telmex.models import Base


class AnalystCaseRecord(Base):
    """Persistent product-internal Analyst Workbench case."""

    __tablename__ = "analyst_cases"

    case_id = Column(String(80), primary_key=True)
    user_id = Column(String(120), nullable=False, index=True)
    role = Column(String(80), nullable=False)
    question = Column(Text, nullable=False)
    analyst_intent = Column(JSON, nullable=False)
    status = Column(String(40), nullable=False, index=True)
    evidence = Column(JSON, nullable=False)
    current_answer = Column(Text, nullable=False)
    next_questions = Column(JSON, nullable=False)
    suggested_routes = Column(JSON, nullable=False)
    caveats = Column(JSON, nullable=False)
    writes_policy = Column(JSON, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        index=True,
    )
    updated_by = Column(String(120), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by = Column(String(120), nullable=True)

    versions = relationship(
        "AnalystCaseVersionRecord",
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="AnalystCaseVersionRecord.version_number",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('open', 'waiting_context', 'analyzed', 'reviewed', 'closed')",
            name="check_analyst_cases_status",
        ),
        Index("idx_analyst_cases_updated_at", "updated_at"),
    )


class AnalystCaseVersionRecord(Base):
    """Immutable version snapshot for an AnalystCase."""

    __tablename__ = "analyst_case_versions"

    version_id = Column(String(96), primary_key=True)
    case_id = Column(
        String(80),
        ForeignKey("analyst_cases.case_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number = Column(Integer, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )
    created_by = Column(String(120), nullable=False)
    status = Column(String(40), nullable=False)
    answer = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=False)
    next_questions = Column(JSON, nullable=False)
    suggested_routes = Column(JSON, nullable=False)
    caveats = Column(JSON, nullable=False)
    answer_contract = Column(JSON, nullable=False)
    changed_fields = Column(JSON, nullable=False)

    case = relationship("AnalystCaseRecord", back_populates="versions")

    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "version_number",
            name="ux_analyst_case_versions_case_version",
        ),
        CheckConstraint(
            "status IN "
            "('open', 'waiting_context', 'analyzed', 'reviewed', 'closed')",
            name="check_analyst_case_versions_status",
        ),
    )
