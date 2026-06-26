"""
Common result and response patterns.

Consolidates duplicate result class patterns from:
- response_orchestrator.py (ResponseGenerationResult)
- advanced_response_generator.py (GenerationResult)
- learning_personalization.py (ABTestResult)
- Various debate modules
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ResultStatus(Enum):
    """Standard result status values."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ConfidenceLevel(Enum):
    """Standard confidence levels."""

    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    VERY_LOW = "very_low"


@dataclass
class BaseResult:
    """Base result class for all operations."""

    status: ResultStatus
    success: bool
    message: str = ""
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    execution_time_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Check if result indicates success."""
        return self.status == ResultStatus.SUCCESS

    @property
    def has_error(self) -> bool:
        """Check if result has error."""
        return self.error is not None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "status": self.status.value,
            "success": self.success,
            "message": self.message,
            "error": self.error,
            "timestamp": self.timestamp.isoformat(),
            "execution_time_ms": self.execution_time_ms,
            "metadata": self.metadata,
        }


@dataclass
class GenerationResult(BaseResult):
    """Result for content generation operations."""

    content: str = ""
    confidence: float = 0.0
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM
    alternatives: List[str] = field(default_factory=list)
    generation_method: str = ""
    token_count: Optional[int] = None


@dataclass
class ValidationResult(BaseResult):
    """Result for validation operations."""

    is_valid: bool = False
    validation_score: float = 0.0
    validation_issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


@dataclass
class ConsensusResult(BaseResult):
    """Result for consensus operations."""

    consensus_reached: bool = False
    consensus_score: float = 0.0
    consensus_level: str = "none"
    participating_agents: List[str] = field(default_factory=list)
    final_decision: str = ""
    vote_distribution: Dict[str, int] = field(default_factory=dict)
    reasoning: str = ""


@dataclass
class AnalysisResult(BaseResult):
    """Result for analysis operations."""

    analysis_data: Dict[str, Any] = field(default_factory=dict)
    insights: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    confidence: float = 0.0
    analysis_method: str = ""


@dataclass
class ProcessingResult(BaseResult):
    """Result for processing operations."""

    processed_items: int = 0
    total_items: int = 0
    failed_items: int = 0
    processing_details: Dict[str, Any] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """Calculate processing success rate."""
        if self.total_items == 0:
            return 0.0
        return (self.processed_items - self.failed_items) / self.total_items


class ResultFactory:
    """Factory for creating standardized results."""

    @staticmethod
    def success(
        message: str = "Operation completed successfully",
        data: Optional[Dict[str, Any]] = None,
        execution_time_ms: Optional[float] = None,
    ) -> BaseResult:
        """Create success result."""
        return BaseResult(
            status=ResultStatus.SUCCESS,
            success=True,
            message=message,
            execution_time_ms=execution_time_ms,
            metadata=data or {},
        )

    @staticmethod
    def failure(
        error: str,
        message: str = "Operation failed",
        execution_time_ms: Optional[float] = None,
    ) -> BaseResult:
        """Create failure result."""
        return BaseResult(
            status=ResultStatus.FAILURE,
            success=False,
            message=message,
            error=error,
            execution_time_ms=execution_time_ms,
        )

    @staticmethod
    def timeout(
        message: str = "Operation timed out", execution_time_ms: Optional[float] = None
    ) -> BaseResult:
        """Create timeout result."""
        return BaseResult(
            status=ResultStatus.TIMEOUT,
            success=False,
            message=message,
            execution_time_ms=execution_time_ms,
        )

    @staticmethod
    def generation_success(
        content: str,
        confidence: float,
        method: str,
        alternatives: Optional[List[str]] = None,
        token_count: Optional[int] = None,
    ) -> GenerationResult:
        """Create generation success result."""
        confidence_level = (
            ConfidenceLevel.VERY_HIGH
            if confidence > 0.9
            else (
                ConfidenceLevel.HIGH
                if confidence > 0.7
                else (
                    ConfidenceLevel.MEDIUM
                    if confidence > 0.5
                    else (
                        ConfidenceLevel.LOW
                        if confidence > 0.3
                        else ConfidenceLevel.VERY_LOW
                    )
                )
            )
        )

        return GenerationResult(
            status=ResultStatus.SUCCESS,
            success=True,
            message="Content generated successfully",
            content=content,
            confidence=confidence,
            confidence_level=confidence_level,
            alternatives=alternatives or [],
            generation_method=method,
            token_count=token_count,
        )

    @staticmethod
    def consensus_reached(
        decision: str,
        score: float,
        participants: List[str],
        votes: Dict[str, int],
        reasoning: str = "",
    ) -> ConsensusResult:
        """Create consensus reached result."""
        consensus_level = (
            "strong"
            if score > 0.8
            else "moderate" if score > 0.6 else "weak" if score > 0.4 else "none"
        )

        return ConsensusResult(
            status=ResultStatus.SUCCESS,
            success=True,
            message="Consensus reached successfully",
            consensus_reached=True,
            consensus_score=score,
            consensus_level=consensus_level,
            participating_agents=participants,
            final_decision=decision,
            vote_distribution=votes,
            reasoning=reasoning,
        )
