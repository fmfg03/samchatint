"""Pydantic models for DevNous tool system."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(UTC)


class MessageChannel(str, Enum):
    """Supported message channels."""
    SLACK = "slack"
    TEAMS = "teams"
    EMAIL = "email"
    WEBHOOK = "webhook"


class TaskStatus(str, Enum):
    """Task status enumeration."""
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    """Task priority levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WorkflowStatus(str, Enum):
    """Workflow status enumeration."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Memory Tool Models
class MemoryEntry(BaseModel):
    """Memory storage entry model."""
    key: str
    value: str
    ttl: Optional[int] = None
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: Optional[datetime] = None


class ConversationMessage(BaseModel):
    """Conversation message model."""
    id: UUID = Field(default_factory=uuid4)
    conversation_id: str
    sender: str
    content: str
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: Optional[Dict[str, Any]] = None


class TeamInfo(BaseModel):
    """Team information model."""
    team_id: str
    name: str
    description: Optional[str] = None
    members: List[str] = []
    roles: Dict[str, str] = {}
    preferences: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


# Chat Application Models
class IncomingMessage(BaseModel):
    """Incoming message for processing."""
    channel: MessageChannel = MessageChannel.SLACK
    sender: str
    content: str
    channel_id: Optional[str] = None
    thread_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class OutgoingMessage(BaseModel):
    """Outgoing message model."""
    channel: MessageChannel
    recipient: str
    content: str
    thread_id: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None


class MessageResponse(BaseModel):
    """Message processing response."""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    processed_at: datetime = Field(default_factory=utc_now)


# PM Software Models
class TaskFilter(BaseModel):
    """Task filtering criteria."""
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    assignee: Optional[str] = None
    project: Optional[str] = None
    labels: Optional[List[str]] = None
    created_after: Optional[datetime] = None
    created_before: Optional[datetime] = None


class Task(BaseModel):
    """Task model."""
    id: str
    title: str
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    assignee: Optional[str] = None
    reporter: Optional[str] = None
    project: Optional[str] = None
    labels: List[str] = []
    estimated_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    due_date: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: Optional[Dict[str, Any]] = None


class TaskCreate(BaseModel):
    """Task creation model."""
    title: str
    description: Optional[str] = None
    priority: TaskPriority = TaskPriority.MEDIUM
    assignee: Optional[str] = None
    project: Optional[str] = None
    labels: List[str] = []
    estimated_hours: Optional[float] = None
    due_date: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


class TaskUpdate(BaseModel):
    """Task update model."""
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    assignee: Optional[str] = None
    labels: Optional[List[str]] = None
    estimated_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    due_date: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


# Workflow Models
class WorkflowState(BaseModel):
    """Workflow state model."""
    workflow_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    current_step: str
    data: Dict[str, Any] = {}
    version: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class WorkflowStep(BaseModel):
    """Workflow step definition."""
    name: str
    description: Optional[str] = None
    required_data: List[str] = []
    next_steps: List[str] = []
    timeout_seconds: Optional[int] = None


class WorkflowDefinition(BaseModel):
    """Workflow definition model."""
    name: str
    description: Optional[str] = None
    steps: Dict[str, WorkflowStep]
    initial_step: str

    @field_validator('initial_step', mode='after')
    @classmethod
    def validate_initial_step(cls, v, info):
        """Validate that initial_step exists in steps dict."""
        if info.data and 'steps' in info.data and v not in info.data['steps']:
            raise ValueError(f"Initial step '{v}' not found in workflow steps")
        return v


# Response Models
class PaginatedResponse(BaseModel):
    """Generic paginated response model."""
    items: List[Any]
    total: int
    page: int = 1
    page_size: int = 50
    has_next: bool = False
    has_previous: bool = False


class OperationResult(BaseModel):
    """Generic operation result model."""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    message: Optional[str] = None
    timestamp: datetime = Field(default_factory=utc_now)


# Health Check Models
class HealthStatus(BaseModel):
    """Health check status model."""
    service: str
    status: str  # "healthy", "unhealthy", "degraded"
    details: Optional[Dict[str, Any]] = None
    last_check: datetime = Field(default_factory=utc_now)


class SystemHealth(BaseModel):
    """Overall system health model."""
    status: str
    services: List[HealthStatus]
    timestamp: datetime = Field(default_factory=utc_now)


# Context Detection Models
class EmotionalState(str, Enum):
    """Emotional state categories."""
    STRESSED = "stressed"
    FOCUSED = "focused"
    ENGAGED = "engaged"
    FRUSTRATED = "frustrated"
    COLLABORATIVE = "collaborative"
    PRODUCTIVE = "productive"
    OVERWHELMED = "overwhelmed"
    NEUTRAL = "neutral"
    CONFIDENT = "confident"
    CONCERNED = "concerned"


class EmotionalIntensity(str, Enum):
    """Emotional intensity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class EmotionalVector(BaseModel):
    """Emotional state representation in valence-arousal space."""
    valence: float = Field(ge=-1.0, le=1.0, description="Positive/negative dimension")
    arousal: float = Field(ge=0.0, le=1.0, description="Energy/activation level")
    confidence: float = Field(ge=0.0, le=1.0, description="Detection confidence")
    timestamp: datetime = Field(default_factory=utc_now)


class EmotionalContext(BaseModel):
    """Emotional context with enriched information."""
    valence: float = Field(ge=-1.0, le=1.0, description="Positive/negative dimension")
    arousal: float = Field(ge=0.0, le=1.0, description="Energy/activation level")
    sentiment: str = Field(description="Sentiment label (positive/negative/neutral)")
    emotions: List[str] = Field(default_factory=list, description="Detected emotions")
    confidence: float = Field(ge=0.0, le=1.0, description="Detection confidence")
    intensity: Optional[EmotionalIntensity] = None
    timestamp: datetime = Field(default_factory=utc_now)


class CommunicationPattern(BaseModel):
    """Communication pattern analysis."""
    message_frequency: float = 1.0  # messages per hour
    avg_response_time: float = 60.0  # seconds
    message_length_avg: float = 50.0  # characters
    message_length_variance: float = Field(default=10.0, ge=0.0)  # variance in message length
    tone_score: float = Field(default=0.0, ge=-1.0, le=1.0)  # negative to positive
    urgency_indicators: float = Field(default=0.0, ge=0.0)  # urgency count (removed upper bound)
    thread_depth: int = 1  # conversation thread depth
    engagement_level: float = Field(default=0.5, ge=0.0, le=1.0)  # engagement score
    formality_level: float = Field(default=0.5, ge=0.0, le=1.0)  # formality score
    formality_score: float = Field(default=0.5, ge=0.0, le=1.0)  # alias for formality_level
    emoji_usage: float = Field(default=0.0, ge=0.0, le=1.0)  # emoji usage rate
    caps_usage: float = Field(default=0.0, ge=0.0, le=1.0)  # caps usage rate
    timestamp: datetime = Field(default_factory=utc_now)

    @field_validator('thread_depth', mode='before')
    @classmethod
    def coerce_to_int(cls, v):
        """Convert float values to int for count fields."""
        if isinstance(v, float):
            return int(round(v))
        return v


class DigitalActivity(BaseModel):
    """Digital activity tracking."""
    typing_speed: Optional[float] = None  # characters per minute
    response_delay: float = 0.0  # seconds since last message
    concurrent_conversations: int = 1  # number of concurrent conversations
    context_switches: int = 0  # topic/thread changes
    active_duration: float = 0.0  # minutes active
    idle_periods: List[float] = []  # idle durations in minutes
    timestamp: datetime = Field(default_factory=utc_now)


class TeamDynamic(BaseModel):
    """Team dynamics assessment."""
    collaboration_score: float = Field(ge=0.0, le=1.0)
    conflict_indicators: List[str] = []  # list of conflict indicators detected
    decision_velocity: float = 0.0  # decisions per day
    participation_balance: float = Field(ge=0.0, le=1.0)  # even participation
    support_interactions: int = 0  # number of support interactions
    knowledge_sharing_score: float = Field(ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=utc_now)


class ProjectPhase(str, Enum):
    """Project phase detection."""
    UNKNOWN = "unknown"
    PLANNING = "planning"
    DEVELOPMENT = "development"
    TESTING = "testing"
    EXECUTION = "execution"
    REVIEW = "review"
    RETROSPECTIVE = "retrospective"
    CRISIS = "crisis"
    MAINTENANCE = "maintenance"


class ContextSignal(BaseModel):
    """Individual context signal."""
    signal_type: str
    value: Union[float, int, str, bool]
    confidence: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0)
    source: str  # which sensor/analyzer produced this
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: Optional[Dict[str, Any]] = None
    content: Optional[Any] = None  # Legacy field for compatibility


class UserContext(BaseModel):
    """Comprehensive user context."""
    user_id: str
    emotional_state: EmotionalState
    emotional_vector: EmotionalVector
    communication_pattern: CommunicationPattern
    digital_activity: DigitalActivity
    signals: List[ContextSignal] = []
    context_confidence: float = Field(ge=0.0, le=1.0)
    last_updated: datetime = Field(default_factory=utc_now)


class TeamContext(BaseModel):
    """Team-level context aggregation."""
    team_id: str
    project_phase: ProjectPhase
    team_dynamic: TeamDynamic
    average_emotional_vector: EmotionalVector
    dominant_emotions: List[EmotionalState] = []
    stress_level: float = Field(ge=0.0, le=1.0)
    productivity_score: float = Field(ge=0.0, le=1.0)
    context_history: List[Dict[str, Any]] = []
    last_updated: datetime = Field(default_factory=utc_now)


class ContextEvent(BaseModel):
    """Context change event."""
    event_id: UUID = Field(default_factory=uuid4)
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    event_type: str  # "state_change", "anomaly", "threshold_breach"
    previous_state: Optional[Dict[str, Any]] = None
    new_state: Dict[str, Any]
    severity: str = "info"  # "info", "warning", "critical"
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: Optional[Dict[str, Any]] = None


class ContextConfiguration(BaseModel):
    """Context detection configuration."""
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    enabled_sensors: List[str] = []
    detection_thresholds: Dict[str, float] = {}
    temporal_decay_factor: float = Field(default=0.1, ge=0.0, le=1.0)
    context_window_minutes: int = Field(default=60, ge=1)
    privacy_mode: bool = False
    alert_settings: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
