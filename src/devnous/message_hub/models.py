"""Comprehensive message hub models and schemas."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, validator


class PlatformType(str, Enum):
    """Supported messaging platforms."""
    SLACK = "slack"
    TEAMS = "teams"
    DISCORD = "discord"
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    SMS = "sms"
    WEBHOOK = "webhook"
    WEB_CHAT = "web_chat"


class MessageType(str, Enum):
    """Message type classification."""
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    VIDEO = "video"
    LOCATION = "location"
    STICKER = "sticker"
    REACTION = "reaction"
    SYSTEM = "system"
    COMMAND = "command"


class MessagePriority(str, Enum):
    """Message priority levels."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"
    CRITICAL = "critical"


class MessageStatus(str, Enum):
    """Message processing status."""
    PENDING = "pending"
    PROCESSING = "processing"
    ENRICHED = "enriched"
    ROUTED = "routed"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class ContextEnrichmentStatus(str, Enum):
    """Context enrichment processing status."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlatformMetadata(BaseModel):
    """Platform-specific message metadata."""
    platform: PlatformType
    channel_id: Optional[str] = None
    thread_id: Optional[str] = None
    message_id: Optional[str] = None
    user_id: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    mentions: List[str] = []
    reactions: List[Dict[str, Any]] = []
    attachments: List[Dict[str, Any]] = []
    reply_to_id: Optional[str] = None
    is_bot: bool = False
    raw_data: Dict[str, Any] = {}


class MessageContent(BaseModel):
    """Normalized message content structure."""
    text: Optional[str] = None
    html: Optional[str] = None
    markdown: Optional[str] = None
    attachments: List[Dict[str, Any]] = []
    mentions: List[str] = []
    hashtags: List[str] = []
    links: List[str] = []
    language: Optional[str] = None
    sentiment_score: Optional[float] = Field(None, ge=-1.0, le=1.0)
    
    @validator('sentiment_score')
    def validate_sentiment(cls, v):
        if v is not None and not (-1.0 <= v <= 1.0):
            raise ValueError('Sentiment score must be between -1.0 and 1.0')
        return v


class ContextEnrichment(BaseModel):
    """Context enrichment data structure."""
    user_context: Optional[Dict[str, Any]] = None
    conversation_history: Optional[List[Dict[str, Any]]] = None
    emotional_state: Optional[Dict[str, Any]] = None
    team_context: Optional[Dict[str, Any]] = None
    project_context: Optional[Dict[str, Any]] = None
    temporal_context: Optional[Dict[str, Any]] = None
    semantic_analysis: Optional[Dict[str, Any]] = None
    intent_classification: Optional[Dict[str, Any]] = None
    enrichment_metadata: Dict[str, Any] = {}
    enrichment_timestamp: datetime = Field(default_factory=datetime.utcnow)
    enrichment_version: str = "1.0"


class UnifiedMessage(BaseModel):
    """Unified message format for the message hub."""
    id: UUID = Field(default_factory=uuid4)
    correlation_id: Optional[str] = None
    parent_id: Optional[UUID] = None
    
    # Core message data
    message_type: MessageType
    priority: MessagePriority = MessagePriority.NORMAL
    content: MessageContent
    
    # Platform information
    source_platform: PlatformType
    target_platforms: List[PlatformType] = []
    platform_metadata: PlatformMetadata
    
    # Routing and processing
    routing_key: str
    processing_status: MessageStatus = MessageStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    
    # Context enrichment
    context_enrichment: Optional[ContextEnrichment] = None
    enrichment_status: ContextEnrichmentStatus = ContextEnrichmentStatus.NOT_STARTED
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    received_at: datetime = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    
    # Error handling
    error_message: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None
    
    # Metadata and tracking
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    metadata: Dict[str, Any] = {}


class MessageBatch(BaseModel):
    """Batch message processing container."""
    batch_id: UUID = Field(default_factory=uuid4)
    messages: List[UnifiedMessage]
    batch_size: int
    priority: MessagePriority = MessagePriority.NORMAL
    created_at: datetime = Field(default_factory=datetime.utcnow)
    processed_count: int = 0
    failed_count: int = 0
    
    @validator('batch_size', always=True)
    def validate_batch_size(cls, v, values):
        if 'messages' in values:
            return len(values['messages'])
        return v


class PlatformConfiguration(BaseModel):
    """Platform-specific configuration."""
    platform: PlatformType
    enabled: bool = True
    rate_limit_per_second: int = 10
    rate_limit_burst: int = 20
    max_message_size: int = 4000
    supported_message_types: List[MessageType] = []
    authentication_config: Dict[str, Any] = {}
    webhook_config: Optional[Dict[str, Any]] = None
    retry_config: Dict[str, Any] = {
        "max_retries": 3,
        "backoff_factor": 2,
        "initial_delay": 1
    }
    transformation_rules: List[Dict[str, Any]] = []
    filter_rules: List[Dict[str, Any]] = []


class RoutingRule(BaseModel):
    """Message routing rule definition."""
    rule_id: str
    name: str
    description: Optional[str] = None
    enabled: bool = True
    priority: int = 0
    
    # Matching criteria
    source_platforms: List[PlatformType] = []
    message_types: List[MessageType] = []
    content_patterns: List[str] = []
    user_patterns: List[str] = []
    metadata_filters: Dict[str, Any] = {}
    
    # Routing actions
    target_platforms: List[PlatformType] = []
    transformation_rules: List[str] = []
    enrichment_rules: List[str] = []
    
    # Conditions
    time_conditions: Optional[Dict[str, Any]] = None
    user_conditions: Optional[Dict[str, Any]] = None
    context_conditions: Optional[Dict[str, Any]] = None
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DeadLetterMessage(BaseModel):
    """Dead letter queue message."""
    id: UUID = Field(default_factory=uuid4)
    original_message: UnifiedMessage
    failure_reason: str
    failure_details: Dict[str, Any] = {}
    failure_count: int
    first_failure_at: datetime
    last_failure_at: datetime = Field(default_factory=datetime.utcnow)
    retry_after: Optional[datetime] = None
    resolution_status: str = "unresolved"  # unresolved, investigating, resolved
    resolution_notes: Optional[str] = None


class MessageMetrics(BaseModel):
    """Message processing metrics."""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    platform: Optional[PlatformType] = None
    message_type: Optional[MessageType] = None
    
    # Throughput metrics
    messages_processed: int = 0
    messages_failed: int = 0
    messages_delivered: int = 0
    
    # Latency metrics
    avg_processing_time_ms: float = 0.0
    p95_processing_time_ms: float = 0.0
    p99_processing_time_ms: float = 0.0
    
    # Error metrics
    error_rate: float = Field(ge=0.0, le=1.0)
    retry_rate: float = Field(ge=0.0, le=1.0)
    dead_letter_rate: float = Field(ge=0.0, le=1.0)
    
    # Resource metrics
    memory_usage_mb: Optional[float] = None
    cpu_usage_percent: Optional[float] = None
    active_connections: Optional[int] = None


class HealthCheckResult(BaseModel):
    """Health check result for message hub components."""
    component: str
    status: str  # "healthy", "degraded", "unhealthy"
    response_time_ms: float
    details: Dict[str, Any] = {}
    last_check: datetime = Field(default_factory=datetime.utcnow)
    error_message: Optional[str] = None


class MessageHubHealth(BaseModel):
    """Overall message hub health status."""
    status: str  # "healthy", "degraded", "unhealthy"
    components: List[HealthCheckResult]
    uptime_seconds: float
    version: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class CircuitBreakerState(BaseModel):
    """Circuit breaker state tracking."""
    component: str
    state: str  # "closed", "open", "half_open"
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    next_attempt_time: Optional[datetime] = None
    failure_threshold: int = 5
    recovery_timeout_seconds: int = 60
    
    
class MessageTransformation(BaseModel):
    """Message transformation rule."""
    transformation_id: str
    name: str
    description: Optional[str] = None
    enabled: bool = True
    
    # Transformation logic
    source_field: str
    target_field: str
    transformation_type: str  # "map", "filter", "enrich", "format", "validate"
    transformation_config: Dict[str, Any] = {}
    
    # Conditions
    apply_conditions: List[Dict[str, Any]] = []
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MessageAuditLog(BaseModel):
    """Message audit log entry."""
    id: UUID = Field(default_factory=uuid4)
    message_id: UUID
    correlation_id: Optional[str] = None
    
    # Event information
    event_type: str
    event_description: str
    component: str
    
    # State information
    previous_state: Optional[Dict[str, Any]] = None
    new_state: Optional[Dict[str, Any]] = None
    
    # Context
    user_id: Optional[str] = None
    platform: Optional[PlatformType] = None
    
    # Metadata
    duration_ms: Optional[float] = None
    error_details: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = {}
    
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class ErrorCode(str, Enum):
    """Error codes for message delivery."""
    SUCCESS = "success"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION_FAILED = "authentication_failed"
    INVALID_RECIPIENT = "invalid_recipient"
    MESSAGE_TOO_LARGE = "message_too_large"
    NETWORK_ERROR = "network_error"
    PLATFORM_ERROR = "platform_error"
    VALIDATION_ERROR = "validation_error"
    TIMEOUT = "timeout"
    UNKNOWN_ERROR = "unknown_error"


class RateLimitInfo(BaseModel):
    """Rate limit information."""
    limit: int
    remaining: int
    reset_at: datetime
    retry_after_seconds: Optional[int] = None


class SecurityContext(BaseModel):
    """Security context for message operations."""
    user_id: str
    platform: PlatformType
    authenticated: bool = True
    permissions: List[str] = []
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    session_id: Optional[str] = None


class MessageDeliveryResult(BaseModel):
    """Result of a message delivery operation."""
    success: bool
    message_id: Optional[UUID] = None
    platform_message_id: Optional[str] = None
    error_code: Optional[ErrorCode] = None
    error_message: Optional[str] = None
    rate_limit_info: Optional[RateLimitInfo] = None
    retry_able: bool = False
    delivered_at: Optional[datetime] = None
    metadata: Dict[str, Any] = {}
