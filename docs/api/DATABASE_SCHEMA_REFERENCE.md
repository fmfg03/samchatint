# Database Schema Reference

**Version**: 1.0.0  
**Last Updated**: 2024-01-15  
**Database**: PostgreSQL 14+  
**Target**: Database Administrators, Backend Developers, Data Analysts

## Overview

This document provides comprehensive database schema documentation for the SamChat/DevNous system. The database is designed for high-performance debate orchestration, context detection, memory management, and enterprise messaging integration.

## Table of Contents

- [Database Architecture](#database-architecture)
- [Core Tables](#core-tables)
- [Debate System Tables](#debate-system-tables)
- [Context Detection Tables](#context-detection-tables)
- [Memory System Tables](#memory-system-tables)
- [Messaging Hub Tables](#messaging-hub-tables)
- [Monitoring Tables](#monitoring-tables)
- [Security Tables](#security-tables)
- [Indexes and Performance](#indexes-and-performance)
- [Views and Functions](#views-and-functions)
- [Data Types and Enums](#data-types-and-enums)
- [Relationships and Constraints](#relationships-and-constraints)
- [Partitioning Strategy](#partitioning-strategy)
- [Backup and Recovery](#backup-and-recovery)

---

## Database Architecture

### Design Principles

- **High Performance**: Optimized indexes, partitioning, and query patterns
- **Scalability**: Designed for concurrent operations and large datasets
- **Analytics Ready**: Built-in support for reporting and machine learning
- **ACID Compliance**: Full transaction support with referential integrity
- **PostgreSQL Native**: Leverages advanced PostgreSQL features (JSONB, arrays, custom types)

### Schema Organization

```sql
-- Primary schemas
CREATE SCHEMA IF NOT EXISTS core;      -- Core system tables
CREATE SCHEMA IF NOT EXISTS debate;    -- Debate system tables
CREATE SCHEMA IF NOT EXISTS context;   -- Context detection tables
CREATE SCHEMA IF NOT EXISTS memory;    -- Memory system tables
CREATE SCHEMA IF NOT EXISTS messaging; -- Messaging hub tables
CREATE SCHEMA IF NOT EXISTS monitoring;-- Monitoring and metrics
CREATE SCHEMA IF NOT EXISTS security;  -- Security and audit tables
```

### Connection Settings

```sql
-- Recommended PostgreSQL settings
max_connections = 100
shared_buffers = 256MB
effective_cache_size = 1GB
work_mem = 4MB
maintenance_work_mem = 64MB
random_page_cost = 1.1
effective_io_concurrency = 200
```

---

## Core Tables

### core.organizations

**Purpose**: Multi-tenant organization management
**Records**: 1K-10K expected
**Partitioning**: None

```sql
CREATE TABLE core.organizations (
    org_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    settings JSONB DEFAULT '{}',
    subscription_tier VARCHAR(50) DEFAULT 'free',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true
);

-- Indexes
CREATE INDEX idx_organizations_slug ON core.organizations(slug);
CREATE INDEX idx_organizations_active ON core.organizations(is_active);
CREATE INDEX idx_organizations_subscription ON core.organizations(subscription_tier);
```

**Key Fields**:
- `org_id`: Unique organization identifier
- `slug`: URL-friendly organization identifier
- `settings`: JSONB configuration and preferences
- `subscription_tier`: Plan level (`free`, `pro`, `enterprise`)

### core.users

**Purpose**: User account and profile management
**Records**: 10K-1M expected
**Partitioning**: By org_id (if multi-tenant)

```sql
CREATE TABLE core.users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES core.organizations(org_id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    username VARCHAR(100),
    full_name VARCHAR(255),
    avatar_url TEXT,
    role VARCHAR(50) DEFAULT 'member',
    preferences JSONB DEFAULT '{}',
    last_login TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true,
    
    UNIQUE(org_id, email),
    UNIQUE(org_id, username)
);

-- Indexes
CREATE INDEX idx_users_org_id ON core.users(org_id);
CREATE INDEX idx_users_email ON core.users(email);
CREATE INDEX idx_users_active ON core.users(org_id, is_active);
CREATE INDEX idx_users_role ON core.users(org_id, role);
```

**Key Fields**:
- `user_id`: Unique user identifier
- `org_id`: Organization association
- `role`: User role (`admin`, `manager`, `developer`, `member`)
- `preferences`: JSONB user configuration and settings

### core.teams

**Purpose**: Team structure and membership
**Records**: 1K-100K expected
**Partitioning**: By org_id

```sql
CREATE TABLE core.teams (
    team_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES core.organizations(org_id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    settings JSONB DEFAULT '{}',
    created_by UUID NOT NULL REFERENCES core.users(user_id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true,
    
    UNIQUE(org_id, name)
);

-- Indexes
CREATE INDEX idx_teams_org_id ON core.teams(org_id);
CREATE INDEX idx_teams_active ON core.teams(org_id, is_active);
CREATE INDEX idx_teams_created_by ON core.teams(created_by);
```

### core.team_members

**Purpose**: Team membership and roles
**Records**: 10K-1M expected
**Partitioning**: By team_id

```sql
CREATE TABLE core.team_members (
    team_id UUID NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    role VARCHAR(50) DEFAULT 'member',
    joined_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true,
    
    PRIMARY KEY (team_id, user_id)
);

-- Indexes
CREATE INDEX idx_team_members_user_id ON core.team_members(user_id);
CREATE INDEX idx_team_members_active ON core.team_members(team_id, is_active);
CREATE INDEX idx_team_members_role ON core.team_members(team_id, role);
```

**Key Fields**:
- `role`: Team role (`lead`, `senior`, `developer`, `qa`, `designer`, `member`)

### core.conversations

**Purpose**: Conversation threads and context
**Records**: 100K-10M expected
**Partitioning**: By date (monthly)

```sql
CREATE TABLE core.conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    title VARCHAR(500),
    channel_type VARCHAR(50) NOT NULL,
    channel_id VARCHAR(255),
    thread_id VARCHAR(255),
    metadata JSONB DEFAULT '{}',
    summary TEXT,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    closed_at TIMESTAMP WITH TIME ZONE
) PARTITION BY RANGE (created_at);

-- Monthly partitions
CREATE TABLE core.conversations_2024_01 PARTITION OF core.conversations
FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

-- Indexes
CREATE INDEX idx_conversations_team_id ON core.conversations(team_id);
CREATE INDEX idx_conversations_channel ON core.conversations(channel_type, channel_id);
CREATE INDEX idx_conversations_status ON core.conversations(status);
CREATE INDEX idx_conversations_created_at ON core.conversations(created_at);
```

**Key Fields**:
- `channel_type`: Source platform (`slack`, `teams`, `telegram`, `whatsapp`, `web`)
- `status`: Conversation status (`active`, `paused`, `closed`, `archived`)

### core.messages

**Purpose**: Individual messages within conversations
**Records**: 1M-100M expected
**Partitioning**: By date (daily)

```sql
CREATE TABLE core.messages (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES core.conversations(conversation_id) ON DELETE CASCADE,
    user_id UUID REFERENCES core.users(user_id) ON DELETE SET NULL,
    sender_name VARCHAR(255) NOT NULL,
    sender_id VARCHAR(255),
    content TEXT NOT NULL,
    content_type VARCHAR(50) DEFAULT 'text',
    metadata JSONB DEFAULT '{}',
    platform_message_id VARCHAR(255),
    reply_to_message_id UUID REFERENCES core.messages(message_id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_deleted BOOLEAN DEFAULT false
) PARTITION BY RANGE (created_at);

-- Daily partitions example
CREATE TABLE core.messages_2024_01_01 PARTITION OF core.messages
FOR VALUES FROM ('2024-01-01') TO ('2024-01-02');

-- Indexes
CREATE INDEX idx_messages_conversation_id ON core.messages(conversation_id);
CREATE INDEX idx_messages_user_id ON core.messages(user_id);
CREATE INDEX idx_messages_created_at ON core.messages(created_at);
CREATE INDEX idx_messages_reply_to ON core.messages(reply_to_message_id);
CREATE INDEX idx_messages_platform_id ON core.messages(platform_message_id);
```

**Key Fields**:
- `content_type`: Message type (`text`, `image`, `file`, `video`, `audio`)
- `platform_message_id`: External platform message identifier

---

## Debate System Tables

### debate.sessions

**Purpose**: Main container for debate instances with lifecycle tracking
**Records**: 10K-1M expected
**Partitioning**: By date (monthly)

```sql
CREATE TABLE debate.sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_session_id VARCHAR(255) NOT NULL UNIQUE,
    team_id UUID NOT NULL REFERENCES core.teams(team_id),
    conversation_id UUID REFERENCES core.conversations(conversation_id),
    protocol_type VARCHAR(50) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    current_phase VARCHAR(50),
    consensus_level DECIMAL(3,2) DEFAULT 0.00 CHECK (consensus_level >= 0 AND consensus_level <= 1),
    protocol_config JSONB DEFAULT '{}',
    performance_metrics JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    
    CHECK (consensus_level BETWEEN 0.00 AND 1.00)
) PARTITION BY RANGE (created_at);

-- Indexes
CREATE INDEX idx_debate_sessions_team_id ON debate.sessions(team_id);
CREATE INDEX idx_debate_sessions_status ON debate.sessions(status);
CREATE INDEX idx_debate_sessions_protocol ON debate.sessions(protocol_type);
CREATE INDEX idx_debate_sessions_conversation ON debate.sessions(conversation_id);
CREATE INDEX idx_debate_sessions_created_at ON debate.sessions(created_at);
```

**Key Fields**:
- `protocol_type`: Debate protocol (`structured_consensus`, `devil_advocate`, `multi_perspective`, `rapid_fire`, `consensus_building`, `critical_analysis`)
- `status`: Debate status (`pending`, `active`, `completed`, `failed`, `cancelled`)
- `current_phase`: Active phase (`initialization`, `argument`, `counter_argument`, `synthesis`, `consensus`)

### debate.rounds

**Purpose**: Individual rounds within debate sessions
**Records**: 100K-10M expected
**Partitioning**: By session_id range

```sql
CREATE TABLE debate.rounds (
    round_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate.sessions(session_id) ON DELETE CASCADE,
    round_number INTEGER NOT NULL,
    phase VARCHAR(50) NOT NULL,
    consensus_score DECIMAL(3,2) DEFAULT 0.00,
    quality_score DECIMAL(3,2) DEFAULT 0.00,
    quality_metrics JSONB DEFAULT '{}',
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    
    UNIQUE(session_id, round_number),
    CHECK (consensus_score BETWEEN 0.00 AND 1.00),
    CHECK (quality_score BETWEEN 0.00 AND 1.00)
);

-- Indexes
CREATE INDEX idx_debate_rounds_session_id ON debate.rounds(session_id);
CREATE INDEX idx_debate_rounds_phase ON debate.rounds(phase);
CREATE INDEX idx_debate_rounds_consensus ON debate.rounds(consensus_score);
CREATE INDEX idx_debate_rounds_quality ON debate.rounds(quality_score);
```

### debate.agent_responses

**Purpose**: Individual agent contributions with quality assessment
**Records**: 1M-100M expected
**Partitioning**: By session_id range

```sql
CREATE TABLE debate.agent_responses (
    response_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate.sessions(session_id) ON DELETE CASCADE,
    round_id UUID NOT NULL REFERENCES debate.rounds(round_id) ON DELETE CASCADE,
    agent_role VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    confidence_score DECIMAL(3,2) DEFAULT 0.00,
    quality_score DECIMAL(3,2) DEFAULT 0.00,
    supporting_evidence JSONB DEFAULT '{}',
    response_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    processing_time_ms INTEGER,
    
    CHECK (confidence_score BETWEEN 0.00 AND 1.00),
    CHECK (quality_score BETWEEN 0.00 AND 1.00)
);

-- Indexes
CREATE INDEX idx_agent_responses_session_id ON debate.agent_responses(session_id);
CREATE INDEX idx_agent_responses_round_id ON debate.agent_responses(round_id);
CREATE INDEX idx_agent_responses_agent_role ON debate.agent_responses(agent_role);
CREATE INDEX idx_agent_responses_confidence ON debate.agent_responses(confidence_score);
CREATE INDEX idx_agent_responses_quality ON debate.agent_responses(quality_score);
```

**Key Fields**:
- `agent_role`: Agent type (`product_owner`, `scrum_master`, `developer`, `architect`, `qa_engineer`, `ui_ux_designer`)

### debate.complexity_analysis

**Purpose**: 4D complexity analysis results for optimization
**Records**: 100K-1M expected
**Partitioning**: By session_id range

```sql
CREATE TABLE debate.complexity_analysis (
    analysis_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate.sessions(session_id) ON DELETE CASCADE,
    technical_complexity DECIMAL(3,2) DEFAULT 0.00,
    architectural_complexity DECIMAL(3,2) DEFAULT 0.00,
    stakeholder_complexity DECIMAL(3,2) DEFAULT 0.00,
    temporal_complexity DECIMAL(3,2) DEFAULT 0.00,
    overall_complexity DECIMAL(3,2) DEFAULT 0.00,
    complexity_factors JSONB DEFAULT '{}',
    analysis_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CHECK (technical_complexity BETWEEN 0.00 AND 1.00),
    CHECK (architectural_complexity BETWEEN 0.00 AND 1.00),
    CHECK (stakeholder_complexity BETWEEN 0.00 AND 1.00),
    CHECK (temporal_complexity BETWEEN 0.00 AND 1.00),
    CHECK (overall_complexity BETWEEN 0.00 AND 1.00)
);

-- Indexes
CREATE INDEX idx_complexity_session_id ON debate.complexity_analysis(session_id);
CREATE INDEX idx_complexity_overall ON debate.complexity_analysis(overall_complexity);
CREATE INDEX idx_complexity_technical ON debate.complexity_analysis(technical_complexity);
```

### debate.confidence_assessments

**Purpose**: Agent confidence tracking and divergence metrics
**Records**: 100K-10M expected
**Partitioning**: By session_id range

```sql
CREATE TABLE debate.confidence_assessments (
    assessment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate.sessions(session_id) ON DELETE CASCADE,
    round_id UUID NOT NULL REFERENCES debate.rounds(round_id) ON DELETE CASCADE,
    agent_role VARCHAR(50) NOT NULL,
    initial_confidence DECIMAL(3,2) DEFAULT 0.00,
    final_confidence DECIMAL(3,2) DEFAULT 0.00,
    confidence_change DECIMAL(4,2) DEFAULT 0.00,
    response_divergence DECIMAL(3,2) DEFAULT 0.00,
    overall_uncertainty DECIMAL(3,2) DEFAULT 0.00,
    confidence_factors JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CHECK (initial_confidence BETWEEN 0.00 AND 1.00),
    CHECK (final_confidence BETWEEN 0.00 AND 1.00),
    CHECK (confidence_change BETWEEN -1.00 AND 1.00),
    CHECK (response_divergence BETWEEN 0.00 AND 1.00),
    CHECK (overall_uncertainty BETWEEN 0.00 AND 1.00)
);

-- Indexes
CREATE INDEX idx_confidence_session_id ON debate.confidence_assessments(session_id);
CREATE INDEX idx_confidence_agent_role ON debate.confidence_assessments(agent_role);
CREATE INDEX idx_confidence_divergence ON debate.confidence_assessments(response_divergence);
```

### debate.protocol_performance

**Purpose**: Per-team protocol effectiveness tracking
**Records**: 10K-100K expected
**Partitioning**: By team_id

```sql
CREATE TABLE debate.protocol_performance (
    performance_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    protocol_type VARCHAR(50) NOT NULL,
    total_sessions INTEGER DEFAULT 0,
    successful_sessions INTEGER DEFAULT 0,
    avg_consensus_level DECIMAL(3,2) DEFAULT 0.00,
    avg_duration_seconds INTEGER DEFAULT 0,
    avg_quality_score DECIMAL(3,2) DEFAULT 0.00,
    effectiveness_score DECIMAL(3,2) DEFAULT 0.00,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    performance_trend JSONB DEFAULT '{}',
    
    CHECK (avg_consensus_level BETWEEN 0.00 AND 1.00),
    CHECK (avg_quality_score BETWEEN 0.00 AND 1.00),
    CHECK (effectiveness_score BETWEEN 0.00 AND 1.00),
    UNIQUE(team_id, protocol_type)
);

-- Indexes
CREATE INDEX idx_protocol_performance_team ON debate.protocol_performance(team_id);
CREATE INDEX idx_protocol_performance_protocol ON debate.protocol_performance(protocol_type);
CREATE INDEX idx_protocol_performance_effectiveness ON debate.protocol_performance(effectiveness_score);
```

### debate.ml_training_data

**Purpose**: Machine learning training data collection
**Records**: 1M-100M expected
**Partitioning**: By date (weekly)

```sql
CREATE TABLE debate.ml_training_data (
    training_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES debate.sessions(session_id) ON DELETE CASCADE,
    feature_vector JSONB NOT NULL,
    target_metrics JSONB NOT NULL,
    model_version VARCHAR(50),
    data_quality_score DECIMAL(3,2) DEFAULT 1.00,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_validated BOOLEAN DEFAULT false,
    
    CHECK (data_quality_score BETWEEN 0.00 AND 1.00)
) PARTITION BY RANGE (created_at);

-- Indexes
CREATE INDEX idx_ml_training_session ON debate.ml_training_data(session_id);
CREATE INDEX idx_ml_training_model ON debate.ml_training_data(model_version);
CREATE INDEX idx_ml_training_quality ON debate.ml_training_data(data_quality_score);
CREATE INDEX idx_ml_training_validated ON debate.ml_training_data(is_validated);
```

### debate.cached_results

**Purpose**: High-performance result caching with TTL
**Records**: 100K-1M expected
**Partitioning**: By cache_key hash

```sql
CREATE TABLE debate.cached_results (
    cache_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cache_key VARCHAR(255) NOT NULL UNIQUE,
    result_data JSONB NOT NULL,
    cache_type VARCHAR(50) NOT NULL,
    ttl_seconds INTEGER DEFAULT 600,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    hit_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_cached_results_key ON debate.cached_results(cache_key);
CREATE INDEX idx_cached_results_expires ON debate.cached_results(expires_at);
CREATE INDEX idx_cached_results_type ON debate.cached_results(cache_type);

-- Automatic cleanup of expired entries
CREATE INDEX idx_cached_results_cleanup ON debate.cached_results(expires_at)
WHERE expires_at < NOW();
```

---

## Context Detection Tables

### context.user_contexts

**Purpose**: Individual user context and emotional state
**Records**: 100K-10M expected
**Partitioning**: By user_id hash

```sql
CREATE TABLE context.user_contexts (
    context_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    emotional_state VARCHAR(50) NOT NULL,
    emotional_vector JSONB NOT NULL, -- {valence, arousal, confidence}
    communication_pattern JSONB NOT NULL,
    digital_activity JSONB NOT NULL,
    context_signals JSONB DEFAULT '[]',
    context_confidence DECIMAL(3,2) DEFAULT 0.00,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CHECK (context_confidence BETWEEN 0.00 AND 1.00)
);

-- Indexes
CREATE INDEX idx_user_contexts_user_id ON context.user_contexts(user_id);
CREATE INDEX idx_user_contexts_emotional_state ON context.user_contexts(emotional_state);
CREATE INDEX idx_user_contexts_confidence ON context.user_contexts(context_confidence);
CREATE INDEX idx_user_contexts_updated_at ON context.user_contexts(updated_at);
```

**Key Fields**:
- `emotional_state`: Detected state (`stressed`, `focused`, `engaged`, `frustrated`, `collaborative`, `productive`, `overwhelmed`, `neutral`)
- `emotional_vector`: JSONB with valence (-1.0 to 1.0), arousal (0.0 to 1.0), confidence (0.0 to 1.0)

### context.team_contexts

**Purpose**: Team-level context aggregation and dynamics
**Records**: 10K-100K expected
**Partitioning**: By team_id

```sql
CREATE TABLE context.team_contexts (
    context_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    project_phase VARCHAR(50) NOT NULL,
    team_dynamic JSONB NOT NULL,
    average_emotional_vector JSONB NOT NULL,
    dominant_emotions VARCHAR(50)[] DEFAULT '{}',
    stress_level DECIMAL(3,2) DEFAULT 0.00,
    productivity_score DECIMAL(3,2) DEFAULT 0.00,
    context_history JSONB DEFAULT '[]',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CHECK (stress_level BETWEEN 0.00 AND 1.00),
    CHECK (productivity_score BETWEEN 0.00 AND 1.00)
);

-- Indexes
CREATE INDEX idx_team_contexts_team_id ON context.team_contexts(team_id);
CREATE INDEX idx_team_contexts_project_phase ON context.team_contexts(project_phase);
CREATE INDEX idx_team_contexts_stress_level ON context.team_contexts(stress_level);
CREATE INDEX idx_team_contexts_productivity ON context.team_contexts(productivity_score);
```

**Key Fields**:
- `project_phase`: Detected phase (`planning`, `execution`, `review`, `retrospective`, `crisis`, `maintenance`)
- `team_dynamic`: JSONB with collaboration_score, conflict_indicators, decision_velocity, participation_balance

### context.context_events

**Purpose**: Context change events and anomaly detection
**Records**: 1M-100M expected
**Partitioning**: By date (daily)

```sql
CREATE TABLE context.context_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES core.users(user_id) ON DELETE CASCADE,
    team_id UUID REFERENCES core.teams(team_id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    previous_state JSONB,
    new_state JSONB NOT NULL,
    severity VARCHAR(20) DEFAULT 'info',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
) PARTITION BY RANGE (created_at);

-- Indexes
CREATE INDEX idx_context_events_user_id ON context.context_events(user_id);
CREATE INDEX idx_context_events_team_id ON context.context_events(team_id);
CREATE INDEX idx_context_events_type ON context.context_events(event_type);
CREATE INDEX idx_context_events_severity ON context.context_events(severity);
CREATE INDEX idx_context_events_created_at ON context.context_events(created_at);
```

**Key Fields**:
- `event_type`: Event category (`state_change`, `anomaly`, `threshold_breach`, `pattern_detected`)
- `severity`: Alert level (`info`, `warning`, `critical`)

### context.configurations

**Purpose**: Context detection configuration per user/team
**Records**: 10K-100K expected
**Partitioning**: None

```sql
CREATE TABLE context.configurations (
    config_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES core.users(user_id) ON DELETE CASCADE,
    team_id UUID REFERENCES core.teams(team_id) ON DELETE CASCADE,
    enabled_sensors VARCHAR(100)[] DEFAULT '{}',
    detection_thresholds JSONB DEFAULT '{}',
    temporal_decay_factor DECIMAL(3,2) DEFAULT 0.10,
    context_window_minutes INTEGER DEFAULT 60,
    privacy_mode BOOLEAN DEFAULT false,
    alert_settings JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CHECK (temporal_decay_factor BETWEEN 0.00 AND 1.00),
    CHECK (context_window_minutes > 0),
    CHECK ((user_id IS NOT NULL) != (team_id IS NOT NULL)) -- XOR constraint
);

-- Indexes
CREATE INDEX idx_context_configs_user_id ON context.configurations(user_id);
CREATE INDEX idx_context_configs_team_id ON context.configurations(team_id);
CREATE INDEX idx_context_configs_privacy ON context.configurations(privacy_mode);
```

---

## Memory System Tables

### memory.entries

**Purpose**: Adaptive memory storage with embeddings
**Records**: 1M-100M expected
**Partitioning**: By user_id hash

```sql
CREATE TABLE memory.entries (
    entry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES core.users(user_id) ON DELETE CASCADE,
    team_id UUID REFERENCES core.teams(team_id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES core.conversations(conversation_id) ON DELETE CASCADE,
    key VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(384), -- Using pgvector extension
    content_type VARCHAR(50) DEFAULT 'text',
    importance_score DECIMAL(3,2) DEFAULT 0.50,
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}',
    tags VARCHAR(100)[] DEFAULT '{}',
    
    CHECK (importance_score BETWEEN 0.00 AND 1.00)
);

-- Indexes
CREATE INDEX idx_memory_entries_user_id ON memory.entries(user_id);
CREATE INDEX idx_memory_entries_team_id ON memory.entries(team_id);
CREATE INDEX idx_memory_entries_conversation_id ON memory.entries(conversation_id);
CREATE INDEX idx_memory_entries_key ON memory.entries(key);
CREATE INDEX idx_memory_entries_importance ON memory.entries(importance_score);
CREATE INDEX idx_memory_entries_expires ON memory.entries(expires_at);
CREATE INDEX idx_memory_entries_tags ON memory.entries USING GIN(tags);

-- Vector similarity index (requires pgvector extension)
CREATE INDEX idx_memory_entries_embedding ON memory.entries 
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

### memory.patterns

**Purpose**: Learned patterns and behaviors
**Records**: 100K-1M expected
**Partitioning**: By pattern_type

```sql
CREATE TABLE memory.patterns (
    pattern_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID REFERENCES core.teams(team_id) ON DELETE CASCADE,
    pattern_type VARCHAR(50) NOT NULL,
    pattern_data JSONB NOT NULL,
    confidence_score DECIMAL(3,2) DEFAULT 0.00,
    occurrence_count INTEGER DEFAULT 1,
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true,
    
    CHECK (confidence_score BETWEEN 0.00 AND 1.00)
) PARTITION BY LIST (pattern_type);

-- Pattern type partitions
CREATE TABLE memory.patterns_communication PARTITION OF memory.patterns
FOR VALUES IN ('communication_style', 'response_pattern', 'interaction_frequency');

CREATE TABLE memory.patterns_workflow PARTITION OF memory.patterns
FOR VALUES IN ('workflow_preference', 'decision_pattern', 'collaboration_style');

-- Indexes
CREATE INDEX idx_memory_patterns_team_id ON memory.patterns(team_id);
CREATE INDEX idx_memory_patterns_confidence ON memory.patterns(confidence_score);
CREATE INDEX idx_memory_patterns_occurrence ON memory.patterns(occurrence_count);
CREATE INDEX idx_memory_patterns_active ON memory.patterns(is_active);
```

### memory.retrievals

**Purpose**: Memory access tracking for optimization
**Records**: 10M-1B expected
**Partitioning**: By date (daily)

```sql
CREATE TABLE memory.retrievals (
    retrieval_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id UUID NOT NULL REFERENCES memory.entries(entry_id) ON DELETE CASCADE,
    user_id UUID REFERENCES core.users(user_id) ON DELETE CASCADE,
    query_text TEXT,
    similarity_score DECIMAL(5,4),
    retrieval_method VARCHAR(50) NOT NULL,
    response_time_ms INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CHECK (similarity_score BETWEEN 0.0000 AND 1.0000)
) PARTITION BY RANGE (created_at);

-- Indexes
CREATE INDEX idx_memory_retrievals_entry_id ON memory.retrievals(entry_id);
CREATE INDEX idx_memory_retrievals_user_id ON memory.retrievals(user_id);
CREATE INDEX idx_memory_retrievals_similarity ON memory.retrievals(similarity_score);
CREATE INDEX idx_memory_retrievals_method ON memory.retrievals(retrieval_method);
```

---

## Messaging Hub Tables

### messaging.channels

**Purpose**: Platform-specific channel configurations
**Records**: 10K-100K expected
**Partitioning**: By platform

```sql
CREATE TABLE messaging.channels (
    channel_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    platform VARCHAR(50) NOT NULL,
    external_channel_id VARCHAR(255) NOT NULL,
    channel_name VARCHAR(255),
    channel_type VARCHAR(50) DEFAULT 'group',
    configuration JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(platform, external_channel_id)
);

-- Indexes
CREATE INDEX idx_messaging_channels_team_id ON messaging.channels(team_id);
CREATE INDEX idx_messaging_channels_platform ON messaging.channels(platform);
CREATE INDEX idx_messaging_channels_active ON messaging.channels(is_active);
CREATE INDEX idx_messaging_channels_external ON messaging.channels(external_channel_id);
```

**Key Fields**:
- `platform`: Platform type (`slack`, `teams`, `telegram`, `whatsapp`, `discord`)
- `channel_type`: Channel type (`direct`, `group`, `channel`, `thread`)

### messaging.webhooks

**Purpose**: Webhook endpoint management and security
**Records**: 1K-10K expected
**Partitioning**: None

```sql
CREATE TABLE messaging.webhooks (
    webhook_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    platform VARCHAR(50) NOT NULL,
    endpoint_url TEXT NOT NULL,
    secret_token VARCHAR(255),
    verification_token VARCHAR(255),
    is_active BOOLEAN DEFAULT true,
    last_ping TIMESTAMP WITH TIME ZONE,
    failure_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_messaging_webhooks_team_id ON messaging.webhooks(team_id);
CREATE INDEX idx_messaging_webhooks_platform ON messaging.webhooks(platform);
CREATE INDEX idx_messaging_webhooks_active ON messaging.webhooks(is_active);
```

### messaging.message_routing

**Purpose**: Message routing and delivery tracking
**Records**: 10M-1B expected
**Partitioning**: By date (daily)

```sql
CREATE TABLE messaging.message_routing (
    routing_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_message_id UUID REFERENCES core.messages(message_id) ON DELETE CASCADE,
    target_platform VARCHAR(50) NOT NULL,
    target_channel_id VARCHAR(255) NOT NULL,
    routing_status VARCHAR(50) DEFAULT 'pending',
    delivery_attempts INTEGER DEFAULT 0,
    last_attempt TIMESTAMP WITH TIME ZONE,
    delivered_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    routing_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
) PARTITION BY RANGE (created_at);

-- Indexes
CREATE INDEX idx_message_routing_source ON messaging.message_routing(source_message_id);
CREATE INDEX idx_message_routing_target ON messaging.message_routing(target_platform, target_channel_id);
CREATE INDEX idx_message_routing_status ON messaging.message_routing(routing_status);
CREATE INDEX idx_message_routing_attempts ON messaging.message_routing(delivery_attempts);
```

---

## Monitoring Tables

### monitoring.system_metrics

**Purpose**: System performance and health metrics
**Records**: 10M-1B expected (high volume)
**Partitioning**: By date (hourly)

```sql
CREATE TABLE monitoring.system_metrics (
    metric_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_name VARCHAR(100) NOT NULL,
    metric_value DECIMAL(15,4) NOT NULL,
    metric_unit VARCHAR(20),
    component VARCHAR(50) NOT NULL,
    instance_id VARCHAR(100),
    labels JSONB DEFAULT '{}',
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
) PARTITION BY RANGE (collected_at);

-- Hourly partitions for high-volume metrics
CREATE TABLE monitoring.system_metrics_2024_01_01_00 PARTITION OF monitoring.system_metrics
FOR VALUES FROM ('2024-01-01 00:00:00+00') TO ('2024-01-01 01:00:00+00');

-- Indexes
CREATE INDEX idx_system_metrics_name ON monitoring.system_metrics(metric_name);
CREATE INDEX idx_system_metrics_component ON monitoring.system_metrics(component);
CREATE INDEX idx_system_metrics_collected_at ON monitoring.system_metrics(collected_at);
CREATE INDEX idx_system_metrics_labels ON monitoring.system_metrics USING GIN(labels);
```

### monitoring.alerts

**Purpose**: Alert and notification management
**Records**: 100K-10M expected
**Partitioning**: By date (weekly)

```sql
CREATE TABLE monitoring.alerts (
    alert_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_rule VARCHAR(255) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    status VARCHAR(50) DEFAULT 'active',
    title VARCHAR(500) NOT NULL,
    description TEXT,
    labels JSONB DEFAULT '{}',
    annotations JSONB DEFAULT '{}',
    triggered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at TIMESTAMP WITH TIME ZONE,
    acknowledged_at TIMESTAMP WITH TIME ZONE,
    acknowledged_by UUID REFERENCES core.users(user_id),
    
    CHECK (severity IN ('info', 'warning', 'critical'))
) PARTITION BY RANGE (triggered_at);

-- Indexes
CREATE INDEX idx_alerts_rule ON monitoring.alerts(alert_rule);
CREATE INDEX idx_alerts_severity ON monitoring.alerts(severity);
CREATE INDEX idx_alerts_status ON monitoring.alerts(status);
CREATE INDEX idx_alerts_triggered_at ON monitoring.alerts(triggered_at);
```

### monitoring.performance_logs

**Purpose**: Detailed performance logging and analysis
**Records**: 100M-10B expected (very high volume)
**Partitioning**: By date (hourly) with automatic cleanup

```sql
CREATE TABLE monitoring.performance_logs (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation VARCHAR(100) NOT NULL,
    duration_ms INTEGER NOT NULL,
    cpu_usage_percent DECIMAL(5,2),
    memory_usage_mb INTEGER,
    error_count INTEGER DEFAULT 0,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
) PARTITION BY RANGE (created_at);

-- Indexes
CREATE INDEX idx_performance_logs_operation ON monitoring.performance_logs(operation);
CREATE INDEX idx_performance_logs_duration ON monitoring.performance_logs(duration_ms);
CREATE INDEX idx_performance_logs_created_at ON monitoring.performance_logs(created_at);

-- Automatic cleanup function
CREATE OR REPLACE FUNCTION cleanup_old_performance_logs()
RETURNS void AS $$
BEGIN
    DELETE FROM monitoring.performance_logs 
    WHERE created_at < NOW() - INTERVAL '7 days';
END;
$$ LANGUAGE plpgsql;
```

---

## Security Tables

### security.audit_logs

**Purpose**: Comprehensive audit trail for security compliance
**Records**: 10M-1B expected
**Partitioning**: By date (daily)

```sql
CREATE TABLE security.audit_logs (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES core.users(user_id),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id VARCHAR(255),
    ip_address INET,
    user_agent TEXT,
    session_id VARCHAR(255),
    success BOOLEAN DEFAULT true,
    error_message TEXT,
    additional_data JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
) PARTITION BY RANGE (created_at);

-- Indexes
CREATE INDEX idx_audit_logs_user_id ON security.audit_logs(user_id);
CREATE INDEX idx_audit_logs_action ON security.audit_logs(action);
CREATE INDEX idx_audit_logs_resource ON security.audit_logs(resource_type, resource_id);
CREATE INDEX idx_audit_logs_ip_address ON security.audit_logs(ip_address);
CREATE INDEX idx_audit_logs_success ON security.audit_logs(success);
CREATE INDEX idx_audit_logs_created_at ON security.audit_logs(created_at);
```

### security.api_keys

**Purpose**: API key management and rotation
**Records**: 10K-100K expected
**Partitioning**: None

```sql
CREATE TABLE security.api_keys (
    key_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    key_hash VARCHAR(255) NOT NULL UNIQUE, -- bcrypt hash of API key
    key_prefix VARCHAR(10) NOT NULL, -- First 8 chars for identification
    name VARCHAR(255) NOT NULL,
    permissions JSONB DEFAULT '{}',
    rate_limit_per_hour INTEGER DEFAULT 1000,
    last_used TIMESTAMP WITH TIME ZONE,
    usage_count BIGINT DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_ip INET,
    
    UNIQUE(user_id, name)
);

-- Indexes
CREATE INDEX idx_api_keys_user_id ON security.api_keys(user_id);
CREATE INDEX idx_api_keys_hash ON security.api_keys(key_hash);
CREATE INDEX idx_api_keys_prefix ON security.api_keys(key_prefix);
CREATE INDEX idx_api_keys_active ON security.api_keys(is_active);
CREATE INDEX idx_api_keys_expires_at ON security.api_keys(expires_at);
```

### security.sessions

**Purpose**: User session management and tracking
**Records**: 100K-10M expected
**Partitioning**: None (with TTL cleanup)

```sql
CREATE TABLE security.sessions (
    session_id VARCHAR(255) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_activity TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    is_active BOOLEAN DEFAULT true,
    session_data JSONB DEFAULT '{}'
);

-- Indexes
CREATE INDEX idx_sessions_user_id ON security.sessions(user_id);
CREATE INDEX idx_sessions_expires_at ON security.sessions(expires_at);
CREATE INDEX idx_sessions_last_activity ON security.sessions(last_activity);
CREATE INDEX idx_sessions_active ON security.sessions(is_active);

-- Automatic session cleanup
CREATE OR REPLACE FUNCTION cleanup_expired_sessions()
RETURNS void AS $$
BEGIN
    DELETE FROM security.sessions 
    WHERE expires_at < NOW() OR last_activity < NOW() - INTERVAL '30 days';
END;
$$ LANGUAGE plpgsql;
```

---

## Data Types and Enums

### Custom Types

```sql
-- Emotional state enumeration
CREATE TYPE emotional_state_enum AS ENUM (
    'stressed', 'focused', 'engaged', 'frustrated', 
    'collaborative', 'productive', 'overwhelmed', 'neutral'
);

-- Project phase enumeration
CREATE TYPE project_phase_enum AS ENUM (
    'planning', 'execution', 'review', 'retrospective', 'crisis', 'maintenance'
);

-- Message channel enumeration
CREATE TYPE message_channel_enum AS ENUM (
    'slack', 'teams', 'email', 'webhook', 'telegram', 'whatsapp', 'discord'
);

-- Task status enumeration
CREATE TYPE task_status_enum AS ENUM (
    'todo', 'in_progress', 'done', 'blocked', 'cancelled'
);

-- Task priority enumeration
CREATE TYPE task_priority_enum AS ENUM (
    'low', 'medium', 'high', 'critical'
);

-- Debate protocol enumeration
CREATE TYPE debate_protocol_enum AS ENUM (
    'structured_consensus', 'devil_advocate', 'multi_perspective', 
    'rapid_fire', 'consensus_building', 'critical_analysis'
);

-- Agent role enumeration
CREATE TYPE agent_role_enum AS ENUM (
    'product_owner', 'scrum_master', 'developer', 'architect', 
    'qa_engineer', 'ui_ux_designer', 'business_analyst'
);
```

### JSONB Schemas

Common JSONB field schemas for validation:

```sql
-- Emotional vector schema
-- {valence: -1.0 to 1.0, arousal: 0.0 to 1.0, confidence: 0.0 to 1.0, timestamp: ISO8601}

-- Communication pattern schema
-- {message_frequency: float, avg_response_time: float, message_length_avg: float, 
--  tone_score: -1.0 to 1.0, urgency_indicators: int, thread_depth: int}

-- Digital activity schema
-- {typing_speed: float, response_delay: float, concurrent_conversations: int,
--  context_switches: int, active_duration: float, idle_periods: float[]}

-- Team dynamic schema
-- {collaboration_score: 0.0-1.0, conflict_indicators: int, decision_velocity: float,
--  participation_balance: 0.0-1.0, support_interactions: int, knowledge_sharing_score: 0.0-1.0}

-- Performance metrics schema
-- {cpu_usage: float, memory_usage: float, response_time_p95: float, error_rate: float,
--  throughput_rps: float, active_connections: int}
```

---

## Indexes and Performance

### Primary Indexes

All tables include optimized primary and foreign key indexes as shown in table definitions.

### Composite Indexes

```sql
-- High-performance composite indexes for common query patterns

-- Conversation analysis
CREATE INDEX idx_messages_conversation_time ON core.messages(conversation_id, created_at);
CREATE INDEX idx_messages_user_time ON core.messages(user_id, created_at);

-- Debate performance analysis
CREATE INDEX idx_debate_team_protocol_time ON debate.sessions(team_id, protocol_type, created_at);
CREATE INDEX idx_debate_status_consensus ON debate.sessions(status, consensus_level);

-- Context tracking
CREATE INDEX idx_context_user_emotional_time ON context.user_contexts(user_id, emotional_state, updated_at);
CREATE INDEX idx_context_team_stress_time ON context.team_contexts(team_id, stress_level, updated_at);

-- Memory retrieval optimization
CREATE INDEX idx_memory_user_importance ON memory.entries(user_id, importance_score);
CREATE INDEX idx_memory_team_tags ON memory.entries(team_id) WHERE tags IS NOT NULL;

-- Monitoring and alerting
CREATE INDEX idx_metrics_component_name_time ON monitoring.system_metrics(component, metric_name, collected_at);
CREATE INDEX idx_alerts_severity_status_time ON monitoring.alerts(severity, status, triggered_at);
```

### Partial Indexes

```sql
-- Indexes on filtered data for better performance
CREATE INDEX idx_active_conversations ON core.conversations(team_id, updated_at) 
WHERE status = 'active';

CREATE INDEX idx_active_debate_sessions ON debate.sessions(team_id, created_at) 
WHERE status IN ('pending', 'active');

CREATE INDEX idx_unresolved_alerts ON monitoring.alerts(severity, triggered_at) 
WHERE status = 'active';

CREATE INDEX idx_recent_context_events ON context.context_events(team_id, created_at) 
WHERE created_at > NOW() - INTERVAL '24 hours';
```

### Performance Tuning

```sql
-- Analyze table statistics regularly
CREATE OR REPLACE FUNCTION update_table_statistics()
RETURNS void AS $$
DECLARE
    tbl record;
BEGIN
    FOR tbl IN 
        SELECT schemaname, tablename 
        FROM pg_tables 
        WHERE schemaname IN ('core', 'debate', 'context', 'memory', 'messaging', 'monitoring', 'security')
    LOOP
        EXECUTE 'ANALYZE ' || quote_ident(tbl.schemaname) || '.' || quote_ident(tbl.tablename);
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Schedule to run every hour
SELECT cron.schedule('update-stats', '0 * * * *', 'SELECT update_table_statistics();');
```

---

## Views and Functions

### Analytical Views

```sql
-- Team performance summary view
CREATE VIEW analytics.team_performance_summary AS
SELECT 
    t.team_id,
    t.name as team_name,
    COUNT(DISTINCT ds.session_id) as total_debates,
    AVG(ds.consensus_level) as avg_consensus,
    AVG(ds.duration_seconds) as avg_duration_seconds,
    COUNT(DISTINCT c.conversation_id) as total_conversations,
    AVG(tc.stress_level) as avg_stress_level,
    AVG(tc.productivity_score) as avg_productivity
FROM core.teams t
LEFT JOIN debate.sessions ds ON t.team_id = ds.team_id AND ds.status = 'completed'
LEFT JOIN core.conversations c ON t.team_id = c.team_id 
LEFT JOIN context.team_contexts tc ON t.team_id = tc.team_id
WHERE t.is_active = true
GROUP BY t.team_id, t.name;

-- User context timeline view
CREATE VIEW analytics.user_context_timeline AS
SELECT 
    uc.user_id,
    u.full_name,
    uc.emotional_state,
    uc.context_confidence,
    uc.updated_at,
    LAG(uc.emotional_state) OVER (PARTITION BY uc.user_id ORDER BY uc.updated_at) as previous_state,
    uc.updated_at - LAG(uc.updated_at) OVER (PARTITION BY uc.user_id ORDER BY uc.updated_at) as state_duration
FROM context.user_contexts uc
JOIN core.users u ON uc.user_id = u.user_id
WHERE u.is_active = true;

-- Debate effectiveness by protocol view
CREATE VIEW analytics.protocol_effectiveness AS
SELECT 
    ds.protocol_type,
    COUNT(*) as session_count,
    AVG(ds.consensus_level) as avg_consensus,
    AVG(ds.duration_seconds) as avg_duration,
    COUNT(*) FILTER (WHERE ds.consensus_level > 0.8) as high_consensus_count,
    ROUND(
        COUNT(*) FILTER (WHERE ds.consensus_level > 0.8) * 100.0 / COUNT(*),
        2
    ) as success_rate_percent
FROM debate.sessions ds
WHERE ds.status = 'completed' 
  AND ds.created_at > NOW() - INTERVAL '30 days'
GROUP BY ds.protocol_type
ORDER BY success_rate_percent DESC;
```

### Utility Functions

```sql
-- Function to get team emotional overview
CREATE OR REPLACE FUNCTION get_team_emotional_overview(p_team_id UUID)
RETURNS TABLE (
    emotional_state VARCHAR(50),
    user_count BIGINT,
    avg_confidence DECIMAL(3,2)
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        uc.emotional_state,
        COUNT(*) as user_count,
        AVG(uc.context_confidence) as avg_confidence
    FROM context.user_contexts uc
    JOIN core.users u ON uc.user_id = u.user_id
    JOIN core.team_members tm ON u.user_id = tm.user_id
    WHERE tm.team_id = p_team_id 
      AND tm.is_active = true
      AND uc.updated_at > NOW() - INTERVAL '1 hour'
    GROUP BY uc.emotional_state
    ORDER BY user_count DESC;
END;
$$ LANGUAGE plpgsql;

-- Function to trigger debate based on complexity
CREATE OR REPLACE FUNCTION should_trigger_debate(
    p_team_id UUID,
    p_complexity_threshold DECIMAL DEFAULT 0.7
) RETURNS BOOLEAN AS $$
DECLARE
    recent_complexity DECIMAL;
    team_stress DECIMAL;
    active_debates INTEGER;
BEGIN
    -- Get recent complexity analysis
    SELECT AVG(overall_complexity) INTO recent_complexity
    FROM debate.complexity_analysis ca
    JOIN debate.sessions ds ON ca.session_id = ds.session_id
    WHERE ds.team_id = p_team_id 
      AND ca.created_at > NOW() - INTERVAL '1 hour';
    
    -- Get current team stress level
    SELECT stress_level INTO team_stress
    FROM context.team_contexts 
    WHERE team_id = p_team_id 
    ORDER BY updated_at DESC 
    LIMIT 1;
    
    -- Count active debates
    SELECT COUNT(*) INTO active_debates
    FROM debate.sessions
    WHERE team_id = p_team_id 
      AND status IN ('pending', 'active');
    
    -- Decision logic
    RETURN (
        COALESCE(recent_complexity, 0) > p_complexity_threshold 
        OR COALESCE(team_stress, 0) > 0.8
    ) AND active_debates < 3;
END;
$$ LANGUAGE plpgsql;
```

---

## Partitioning Strategy

### Time-based Partitioning

Most high-volume tables use time-based partitioning for optimal performance:

```sql
-- Automatic partition creation function
CREATE OR REPLACE FUNCTION create_monthly_partitions(
    table_schema TEXT,
    table_name TEXT,
    start_date DATE,
    end_date DATE
) RETURNS void AS $$
DECLARE
    partition_date DATE;
    partition_name TEXT;
    partition_start TEXT;
    partition_end TEXT;
BEGIN
    partition_date := date_trunc('month', start_date);
    
    WHILE partition_date < end_date LOOP
        partition_name := table_name || '_' || to_char(partition_date, 'YYYY_MM');
        partition_start := partition_date::TEXT;
        partition_end := (partition_date + INTERVAL '1 month')::TEXT;
        
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
            table_schema, partition_name, table_schema, table_name,
            partition_start, partition_end
        );
        
        partition_date := partition_date + INTERVAL '1 month';
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Create partitions for next 12 months
SELECT create_monthly_partitions('core', 'conversations', CURRENT_DATE, CURRENT_DATE + INTERVAL '12 months');
SELECT create_monthly_partitions('core', 'messages', CURRENT_DATE, CURRENT_DATE + INTERVAL '12 months');
SELECT create_monthly_partitions('debate', 'sessions', CURRENT_DATE, CURRENT_DATE + INTERVAL '12 months');
```

### Hash-based Partitioning

For even distribution across partitions:

```sql
-- User context partitioning by user_id hash
CREATE TABLE context.user_contexts_0 PARTITION OF context.user_contexts
FOR VALUES WITH (modulus 4, remainder 0);

CREATE TABLE context.user_contexts_1 PARTITION OF context.user_contexts
FOR VALUES WITH (modulus 4, remainder 1);

CREATE TABLE context.user_contexts_2 PARTITION OF context.user_contexts
FOR VALUES WITH (modulus 4, remainder 2);

CREATE TABLE context.user_contexts_3 PARTITION OF context.user_contexts
FOR VALUES WITH (modulus 4, remainder 3);
```

### Partition Maintenance

```sql
-- Automatic partition maintenance
CREATE OR REPLACE FUNCTION maintain_partitions()
RETURNS void AS $$
BEGIN
    -- Drop old partitions (older than 1 year for most tables)
    PERFORM drop_old_partitions('core', 'messages', INTERVAL '1 year');
    PERFORM drop_old_partitions('monitoring', 'system_metrics', INTERVAL '30 days');
    PERFORM drop_old_partitions('monitoring', 'performance_logs', INTERVAL '7 days');
    
    -- Create future partitions
    PERFORM create_future_partitions('core', 'conversations', INTERVAL '3 months');
    PERFORM create_future_partitions('core', 'messages', INTERVAL '3 months');
    PERFORM create_future_partitions('debate', 'sessions', INTERVAL '3 months');
END;
$$ LANGUAGE plpgsql;

-- Schedule partition maintenance
SELECT cron.schedule('partition-maintenance', '0 2 * * 0', 'SELECT maintain_partitions();');
```

---

## Backup and Recovery

### Backup Strategy

```sql
-- Backup configuration
-- Full backup: Daily at 2 AM
-- Incremental backup: Every 6 hours
-- Transaction log backup: Every 15 minutes

-- Point-in-time recovery setup
ALTER SYSTEM SET wal_level = 'replica';
ALTER SYSTEM SET archive_mode = 'on';
ALTER SYSTEM SET archive_command = 'cp %p /backup/archive/%f';
ALTER SYSTEM SET max_wal_senders = 3;
ALTER SYSTEM SET wal_keep_segments = 32;
```

### Recovery Procedures

```bash
# Example recovery commands
# Restore from backup
pg_restore -d devnous_prod /backup/devnous_backup_20240115.dump

# Point-in-time recovery
pg_ctl stop -D /var/lib/postgresql/data
rm -rf /var/lib/postgresql/data/*
pg_basebackup -h backup-server -D /var/lib/postgresql/data -U postgres -v -P
# Edit recovery.conf for target time
pg_ctl start -D /var/lib/postgresql/data
```

---

## Performance Monitoring

### Key Metrics to Monitor

```sql
-- Database performance queries

-- Connection usage
SELECT 
    datname,
    numbackends,
    xact_commit,
    xact_rollback,
    blks_read,
    blks_hit,
    tup_returned,
    tup_fetched,
    tup_inserted,
    tup_updated,
    tup_deleted
FROM pg_stat_database 
WHERE datname = 'devnous';

Note:

- `devnous` in this query is a standalone DevNous example database name.
- It is not the production database name for the current `sam.chat` deployment in this repository.
- See `docs/install_matrix.md` for the active runtime split.

-- Table sizes and statistics
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
    n_tup_ins,
    n_tup_upd,
    n_tup_del,
    n_live_tup,
    n_dead_tup,
    seq_scan,
    seq_tup_read,
    idx_scan,
    idx_tup_fetch
FROM pg_stat_user_tables 
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Index usage statistics
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes 
ORDER BY idx_scan DESC;

-- Slow queries
SELECT 
    query,
    calls,
    total_time,
    mean_time,
    rows
FROM pg_stat_statements 
ORDER BY mean_time DESC 
LIMIT 10;
```

---

## See Also

- [Configuration Reference](CONFIGURATION_REFERENCE.md)
- [API Quick Reference](API_QUICK_REFERENCE.md)
- [Performance Benchmarks Reference](PERFORMANCE_BENCHMARKS_REFERENCE.md)
- [Security Configuration Reference](SECURITY_CONFIGURATION_REFERENCE.md)
- [Deployment Reference](DEPLOYMENT_REFERENCE.md)
