# DevNous Enterprise Messaging Implementation Guide

## Executive Summary

This implementation guide provides step-by-step instructions for deploying the comprehensive enterprise messaging architecture that integrates WhatsApp, Telegram, and Slack with DevNous. The architecture implements Enterprise Integration Patterns (EIP), supports millions of messages per day, and maintains 99.9% uptime guarantees while preserving context awareness and emotional intelligence.

Important:

- This guide documents a standalone DevNous messaging deployment shape.
- Database names, service names, and connection strings in this document are not the current production defaults for the live `sam.chat` deployment in this repository.
- For the active runtime/install split, see:
  - `docs/install_matrix.md`

## Architecture Overview

### High-Level System Design
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   WhatsApp      │    │    Telegram     │    │     Slack       │
│   Platform      │    │    Platform     │    │   Platform      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    API Gateway (Nginx)                         │
└─────────────────────────────────────────────────────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ WhatsApp Adapter│    │ Telegram Adapter│    │  Slack Adapter  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Apache Kafka (Event Stream)                 │
└─────────────────────────────────────────────────────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│Message Router   │    │Context Enricher │    │DevNous Core     │
│Service          │    │Service          │    │Service          │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│              Data Layer (PostgreSQL + Redis + ES)              │
└─────────────────────────────────────────────────────────────────┘
```

## Implementation Phases

### Phase 1: Infrastructure Foundation (Week 1-2)

#### Prerequisites
- Kubernetes cluster (1.28+)
- Docker registry access
- Cloud provider account (AWS/GCP/Azure)
- Domain name for API endpoints
- SSL certificates

#### Step 1.1: Deploy Core Infrastructure
```bash
# Create namespaces
kubectl apply -f infrastructure/kubernetes/namespace.yaml

# Deploy configuration maps
kubectl apply -f infrastructure/kubernetes/configmaps.yaml

# Create secrets (update with actual values)
kubectl create secret generic postgres-credentials \
  --from-literal=connection-string="postgresql://devnous:secure_password@postgres-primary:5432/devnous_messaging" \
  --namespace=devnous-messaging

kubectl create secret generic whatsapp-credentials \
  --from-literal=webhook-verify-token="your_whatsapp_verify_token" \
  --from-literal=access-token="your_whatsapp_access_token" \
  --namespace=devnous-messaging

kubectl create secret generic telegram-credentials \
  --from-literal=bot-token="your_telegram_bot_token" \
  --from-literal=webhook-secret="your_telegram_webhook_secret" \
  --namespace=devnous-messaging

kubectl create secret generic slack-credentials \
  --from-literal=bot-token="your_slack_bot_token" \
  --from-literal=signing-secret="your_slack_signing_secret" \
  --namespace=devnous-messaging

kubectl create secret generic openai-credentials \
  --from-literal=api-key="your_openai_api_key" \
  --namespace=devnous-messaging
```

#### Step 1.2: Deploy Data Layer
```bash
# Deploy PostgreSQL cluster with replication
kubectl apply -f infrastructure/kubernetes/statefulsets/postgresql.yaml

# Deploy Redis cluster
kubectl apply -f infrastructure/kubernetes/statefulsets/redis.yaml

# Deploy Elasticsearch cluster
kubectl apply -f infrastructure/kubernetes/statefulsets/elasticsearch.yaml

# Verify deployments
kubectl get pods -n devnous-messaging -l tier=database
```

#### Step 1.3: Initialize Database Schema
```bash
# Port forward to PostgreSQL primary
kubectl port-forward -n devnous-messaging svc/postgres-primary 5432:5432 &

# Run schema initialization
psql postgresql://devnous:secure_password@localhost:5432/devnous_messaging \
  -f enterprise_messaging_schema.sql

# Verify schema
psql postgresql://devnous:secure_password@localhost:5432/devnous_messaging \
  -c "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';"
```

### Phase 2: Message Broker and Event Streaming (Week 2-3)

#### Step 2.1: Deploy Kafka Cluster
```bash
# Deploy Kafka cluster (3 brokers for HA)
kubectl apply -f infrastructure/kubernetes/statefulsets/kafka.yaml

# Deploy Schema Registry
kubectl apply -f infrastructure/kubernetes/deployments/schema-registry.yaml

# Verify Kafka deployment
kubectl exec -n devnous-messaging kafka-0 -- kafka-topics --bootstrap-server kafka-cluster:9092 --list
```

#### Step 2.2: Create Kafka Topics
```bash
# Create topics from configuration
kubectl create configmap kafka-topics \
  --from-file=infrastructure/kafka-topics.json \
  --namespace=devnous-messaging

# Run topic creation job
kubectl apply -f infrastructure/kubernetes/jobs/create-kafka-topics.yaml

# Verify topics
kubectl exec -n devnous-messaging kafka-0 -- \
  kafka-topics --bootstrap-server kafka-cluster:9092 --list
```

#### Step 2.3: Deploy Message Schema
```bash
# Register message schemas
curl -X POST http://schema-registry:8081/subjects/universal-message-value/versions \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d @schemas/universal-message-schema.json

# Verify schema registration
curl http://schema-registry:8081/subjects/universal-message-value/versions/latest
```

### Phase 3: Core Microservices Deployment (Week 3-4)

#### Step 3.1: Deploy Platform Adapters
```bash
# Deploy WhatsApp, Telegram, and Slack adapters
kubectl apply -f infrastructure/kubernetes/deployments.yaml

# Deploy services
kubectl apply -f infrastructure/kubernetes/services.yaml

# Verify adapter deployments
kubectl get pods -n devnous-messaging -l tier=adapter
kubectl logs -n devnous-messaging -l app=whatsapp-adapter
```

#### Step 3.2: Deploy Core Services
```bash
# Deploy message router, context enricher, and DevNous core
kubectl apply -f infrastructure/kubernetes/deployments.yaml

# Verify core service deployments
kubectl get pods -n devnous-messaging -l tier=core
kubectl get svc -n devnous-messaging
```

#### Step 3.3: Deploy API Gateway
```bash
# Deploy Nginx API Gateway
kubectl apply -f infrastructure/kubernetes/deployments.yaml

# Configure ingress
kubectl apply -f infrastructure/kubernetes/ingress.yaml

# Verify gateway deployment
kubectl get ingress -n devnous-messaging
curl -k https://api.devnous.ai/health
```

### Phase 4: Security Implementation (Week 4-5)

#### Step 4.1: Deploy HashiCorp Vault
```bash
# Deploy Vault for secrets management
helm repo add hashicorp https://helm.releases.hashicorp.com
helm install vault hashicorp/vault \
  --namespace devnous-messaging \
  --set "server.dev.enabled=true"

# Initialize and unseal Vault
kubectl exec -n devnous-messaging vault-0 -- vault operator init
kubectl exec -n devnous-messaging vault-0 -- vault operator unseal <unseal_key>
```

#### Step 4.2: Configure Authentication
```bash
# Configure OAuth 2.0 provider
kubectl apply -f security/oauth2-proxy.yaml

# Set up JWT validation
kubectl apply -f security/jwt-validation-config.yaml

# Configure API key management
kubectl create secret generic api-keys \
  --from-file=security/api-keys.json \
  --namespace=devnous-messaging
```

#### Step 4.3: Implement Network Security
```bash
# Deploy network policies
kubectl apply -f security/network-policies.yaml

# Configure service mesh (Istio)
istioctl install --set values.defaultRevision=stable
kubectl label namespace devnous-messaging istio-injection=enabled

# Deploy security policies
kubectl apply -f security/istio-security-policies.yaml
```

### Phase 5: Monitoring and Observability (Week 5-6)

#### Step 5.1: Deploy Monitoring Stack
```bash
# Deploy Prometheus
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus prometheus-community/kube-prometheus-stack \
  --namespace devnous-monitoring \
  --values monitoring/prometheus-values.yaml

# Deploy Grafana dashboards
kubectl apply -f monitoring/grafana-dashboards.yaml

# Deploy Jaeger for distributed tracing
helm repo add jaegertracing https://jaegertracing.github.io/helm-charts
helm install jaeger jaegertracing/jaeger \
  --namespace devnous-monitoring
```

#### Step 5.2: Configure Alerting
```bash
# Deploy alerting rules
kubectl apply -f monitoring/alerting-rules.yaml

# Configure notification channels
kubectl create secret generic alertmanager-config \
  --from-file=monitoring/alertmanager.yml \
  --namespace devnous-monitoring

# Verify monitoring setup
kubectl port-forward -n devnous-monitoring svc/prometheus 9090:9090 &
kubectl port-forward -n devnous-monitoring svc/grafana 3000:80 &
```

#### Step 5.3: Set up Logging
```bash
# Deploy ELK stack
helm repo add elastic https://helm.elastic.co
helm install elasticsearch elastic/elasticsearch \
  --namespace devnous-monitoring \
  --values logging/elasticsearch-values.yaml

helm install kibana elastic/kibana \
  --namespace devnous-monitoring \
  --values logging/kibana-values.yaml

# Deploy log shippers
kubectl apply -f logging/fluent-bit.yaml
```

### Phase 6: Testing and Validation (Week 6-7)

#### Step 6.1: Integration Testing
```bash
# Run integration tests
kubectl apply -f tests/integration-test-job.yaml

# Monitor test results
kubectl logs -n devnous-messaging -l app=integration-tests -f

# Validate message flows
curl -X POST https://api.devnous.ai/api/v1/messaging/inbound \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d @tests/sample-message.json
```

#### Step 6.2: Load Testing
```bash
# Deploy load testing infrastructure
kubectl apply -f tests/load-test-deployment.yaml

# Run load tests with k6
kubectl create configmap load-test-script \
  --from-file=tests/load-test-script.js \
  --namespace devnous-messaging

kubectl apply -f tests/load-test-job.yaml

# Monitor performance during load testing
kubectl top pods -n devnous-messaging
kubectl get hpa -n devnous-messaging
```

#### Step 6.3: Disaster Recovery Testing
```bash
# Test database failover
kubectl exec -n devnous-messaging postgres-primary-0 -- pg_ctl stop

# Verify automatic failover
kubectl get pods -n devnous-messaging -l app=postgres

# Test service recovery
kubectl delete pod -n devnous-messaging -l app=message-router
kubectl get pods -n devnous-messaging -w
```

## Configuration Examples

### Platform-Specific Configurations

#### WhatsApp Business API Configuration
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: whatsapp-adapter-config
  namespace: devnous-messaging
data:
  config.json: |
    {
      "webhook": {
        "verify_token": "${WHATSAPP_WEBHOOK_VERIFY_TOKEN}",
        "port": 8000,
        "path": "/webhooks/whatsapp"
      },
      "api": {
        "base_url": "https://graph.facebook.com/v18.0",
        "timeout": 30000,
        "retry_attempts": 3
      },
      "kafka": {
        "topics": {
          "inbound": "platform.whatsapp.inbound",
          "outbound": "platform.outbound.formatted"
        }
      },
      "rate_limiting": {
        "messages_per_second": 10,
        "burst_capacity": 20
      }
    }
```

#### Telegram Bot API Configuration
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: telegram-adapter-config
  namespace: devnous-messaging
data:
  config.json: |
    {
      "webhook": {
        "url": "https://api.devnous.ai/webhooks/telegram",
        "secret_token": "${TELEGRAM_WEBHOOK_SECRET}",
        "max_connections": 40,
        "allowed_updates": ["message", "edited_message", "callback_query"]
      },
      "api": {
        "base_url": "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}",
        "timeout": 30000,
        "retry_attempts": 3
      },
      "kafka": {
        "topics": {
          "inbound": "platform.telegram.inbound",
          "outbound": "platform.outbound.formatted"
        }
      },
      "rate_limiting": {
        "messages_per_second": 30,
        "burst_capacity": 60
      }
    }
```

#### Slack App Configuration
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: slack-adapter-config
  namespace: devnous-messaging
data:
  config.json: |
    {
      "events": {
        "request_url": "https://api.devnous.ai/webhooks/slack",
        "subscriptions": ["message.channels", "message.groups", "message.im"],
        "signing_secret": "${SLACK_SIGNING_SECRET}"
      },
      "oauth": {
        "client_id": "${SLACK_CLIENT_ID}",
        "client_secret": "${SLACK_CLIENT_SECRET}",
        "scopes": ["chat:write", "channels:read", "groups:read", "im:read", "users:read"]
      },
      "kafka": {
        "topics": {
          "inbound": "platform.slack.inbound",
          "outbound": "platform.outbound.formatted"
        }
      },
      "rate_limiting": {
        "messages_per_second": 1,
        "tier_limits": {
          "tier1": 1,
          "tier2": 20,
          "tier3": 50
        }
      }
    }
```

## Performance Benchmarks

### Expected Performance Metrics
```yaml
Message Processing:
  Throughput: 10,000 messages/minute per service instance
  Latency: P95 < 500ms, P99 < 1s
  Error Rate: < 0.1%
  
Context Enrichment:
  Processing Time: P95 < 200ms
  Accuracy: > 95%
  Cache Hit Rate: > 80%
  
Database Performance:
  Query Response: P95 < 50ms
  Connection Pool: 85% utilization max
  Replication Lag: < 1 second
  
Cache Performance:
  Redis Response: P95 < 5ms
  Hit Rate: > 90%
  Memory Usage: < 80%
  
System Resources:
  CPU Utilization: 70% average, 90% max
  Memory Usage: 80% average, 95% max
  Network I/O: < 80% capacity
  Disk I/O: < 70% capacity
```

### Scaling Characteristics
```yaml
Horizontal Scaling:
  Platform Adapters: Linear scaling up to 10 instances
  Message Router: Linear scaling up to 20 instances
  Context Enricher: Linear scaling up to 15 instances
  DevNous Core: Scaling efficiency 85% up to 10 instances
  
Resource Requirements per Instance:
  WhatsApp Adapter: 0.5 CPU, 512MB RAM
  Telegram Adapter: 0.5 CPU, 512MB RAM
  Slack Adapter: 0.5 CPU, 512MB RAM
  Message Router: 1.0 CPU, 1GB RAM
  Context Enricher: 1.0 CPU, 1GB RAM
  DevNous Core: 2.0 CPU, 2GB RAM
```

## Troubleshooting Guide

### Common Issues and Solutions

#### Message Processing Delays
```yaml
Symptoms:
  - High P95/P99 latency
  - Kafka consumer lag increasing
  - Queue depth growing

Diagnosis:
  - Check service resource utilization
  - Monitor Kafka consumer lag
  - Analyze database query performance
  - Review network latency

Solutions:
  - Scale up processing services
  - Optimize database queries
  - Increase Kafka partitions
  - Tune consumer configurations
```

#### Context Enrichment Failures
```yaml
Symptoms:
  - Context enrichment accuracy dropping
  - DevNous API timeouts
  - Incomplete user profiles

Diagnosis:
  - Check DevNous service health
  - Monitor API response times
  - Review context cache hit rates
  - Analyze error logs

Solutions:
  - Scale DevNous core service
  - Optimize context queries
  - Increase cache size
  - Implement circuit breakers
```

#### Platform Integration Issues
```yaml
Symptoms:
  - Webhook delivery failures
  - API rate limit errors
  - Authentication failures

Diagnosis:
  - Check webhook endpoint health
  - Monitor API call rates
  - Verify credentials and tokens
  - Review platform-specific logs

Solutions:
  - Implement exponential backoff
  - Optimize API usage patterns
  - Refresh authentication tokens
  - Configure proper rate limiting
```

## Maintenance Procedures

### Regular Maintenance Tasks
```yaml
Daily:
  - Monitor system health dashboards
  - Review error rates and alerts
  - Check resource utilization
  - Validate backup completion

Weekly:
  - Analyze performance trends
  - Review capacity planning
  - Update security patches
  - Clean up old log data

Monthly:
  - Update platform API versions
  - Review and rotate secrets
  - Analyze cost optimization
  - Update documentation

Quarterly:
  - Conduct disaster recovery testing
  - Review security assessments
  - Update capacity planning models
  - Evaluate new technology adoption
```

This comprehensive implementation guide provides all the necessary steps, configurations, and best practices to successfully deploy and operate the DevNous enterprise messaging architecture. The system is designed to handle millions of messages per day while maintaining high availability, security, and operational excellence.
