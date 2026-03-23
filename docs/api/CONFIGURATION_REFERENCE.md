# SamChat/DevNous Configuration Reference

**Version**: 1.0.0  
**Last Updated**: 2024-01-15  
**Target**: Developers, DevOps Engineers, System Administrators

## Overview

This document provides exhaustive configuration reference for the SamChat/DevNous system. All configuration parameters are documented with descriptions, default values, validation rules, and examples.

Important:

- Several examples in this document describe the standalone DevNous API surface and use historical `devnous` naming.
- They should not be treated as the current production `sam.chat` deployment defaults.
- For the current runtime/install split, see:
  - `docs/install_matrix.md`

## Table of Contents

- [Environment Variables](#environment-variables)
- [Database Configuration](#database-configuration)
- [Cache Configuration](#cache-configuration)
- [External APIs Configuration](#external-apis-configuration)
- [LLM Provider Configuration](#llm-provider-configuration)
- [Messaging Platform Configuration](#messaging-platform-configuration)
- [Debate System Configuration](#debate-system-configuration)
- [Context Detection Configuration](#context-detection-configuration)
- [Memory System Configuration](#memory-system-configuration)
- [Monitoring Configuration](#monitoring-configuration)
- [Security Configuration](#security-configuration)
- [Performance Configuration](#performance-configuration)
- [Feature Flags](#feature-flags)
- [Configuration Validation](#configuration-validation)
- [Environment-Specific Configurations](#environment-specific-configurations)

---

## Environment Variables

### Core System Settings

#### **ENVIRONMENT**
- **Type**: `string`
- **Default**: `"development"`
- **Required**: `No`
- **Valid Values**: `"development"`, `"staging"`, `"production"`
- **Description**: Current deployment environment
- **Example**: `ENVIRONMENT=production`

#### **DEBUG**
- **Type**: `boolean`
- **Default**: `false`
- **Required**: `No`
- **Description**: Enable debug mode with verbose logging
- **Example**: `DEBUG=true`

#### **LOG_LEVEL**
- **Type**: `string`
- **Default**: `"INFO"`
- **Required**: `No`
- **Valid Values**: `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"`
- **Description**: Logging level for application logs
- **Example**: `LOG_LEVEL=DEBUG`

#### **LOG_FORMAT**
- **Type**: `string`
- **Default**: `"%(asctime)s - %(name)s - %(levelname)s - %(message)s"`
- **Required**: `No`
- **Description**: Python logging format string
- **Example**: `LOG_FORMAT=%(levelname)s:%(name)s:%(message)s`

#### **LOG_FILE_PATH**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Description**: File path for log output (if null, logs to stdout)
- **Example**: `LOG_FILE_PATH=/var/log/devnous/app.log`

---

## Database Configuration

### PostgreSQL Settings

#### **POSTGRESQL_URL**
- **Type**: `string`
- **Default**: `"postgresql://devnous:password@localhost:5432/devnous"`
- **Required**: `Yes`
- **Format**: `postgresql://[user[:password]@]host[:port]/database`
- **Description**: PostgreSQL connection string with authentication
- **Example**: `POSTGRESQL_URL=postgresql://user:pass@db.example.com:5432/devnous_prod`

#### **DB_POOL_SIZE**
- **Type**: `integer`
- **Default**: `10`
- **Required**: `No`
- **Range**: `1-100`
- **Description**: Maximum number of database connections in pool
- **Example**: `DB_POOL_SIZE=20`

#### **DB_POOL_OVERFLOW**
- **Type**: `integer`
- **Default**: `20`
- **Required**: `No`
- **Range**: `0-100`
- **Description**: Additional connections beyond pool size
- **Example**: `DB_POOL_OVERFLOW=30`

#### **DATABASE_POOL_TIMEOUT**
- **Type**: `integer`
- **Default**: `30`
- **Required**: `No`
- **Range**: `1-300`
- **Unit**: `seconds`
- **Description**: Timeout for getting connection from pool
- **Example**: `DATABASE_POOL_TIMEOUT=60`

#### **DATABASE_STATEMENT_TIMEOUT**
- **Type**: `integer`
- **Default**: `20000`
- **Required**: `No`
- **Range**: `1000-60000`
- **Unit**: `milliseconds`
- **Description**: Query execution timeout
- **Example**: `DATABASE_STATEMENT_TIMEOUT=30000`

#### **DATABASE_QUERY_LOG_ENABLED**
- **Type**: `boolean`
- **Default**: `false`
- **Required**: `No`
- **Description**: Enable SQL query logging
- **Example**: `DATABASE_QUERY_LOG_ENABLED=true`

### Redis Settings

#### **REDIS_URL**
- **Type**: `string`
- **Default**: `"redis://localhost:6379/0"`
- **Required**: `Yes`
- **Format**: `redis://[:password@]host[:port][/database]`
- **Description**: Redis connection string
- **Example**: `REDIS_URL=redis://:password@redis.example.com:6379/1`

#### **REDIS_POOL_SIZE**
- **Type**: `integer`
- **Default**: `30`
- **Required**: `No`
- **Range**: `1-100`
- **Description**: Redis connection pool size
- **Example**: `REDIS_POOL_SIZE=50`

#### **REDIS_POOL_TIMEOUT**
- **Type**: `integer`
- **Default**: `5`
- **Required**: `No`
- **Range**: `1-30`
- **Unit**: `seconds`
- **Description**: Redis connection timeout
- **Example**: `REDIS_POOL_TIMEOUT=10`

---

## Cache Configuration

#### **CACHE_DEFAULT_TTL**
- **Type**: `integer`
- **Default**: `3600`
- **Required**: `No`
- **Range**: `60-86400`
- **Unit**: `seconds`
- **Description**: Default cache entry time-to-live (1 hour)
- **Example**: `CACHE_DEFAULT_TTL=7200`

#### **CACHE_CONVERSATION_TTL**
- **Type**: `integer`
- **Default**: `604800`
- **Required**: `No`
- **Range**: `3600-2592000`
- **Unit**: `seconds`
- **Description**: Conversation cache TTL (1 week)
- **Example**: `CACHE_CONVERSATION_TTL=1209600`

#### **CACHE_TEAM_INFO_TTL**
- **Type**: `integer`
- **Default**: `86400`
- **Required**: `No`
- **Range**: `3600-604800`
- **Unit**: `seconds`
- **Description**: Team information cache TTL (1 day)
- **Example**: `CACHE_TEAM_INFO_TTL=172800`

#### **CACHE_MAX_MEMORY_POLICY**
- **Type**: `string`
- **Default**: `"allkeys-lru"`
- **Required**: `No`
- **Valid Values**: `"noeviction"`, `"allkeys-lru"`, `"volatile-lru"`, `"allkeys-random"`, `"volatile-random"`, `"volatile-ttl"`
- **Description**: Redis memory eviction policy
- **Example**: `CACHE_MAX_MEMORY_POLICY=volatile-ttl`

---

## External APIs Configuration

### Jira Integration

#### **JIRA_BASE_URL**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Format**: `https://domain.atlassian.net`
- **Description**: Jira instance base URL
- **Example**: `JIRA_BASE_URL=https://mycompany.atlassian.net`

#### **JIRA_USERNAME**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Description**: Jira username/email for authentication
- **Example**: `JIRA_USERNAME=user@example.com`

#### **JIRA_API_TOKEN**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Security**: `Sensitive`
- **Description**: Jira API token for authentication
- **Example**: `JIRA_API_TOKEN=ATATT3xFf...`

### GitHub Integration

#### **GITHUB_TOKEN**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Security**: `Sensitive`
- **Description**: GitHub personal access token
- **Example**: `GITHUB_TOKEN=ghp_xxxxxxxxxxxx`

#### **GITHUB_ORG**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Description**: Default GitHub organization
- **Example**: `GITHUB_ORG=mycompany`

### Slack Integration

#### **SLACK_BOT_TOKEN**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Security**: `Sensitive`
- **Format**: `xoxb-*`
- **Description**: Slack bot token for API access
- **Example**: `SLACK_BOT_TOKEN=xoxb-1234567890-1234567890-abcdefghijk`

#### **SLACK_WEBHOOK_URL**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Security**: `Sensitive`
- **Description**: Slack webhook URL for notifications
- **Example**: `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXX`

#### **SLACK_SIGNING_SECRET**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Security**: `Sensitive`
- **Description**: Slack signing secret for webhook verification
- **Example**: `SLACK_SIGNING_SECRET=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6`

### Microsoft Teams Integration

#### **TEAMS_WEBHOOK_URL**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Security**: `Sensitive`
- **Description**: Microsoft Teams webhook URL
- **Example**: `TEAMS_WEBHOOK_URL=https://outlook.office.com/webhook/...`

---

## LLM Provider Configuration

### OpenAI Settings

#### **OPENAI_API_KEY**
- **Type**: `string`
- **Default**: `null`
- **Required**: `Yes`
- **Security**: `Sensitive`
- **Description**: OpenAI API key for GPT models
- **Example**: `OPENAI_API_KEY=sk-...`

#### **OPENAI_API_TIMEOUT**
- **Type**: `integer`
- **Default**: `15000`
- **Required**: `No`
- **Range**: `5000-60000`
- **Unit**: `milliseconds`
- **Description**: OpenAI API request timeout
- **Example**: `OPENAI_API_TIMEOUT=30000`

### Anthropic Settings

#### **ANTHROPIC_API_KEY**
- **Type**: `string`
- **Default**: `null`
- **Required**: `Yes`
- **Security**: `Sensitive`
- **Description**: Anthropic API key for Claude models
- **Example**: `ANTHROPIC_API_KEY=sk-ant-...`

#### **ANTHROPIC_API_TIMEOUT**
- **Type**: `integer`
- **Default**: `15000`
- **Required**: `No`
- **Range**: `5000-60000`
- **Unit**: `milliseconds`
- **Description**: Anthropic API request timeout
- **Example**: `ANTHROPIC_API_TIMEOUT=30000`

### LLM Request Settings

#### **LLM_REQUEST_RETRY_COUNT**
- **Type**: `integer`
- **Default**: `3`
- **Required**: `No`
- **Range**: `1-10`
- **Description**: Number of retries for failed LLM requests
- **Example**: `LLM_REQUEST_RETRY_COUNT=5`

#### **LLM_REQUEST_RETRY_DELAY**
- **Type**: `integer`
- **Default**: `3000`
- **Required**: `No`
- **Range**: `1000-30000`
- **Unit**: `milliseconds`
- **Description**: Delay between LLM request retries
- **Example**: `LLM_REQUEST_RETRY_DELAY=5000`

---

## Messaging Platform Configuration

### WhatsApp Configuration

#### **WHATSAPP_ACCESS_TOKEN**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Security**: `Sensitive`
- **Description**: WhatsApp Business API access token
- **Example**: `WHATSAPP_ACCESS_TOKEN=EAAxxxxxxxx`

#### **WHATSAPP_WEBHOOK_VERIFY_TOKEN**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Security**: `Sensitive`
- **Description**: WhatsApp webhook verification token
- **Example**: `WHATSAPP_WEBHOOK_VERIFY_TOKEN=my_verify_token`

#### **WHATSAPP_PHONE_NUMBER_ID**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Description**: WhatsApp phone number ID
- **Example**: `WHATSAPP_PHONE_NUMBER_ID=1234567890123`

### Telegram Configuration

#### **TELEGRAM_BOT_TOKEN**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Security**: `Sensitive`
- **Format**: `[0-9]+:[a-zA-Z0-9_-]+`
- **Description**: Telegram bot token from BotFather
- **Example**: `TELEGRAM_BOT_TOKEN=123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw`

#### **TELEGRAM_WEBHOOK_URL**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Description**: Telegram webhook URL for receiving updates
- **Example**: `TELEGRAM_WEBHOOK_URL=https://api.mycompany.com/telegram/webhook`

### Kafka Configuration

#### **KAFKA_BOOTSTRAP_SERVERS**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Format**: `host1:port1,host2:port2,...`
- **Description**: Kafka broker addresses
- **Example**: `KAFKA_BOOTSTRAP_SERVERS=kafka1:9092,kafka2:9092,kafka3:9092`

---

## Debate System Configuration

### Orchestrator Settings

#### **DEBATE_PERFORMANCE_THRESHOLD_MS**
- **Type**: `integer`
- **Default**: `2000`
- **Required**: `No`
- **Range**: `500-10000`
- **Unit**: `milliseconds`
- **Description**: Performance threshold for debate orchestration
- **Example**: `DEBATE_PERFORMANCE_THRESHOLD_MS=3000`

#### **DEBATE_MAX_CONCURRENT**
- **Type**: `integer`
- **Default**: `100`
- **Required**: `No`
- **Range**: `1-1000`
- **Description**: Maximum concurrent debate sessions
- **Example**: `DEBATE_MAX_CONCURRENT=200`

#### **DEBATE_CACHE_TTL_SECONDS**
- **Type**: `integer`
- **Default**: `600`
- **Required**: `No`
- **Range**: `60-3600`
- **Unit**: `seconds`
- **Description**: Debate result cache TTL (10 minutes)
- **Example**: `DEBATE_CACHE_TTL_SECONDS=1200`

#### **RESOURCE_POOL_SIZE**
- **Type**: `integer`
- **Default**: `20`
- **Required**: `No`
- **Range**: `5-100`
- **Description**: Resource pool size for debate processing
- **Example**: `RESOURCE_POOL_SIZE=40`

#### **CIRCUIT_BREAKER_THRESHOLD**
- **Type**: `integer`
- **Default**: `10`
- **Required**: `No`
- **Range**: `5-50`
- **Description**: Circuit breaker failure threshold
- **Example**: `CIRCUIT_BREAKER_THRESHOLD=15`

### Performance Optimizer Settings

#### **PERFORMANCE_MONITORING_INTERVAL**
- **Type**: `integer`
- **Default**: `5`
- **Required**: `No`
- **Range**: `1-60`
- **Unit**: `seconds`
- **Description**: Performance monitoring interval
- **Example**: `PERFORMANCE_MONITORING_INTERVAL=10`

#### **OPTIMIZATION_THRESHOLD**
- **Type**: `float`
- **Default**: `0.9`
- **Required**: `No`
- **Range**: `0.5-1.0`
- **Description**: Threshold for triggering optimization
- **Example**: `OPTIMIZATION_THRESHOLD=0.85`

#### **PREDICTION_MODEL_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable ML prediction model for optimization
- **Example**: `PREDICTION_MODEL_ENABLED=false`

#### **AUTO_SCALING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable automatic scaling based on load
- **Example**: `AUTO_SCALING_ENABLED=false`

#### **METRIC_RETENTION_HOURS**
- **Type**: `integer`
- **Default**: `24`
- **Required**: `No`
- **Range**: `1-168`
- **Unit**: `hours`
- **Description**: How long to retain performance metrics
- **Example**: `METRIC_RETENTION_HOURS=48`

#### **OPTIMIZATION_COOLDOWN_MINUTES**
- **Type**: `integer`
- **Default**: `5`
- **Required**: `No`
- **Range**: `1-60`
- **Unit**: `minutes`
- **Description**: Cooldown period between optimizations
- **Example**: `OPTIMIZATION_COOLDOWN_MINUTES=10`

### Trigger Service Settings

#### **TRIGGER_CONFIDENCE_THRESHOLD**
- **Type**: `float`
- **Default**: `0.8`
- **Required**: `No`
- **Range**: `0.1-1.0`
- **Description**: Minimum confidence to trigger debate
- **Example**: `TRIGGER_CONFIDENCE_THRESHOLD=0.75`

#### **COMPLEXITY_ANALYSIS_TIMEOUT**
- **Type**: `integer`
- **Default**: `3000`
- **Required**: `No`
- **Range**: `1000-30000`
- **Unit**: `milliseconds`
- **Description**: Timeout for complexity analysis
- **Example**: `COMPLEXITY_ANALYSIS_TIMEOUT=5000`

#### **BATCH_PROCESSING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable batch processing for triggers
- **Example**: `BATCH_PROCESSING_ENABLED=false`

#### **PREDICTIVE_TRIGGERING**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable predictive debate triggering
- **Example**: `PREDICTIVE_TRIGGERING=false`

#### **DISAGREEMENT_SENSITIVITY**
- **Type**: `float`
- **Default**: `0.6`
- **Required**: `No`
- **Range**: `0.1-1.0`
- **Description**: Sensitivity for detecting disagreements
- **Example**: `DISAGREEMENT_SENSITIVITY=0.7`

#### **DECISION_POINT_DETECTION**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable decision point detection
- **Example**: `DECISION_POINT_DETECTION=false`

#### **TRIGGER_COOLDOWN_SECONDS**
- **Type**: `integer`
- **Default**: `30`
- **Required**: `No`
- **Range**: `5-300`
- **Unit**: `seconds`
- **Description**: Cooldown period between triggers
- **Example**: `TRIGGER_COOLDOWN_SECONDS=60`

---

## Context Detection Configuration

### Emotional Detection Settings

#### **EMOTIONAL_PROCESSING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable emotional state detection
- **Example**: `EMOTIONAL_PROCESSING_ENABLED=false`

#### **CONTEXT_AWARENESS_LEVEL**
- **Type**: `string`
- **Default**: `"standard"`
- **Required**: `No`
- **Valid Values**: `"minimal"`, `"standard"`, `"enhanced"`, `"comprehensive"`
- **Description**: Level of context awareness processing
- **Example**: `CONTEXT_AWARENESS_LEVEL=enhanced`

#### **PROACTIVE_PROCESSING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable proactive context processing
- **Example**: `PROACTIVE_PROCESSING_ENABLED=false`

#### **TEMPORAL_DECAY_FACTOR**
- **Type**: `float`
- **Default**: `0.1`
- **Required**: `No`
- **Range**: `0.01-1.0`
- **Description**: Factor for temporal decay of context signals
- **Example**: `TEMPORAL_DECAY_FACTOR=0.2`

#### **CONTEXT_WINDOW_MINUTES**
- **Type**: `integer`
- **Default**: `60`
- **Required**: `No`
- **Range**: `5-1440`
- **Unit**: `minutes`
- **Description**: Time window for context analysis
- **Example**: `CONTEXT_WINDOW_MINUTES=120`

---

## Memory System Configuration

#### **MEMORY_INTEGRATION_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable adaptive memory system
- **Example**: `MEMORY_INTEGRATION_ENABLED=false`

#### **VECTOR_STORE_DIMENSION**
- **Type**: `integer`
- **Default**: `384`
- **Required**: `No`
- **Valid Values**: `128`, `256`, `384`, `512`, `768`, `1024`
- **Description**: Vector embedding dimensions
- **Example**: `VECTOR_STORE_DIMENSION=512`

#### **MEMORY_RETENTION_DAYS**
- **Type**: `integer`
- **Default**: `30`
- **Required**: `No`
- **Range**: `1-365`
- **Unit**: `days`
- **Description**: How long to retain memory entries
- **Example**: `MEMORY_RETENTION_DAYS=90`

#### **PATTERN_LEARNING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable pattern learning from interactions
- **Example**: `PATTERN_LEARNING_ENABLED=false`

---

## Monitoring Configuration

### Metrics Collection

#### **MONITORING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable system monitoring
- **Example**: `MONITORING_ENABLED=false`

#### **PROMETHEUS_METRICS_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable Prometheus metrics collection
- **Example**: `PROMETHEUS_METRICS_ENABLED=false`

#### **JAEGER_TRACING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable Jaeger distributed tracing
- **Example**: `JAEGER_TRACING_ENABLED=false`

#### **SENTRY_ERROR_TRACKING**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable Sentry error tracking
- **Example**: `SENTRY_ERROR_TRACKING=false`

#### **SENTRY_DSN**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Security**: `Sensitive`
- **Description**: Sentry DSN for error reporting
- **Example**: `SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0`

### Health Check Settings

#### **HEALTH_CHECK_INTERVAL**
- **Type**: `integer`
- **Default**: `10`
- **Required**: `No`
- **Range**: `5-300`
- **Unit**: `seconds`
- **Description**: Health check interval
- **Example**: `HEALTH_CHECK_INTERVAL=30`

#### **GRACEFUL_SHUTDOWN_TIMEOUT**
- **Type**: `integer`
- **Default**: `30`
- **Required**: `No`
- **Range**: `10-300`
- **Unit**: `seconds`
- **Description**: Graceful shutdown timeout
- **Example**: `GRACEFUL_SHUTDOWN_TIMEOUT=60`

---

## Security Configuration

### Rate Limiting

#### **RATE_LIMITING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable API rate limiting
- **Example**: `RATE_LIMITING_ENABLED=false`

#### **RATE_LIMIT_PER_MINUTE**
- **Type**: `integer`
- **Default**: `100`
- **Required**: `No`
- **Range**: `10-10000`
- **Description**: Requests per minute per client
- **Example**: `RATE_LIMIT_PER_MINUTE=500`

### Authentication Settings

#### **API_KEY_VALIDATION_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable API key validation
- **Example**: `API_KEY_VALIDATION_ENABLED=false`

#### **JWT_SECRET_KEY**
- **Type**: `string`
- **Default**: `null`
- **Required**: `No`
- **Security**: `Sensitive`
- **Description**: Secret key for JWT token generation
- **Example**: `JWT_SECRET_KEY=your-secret-key-here`

#### **JWT_ALGORITHM**
- **Type**: `string`
- **Default**: `"HS256"`
- **Required**: `No`
- **Valid Values**: `"HS256"`, `"HS384"`, `"HS512"`, `"RS256"`
- **Description**: JWT signing algorithm
- **Example**: `JWT_ALGORITHM=RS256`

#### **JWT_EXPIRATION_HOURS**
- **Type**: `integer`
- **Default**: `24`
- **Required**: `No`
- **Range**: `1-720`
- **Unit**: `hours`
- **Description**: JWT token expiration time
- **Example**: `JWT_EXPIRATION_HOURS=168`

### Request Security

#### **REQUEST_LOGGING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable request/response logging
- **Example**: `REQUEST_LOGGING_ENABLED=false`

#### **SECURITY_HEADERS_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable security headers in responses
- **Example**: `SECURITY_HEADERS_ENABLED=false`

#### **CORS_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable Cross-Origin Resource Sharing
- **Example**: `CORS_ENABLED=false`

#### **CORS_ORIGINS**
- **Type**: `string`
- **Default**: `"*"`
- **Required**: `No`
- **Format**: `origin1,origin2,origin3`
- **Description**: Allowed CORS origins (comma-separated)
- **Example**: `CORS_ORIGINS=https://app.example.com,https://dashboard.example.com`

---

## Performance Configuration

### Optimization Settings

#### **EARLY_TERMINATION_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable early termination for optimizations
- **Example**: `EARLY_TERMINATION_ENABLED=false`

#### **OPTIMISTIC_PROCESSING**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable optimistic processing for better performance
- **Example**: `OPTIMISTIC_PROCESSING=false`

#### **PARALLEL_ROUNDS_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable parallel processing of debate rounds
- **Example**: `PARALLEL_ROUNDS_ENABLED=false`

#### **CONNECTION_POOLING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable connection pooling for better performance
- **Example**: `CONNECTION_POOLING_ENABLED=false`

#### **QUERY_CACHING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable query result caching
- **Example**: `QUERY_CACHING_ENABLED=false`

#### **RESULT_CACHING_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable result caching for API responses
- **Example**: `RESULT_CACHING_ENABLED=false`

#### **COMPRESSION_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable response compression
- **Example**: `COMPRESSION_ENABLED=false`

#### **CDN_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable CDN for static assets
- **Example**: `CDN_ENABLED=false`

---

## Feature Flags

### Core Features

#### **FEATURE_DEBATE_ANALYTICS**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable debate analytics and reporting
- **Example**: `FEATURE_DEBATE_ANALYTICS=false`

#### **FEATURE_PERFORMANCE_PREDICTION**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable ML-based performance prediction
- **Example**: `FEATURE_PERFORMANCE_PREDICTION=false`

#### **FEATURE_ADAPTIVE_THRESHOLDS**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable adaptive threshold adjustment
- **Example**: `FEATURE_ADAPTIVE_THRESHOLDS=false`

#### **FEATURE_ADVANCED_CACHING**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable advanced caching strategies
- **Example**: `FEATURE_ADVANCED_CACHING=false`

#### **FEATURE_REAL_TIME_MONITORING**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable real-time monitoring and alerts
- **Example**: `FEATURE_REAL_TIME_MONITORING=false`

### Integration Features

#### **DEBATE_INTEGRATION_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable debate system integration
- **Example**: `DEBATE_INTEGRATION_ENABLED=false`

#### **MESSAGE_HUB_INTEGRATION**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable message hub integration
- **Example**: `MESSAGE_HUB_INTEGRATION=false`

#### **DEVNOUS_CONTEXT_INTEGRATION**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable DevNous context integration
- **Example**: `DEVNOUS_CONTEXT_INTEGRATION=false`

#### **SAMCHAT_AGENT_INTEGRATION**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable SamChat agent integration
- **Example**: `SAMCHAT_AGENT_INTEGRATION=false`

### DevNous Orchestrator Features

#### **DEVNOUS_CONTEXT_MANAGER_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable DevNous context manager
- **Example**: `DEVNOUS_CONTEXT_MANAGER_ENABLED=false`

#### **DEVNOUS_MEMORY_SYSTEM_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable DevNous memory system
- **Example**: `DEVNOUS_MEMORY_SYSTEM_ENABLED=false`

#### **DEVNOUS_RESPONSE_GENERATION_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable DevNous response generation
- **Example**: `DEVNOUS_RESPONSE_GENERATION_ENABLED=false`

#### **DEVNOUS_TOOL_ORCHESTRATION_ENABLED**
- **Type**: `boolean`
- **Default**: `true`
- **Required**: `No`
- **Description**: Enable DevNous tool orchestration
- **Example**: `DEVNOUS_TOOL_ORCHESTRATION_ENABLED=false`

### Development/Testing Features

#### **MOCK_LLM_RESPONSES**
- **Type**: `boolean`
- **Default**: `false`
- **Required**: `No`
- **Description**: Use mock LLM responses for testing
- **Example**: `MOCK_LLM_RESPONSES=true`

#### **ENABLE_TEST_ENDPOINTS**
- **Type**: `boolean`
- **Default**: `false`
- **Required**: `No`
- **Description**: Enable test-only API endpoints
- **Example**: `ENABLE_TEST_ENDPOINTS=true`

#### **DEBATE_SIMULATION_MODE**
- **Type**: `boolean`
- **Default**: `false`
- **Required**: `No`
- **Description**: Enable debate simulation mode
- **Example**: `DEBATE_SIMULATION_MODE=true`

#### **PROFILING_ENABLED**
- **Type**: `boolean`
- **Default**: `false`
- **Required**: `No`
- **Description**: Enable performance profiling
- **Example**: `PROFILING_ENABLED=true`

#### **SYNTHETIC_LOAD_TESTING**
- **Type**: `boolean`
- **Default**: `false`
- **Required**: `No`
- **Description**: Enable synthetic load testing
- **Example**: `SYNTHETIC_LOAD_TESTING=true`

---

## Configuration Validation

### Validation Rules

The system validates configuration on startup with the following rules:

#### Required Dependencies
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` must be provided
- `POSTGRESQL_URL` is required for core functionality
- `REDIS_URL` is required for caching and pub/sub

#### Range Validation
- All numeric values are validated against their specified ranges
- Boolean values must be `true`, `false`, `1`, or `0`
- Enum values must match exactly (case-sensitive)

#### Format Validation
- URL fields must be valid HTTP/HTTPS URLs
- Database URLs must follow the correct connection string format
- Token formats are validated when possible

#### Cross-Validation
- Pool size settings are validated against system resources
- Cache TTL values are checked for logical consistency
- Feature flags dependencies are verified

### Configuration Loading Order

1. **Environment Variables**: Loaded first with highest priority
2. **`.env` Files**: Loaded from current directory
3. **Default Values**: Applied for missing configurations
4. **Validation**: All values validated before application start

### Environment-Specific Overrides

Configuration can be overridden per environment:

```bash
# Load environment-specific configuration
cp infrastructure/config/debate-system.production.env .env
```

---

## Environment-Specific Configurations

### Development Environment
```bash
# Basic development settings
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=DEBUG
MOCK_LLM_RESPONSES=false
ENABLE_TEST_ENDPOINTS=true

# Minimal external dependencies
DB_POOL_SIZE=5
REDIS_POOL_SIZE=10
CACHE_DEFAULT_TTL=300
```

### Staging Environment
```bash
# Staging settings
ENVIRONMENT=staging
DEBUG=false
LOG_LEVEL=INFO
MONITORING_ENABLED=true

# Production-like settings with reduced scale
DB_POOL_SIZE=15
REDIS_POOL_SIZE=20
DEBATE_MAX_CONCURRENT=50
RATE_LIMIT_PER_MINUTE=200
```

### Production Environment
```bash
# Production settings
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO
MOCK_LLM_RESPONSES=false
ENABLE_TEST_ENDPOINTS=false

# High-performance settings
DB_POOL_SIZE=50
REDIS_POOL_SIZE=30
DEBATE_MAX_CONCURRENT=100
RATE_LIMIT_PER_MINUTE=500
MONITORING_ENABLED=true
PERFORMANCE_ALERTS_ENABLED=true
```

---

## Configuration Templates

### Quick Start Template
```bash
# Minimal working configuration
ENVIRONMENT=development
LOG_LEVEL=INFO
POSTGRESQL_URL=postgresql://user:pass@localhost:5432/devnous
REDIS_URL=redis://localhost:6379/0
OPENAI_API_KEY=sk-your-key-here
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### Full Production Template
```bash
# Copy infrastructure/config/debate-system.production.env
# and customize for your deployment
source infrastructure/config/debate-system.production.env
```

---

## Troubleshooting Configuration Issues

### Common Issues

#### Database Connection Failures
- **Issue**: `database connection failed`
- **Check**: `POSTGRESQL_URL` format and credentials
- **Solution**: Verify database accessibility and authentication

#### Redis Connection Failures
- **Issue**: `redis connection failed`
- **Check**: `REDIS_URL` and Redis server status
- **Solution**: Ensure Redis is running and accessible

#### LLM API Failures
- **Issue**: `LLM API authentication failed`
- **Check**: `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` validity
- **Solution**: Verify API keys and quota limits

#### High Memory Usage
- **Issue**: Excessive memory consumption
- **Check**: Pool sizes and cache TTL settings
- **Solution**: Reduce `DB_POOL_SIZE`, `REDIS_POOL_SIZE`, or cache TTL values

### Configuration Debugging

Enable debug mode for detailed configuration logging:
```bash
DEBUG=true
LOG_LEVEL=DEBUG
```

Check configuration validation:
```bash
python -c "from devnous.config import config; print(config.dict())"
```

---

## See Also

- [Database Schema Reference](DATABASE_SCHEMA_REFERENCE.md)
- [API Quick Reference](API_QUICK_REFERENCE.md)
- [CLI Commands Reference](CLI_COMMANDS_REFERENCE.md)
- [Performance Benchmarks Reference](PERFORMANCE_BENCHMARKS_REFERENCE.md)
- [Security Configuration Reference](SECURITY_CONFIGURATION_REFERENCE.md)
- [Deployment Reference](DEPLOYMENT_REFERENCE.md)
