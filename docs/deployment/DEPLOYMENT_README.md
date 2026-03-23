# DevNous Debate System - Production Deployment Guide

This document provides comprehensive guidance for deploying the SamChat/DevNous smart debate system in production environments.

Important:

- This guide describes a standalone DevNous debate deployment shape.
- Service names, database names, and commands in this document are not the current production bootstrap for the live `sam.chat` deployment in this repository.
- For the active runtime/install split, see:
  - `docs/install_matrix.md`

## 🏗️ Architecture Overview

The debate system consists of three core microservices integrated with the existing SamChat infrastructure:

- **Debate Orchestrator**: Central coordinator for debate sessions and consensus building
- **Performance Optimizer**: Real-time performance monitoring and optimization engine
- **Trigger Service**: Intelligent conversation analysis and debate triggering system

## 📋 Prerequisites

### Required Software
- Docker 20.10+
- Kubernetes 1.24+
- Helm 3.8+
- kubectl configured with cluster access
- PostgreSQL 15+ (for database)
- Redis 7+ (for caching)
- Prometheus + Grafana (for monitoring)

### Resource Requirements

| Component | CPU | Memory | Storage | Replicas |
|-----------|-----|--------|---------|----------|
| Debate Orchestrator | 2 cores | 4 GB | 10 GB | 3-10 |
| Performance Optimizer | 1 core | 2 GB | 5 GB | 2-5 |
| Trigger Service | 1 core | 1.5 GB | 5 GB | 2-6 |

### Environment Variables

Required secrets and configurations:
- `OPENAI_API_KEY`: OpenAI API key for LLM integration
- `ANTHROPIC_API_KEY`: Anthropic Claude API key
- Database connection strings
- Redis connection details
- Service mesh configuration

## 🚀 Quick Start Deployment

### Option 1: Docker Compose (Development/Testing)

```bash
# Start the complete system with debate components
cd infrastructure/
docker-compose -f docker-compose.yml -f docker-compose.production.yml up -d

# View services
docker-compose ps

# Monitor logs
docker-compose logs -f debate-orchestrator
```

### Option 2: Kubernetes (Production)

```bash
# Deploy using the provided script
./scripts/deploy-debate-system.sh production devnous-messaging

# Manual deployment steps
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/configmaps.yaml
kubectl apply -f k8s/persistent-volumes.yaml
kubectl apply -f k8s/deployments.yaml
kubectl apply -f k8s/services.yaml
kubectl apply -f k8s/hpa.yaml
```

## 🔧 Configuration Management

### Environment-Specific Configuration

The system supports three deployment environments with optimized settings:

#### Development (`debate-system.dev.env`)
- Lower performance thresholds for testing
- Debug logging enabled
- Reduced resource requirements
- Mock endpoints available

#### Staging (`debate-system.staging.env`)
- Production-like performance settings
- Load testing capabilities
- Performance monitoring enabled
- Synthetic traffic generation

#### Production (`debate-system.production.env`)
- Optimized for sub-3-second response times
- Maximum concurrency (100+ debates)
- Full monitoring and alerting
- High availability configuration

### Key Configuration Parameters

```bash
# Performance Tuning
DEBATE_PERFORMANCE_THRESHOLD_MS=2000    # Target response time
DEBATE_MAX_CONCURRENT=100               # Maximum concurrent debates
DEBATE_CACHE_TTL_SECONDS=600           # Cache expiration time

# Trigger System
TRIGGER_CONFIDENCE_THRESHOLD=0.8        # Minimum confidence to trigger
COMPLEXITY_ANALYSIS_TIMEOUT=3000       # Analysis timeout in ms
PREDICTIVE_TRIGGERING=true             # Enable predictive analysis

# Database Optimization
DATABASE_POOL_SIZE=50                   # Connection pool size
DATABASE_STATEMENT_TIMEOUT=20000       # Query timeout in ms
DATABASE_QUERY_LOG_ENABLED=false       # Disable in production

# Security
RATE_LIMITING_ENABLED=true              # Enable rate limiting
RATE_LIMIT_PER_MINUTE=500              # Requests per minute limit
API_KEY_VALIDATION_ENABLED=true        # Validate API keys
```

## 💾 Database Setup

### Schema Migration

The system includes automated database migrations:

```bash
# Run migrations manually
psql -h postgres-host -U devnous -d devnous_messaging -f infrastructure/migrations/001_debate_schema_initialization.sql
psql -h postgres-host -U devnous -d devnous_messaging -f infrastructure/migrations/002_debate_performance_optimization.sql

# Migrations are automatically applied during deployment
```

### Database Optimization

Production database settings for optimal performance:

```sql
-- PostgreSQL configuration optimizations
shared_buffers = 1GB
effective_cache_size = 4GB
work_mem = 16MB
maintenance_work_mem = 256MB
wal_buffers = 16MB
checkpoint_completion_target = 0.9
random_page_cost = 1.1
```

## 📊 Monitoring and Observability

### Prometheus Metrics

The debate system exposes comprehensive metrics:

```yaml
# Key metrics to monitor
debate_orchestrator_response_time_seconds    # Response time histogram
debate_orchestrator_active_sessions         # Current active debates
debate_orchestrator_success_rate            # Success rate percentage
debate_cache_hit_rate                       # Cache efficiency
debate_trigger_accuracy                     # Trigger precision
```

### Grafana Dashboards

Pre-configured dashboards available:
- **Debate System Overview**: Key performance indicators
- **Performance Analysis**: Response times and throughput
- **Resource Utilization**: CPU, memory, and storage metrics
- **Error Tracking**: Error rates and failure analysis

### Alert Configuration

Critical alerts configured in Prometheus:

```yaml
# High-priority alerts
- DebateResponseTimeHigh (>3 seconds)
- DebateOrchestratorDown
- HighDebateConcurrency (>80 sessions)
- DebateSuccessRateLow (<95%)
- DebateCacheHitRateLow (<80%)
```

## 🔄 CI/CD Integration

### GitHub Actions Pipeline

The system integrates with the existing CI/CD pipeline:

```yaml
# Extended pipeline includes:
- Debate system unit tests
- Performance benchmarking
- Container security scanning
- Automated deployment to staging/production
- Canary deployments with automatic rollback
```

### Deployment Strategy

**Production deployment process:**
1. Code changes trigger automated tests
2. Build and security scan container images
3. Deploy to staging environment
4. Run integration and performance tests
5. Deploy to production with 10% canary traffic
6. Validate performance and gradually increase traffic
7. Complete rollout or automatic rollback on issues

## 🔒 Security Considerations

### API Security
- JWT-based authentication
- Rate limiting per client
- Request/response logging
- Input validation and sanitization

### Network Security
- TLS encryption for all communications
- Network policies restricting pod-to-pod traffic
- Secrets management with Kubernetes secrets
- Regular security scanning of container images

### Data Protection
- Encryption at rest for debate data
- PII anonymization in logs
- Compliance with data retention policies
- Regular security audits

## 📈 Performance Optimization

### Response Time Targets
- **Fast debates** (simple consensus): < 1 second
- **Standard debates** (typical scenarios): < 3 seconds
- **Complex debates** (high complexity): < 10 seconds

### Scaling Strategy

**Horizontal Pod Autoscaler (HPA) Configuration:**
```yaml
# Debate Orchestrator scaling
minReplicas: 3
maxReplicas: 10
targetCPUUtilizationPercentage: 70
targetMemoryUtilizationPercentage: 80

# Custom metrics scaling
- debate_active_sessions (target: 5 per pod)
- debate_queue_size (target: 10 per pod)
```

### Cache Strategy
- **Conversation Analysis Cache**: 5-minute TTL
- **Agent Response Cache**: 10-minute TTL
- **Consensus Results Cache**: 1-hour TTL
- **Performance Data Cache**: 24-hour TTL

## 🛠️ Troubleshooting

### Common Issues

#### High Response Times
```bash
# Check debate orchestrator performance
kubectl logs -f deployment/debate-orchestrator -n devnous-messaging

# Monitor resource usage
kubectl top pods -n devnous-messaging

# Check database connections
kubectl exec -it deployment/debate-orchestrator -n devnous-messaging -- psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity;"
```

#### Failed Debates
```bash
# Check trigger service logs
kubectl logs -f deployment/debate-trigger-service -n devnous-messaging

# Verify LLM API connectivity
kubectl exec -it deployment/debate-trigger-service -n devnous-messaging -- curl -I https://api.openai.com/v1/models
```

#### Cache Issues
```bash
# Check Redis connectivity
kubectl exec -it deployment/debate-orchestrator -n devnous-messaging -- redis-cli -h redis-primary ping

# Monitor cache hit rates
curl http://debate-orchestrator:8000/metrics | grep cache_hit_rate
```

### Health Checks

All services provide comprehensive health endpoints:

```bash
# Service health
curl http://debate-orchestrator:8000/health
curl http://debate-performance-optimizer:8000/health
curl http://debate-trigger-service:8000/health

# Readiness checks
curl http://debate-orchestrator:8000/ready
```

## 🔄 Rollback Procedures

### Automated Rollback
```bash
# Rollback using the deployment script
./scripts/deploy-debate-system.sh --rollback

# Manual rollback
kubectl rollout undo deployment/debate-orchestrator -n devnous-messaging
kubectl rollout undo deployment/debate-performance-optimizer -n devnous-messaging
kubectl rollout undo deployment/debate-trigger-service -n devnous-messaging
```

### Database Rollback
```bash
# Restore from backup (implement according to your backup strategy)
pg_restore -h postgres-host -U devnous -d devnous_messaging debate_backup_$(date +%Y%m%d).dump
```

## 🎯 Performance Tuning Guide

### Environment-Specific Optimizations

#### Production Tuning
```bash
# Increase connection pools
DATABASE_POOL_SIZE=50
REDIS_POOL_SIZE=30

# Optimize timeouts
DEBATE_PERFORMANCE_THRESHOLD_MS=2000
LLM_API_TIMEOUT=15000

# Enable advanced features
PREDICTIVE_TRIGGERING=true
AUTO_SCALING_ENABLED=true
```

#### Resource Allocation
```yaml
# Production resource requests/limits
debate-orchestrator:
  requests: { cpu: 2000m, memory: 2Gi }
  limits: { cpu: 4000m, memory: 4Gi }

debate-performance-optimizer:
  requests: { cpu: 500m, memory: 1Gi }
  limits: { cpu: 2000m, memory: 2Gi }
```

## 📞 Support and Maintenance

### Regular Maintenance Tasks
1. **Weekly**: Review performance metrics and optimize thresholds
2. **Monthly**: Clean up old debate session data and logs
3. **Quarterly**: Update dependencies and security patches
4. **Annually**: Comprehensive security audit and disaster recovery testing

### Monitoring Checklist
- [ ] All services are healthy and responsive
- [ ] Response times meet SLA targets (< 3 seconds)
- [ ] Cache hit rates are above 80%
- [ ] Database performance is optimal
- [ ] No critical alerts in monitoring systems
- [ ] Resource utilization is within acceptable ranges

## 📚 Additional Resources

- [API Documentation](./API_DOCUMENTATION.md)
- [Architecture Deep Dive](./SMART_DEBATE_ARCHITECTURE.md)
- [Performance Engineering](./PERFORMANCE_ENGINEERING_CRITIQUE.md)
- [Database Schema](./DEBATE_DATABASE_README.md)
- [Monitoring Guide](./infrastructure/monitoring/README.md)

---

**Important Notes:**
- Always test deployments in staging before production
- Monitor system performance closely after deployments
- Keep backups of configuration and database before major updates
- Follow security best practices for API keys and secrets management
- Document any custom modifications for future reference
