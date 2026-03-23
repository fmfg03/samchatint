# SamChat/DevNous - Production Deployment and Operations Reference Guide

Important:

- This operations guide mixes general SamChat/DevNous operational guidance with standalone DevNous-era examples.
- It should not be treated as the sole production source of truth for the live `sam.chat` deployment in this repository.
- For the active runtime/install split, see:
  - `docs/install_matrix.md`

## Table of Contents
1. [Infrastructure Requirements](#1-infrastructure-requirements)
2. [Environment Setup](#2-environment-setup)
3. [Docker and Kubernetes Deployment](#3-docker-and-kubernetes-deployment)
4. [Database Configuration](#4-database-configuration)
5. [Security Configuration](#5-security-configuration)
6. [Monitoring and Observability](#6-monitoring-and-observability)
7. [Backup and Disaster Recovery](#7-backup-and-disaster-recovery)
8. [Performance Tuning](#8-performance-tuning)
9. [Troubleshooting](#9-troubleshooting)
10. [Maintenance and Updates](#10-maintenance-and-updates)

## 1. Infrastructure Requirements

### 1.1 Minimum Hardware Specifications
- **CPU**: 16 vCPUs (x86_64 architecture)
- **RAM**: 64 GB 
- **Storage**: 
  - Minimum 500 GB SSD (preferably NVMe)
  - Recommended 1 TB for production workloads
- **Network**: 
  - 1 Gbps network interface
  - Static IP recommended for stable service

### 1.2 Software Prerequisites
- **Operating System**:
  - Linux (Ubuntu 22.04 LTS or Rocky Linux 9)
  - RHEL/CentOS 8+ compatible
- **Container Runtime**:
  - Docker 20.10+ 
  - Kubernetes 1.24+ (recommended 1.26+)
- **Database**:
  - PostgreSQL 14+ 
  - Redis 6.2+
- **Messaging**:
  - Apache Kafka 3.0+
- **Monitoring**:
  - Prometheus 2.30+
  - Grafana 8.3+

### 1.3 Network Requirements
- Open ports:
  ```
  80/TCP   - HTTP
  443/TCP  - HTTPS
  5432/TCP - PostgreSQL
  6379/TCP - Redis
  9092/TCP - Kafka
  9090/TCP - Prometheus
  3000/TCP - Grafana
  ```

## 2. Environment Setup

### 2.1 Environment Variables
Create a `.env` file with the following critical configurations:

```bash
# API Configuration
OPENAI_API_KEY=sk-your_openai_key
ANTHROPIC_API_KEY=your_anthropic_key

# Database Connection
DB_HOST=postgres.samchat.internal
DB_PORT=5432
DB_NAME=samchat_production
DB_USER=samchat_app
DB_PASSWORD=secure_password_here

# Redis Configuration
REDIS_HOST=redis.samchat.internal
REDIS_PORT=6379
REDIS_PASSWORD=secure_redis_password

# Kafka Configuration
KAFKA_BOOTSTRAP_SERVERS=kafka1.samchat.internal:9092,kafka2.samchat.internal:9092
KAFKA_GROUP_ID=samchat-consumer-group

# Security
JWT_SECRET=long_random_secret_key
ENCRYPTION_KEY=secure_encryption_key

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=JSON

# Performance
MAX_WORKERS=16
WORKER_TIMEOUT=300
```

### 2.2 Dependency Installation
```bash
# Install system dependencies
sudo apt-get update && sudo apt-get install -y \
    python3.10 \
    python3-pip \
    docker.io \
    kubectl \
    postgresql-client

# Install Python dependencies
pip install -r requirements.txt
```

## 3. Docker and Kubernetes Deployment

### 3.1 Dockerfile Template
```dockerfile
FROM python:3.10-slim-bullseye

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment to production
ENV PYTHONUNBUFFERED=1
ENV APP_ENV=production

# Expose application port
EXPOSE 8000

# Run the application
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "samchat.main:app"]
```

### 3.2 Kubernetes Deployment (samchat-deployment.yaml)
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: samchat-deployment
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1
      maxSurge: 1
  selector:
    matchLabels:
      app: samchat
  template:
    metadata:
      labels:
        app: samchat
    spec:
      containers:
      - name: samchat
        image: samchat/devnous:v1.2.0
        ports:
        - containerPort: 8000
        resources:
          requests:
            cpu: 500m
            memory: 512Mi
          limits:
            cpu: 2
            memory: 2Gi
        env:
        - name: APP_ENV
          value: production
        - name: LOG_LEVEL
          value: INFO
        volumeMounts:
        - name: config
          mountPath: /app/config
      volumes:
      - name: config
        configMap:
          name: samchat-config
```

## 4. Database Configuration

### 4.1 PostgreSQL Optimization
```sql
-- Recommended PostgreSQL tuning
ALTER SYSTEM SET 
    max_connections = 200;
ALTER SYSTEM SET 
    shared_buffers = '16GB';
ALTER SYSTEM SET 
    effective_cache_size = '48GB';
ALTER SYSTEM SET 
    maintenance_work_mem = '2GB';
ALTER SYSTEM SET 
    checkpoint_completion_target = 0.9;
ALTER SYSTEM SET 
    wal_buffers = '16MB';
```

### 4.2 Database Migration
```bash
# Run database migrations
alembic upgrade head
```

## 5. Security Configuration

### 5.1 Security Checklist
- [ ] Use strong, unique passwords for all services
- [ ] Enable SSL/TLS for all connections
- [ ] Implement IP whitelisting
- [ ] Use hardware security modules (HSM) for key management
- [ ] Enable multi-factor authentication
- [ ] Regularly rotate API keys and secrets

### 5.2 SSL/TLS Configuration
```nginx
# Nginx SSL Configuration
server {
    listen 443 ssl http2;
    ssl_certificate /etc/letsencrypt/live/samchat.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/samchat.example.com/privkey.pem;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers EECDH+AESGCM:EDH+AESGCM;
    ssl_ecdh_curve secp384r1;
    ssl_session_cache shared:SSL:10m;
    ssl_session_tickets off;
    ssl_stapling on;
    ssl_stapling_verify on;
}
```

## 6. Monitoring and Observability

### 6.1 Prometheus Metrics Configuration
```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'samchat'
    static_configs:
      - targets: 
        - 'samchat-service:8000'
        - 'postgres-exporter:9187'
        - 'redis-exporter:9121'
```

### 6.2 Grafana Dashboard
Create dashboards tracking:
- Request latency
- Error rates
- CPU/Memory usage
- Database connection pool
- Kafka message processing
- LLM API call metrics

## 7. Backup and Disaster Recovery

### 7.1 Backup Strategy
```bash
#!/bin/bash
# Backup script (backup.sh)
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="/var/backups/samchat"

# PostgreSQL Backup
pg_dump -h $DB_HOST -U $DB_USER $DB_NAME | gzip > $BACKUP_DIR/db_backup_$TIMESTAMP.sql.gz

# Redis Backup
redis-cli SAVE
cp /var/lib/redis/dump.rdb $BACKUP_DIR/redis_backup_$TIMESTAMP.rdb

# Configuration Backup
tar -czvf $BACKUP_DIR/config_backup_$TIMESTAMP.tar.gz /app/samchat/config
```

### 7.2 Disaster Recovery Procedure
1. Validate last known good backup
2. Restore PostgreSQL database
3. Restore Redis data
4. Redeploy Kubernetes services
5. Validate system health

## 8. Performance Tuning

### 8.1 Scaling Recommendations
- Horizontal Pod Autoscaler (HPA) configuration
```yaml
apiVersion: autoscaling/v2beta1
kind: HorizontalPodAutoscaler
metadata:
  name: samchat-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: samchat-deployment
  minReplicas: 3
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      targetAverageUtilization: 70
```

## 9. Troubleshooting

### 9.1 Common Troubleshooting Commands
```bash
# Check service status
kubectl get pods
kubectl describe pod samchat-deployment

# View logs
kubectl logs samchat-deployment-xyz
docker logs samchat-container

# Database connection test
pg_isready -h $DB_HOST -p $DB_PORT -U $DB_USER

# Redis connectivity
redis-cli ping

# Kafka topic health
kafka-topics.sh --bootstrap-server $KAFKA_SERVERS --describe
```

## 10. Maintenance and Updates

### 10.1 Update Workflow
1. Pull latest Docker image
2. Run database migrations
3. Deploy to staging
4. Run comprehensive test suite
5. Perform blue/green deployment
6. Monitor for 1 hour
7. Rollback if issues detected

```bash
# Update process
docker pull samchat/devnous:latest
kubectl rollout restart deployment samchat-deployment
```

### 10.2 Version Management
- Maintain semantic versioning
- Use immutable infrastructure principles
- Implement canary deployments
- Maintain detailed changelog

## Appendix A: Recommended Reading
- [12-Factor App Methodology](https://12factor.net/)
- [Kubernetes Best Practices](https://kubernetes.io/docs/concepts/cluster-administration/best-practices/)
- [PostgreSQL Performance Tuning](https://www.postgresql.org/docs/current/runtime-config.html)

## Appendix B: Support and Contact
**Support Email**: devops@samchat.com
**Incident Response**: +1 (888) SAMCHAT-OPS

---

**Note**: This reference guide is a living document. Always consult the latest version and adapt to your specific infrastructure requirements.
