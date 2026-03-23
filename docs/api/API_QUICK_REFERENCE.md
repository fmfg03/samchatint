# API Quick Reference

**Version**: 1.0.0  
**Last Updated**: 2024-01-15  
**Base URL**: `https://api.devnous.example.com/v1`  
**Target**: Frontend Developers, API Integrators, Mobile Developers

## Overview

This document provides condensed API documentation for rapid lookup during development. For complete documentation, see the full API specification.

Important:

- The `api.devnous.example.com` URLs in this document are standalone DevNous API examples.
- They are not the current production `sam.chat` base URLs for this repository deployment.
- For the active runtime/install split, see:
  - `docs/install_matrix.md`

## Table of Contents

- [Authentication](#authentication)
- [System Endpoints](#system-endpoints)
- [Memory Tools](#memory-tools)
- [Chat Application](#chat-application)
- [Project Management](#project-management)
- [Workflow Management](#workflow-management)
- [Context Detection](#context-detection)
- [Debate System](#debate-system)
- [Messaging Hub](#messaging-hub)
- [Monitoring](#monitoring)
- [Response Codes](#response-codes)
- [Rate Limits](#rate-limits)
- [SDKs](#sdks)

---

## Authentication

### API Key Authentication
```http
X-API-Key: your-api-key-here
```

### JWT Authentication
```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### OAuth 2.0 (External Integrations)
```http
Authorization: Bearer oauth-access-token
```

---

## System Endpoints

### Health Check
```http
GET /health
```
**Response**: `200 OK`
```json
{
  "status": "healthy",
  "services": [
    {
      "service": "database",
      "status": "healthy",
      "last_check": "2024-01-15T10:30:00Z"
    }
  ]
}
```

### API Information
```http
GET /
```
**Response**: `200 OK`
```json
{
  "message": "DevNous API - Development Team Assistant",
  "version": "1.0.0",
  "tools": ["Memory Management", "Chat Application", "Project Management"]
}
```

### System Metrics
```http
GET /monitoring
```
**Response**: `200 OK`
```json
{
  "metrics": {
    "requests_per_minute": 150,
    "memory_usage_percent": 68.5,
    "cpu_usage_percent": 45.2
  },
  "uptime": "72h 15m 32s"
}
```

---

## Memory Tools

### Store Memory
```http
POST /memory/store
Content-Type: application/json
```
**Request Body**:
```json
{
  "key": "user_preferences",
  "value": "{\"theme\": \"dark\", \"language\": \"en\"}",
  "ttl": 3600
}
```
**Response**: `200 OK`
```json
{
  "success": true,
  "message": "String memorized with key: user_preferences",
  "data": {
    "key": "user_preferences",
    "expires_at": "2024-01-15T11:30:00Z"
  }
}
```

### Retrieve Memory
```http
GET /memory/{key}
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "key": "user_preferences",
    "value": "{\"theme\": \"dark\", \"language\": \"en\"}",
    "created_at": "2024-01-15T10:30:00Z"
  }
}
```

### Delete Memory
```http
DELETE /memory/{key}
```
**Response**: `200 OK`
```json
{
  "success": true,
  "message": "Memory deleted for key: user_preferences"
}
```

### List Memory Keys
```http
GET /memory/list?pattern=user_*&limit=50
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "keys": ["user_preferences", "user_settings", "user_cache"],
    "total": 3
  }
}
```

### Store Conversation
```http
POST /memory/conversation/store
```
**Request Body**:
```json
{
  "conversation_id": "conv_123",
  "sender": "john_doe",
  "content": "Can we discuss the new feature requirements?"
}
```

### Get Conversation History
```http
GET /memory/conversation/{conversation_id}?limit=50&since=2024-01-15T10:00:00Z
```

---

## Chat Application

### Process Message
```http
POST /chat/process
Content-Type: application/json
```
**Request Body**:
```json
{
  "channel": "slack",
  "sender": "john_doe",
  "content": "What are the current sprint tasks?",
  "channel_id": "C1234567890",
  "metadata": {
    "thread_ts": "1642234567.123456"
  }
}
```
**Response**: `200 OK`
```json
{
  "success": true,
  "message_id": "msg_abc123",
  "processed_at": "2024-01-15T10:30:00Z",
  "response": {
    "content": "Here are your current sprint tasks...",
    "suggestions": ["View task details", "Update task status"]
  }
}
```

### Send Message
```http
POST /chat/send
```
**Request Body**:
```json
{
  "channel": "slack",
  "recipient": "#general",
  "content": "Sprint planning meeting at 2 PM today",
  "metadata": {
    "priority": "high"
  }
}
```

### Get Team Information
```http
GET /chat/teams/{team_id}
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "team_id": "team_123",
    "name": "Development Team",
    "members": ["john_doe", "jane_smith"],
    "preferences": {
      "notification_channel": "#dev-alerts",
      "daily_standup_time": "09:00"
    }
  }
}
```

### Update Team Information
```http
PUT /chat/teams/{team_id}
```
**Request Body**:
```json
{
  "name": "Product Development Team",
  "preferences": {
    "notification_channel": "#product-alerts",
    "daily_standup_time": "09:30"
  }
}
```

---

## Project Management

### Create Task
```http
POST /pm/tasks
```
**Request Body**:
```json
{
  "title": "Implement user authentication",
  "description": "Add JWT-based authentication to the API",
  "priority": "high",
  "assignee": "john_doe",
  "labels": ["backend", "security"],
  "estimated_hours": 8,
  "due_date": "2024-01-20T23:59:59Z"
}
```
**Response**: `201 Created`
```json
{
  "success": true,
  "data": {
    "id": "TASK-123",
    "title": "Implement user authentication",
    "status": "todo",
    "created_at": "2024-01-15T10:30:00Z"
  }
}
```

### Get Tasks
```http
GET /pm/tasks?status=in_progress&assignee=john_doe&limit=20
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "tasks": [
      {
        "id": "TASK-123",
        "title": "Implement user authentication",
        "status": "in_progress",
        "priority": "high",
        "assignee": "john_doe"
      }
    ],
    "pagination": {
      "total": 15,
      "page": 1,
      "page_size": 20,
      "has_next": false
    }
  }
}
```

### Update Task
```http
PUT /pm/tasks/{task_id}
```
**Request Body**:
```json
{
  "status": "done",
  "actual_hours": 6.5,
  "metadata": {
    "completion_notes": "Implemented with additional 2FA support"
  }
}
```

### Delete Task
```http
DELETE /pm/tasks/{task_id}
```

### Task Statistics
```http
GET /pm/tasks/stats?team_id=team_123&period=week
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "total_tasks": 45,
    "completed_tasks": 38,
    "completion_rate": 84.4,
    "avg_completion_time_hours": 12.5,
    "by_priority": {
      "critical": 2,
      "high": 8,
      "medium": 25,
      "low": 10
    }
  }
}
```

---

## Workflow Management

### Create Workflow
```http
POST /workflow/create
```
**Request Body**:
```json
{
  "name": "Feature Development",
  "description": "Standard feature development workflow",
  "initial_step": "planning",
  "steps": {
    "planning": {
      "name": "Planning Phase",
      "required_data": ["requirements", "acceptance_criteria"],
      "next_steps": ["development", "design_review"]
    },
    "development": {
      "name": "Development Phase",
      "required_data": ["implementation_plan"],
      "next_steps": ["code_review", "testing"]
    }
  }
}
```

### Execute Workflow
```http
POST /workflow/execute
```
**Request Body**:
```json
{
  "workflow_name": "Feature Development",
  "workflow_id": "wf_feature_123",
  "initial_data": {
    "requirements": "User should be able to reset password",
    "priority": "high"
  }
}
```

### Get Workflow Status
```http
GET /workflow/status/{workflow_id}
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "workflow_id": "wf_feature_123",
    "status": "running",
    "current_step": "development",
    "progress": 60,
    "data": {
      "requirements": "User should be able to reset password",
      "implementation_plan": "Use JWT tokens with expiry"
    }
  }
}
```

### Update Workflow State
```http
PUT /workflow/state/{workflow_id}
```
**Request Body**:
```json
{
  "step": "code_review",
  "data": {
    "review_status": "approved",
    "reviewer": "tech_lead"
  }
}
```

---

## Context Detection

### Get User Context
```http
GET /context/users/{user_id}
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "user_id": "user_123",
    "emotional_state": "focused",
    "emotional_vector": {
      "valence": 0.7,
      "arousal": 0.8,
      "confidence": 0.85
    },
    "communication_pattern": {
      "message_frequency": 12.5,
      "avg_response_time": 45.2,
      "tone_score": 0.6
    },
    "context_confidence": 0.78,
    "last_updated": "2024-01-15T10:25:00Z"
  }
}
```

### Get Team Context
```http
GET /context/teams/{team_id}
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "team_id": "team_123",
    "project_phase": "execution",
    "team_dynamic": {
      "collaboration_score": 0.85,
      "stress_level": 0.45,
      "productivity_score": 0.78
    },
    "dominant_emotions": ["focused", "productive"],
    "last_updated": "2024-01-15T10:20:00Z"
  }
}
```

### Context Events Stream
```http
GET /context/events/stream?team_id=team_123
```
**Server-Sent Events Stream**:
```
data: {"event_type": "state_change", "user_id": "user_123", "new_state": "stressed"}

data: {"event_type": "anomaly", "team_id": "team_123", "severity": "warning"}
```

### Update Context Configuration
```http
PUT /context/config/{user_id}
```
**Request Body**:
```json
{
  "enabled_sensors": ["emotional_detection", "communication_analysis"],
  "detection_thresholds": {
    "stress_threshold": 0.8,
    "productivity_threshold": 0.6
  },
  "privacy_mode": false
}
```

---

## Debate System

### Trigger Debate
```http
POST /debates/trigger
```
**Request Body**:
```json
{
  "session_id": "debate_session_123",
  "team_id": "team_123",
  "conversation_id": "conv_456",
  "protocol_type": "structured_consensus",
  "context": "Should we implement microservices architecture?"
}
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "session_id": "debate_session_123",
    "status": "pending",
    "protocol_type": "structured_consensus",
    "estimated_duration": 1800
  }
}
```

### Get Debate Status
```http
GET /debates/{session_id}/status
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "session_id": "debate_session_123",
    "status": "active",
    "current_phase": "argument",
    "consensus_level": 0.65,
    "participants": ["product_owner", "developer", "architect"],
    "duration_seconds": 450
  }
}
```

### Get Debate Results
```http
GET /debates/{session_id}/results
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "session_id": "debate_session_123",
    "final_consensus": 0.87,
    "recommendation": "Proceed with microservices implementation",
    "key_points": [
      "Improved scalability and maintainability",
      "Initial complexity overhead acceptable",
      "Team has sufficient expertise"
    ],
    "complexity_analysis": {
      "technical_complexity": 0.75,
      "overall_complexity": 0.68
    }
  }
}
```

### List Team Debates
```http
GET /debates?team_id=team_123&limit=20&status=completed
```

### Debate Analytics
```http
GET /debates/analytics/{team_id}?period=30d
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "total_debates": 15,
    "avg_consensus_level": 0.78,
    "protocol_effectiveness": {
      "structured_consensus": 0.85,
      "devil_advocate": 0.72,
      "rapid_fire": 0.69
    },
    "avg_duration_minutes": 25.5
  }
}
```

---

## Messaging Hub

### Register Webhook
```http
POST /messaging/webhooks
```
**Request Body**:
```json
{
  "platform": "slack",
  "endpoint_url": "https://myapp.example.com/webhook/slack",
  "secret_token": "webhook_secret_123"
}
```

### Process Platform Message
```http
POST /messaging/process/{platform}
```
**Request Body** (Slack example):
```json
{
  "token": "verification_token",
  "team_id": "T1234567890",
  "channel_id": "C1234567890",
  "user_id": "U1234567890",
  "text": "What's the status of project X?",
  "timestamp": "1642234567.123456"
}
```

### Send Platform Message
```http
POST /messaging/send/{platform}
```
**Request Body**:
```json
{
  "channel_id": "C1234567890",
  "message": "Sprint review meeting starts in 10 minutes",
  "message_type": "notification",
  "metadata": {
    "priority": "high",
    "thread_ts": "1642234567.123456"
  }
}
```

### Get Platform Status
```http
GET /messaging/platforms/{platform}/status
```

### Message Routing
```http
POST /messaging/route
```
**Request Body**:
```json
{
  "source_message_id": "msg_123",
  "target_platforms": ["slack", "teams"],
  "routing_rules": {
    "urgent_only": true,
    "team_filter": ["dev_team", "qa_team"]
  }
}
```

---

## Monitoring

### Get System Health
```http
GET /monitoring/health
```
**Response**: `200 OK`
```json
{
  "status": "healthy",
  "components": {
    "api": "healthy",
    "database": "healthy", 
    "redis": "healthy",
    "debate_system": "healthy"
  },
  "last_check": "2024-01-15T10:30:00Z"
}
```

### Get Metrics
```http
GET /monitoring/metrics?component=api&period=1h
```
**Response**: `200 OK`
```json
{
  "success": true,
  "data": {
    "component": "api",
    "period": "1h",
    "metrics": [
      {
        "name": "requests_per_second",
        "value": 15.6,
        "timestamp": "2024-01-15T10:30:00Z"
      },
      {
        "name": "response_time_p95",
        "value": 245.8,
        "timestamp": "2024-01-15T10:30:00Z"
      }
    ]
  }
}
```

### Create Alert
```http
POST /monitoring/alerts
```
**Request Body**:
```json
{
  "name": "High Response Time",
  "condition": "response_time_p95 > 500",
  "severity": "warning",
  "notification_channels": ["slack", "email"]
}
```

### Get Alerts
```http
GET /monitoring/alerts?status=active&severity=critical
```

---

## Response Codes

### Success Codes
- `200 OK` - Request successful
- `201 Created` - Resource created successfully
- `202 Accepted` - Request accepted for processing
- `204 No Content` - Request successful, no content to return

### Client Error Codes
- `400 Bad Request` - Invalid request data
- `401 Unauthorized` - Authentication required
- `403 Forbidden` - Access denied
- `404 Not Found` - Resource not found
- `409 Conflict` - Resource conflict
- `422 Unprocessable Entity` - Validation errors
- `429 Too Many Requests` - Rate limit exceeded

### Server Error Codes
- `500 Internal Server Error` - Server error
- `502 Bad Gateway` - Upstream server error
- `503 Service Unavailable` - Service temporarily unavailable
- `504 Gateway Timeout` - Upstream timeout

### Error Response Format
```json
{
  "success": false,
  "error": "VALIDATION_ERROR",
  "message": "Invalid task priority value",
  "details": {
    "field": "priority",
    "value": "invalid_priority",
    "valid_values": ["low", "medium", "high", "critical"]
  },
  "timestamp": "2024-01-15T10:30:00Z",
  "request_id": "req_abc123"
}
```

---

## Rate Limits

### Default Limits
- **General API**: 1000 requests per hour per API key
- **Message Processing**: 60 requests per minute per sender
- **Debate Triggers**: 10 per hour per team
- **Webhook Endpoints**: 500 requests per hour per endpoint

### Rate Limit Headers
```http
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 987
X-RateLimit-Reset: 1642237200
Retry-After: 60
```

### Rate Limit Response
```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json

{
  "success": false,
  "error": "RATE_LIMIT_EXCEEDED",
  "message": "Too many requests. Limit: 1000 per hour",
  "retry_after": 60,
  "limit": 1000,
  "remaining": 0,
  "reset_at": "2024-01-15T11:00:00Z"
}
```

---

## SDKs

### Python SDK
```python
from devnous_client import DevNousClient

client = DevNousClient(api_key="your-api-key")

# Store memory
result = client.memory.store("user_prefs", {"theme": "dark"}, ttl=3600)

# Process message
response = client.chat.process_message(
    channel="slack",
    sender="john_doe", 
    content="What tasks are due today?"
)

# Trigger debate
debate = client.debates.trigger(
    team_id="team_123",
    protocol="structured_consensus",
    context="Should we refactor the payment service?"
)
```

### JavaScript SDK
```javascript
import { DevNousClient } from '@devnous/client';

const client = new DevNousClient({ apiKey: 'your-api-key' });

// Store memory
const result = await client.memory.store('user_prefs', { theme: 'dark' });

// Process message
const response = await client.chat.processMessage({
  channel: 'slack',
  sender: 'john_doe',
  content: 'What tasks are due today?'
});

// Get team context
const context = await client.context.getTeamContext('team_123');
```

### cURL Examples

#### Store Memory
```bash
curl -X POST https://api.devnous.example.com/v1/memory/store \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "user_preferences",
    "value": "{\"theme\": \"dark\"}",
    "ttl": 3600
  }'
```

#### Process Chat Message
```bash
curl -X POST https://api.devnous.example.com/v1/chat/process \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "slack",
    "sender": "john_doe",
    "content": "What are the current sprint tasks?"
  }'
```

#### Create Task
```bash
curl -X POST https://api.devnous.example.com/v1/pm/tasks \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Implement user authentication",
    "priority": "high",
    "assignee": "john_doe"
  }'
```

#### Trigger Debate
```bash
curl -X POST https://api.devnous.example.com/v1/debates/trigger \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "debate_session_123",
    "team_id": "team_123",
    "protocol_type": "structured_consensus",
    "context": "Should we implement microservices?"
  }'
```

---

## Quick Lookup Tables

### HTTP Methods by Endpoint
| Operation | Method | Endpoint Pattern |
|-----------|--------|------------------|
| Create | POST | `/resource` |
| Read | GET | `/resource/{id}` |
| Update | PUT/PATCH | `/resource/{id}` |
| Delete | DELETE | `/resource/{id}` |
| List | GET | `/resource` |

### Content Types
- **JSON**: `application/json`
- **Form Data**: `application/x-www-form-urlencoded`
- **File Upload**: `multipart/form-data`
- **Stream**: `text/event-stream` (SSE)

### Common Query Parameters
- `limit` - Pagination limit (default: 50, max: 1000)
- `offset` - Pagination offset (default: 0)
- `sort` - Sort field and direction (`field:asc` or `field:desc`)
- `filter` - Filter expression
- `since` - Timestamp filter for recent data
- `until` - Timestamp filter for historical data

---

## See Also

- [Configuration Reference](CONFIGURATION_REFERENCE.md)
- [Database Schema Reference](DATABASE_SCHEMA_REFERENCE.md)
- [CLI Commands Reference](CLI_COMMANDS_REFERENCE.md)
- [Error Codes Reference](ERROR_CODES_REFERENCE.md)
- [Performance Benchmarks Reference](PERFORMANCE_BENCHMARKS_REFERENCE.md)
- [Security Configuration Reference](SECURITY_CONFIGURATION_REFERENCE.md)
- [Deployment Reference](DEPLOYMENT_REFERENCE.md)
