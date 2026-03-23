# Security Configuration Reference

**Version**: 1.0.0  
**Last Updated**: 2024-01-15  
**Target**: Security Engineers, DevOps Engineers, System Administrators

## Overview

This document provides comprehensive security configuration guidance for the SamChat/DevNous system. Covers authentication, authorization, encryption, network security, and compliance requirements.

Important:

- Domains, paths, and contacts using `devnous.example.com` in this document are standalone security examples.
- They should not be read as the current production hostnames or filesystem layout for the live `sam.chat` deployment in this repository.
- For the active runtime/install split, see:
  - `docs/install_matrix.md`

## Table of Contents

- [Security Architecture](#security-architecture)
- [Authentication Configuration](#authentication-configuration)
- [Authorization and Access Control](#authorization-and-access-control)
- [API Security](#api-security)
- [Data Encryption](#data-encryption)
- [Network Security](#network-security)
- [Container and Kubernetes Security](#container-and-kubernetes-security)
- [Database Security](#database-security)
- [External Integration Security](#external-integration-security)
- [Monitoring and Audit Logging](#monitoring-and-audit-logging)
- [Compliance and Privacy](#compliance-and-privacy)
- [Security Incident Response](#security-incident-response)
- [Security Hardening Checklist](#security-hardening-checklist)
- [Vulnerability Management](#vulnerability-management)

---

## Security Architecture

### Defense in Depth Strategy

```yaml
Layer 1 - Network Security:
  - WAF (Web Application Firewall)
  - DDoS protection
  - Network segmentation
  - VPC/VNET isolation

Layer 2 - Infrastructure Security:
  - Container security scanning
  - Host-based firewalls
  - Intrusion detection systems
  - Security monitoring

Layer 3 - Application Security:
  - API authentication/authorization
  - Input validation and sanitization
  - OWASP Top 10 mitigations
  - Secure coding practices

Layer 4 - Data Security:
  - Encryption at rest and in transit
  - Data classification
  - Access controls
  - Data retention policies
```

### Security Principles

```yaml
Principle of Least Privilege:
  - Minimal required permissions
  - Role-based access control
  - Just-in-time access
  - Regular permission reviews

Zero Trust Architecture:
  - Never trust, always verify
  - Microsegmentation
  - Continuous monitoring
  - Identity-centric security

Security by Design:
  - Threat modeling
  - Secure defaults
  - Privacy by design
  - Security testing in CI/CD
```

### Threat Model

```yaml
Assets to Protect:
  - User data and conversations
  - Team information and context
  - LLM API keys and credentials
  - Debate session data
  - System configuration

Threat Actors:
  - External attackers
  - Malicious insiders
  - Compromised accounts
  - Nation-state actors
  - Competitors

Attack Vectors:
  - API vulnerabilities
  - Authentication bypass
  - Data injection attacks
  - Social engineering
  - Supply chain attacks
```

---

## Authentication Configuration

### API Key Authentication

#### API Key Management
```yaml
Key Generation:
  - Cryptographically secure random generation
  - Minimum 32 characters length
  - Alphanumeric with special characters
  - Unique per user/application

Key Format:
  - Prefix: "dvn_" (for identification)
  - Environment indicator: "prod_", "dev_", "test_"
  - Random component: 32+ characters
  - Example: "dvn_prod_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"

Storage:
  - Hashed with bcrypt (cost factor 12)
  - Salted with unique salt per key
  - Never stored in plaintext
  - Regular rotation policy
```

#### Configuration Example
```bash
# Environment variables
API_KEY_HASH_COST=12
API_KEY_ROTATION_DAYS=90
API_KEY_MAX_INACTIVE_DAYS=30

# Database schema
CREATE TABLE security.api_keys (
    key_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES core.users(user_id),
    key_hash VARCHAR(255) NOT NULL UNIQUE,
    key_prefix VARCHAR(20) NOT NULL,
    name VARCHAR(255) NOT NULL,
    permissions JSONB DEFAULT '{}',
    rate_limit_per_hour INTEGER DEFAULT 1000,
    last_used TIMESTAMP WITH TIME ZONE,
    usage_count BIGINT DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### JWT Authentication

#### JWT Configuration
```yaml
Algorithm: RS256 (RSA SHA-256)
Key Size: 2048 bits minimum
Token Expiration: 15 minutes (access token)
Refresh Token Expiration: 7 days
Issuer: "devnous.api"
Audience: "devnous.client"

Claims Structure:
  iss: "devnous.api"
  aud: "devnous.client"
  sub: "user_id"
  exp: expiration_timestamp
  iat: issued_at_timestamp
  jti: unique_token_id
  scope: ["api:read", "api:write", "debate:trigger"]
  org_id: "organization_id"
  team_ids: ["team1", "team2"]
```

#### JWT Environment Configuration
```bash
# RSA key pair for JWT signing
JWT_PRIVATE_KEY_PATH=/secrets/jwt_private_key.pem
JWT_PUBLIC_KEY_PATH=/secrets/jwt_public_key.pem
JWT_ALGORITHM=RS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
JWT_ISSUER=devnous.api
JWT_AUDIENCE=devnous.client

# Key rotation
JWT_KEY_ROTATION_ENABLED=true
JWT_KEY_ROTATION_DAYS=90
JWT_ALLOW_MULTIPLE_KEYS=true
```

#### RSA Key Generation
```bash
# Generate private key
openssl genpkey -algorithm RSA -out jwt_private_key.pem -pkcs8 -aes-256-cbc

# Extract public key
openssl rsa -pubout -in jwt_private_key.pem -out jwt_public_key.pem

# Store securely in Kubernetes secrets
kubectl create secret generic jwt-keys \
  --from-file=private=jwt_private_key.pem \
  --from-file=public=jwt_public_key.pem \
  -n devnous-messaging
```

### OAuth 2.0 Integration

#### External Provider Configuration
```yaml
# Slack OAuth
SLACK_CLIENT_ID=your_slack_client_id
SLACK_CLIENT_SECRET=your_slack_client_secret
SLACK_OAUTH_SCOPES=channels:read,chat:write,users:read

# Microsoft OAuth (Teams)
MICROSOFT_CLIENT_ID=your_microsoft_client_id
MICROSOFT_CLIENT_SECRET=your_microsoft_client_secret
MICROSOFT_TENANT_ID=your_tenant_id
MICROSOFT_OAUTH_SCOPES=https://graph.microsoft.com/Chat.ReadWrite

# Google OAuth
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_OAUTH_SCOPES=https://www.googleapis.com/auth/userinfo.profile
```

### Multi-Factor Authentication (MFA)

#### TOTP Configuration
```yaml
MFA Settings:
  - Algorithm: SHA-1 (TOTP standard)
  - Period: 30 seconds
  - Digits: 6
  - Window: 1 (allow previous/next period)
  - Backup codes: 10 single-use codes

Implementation:
  - QR code generation for authenticator apps
  - Backup code generation and storage
  - MFA enforcement policies per organization
  - Grace period for MFA enrollment
```

#### Environment Configuration
```bash
# MFA settings
MFA_ENABLED=true
MFA_TOTP_ISSUER=DevNous
MFA_BACKUP_CODE_COUNT=10
MFA_GRACE_PERIOD_DAYS=7
MFA_REQUIRED_FOR_ADMIN=true

# Storage encryption for MFA secrets
MFA_SECRET_ENCRYPTION_KEY=base64_encoded_key
MFA_BACKUP_CODE_HASH_ROUNDS=12
```

---

## Authorization and Access Control

### Role-Based Access Control (RBAC)

#### Role Definitions
```yaml
System Roles:
  super_admin:
    description: "Full system access"
    permissions: ["*"]
    
  org_admin:
    description: "Organization administration"
    permissions:
      - "org:manage"
      - "users:manage"
      - "teams:manage"
      - "billing:view"
      
  team_lead:
    description: "Team leadership"
    permissions:
      - "team:manage"
      - "debates:trigger"
      - "workflows:manage"
      - "reports:view"
      
  developer:
    description: "Development team member"
    permissions:
      - "tasks:create"
      - "tasks:update"
      - "debates:participate"
      - "chat:process"
      
  viewer:
    description: "Read-only access"
    permissions:
      - "tasks:view"
      - "reports:view"
      - "debates:view"
```

#### Permission Matrix
```yaml
Resource-Based Permissions:
  api:
    - api:read
    - api:write
    - api:admin
    
  debates:
    - debates:trigger
    - debates:view
    - debates:participate
    - debates:admin
    
  tasks:
    - tasks:create
    - tasks:view
    - tasks:update
    - tasks:delete
    - tasks:assign
    
  teams:
    - teams:create
    - teams:view
    - teams:update
    - teams:delete
    - teams:manage_members
    
  contexts:
    - contexts:view
    - contexts:configure
    - contexts:analyze
```

#### Database Schema for RBAC
```sql
-- Roles table
CREATE TABLE security.roles (
    role_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_name VARCHAR(50) NOT NULL UNIQUE,
    description TEXT,
    permissions JSONB NOT NULL DEFAULT '[]',
    is_system_role BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- User role assignments
CREATE TABLE security.user_roles (
    user_id UUID NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    role_id UUID NOT NULL REFERENCES security.roles(role_id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES core.organizations(org_id) ON DELETE CASCADE,
    assigned_by UUID REFERENCES core.users(user_id),
    assigned_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,
    
    PRIMARY KEY (user_id, role_id, org_id)
);

-- Team-specific permissions
CREATE TABLE security.team_permissions (
    user_id UUID NOT NULL REFERENCES core.users(user_id) ON DELETE CASCADE,
    team_id UUID NOT NULL REFERENCES core.teams(team_id) ON DELETE CASCADE,
    permissions JSONB NOT NULL DEFAULT '[]',
    granted_by UUID REFERENCES core.users(user_id),
    granted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    PRIMARY KEY (user_id, team_id)
);
```

### Attribute-Based Access Control (ABAC)

#### Policy Configuration
```yaml
Policy Rules:
  - name: "team_member_access"
    description: "Team members can access their team resources"
    condition: "user.team_ids CONTAINS resource.team_id"
    effect: "allow"
    
  - name: "org_admin_access" 
    description: "Org admins can access all org resources"
    condition: "user.org_role == 'admin' AND user.org_id == resource.org_id"
    effect: "allow"
    
  - name: "sensitive_data_access"
    description: "Restrict sensitive data access"
    condition: "resource.sensitivity == 'high' AND user.clearance_level >= 'high'"
    effect: "allow"
    
  - name: "time_based_access"
    description: "Restrict access to business hours"
    condition: "current_time BETWEEN '09:00' AND '17:00' AND user.timezone"
    effect: "allow"
```

#### Implementation Example
```python
class ABACPolicy:
    def evaluate_access(self, user_context, resource_context, action):
        """Evaluate ABAC policy for access decision"""
        policies = self.load_applicable_policies(resource_context, action)
        
        for policy in policies:
            if self.evaluate_condition(policy.condition, user_context, resource_context):
                if policy.effect == "deny":
                    return False
                elif policy.effect == "allow":
                    return True
                    
        return False  # Default deny
```

---

## API Security

### Rate Limiting

#### Rate Limit Configuration
```yaml
Global Rate Limits:
  - 1000 requests per hour per API key
  - 60 requests per minute per IP address
  - 10 requests per second per endpoint per user

Endpoint-Specific Limits:
  authentication:
    login: 5 attempts per 15 minutes per IP
    password_reset: 3 attempts per hour per email
    
  debates:
    trigger: 10 debates per hour per team
    status: 300 requests per minute per session
    
  chat:
    process: 60 messages per minute per user
    send: 100 messages per minute per channel

LLM Provider Limits:
  openai: 3500 requests per minute
  anthropic: 1000 requests per minute
```

#### Implementation with Redis
```python
import redis
import time
from typing import Optional

class RateLimiter:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
    
    def check_rate_limit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """Check if rate limit is exceeded"""
        current = time.time()
        pipeline = self.redis.pipeline()
        
        # Remove expired entries
        pipeline.zremrangebyscore(key, 0, current - window)
        
        # Count current requests
        pipeline.zcard(key)
        
        # Add current request
        pipeline.zadd(key, {str(current): current})
        
        # Set expiry
        pipeline.expire(key, window)
        
        results = pipeline.execute()
        current_count = results[1]
        
        return current_count < limit, limit - current_count
```

### Input Validation and Sanitization

#### Validation Rules
```yaml
String Validation:
  - Maximum length limits per field
  - Character set restrictions
  - SQL injection prevention
  - XSS prevention (HTML encoding)
  - Command injection prevention

Numeric Validation:
  - Range validation
  - Type validation (int, float)
  - Precision limits
  - Overflow protection

JSON Validation:
  - Schema validation with JSON Schema
  - Depth limits (max 10 levels)
  - Size limits (max 1MB)
  - Key validation

File Upload Validation:
  - File type validation (whitelist)
  - Size limits (max 10MB)
  - Malware scanning
  - Content validation
```

#### Input Sanitization Implementation
```python
import bleach
import html
from pydantic import BaseModel, validator, Field

class MessageInput(BaseModel):
    content: str = Field(..., max_length=10000)
    channel: str = Field(..., regex=r'^[a-zA-Z0-9_-]+$')
    metadata: dict = Field(default_factory=dict)
    
    @validator('content')
    def sanitize_content(cls, v):
        # Remove potentially dangerous HTML/script tags
        cleaned = bleach.clean(v, tags=[], attributes={}, strip=True)
        # HTML encode to prevent XSS
        return html.escape(cleaned)
    
    @validator('metadata')
    def validate_metadata(cls, v):
        # Limit metadata size and depth
        if len(str(v)) > 1000:
            raise ValueError("Metadata too large")
        return v
```

### CORS Configuration

#### CORS Settings
```yaml
Allowed Origins:
  development:
    - http://localhost:3000
    - http://localhost:8080
    - http://127.0.0.1:3000
    
  staging:
    - https://staging-app.devnous.example.com
    - https://staging-dashboard.devnous.example.com
    
  production:
    - https://app.devnous.example.com
    - https://dashboard.devnous.example.com

Allowed Methods:
  - GET
  - POST
  - PUT
  - DELETE
  - OPTIONS

Allowed Headers:
  - Content-Type
  - Authorization
  - X-API-Key
  - X-Request-ID

Max Age: 86400  # 24 hours
```

#### FastAPI CORS Configuration
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Request-ID"],
    max_age=86400,
)
```

### Security Headers

#### Required Security Headers
```yaml
Strict-Transport-Security: 
  value: "max-age=31536000; includeSubDomains; preload"
  description: "Force HTTPS connections"

Content-Security-Policy:
  value: "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
  description: "Prevent XSS attacks"

X-Content-Type-Options:
  value: "nosniff"
  description: "Prevent MIME type sniffing"

X-Frame-Options:
  value: "DENY"
  description: "Prevent clickjacking"

X-XSS-Protection:
  value: "1; mode=block"
  description: "Enable XSS filtering"

Referrer-Policy:
  value: "strict-origin-when-cross-origin"
  description: "Control referrer information"

Permissions-Policy:
  value: "geolocation=(), microphone=(), camera=()"
  description: "Disable unnecessary features"
```

#### Implementation
```python
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        return response
```

---

## Data Encryption

### Encryption at Rest

#### Database Encryption
```yaml
PostgreSQL TDE (Transparent Data Encryption):
  - Algorithm: AES-256
  - Key management: AWS KMS / Azure Key Vault / Google Cloud KMS
  - Tablespace encryption enabled
  - WAL encryption enabled

Column-Level Encryption:
  - Sensitive fields encrypted with application keys
  - PII data encrypted separately
  - Encryption key rotation every 90 days

Configuration:
  - ssl = on
  - ssl_ciphers = 'HIGH:!aNULL:!eNULL:!EXPORT:!DES:!MD5:!PSK:!SRP'
  - ssl_prefer_server_ciphers = on
  - ssl_cert_file = '/path/to/server.crt'
  - ssl_key_file = '/path/to/server.key'
```

#### File System Encryption
```bash
# LUKS encryption for data volumes
cryptsetup luksFormat /dev/sdb
cryptsetup luksOpen /dev/sdb encrypted_data
mkfs.ext4 /dev/mapper/encrypted_data

# Mount with encryption
mount /dev/mapper/encrypted_data /var/lib/devnous/data

# Automatic mounting in /etc/crypttab
encrypted_data /dev/sdb none luks,discard
```

#### Application-Level Encryption
```python
import os
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

class DataEncryption:
    def __init__(self, master_key: bytes):
        self.fernet = Fernet(master_key)
    
    def encrypt_sensitive_data(self, data: str) -> str:
        """Encrypt sensitive data for storage"""
        return self.fernet.encrypt(data.encode()).decode()
    
    def decrypt_sensitive_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data from storage"""
        return self.fernet.decrypt(encrypted_data.encode()).decode()
    
    @staticmethod
    def derive_key(password: bytes, salt: bytes) -> bytes:
        """Derive encryption key from password"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        return base64.urlsafe_b64encode(kdf.derive(password))
```

### Encryption in Transit

#### TLS Configuration
```yaml
TLS Version: TLS 1.2 minimum, TLS 1.3 preferred
Cipher Suites:
  - TLS_AES_256_GCM_SHA384 (TLS 1.3)
  - TLS_CHACHA20_POLY1305_SHA256 (TLS 1.3)
  - TLS_AES_128_GCM_SHA256 (TLS 1.3)
  - ECDHE-RSA-AES256-GCM-SHA384 (TLS 1.2)
  - ECDHE-RSA-CHACHA20-POLY1305 (TLS 1.2)

Certificate Requirements:
  - RSA 2048-bit minimum (4096-bit preferred)
  - ECDSA P-256 or P-384 curves
  - SHA-256 signature algorithm
  - Certificate transparency logging
  - OCSP stapling enabled
```

#### Nginx TLS Configuration
```nginx
server {
    listen 443 ssl http2;
    server_name api.devnous.example.com;
    
    # SSL Configuration
    ssl_certificate /etc/ssl/certs/api.devnous.example.com.crt;
    ssl_certificate_key /etc/ssl/private/api.devnous.example.com.key;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:50m;
    ssl_session_tickets off;
    
    # Modern configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    
    # HSTS
    add_header Strict-Transport-Security "max-age=63072000" always;
    
    # OCSP stapling
    ssl_stapling on;
    ssl_stapling_verify on;
    
    location / {
        proxy_pass http://backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Key Management

#### Key Rotation Policy
```yaml
Encryption Keys:
  - Database encryption: 1 year rotation
  - Application secrets: 90 days rotation
  - API keys: 90 days rotation
  - JWT signing keys: 180 days rotation
  - TLS certificates: 1 year rotation

Key Storage:
  - Production: AWS KMS / Azure Key Vault / Google Cloud KMS
  - Staging: HashiCorp Vault
  - Development: Local key management (encrypted)

Key Backup:
  - Encrypted backups in separate regions
  - Secure key escrow for critical keys
  - Key recovery procedures documented
  - Access logging for all key operations
```

#### Key Management Implementation
```python
import boto3
from botocore.exceptions import ClientError

class KeyManager:
    def __init__(self, kms_key_id: str):
        self.kms_client = boto3.client('kms')
        self.key_id = kms_key_id
    
    def create_data_key(self) -> tuple[bytes, bytes]:
        """Create a new data encryption key"""
        try:
            response = self.kms_client.generate_data_key(
                KeyId=self.key_id,
                KeySpec='AES_256'
            )
            return response['Plaintext'], response['CiphertextBlob']
        except ClientError as e:
            raise Exception(f"Failed to create data key: {e}")
    
    def decrypt_data_key(self, encrypted_key: bytes) -> bytes:
        """Decrypt a data encryption key"""
        try:
            response = self.kms_client.decrypt(CiphertextBlob=encrypted_key)
            return response['Plaintext']
        except ClientError as e:
            raise Exception(f"Failed to decrypt data key: {e}")
```

---

## Network Security

### Firewall Configuration

#### Network Segmentation
```yaml
Network Zones:
  dmz:
    description: "Demilitarized zone for public-facing services"
    allowed_inbound: [80, 443]
    allowed_outbound: [443, 53, 123]
    
  application:
    description: "Application tier"
    allowed_inbound: [8000, 8001, 8002]
    allowed_outbound: [443, 5432, 6379]
    
  data:
    description: "Database tier"
    allowed_inbound: [5432, 6379]
    allowed_outbound: [53, 123]
    
  management:
    description: "Management and monitoring"
    allowed_inbound: [22, 9090, 3000]
    allowed_outbound: [443, 53, 123]
```

#### iptables Rules Example
```bash
#!/bin/bash
# Basic firewall configuration

# Flush existing rules
iptables -F
iptables -X
iptables -t nat -F
iptables -t nat -X

# Set default policies
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT ACCEPT

# Allow loopback traffic
iptables -A INPUT -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT

# Allow established and related connections
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Allow SSH from management network
iptables -A INPUT -p tcp --dport 22 -s 10.0.1.0/24 -j ACCEPT

# Allow HTTP/HTTPS
iptables -A INPUT -p tcp --dport 80 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT

# Allow application ports from application network
iptables -A INPUT -p tcp --dport 8000:8002 -s 10.0.2.0/24 -j ACCEPT

# Allow database ports from application network only
iptables -A INPUT -p tcp --dport 5432 -s 10.0.2.0/24 -j ACCEPT
iptables -A INPUT -p tcp --dport 6379 -s 10.0.2.0/24 -j ACCEPT

# Log and drop everything else
iptables -A INPUT -j LOG --log-prefix "IPTables-Dropped: " --log-level 4
iptables -A INPUT -j DROP
```

### VPC/VNET Security

#### AWS VPC Configuration
```yaml
VPC Configuration:
  cidr_block: "10.0.0.0/16"
  
  subnets:
    public:
      - cidr: "10.0.1.0/24"
        az: "us-west-2a"
        purpose: "Load balancers, NAT gateways"
      - cidr: "10.0.2.0/24"
        az: "us-west-2b"
        purpose: "Load balancers, NAT gateways"
    
    private:
      - cidr: "10.0.10.0/24"
        az: "us-west-2a" 
        purpose: "Application servers"
      - cidr: "10.0.11.0/24"
        az: "us-west-2b"
        purpose: "Application servers"
    
    database:
      - cidr: "10.0.20.0/24"
        az: "us-west-2a"
        purpose: "Database servers"
      - cidr: "10.0.21.0/24"
        az: "us-west-2b"
        purpose: "Database servers"

Security Groups:
  web-tier:
    ingress:
      - port: 80
        source: "0.0.0.0/0"
      - port: 443
        source: "0.0.0.0/0"
    egress:
      - port: 8000-8002
        destination: "app-tier"
  
  app-tier:
    ingress:
      - port: 8000-8002
        source: "web-tier"
    egress:
      - port: 5432
        destination: "db-tier"
      - port: 6379
        destination: "cache-tier"
      - port: 443
        destination: "0.0.0.0/0"
  
  db-tier:
    ingress:
      - port: 5432
        source: "app-tier"
      - port: 6379
        source: "app-tier"
    egress: []
```

### WAF Configuration

#### CloudFlare WAF Rules
```yaml
Rate Limiting Rules:
  - name: "API Rate Limit"
    threshold: 100 requests per minute
    action: "challenge"
    match: "uri.path matches '/api/'"
    
  - name: "Auth Rate Limit"
    threshold: 5 requests per minute
    action: "block"
    match: "uri.path eq '/api/v1/auth/login'"

Security Rules:
  - name: "SQL Injection Protection"
    action: "block"
    expression: "http.request.body contains 'UNION SELECT'"
    
  - name: "XSS Protection" 
    action: "block"
    expression: "http.request.uri.query contains '<script>'"
    
  - name: "Malicious User-Agent"
    action: "block"
    expression: 'http.user_agent contains "sqlmap"'
    
Geographic Rules:
  - name: "Allow Specific Countries"
    action: "allow"
    countries: ["US", "CA", "GB", "DE", "FR"]
    
  - name: "Block High-Risk Countries"
    action: "block"  
    countries: ["CN", "RU", "KP"]
```

---

## Container and Kubernetes Security

### Container Security

#### Dockerfile Security Best Practices
```dockerfile
# Use minimal base image
FROM python:3.11-slim as builder

# Create non-root user
RUN groupadd -r devnous && useradd -r -g devnous devnous

# Install dependencies as builder
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Production image
FROM python:3.11-slim

# Copy user and dependencies from builder
COPY --from=builder /etc/passwd /etc/passwd
COPY --from=builder /root/.local /home/devnous/.local

# Set up application
WORKDIR /app
COPY . .
RUN chown -R devnous:devnous /app

# Switch to non-root user
USER devnous

# Set security options
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run application
CMD ["python", "-m", "uvicorn", "devnous.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### Container Security Scanning
```yaml
# .github/workflows/security-scan.yml
name: Security Scan

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  container-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Build Docker image
        run: docker build -t devnous/api:${{ github.sha }} .
      
      - name: Run Trivy vulnerability scanner
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: 'devnous/api:${{ github.sha }}'
          format: 'sarif'
          output: 'trivy-results.sarif'
          
      - name: Upload Trivy scan results
        uses: github/codeql-action/upload-sarif@v2
        with:
          sarif_file: 'trivy-results.sarif'
```

### Kubernetes Security

#### Pod Security Standards
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: devnous-messaging
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
```

#### Security Context Configuration
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: devnous-orchestrator
spec:
  template:
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
          
      containers:
      - name: orchestrator
        image: devnous/orchestrator:latest
        securityContext:
          allowPrivilegeEscalation: false
          readOnlyRootFilesystem: true
          runAsNonRoot: true
          runAsUser: 1000
          capabilities:
            drop:
            - ALL
        volumeMounts:
        - name: tmp-volume
          mountPath: /tmp
        - name: cache-volume
          mountPath: /app/cache
          
      volumes:
      - name: tmp-volume
        emptyDir: {}
      - name: cache-volume
        emptyDir: {}
```

#### Network Policies
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: devnous-network-policy
  namespace: devnous-messaging
spec:
  podSelector:
    matchLabels:
      app: devnous-orchestrator
  policyTypes:
  - Ingress
  - Egress
  
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: ingress-system
    ports:
    - protocol: TCP
      port: 8000
      
  - from:
    - podSelector:
        matchLabels:
          app: devnous-processor
    ports:
    - protocol: TCP
      port: 8000
      
  egress:
  - to:
    - namespaceSelector:
        matchLabels:
          name: database-system
    ports:
    - protocol: TCP
      port: 5432
      
  - to: []
    ports:
    - protocol: TCP
      port: 443  # HTTPS outbound
    - protocol: TCP
      port: 53   # DNS
    - protocol: UDP
      port: 53   # DNS
```

#### RBAC Configuration
```yaml
# Service account
apiVersion: v1
kind: ServiceAccount
metadata:
  name: devnous-service-account
  namespace: devnous-messaging

---
# Role
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  namespace: devnous-messaging
  name: devnous-role
rules:
- apiGroups: [""]
  resources: ["pods", "configmaps", "secrets"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["apps"]
  resources: ["deployments"]
  verbs: ["get", "list", "watch"]

---
# Role binding
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: devnous-role-binding
  namespace: devnous-messaging
subjects:
- kind: ServiceAccount
  name: devnous-service-account
  namespace: devnous-messaging
roleRef:
  kind: Role
  name: devnous-role
  apiGroup: rbac.authorization.k8s.io
```

#### Secrets Management
```yaml
# Sealed Secrets for GitOps
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: devnous-secrets
  namespace: devnous-messaging
spec:
  encryptedData:
    database-url: AgBy3i4OJSWK+PiTySYZZA9rO43cGDEQAx...
    openai-api-key: AgBy3i4OJSWK+PiTySYZZA9rO43cGDEQAx...
    anthropic-api-key: AgBy3i4OJSWK+PiTySYZZA9rO43cGDEQAx...
  template:
    type: Opaque
```

---

## Database Security

### PostgreSQL Security Configuration

#### Connection Security
```postgresql
# postgresql.conf
ssl = on
ssl_cert_file = '/path/to/server.crt'
ssl_key_file = '/path/to/server.key'
ssl_ca_file = '/path/to/ca.crt'
ssl_crl_file = '/path/to/revocation.crl'

# Require SSL connections
ssl_prefer_server_ciphers = on
ssl_ciphers = 'HIGH:MEDIUM:+3DES:!aNULL:!eNULL:!EXPORT:!DES:!MD5:!PSK:!SRP'

# Connection restrictions
listen_addresses = '10.0.20.10,10.0.20.11'  # Private IPs only
max_connections = 100
password_encryption = 'scram-sha-256'
```

#### pg_hba.conf Configuration
```
# TYPE  DATABASE        USER            ADDRESS                 METHOD

# Local connections
local   all             postgres                                peer

# Application connections (require SSL and password)
hostssl devnous_prod    devnous_app     10.0.10.0/24           scram-sha-256
hostssl devnous_prod    devnous_readonly 10.0.10.0/24          scram-sha-256

# Replication connections
hostssl replication     replicator      10.0.20.0/24           scram-sha-256

# Deny all others
host    all             all             0.0.0.0/0               reject
```

#### User Roles and Permissions
```sql
-- Application user with limited permissions
CREATE ROLE devnous_app WITH LOGIN PASSWORD 'secure_password_here';

-- Read-only user for reporting
CREATE ROLE devnous_readonly WITH LOGIN PASSWORD 'secure_password_here';

-- Grant schema permissions
GRANT USAGE ON SCHEMA core, debate, context, memory TO devnous_app;
GRANT USAGE ON SCHEMA core, debate, context, memory TO devnous_readonly;

-- Grant table permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO devnous_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA debate TO devnous_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA context TO devnous_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA memory TO devnous_app;

-- Read-only permissions
GRANT SELECT ON ALL TABLES IN SCHEMA core TO devnous_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA debate TO devnous_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA context TO devnous_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA memory TO devnous_readonly;

-- Sequence permissions
GRANT USAGE ON ALL SEQUENCES IN SCHEMA core TO devnous_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA debate TO devnous_app;

-- Revoke dangerous permissions
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA information_schema FROM PUBLIC;
REVOKE ALL ON SCHEMA pg_catalog FROM PUBLIC;
```

### Database Security Monitoring

#### Audit Logging
```postgresql
-- Enable audit logging extension
CREATE EXTENSION IF NOT EXISTS pgaudit;

-- Configure audit settings
SET pgaudit.log = 'write, ddl, role';
SET pgaudit.log_catalog = off;
SET pgaudit.log_parameter = on;
SET pgaudit.log_relation = on;
SET pgaudit.log_statement_once = on;

-- Log configuration in postgresql.conf
log_statement = 'mod'  # Log all modifications
log_min_duration_statement = 1000  # Log slow queries (>1s)
log_connections = on
log_disconnections = on
log_checkpoints = on
log_line_prefix = '%t [%p-%l] %q%u@%d '
```

#### Security Views and Functions
```sql
-- View for monitoring failed login attempts
CREATE VIEW security.failed_logins AS
SELECT 
    log_time,
    user_name,
    database_name,
    remote_host,
    COUNT(*) as attempt_count
FROM pg_log
WHERE message LIKE '%authentication failed%'
GROUP BY log_time::date, user_name, database_name, remote_host
HAVING COUNT(*) > 5;

-- Function to check for suspicious activity
CREATE OR REPLACE FUNCTION security.check_suspicious_activity()
RETURNS TABLE (
    activity_type TEXT,
    details JSONB,
    risk_level TEXT
) AS $$
BEGIN
    -- Check for multiple failed logins
    RETURN QUERY
    SELECT 
        'multiple_failed_logins'::TEXT,
        jsonb_build_object(
            'user', user_name,
            'attempts', attempt_count,
            'sources', array_agg(remote_host)
        ),
        CASE 
            WHEN attempt_count > 20 THEN 'high'
            WHEN attempt_count > 10 THEN 'medium'
            ELSE 'low'
        END
    FROM security.failed_logins
    WHERE attempt_count > 5;
END;
$$ LANGUAGE plpgsql;
```

---

## External Integration Security

### LLM Provider Security

#### API Key Management
```yaml
OpenAI Security:
  api_key_rotation: 90 days
  usage_monitoring: enabled
  rate_limiting: provider_limits + 20% buffer
  request_logging: metadata_only  # No content logging
  
Anthropic Security:
  api_key_rotation: 90 days
  usage_monitoring: enabled
  rate_limiting: provider_limits + 15% buffer
  request_logging: metadata_only

Security Measures:
  - API keys stored in secure key management
  - Request/response content not logged
  - User data anonymization where possible
  - Circuit breakers for API failures
  - Fallback providers configured
```

#### Content Security
```python
class LLMSecurityFilter:
    def __init__(self):
        self.pii_patterns = [
            r'\b\d{3}-\d{2}-\d{4}\b',  # SSN
            r'\b\d{16}\b',              # Credit card
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'  # Email
        ]
    
    def sanitize_request(self, content: str) -> str:
        """Remove PII from LLM requests"""
        sanitized = content
        for pattern in self.pii_patterns:
            sanitized = re.sub(pattern, '[REDACTED]', sanitized)
        return sanitized
    
    def validate_response(self, response: str) -> bool:
        """Validate LLM response for harmful content"""
        harmful_indicators = [
            'provide personal information',
            'ignore previous instructions',
            'system prompt',
            'jailbreak'
        ]
        
        return not any(indicator in response.lower() for indicator in harmful_indicators)
```

### External API Security

#### Webhook Security
```python
import hmac
import hashlib
from fastapi import HTTPException, Request

class WebhookSecurity:
    def __init__(self, secret: str):
        self.secret = secret.encode()
    
    def verify_signature(self, request: Request, payload: bytes) -> bool:
        """Verify webhook signature"""
        signature = request.headers.get('X-Hub-Signature-256', '')
        if not signature.startswith('sha256='):
            return False
            
        expected = hmac.new(
            self.secret,
            payload,
            hashlib.sha256
        ).hexdigest()
        
        received = signature.split('sha256=')[1]
        return hmac.compare_digest(expected, received)
    
    def validate_timestamp(self, request: Request, tolerance: int = 300) -> bool:
        """Validate webhook timestamp to prevent replay attacks"""
        timestamp = request.headers.get('X-Timestamp')
        if not timestamp:
            return False
            
        try:
            webhook_time = int(timestamp)
            current_time = int(time.time())
            return abs(current_time - webhook_time) <= tolerance
        except (ValueError, TypeError):
            return False
```

### Third-Party Integration Security

#### OAuth Token Management
```python
class OAuthTokenManager:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.encryption = DataEncryption(os.getenv('OAUTH_ENCRYPTION_KEY'))
    
    def store_tokens(self, user_id: str, access_token: str, refresh_token: str, expires_in: int):
        """Securely store OAuth tokens"""
        encrypted_access = self.encryption.encrypt_sensitive_data(access_token)
        encrypted_refresh = self.encryption.encrypt_sensitive_data(refresh_token)
        
        token_data = {
            'access_token': encrypted_access,
            'refresh_token': encrypted_refresh,
            'expires_at': int(time.time()) + expires_in
        }
        
        self.redis.hset(f'oauth:{user_id}', mapping=token_data)
        self.redis.expire(f'oauth:{user_id}', expires_in + 3600)  # Grace period
    
    def get_valid_token(self, user_id: str) -> Optional[str]:
        """Get valid access token, refreshing if necessary"""
        token_data = self.redis.hgetall(f'oauth:{user_id}')
        if not token_data:
            return None
            
        expires_at = int(token_data.get('expires_at', 0))
        current_time = int(time.time())
        
        if current_time < expires_at - 300:  # Valid with 5min buffer
            return self.encryption.decrypt_sensitive_data(token_data['access_token'])
        else:
            # Attempt token refresh
            return self._refresh_token(user_id, token_data)
```

---

## Monitoring and Audit Logging

### Security Event Logging

#### Audit Log Configuration
```yaml
Events to Log:
  authentication:
    - login_success
    - login_failure
    - logout
    - password_change
    - mfa_enable/disable
    - api_key_created/deleted
    
  authorization:
    - permission_denied
    - role_change
    - privilege_escalation_attempt
    - resource_access
    
  data_access:
    - sensitive_data_access
    - bulk_data_export
    - data_modification
    - data_deletion
    
  system:
    - configuration_change
    - service_start/stop
    - backup_restore
    - security_scan_results

Log Format:
  timestamp: ISO 8601 UTC
  event_type: structured category
  user_id: authenticated user
  ip_address: source IP
  user_agent: client information
  resource: affected resource
  action: specific action taken
  result: success/failure/blocked
  additional_data: context-specific data
```

#### Structured Logging Implementation
```python
import structlog
from datetime import datetime
from typing import Optional, Dict, Any

logger = structlog.get_logger()

class SecurityAuditor:
    def __init__(self):
        self.logger = logger.bind(component="security_audit")
    
    def log_authentication_event(
        self,
        event_type: str,
        user_id: Optional[str],
        ip_address: str,
        user_agent: str,
        result: str,
        additional_data: Optional[Dict[str, Any]] = None
    ):
        """Log authentication-related security events"""
        self.logger.info(
            "authentication_event",
            event_type=event_type,
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            result=result,
            additional_data=additional_data or {},
            timestamp=datetime.utcnow().isoformat()
        )
    
    def log_authorization_event(
        self,
        user_id: str,
        resource: str,
        action: str,
        result: str,
        required_permissions: list,
        user_permissions: list
    ):
        """Log authorization decisions"""
        self.logger.info(
            "authorization_event",
            user_id=user_id,
            resource=resource,
            action=action,
            result=result,
            required_permissions=required_permissions,
            user_permissions=user_permissions,
            timestamp=datetime.utcnow().isoformat()
        )
```

### SIEM Integration

#### Log Forwarding Configuration
```yaml
# Fluent Bit configuration for log forwarding
service:
  flush: 1
  daemon: off
  log_level: info

input:
  name: tail
  path: /var/log/devnous/*.log
  tag: devnous.*
  parser: json
  refresh_interval: 5

filter:
  name: parser
  match: devnous.*
  key_name: message
  parser: security_log_parser

output:
  name: forward
  match: devnous.*
  host: siem.company.com
  port: 24224
  tls: on
  tls.verify: on
  tls.ca_file: /etc/ssl/certs/siem-ca.crt
```

#### Custom Log Parser
```
[PARSER]
    Name        security_log_parser
    Format      regex
    Regex       ^(?<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{3}Z)\s+(?<level>\w+)\s+(?<component>\w+)\s+(?<event_type>\w+)\s+(?<message>.*)$
    Time_Key    timestamp
    Time_Format %Y-%m-%dT%H:%M:%S.%LZ
```

### Security Metrics and Alerting

#### Prometheus Metrics
```python
from prometheus_client import Counter, Histogram, Gauge

# Security-related metrics
security_events = Counter(
    'devnous_security_events_total',
    'Total number of security events',
    ['event_type', 'result']
)

auth_duration = Histogram(
    'devnous_auth_duration_seconds',
    'Authentication request duration'
)

failed_auth_attempts = Counter(
    'devnous_failed_auth_attempts_total',
    'Failed authentication attempts',
    ['ip_address', 'user_agent']
)

active_sessions = Gauge(
    'devnous_active_sessions',
    'Number of active user sessions'
)

# Usage in application
class SecurityMetrics:
    @staticmethod
    def record_auth_event(event_type: str, result: str):
        security_events.labels(event_type=event_type, result=result).inc()
    
    @staticmethod
    def record_failed_auth(ip_address: str, user_agent: str):
        failed_auth_attempts.labels(
            ip_address=ip_address, 
            user_agent=user_agent
        ).inc()
```

#### Alert Rules
```yaml
# Prometheus alert rules
groups:
- name: security
  rules:
  - alert: HighFailedAuthRate
    expr: rate(devnous_failed_auth_attempts_total[5m]) > 0.1
    for: 2m
    labels:
      severity: warning
    annotations:
      summary: "High rate of failed authentication attempts"
      description: "Failed auth rate is {{ $value }} per second"
      
  - alert: MultipleFailedAuthFromSameIP
    expr: increase(devnous_failed_auth_attempts_total[15m]) > 10
    for: 1m
    labels:
      severity: critical
    annotations:
      summary: "Multiple failed auth attempts from same IP"
      description: "IP {{ $labels.ip_address }} has {{ $value }} failed attempts"
      
  - alert: UnusualAuthPattern
    expr: |
      (
        rate(devnous_security_events_total{event_type="login_success"}[1h]) > 
        rate(devnous_security_events_total{event_type="login_success"}[24h]) * 3
      )
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "Unusual authentication pattern detected"
```

---

## Compliance and Privacy

### Data Protection Compliance

#### GDPR Compliance
```yaml
Data Processing Principles:
  - Lawfulness, fairness, and transparency
  - Purpose limitation
  - Data minimization
  - Accuracy
  - Storage limitation
  - Integrity and confidentiality
  - Accountability

Implementation:
  privacy_by_design: true
  data_retention_policies: configured
  right_to_erasure: implemented
  data_portability: supported
  breach_notification: automated
  dpo_contact: privacy@devnous.example.com

Technical Measures:
  - Pseudonymization of personal data
  - Encryption at rest and in transit
  - Access controls and audit logging
  - Regular security assessments
  - Data processing agreements with vendors
```

#### Data Retention Policies
```sql
-- Data retention configuration
CREATE TABLE security.data_retention_policies (
    policy_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_type VARCHAR(100) NOT NULL,
    retention_period_days INTEGER NOT NULL,
    deletion_method VARCHAR(50) NOT NULL,
    legal_basis TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Example retention policies
INSERT INTO security.data_retention_policies VALUES
('550e8400-e29b-41d4-a716-446655440001', 'user_messages', 2555, 'secure_deletion', 'Contract performance'),
('550e8400-e29b-41d4-a716-446655440002', 'audit_logs', 2190, 'archival', 'Legal obligation'),
('550e8400-e29b-41d4-a716-446655440003', 'session_data', 30, 'secure_deletion', 'Legitimate interest'),
('550e8400-e29b-41d4-a716-446655440004', 'context_data', 365, 'anonymization', 'Consent');

-- Automated cleanup function
CREATE OR REPLACE FUNCTION cleanup_expired_data()
RETURNS void AS $$
DECLARE
    policy RECORD;
BEGIN
    FOR policy IN SELECT * FROM security.data_retention_policies LOOP
        CASE policy.data_type
            WHEN 'user_messages' THEN
                DELETE FROM core.messages 
                WHERE created_at < NOW() - (policy.retention_period_days || ' days')::INTERVAL;
                
            WHEN 'session_data' THEN
                DELETE FROM security.sessions 
                WHERE created_at < NOW() - (policy.retention_period_days || ' days')::INTERVAL;
                
            WHEN 'context_data' THEN
                UPDATE context.user_contexts 
                SET emotional_vector = '{"valence": 0, "arousal": 0, "confidence": 0}'::jsonb,
                    context_signals = '[]'::jsonb
                WHERE created_at < NOW() - (policy.retention_period_days || ' days')::INTERVAL;
        END CASE;
    END LOOP;
END;
$$ LANGUAGE plpgsql;
```

### Privacy Controls

#### Data Subject Rights Implementation
```python
class PrivacyController:
    def __init__(self, db_session):
        self.db = db_session
    
    async def export_user_data(self, user_id: str) -> Dict[str, Any]:
        """Export all user data for GDPR compliance"""
        user_data = {
            'personal_info': await self._get_personal_info(user_id),
            'messages': await self._get_user_messages(user_id),
            'context_data': await self._get_context_data(user_id),
            'audit_logs': await self._get_audit_logs(user_id),
            'preferences': await self._get_user_preferences(user_id)
        }
        
        # Log the data export request
        await self._log_privacy_event(
            user_id, 'data_export', 'User data exported'
        )
        
        return user_data
    
    async def delete_user_data(self, user_id: str, verification_token: str) -> bool:
        """Delete all user data (right to erasure)"""
        # Verify deletion token
        if not await self._verify_deletion_token(user_id, verification_token):
            raise ValueError("Invalid deletion token")
        
        # Start transaction for atomic deletion
        async with self.db.begin():
            # Delete from all relevant tables
            await self._delete_user_messages(user_id)
            await self._delete_user_context(user_id)
            await self._delete_user_preferences(user_id)
            await self._anonymize_audit_logs(user_id)
            
            # Mark user as deleted
            await self._mark_user_deleted(user_id)
        
        await self._log_privacy_event(
            user_id, 'data_deletion', 'User data deleted'
        )
        
        return True
```

---

## Security Incident Response

### Incident Classification

#### Severity Levels
```yaml
Critical (P0):
  description: "Active breach or imminent threat"
  response_time: "15 minutes"
  escalation: "CISO, CEO notification"
  examples:
    - Active data exfiltration
    - Ransomware attack
    - Complete service compromise
    - Nation-state attack indicators

High (P1):
  description: "Significant security incident"
  response_time: "1 hour"
  escalation: "Security team lead, Engineering manager"
  examples:
    - Successful privilege escalation
    - Unauthorized access to sensitive data
    - Major vulnerability exploitation
    - DDoS attack

Medium (P2):
  description: "Security concern requiring investigation"
  response_time: "4 hours"
  escalation: "Security team member"
  examples:
    - Suspicious user activity
    - Failed intrusion attempts
    - Minor vulnerability discovery
    - Policy violations

Low (P3):
  description: "Security awareness or minor issue"
  response_time: "24 hours"
  escalation: "Documentation only"
  examples:
    - Security education needs
    - Minor configuration issues
    - Informational security alerts
```

### Incident Response Playbooks

#### Data Breach Response
```yaml
Phase 1 - Detection and Analysis (0-1 hour):
  immediate_actions:
    - Confirm and validate the incident
    - Assess scope and impact
    - Preserve evidence and logs
    - Notify incident response team
    - Begin containment planning
    
  data_collection:
    - System logs and network traffic
    - User activity logs
    - Database access logs
    - Application logs
    - External threat intelligence

Phase 2 - Containment and Eradication (1-4 hours):
  containment:
    - Isolate affected systems
    - Change compromised credentials
    - Block malicious IP addresses
    - Disable compromised accounts
    - Preserve system images
    
  eradication:
    - Remove malware/backdoors
    - Patch vulnerabilities
    - Update security controls
    - Strengthen access controls

Phase 3 - Recovery and Lessons Learned (4+ hours):
  recovery:
    - Restore systems from clean backups
    - Monitor for continued threats
    - Validate system integrity
    - Gradual service restoration
    
  post_incident:
    - Document timeline and actions
    - Conduct lessons learned session
    - Update procedures and controls
    - Report to stakeholders and regulators
```

#### Automated Response Actions
```python
class IncidentResponse:
    def __init__(self):
        self.alerting = AlertingSystem()
        self.access_control = AccessControlSystem()
        self.monitoring = MonitoringSystem()
    
    async def handle_security_incident(self, incident_type: str, severity: str, details: Dict[str, Any]):
        """Automated incident response based on type and severity"""
        
        incident_id = await self._create_incident(incident_type, severity, details)
        
        if severity == "critical":
            await self._critical_incident_response(incident_id, details)
        elif severity == "high":
            await self._high_incident_response(incident_id, details)
        else:
            await self._standard_incident_response(incident_id, details)
    
    async def _critical_incident_response(self, incident_id: str, details: Dict[str, Any]):
        """Critical incident automated response"""
        
        # Immediate notifications
        await self.alerting.send_critical_alert(
            f"CRITICAL SECURITY INCIDENT: {incident_id}",
            details,
            recipients=["ciso@company.com", "security-team@company.com"]
        )
        
        # Automatic containment actions
        if "compromised_user" in details:
            await self.access_control.disable_user(details["compromised_user"])
            await self.access_control.revoke_all_sessions(details["compromised_user"])
        
        if "malicious_ip" in details:
            await self.access_control.block_ip_address(details["malicious_ip"])
        
        # Enhanced monitoring
        await self.monitoring.increase_monitoring_sensitivity()
        await self.monitoring.enable_detailed_logging()
```

### Forensics and Evidence Collection

#### Log Retention for Forensics
```bash
# Configure extended log retention for security incidents
# /etc/logrotate.d/devnous-security
/var/log/devnous/security.log {
    daily
    rotate 365
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    postrotate
        # Create forensic copy for long-term retention
        cp /var/log/devnous/security.log.1 /var/log/devnous/forensics/security-$(date +%Y%m%d).log
        # Encrypt for tamper protection
        gpg --encrypt --recipient security@devnous.example.com /var/log/devnous/forensics/security-$(date +%Y%m%d).log
        rm /var/log/devnous/forensics/security-$(date +%Y%m%d).log
    endscript
}
```

#### Evidence Collection Script
```bash
#!/bin/bash
# Security incident evidence collection script

INCIDENT_ID=$1
COLLECTION_DIR="/tmp/incident-$INCIDENT_ID"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

echo "Starting evidence collection for incident: $INCIDENT_ID"
mkdir -p "$COLLECTION_DIR"

# System information
uname -a > "$COLLECTION_DIR/system-info.txt"
ps aux > "$COLLECTION_DIR/processes.txt"
netstat -tulpn > "$COLLECTION_DIR/network-connections.txt"
lsof > "$COLLECTION_DIR/open-files.txt"

# Application logs
cp /var/log/devnous/*.log "$COLLECTION_DIR/"

# Database logs
sudo -u postgres pg_dump devnous_prod > "$COLLECTION_DIR/database-dump.sql"

# Container information
docker ps > "$COLLECTION_DIR/docker-containers.txt"
docker logs devnous-orchestrator > "$COLLECTION_DIR/orchestrator-logs.txt"

# Kubernetes information
kubectl get pods -n devnous-messaging -o wide > "$COLLECTION_DIR/k8s-pods.txt"
kubectl describe pods -n devnous-messaging > "$COLLECTION_DIR/k8s-pod-details.txt"

# Create tamper-proof archive
tar -czf "incident-$INCIDENT_ID-evidence-$TIMESTAMP.tar.gz" -C /tmp "incident-$INCIDENT_ID"
sha256sum "incident-$INCIDENT_ID-evidence-$TIMESTAMP.tar.gz" > "incident-$INCIDENT_ID-evidence-$TIMESTAMP.sha256"

echo "Evidence collection completed: incident-$INCIDENT_ID-evidence-$TIMESTAMP.tar.gz"
```

---

## Security Hardening Checklist

### System Hardening

#### Operating System Security
```yaml
User Account Security:
  □ Default passwords changed
  □ Unused accounts disabled
  □ Strong password policies enforced
  □ sudo access restricted
  □ Root login disabled for SSH
  □ User session timeouts configured

Network Security:
  □ Unnecessary services disabled
  □ Firewall rules configured and tested
  □ SSH hardened (key-only auth, non-standard port)
  □ Network segmentation implemented
  □ Intrusion detection system deployed

File System Security:
  □ Sensitive files have proper permissions
  □ World-writable files removed
  □ SUID/SGID files audited
  □ File system encryption enabled
  □ Regular file integrity checks

System Updates:
  □ Automatic security updates enabled
  □ Regular vulnerability scanning
  □ Patch management process established
  □ System hardening baselines applied
```

#### Application Hardening
```yaml
Configuration Security:
  □ Default credentials changed
  □ Unnecessary features disabled
  □ Security headers implemented
  □ Error messages sanitized
  □ Debug mode disabled in production

Data Protection:
  □ Input validation implemented
  □ Output encoding/escaping applied
  □ SQL injection prevention measures
  □ Cross-site scripting (XSS) protection
  □ Cross-site request forgery (CSRF) protection

Authentication & Authorization:
  □ Strong authentication mechanisms
  □ Multi-factor authentication enabled
  □ Session management secure
  □ Proper authorization controls
  □ Regular access reviews

Communication Security:
  □ HTTPS/TLS encryption enabled
  □ Certificate validation implemented
  □ Secure communication protocols
  □ API security controls
  □ Third-party integration security
```

### Container and Orchestration Hardening

#### Docker Security
```yaml
Image Security:
  □ Minimal base images used
  □ Images scanned for vulnerabilities
  □ No secrets in images
  □ Images signed and verified
  □ Regular image updates

Runtime Security:
  □ Non-root user in containers
  □ Read-only file systems where possible
  □ Resource limits configured
  □ Security contexts applied
  □ Capability dropping implemented

Network Security:
  □ Container networks isolated
  □ Unnecessary ports not exposed
  □ Network policies implemented
  □ Service mesh security (if applicable)
```

#### Kubernetes Security
```yaml
Cluster Security:
  □ RBAC properly configured
  □ Network policies implemented
  □ Pod security standards enforced
  □ Admission controllers configured
  □ API server secured

Workload Security:
  □ Security contexts defined
  □ Service accounts properly configured
  □ Secrets management implemented
  □ Resource quotas applied
  □ Pod security policies (or equivalent)

Monitoring and Logging:
  □ Audit logging enabled
  □ Security monitoring tools deployed
  □ Alerting configured for security events
  □ Log aggregation and analysis
  □ Runtime security monitoring
```

---

## Vulnerability Management

### Vulnerability Assessment

#### Regular Security Scanning
```yaml
Automated Scanning Schedule:
  - Infrastructure scanning: Weekly
  - Application scanning: Daily (CI/CD)
  - Container scanning: On every build
  - Dependency scanning: Daily
  - Configuration scanning: Weekly

Scan Types:
  network_scanning:
    - Port scanning and service enumeration
    - SSL/TLS configuration analysis
    - Network device security assessment
    
  web_application_scanning:
    - OWASP Top 10 vulnerability checks
    - Input validation testing
    - Authentication and session management
    - Business logic flaws
    
  infrastructure_scanning:
    - Operating system vulnerabilities
    - Missing security patches
    - Configuration weaknesses
    - Compliance checks

Vulnerability Databases:
  - CVE (Common Vulnerabilities and Exposures)
  - NVD (National Vulnerability Database)
  - Vendor security advisories
  - Security research publications
```

#### Vulnerability Remediation Process
```yaml
Priority Classification:
  Critical (CVSS 9.0-10.0):
    - Timeline: 24 hours
    - Action: Immediate patching or mitigation
    - Approval: Emergency change process
    
  High (CVSS 7.0-8.9):
    - Timeline: 7 days
    - Action: Scheduled patching
    - Approval: Expedited change process
    
  Medium (CVSS 4.0-6.9):
    - Timeline: 30 days
    - Action: Regular maintenance window
    - Approval: Normal change process
    
  Low (CVSS 0.1-3.9):
    - Timeline: 90 days
    - Action: Next major update cycle
    - Approval: Standard change process

Remediation Workflow:
  1. Vulnerability identification and validation
  2. Impact assessment and risk analysis
  3. Remediation planning and testing
  4. Deployment approval and implementation
  5. Verification and closure
  6. Lessons learned and process improvement
```

### Security Testing Integration

#### CI/CD Security Pipeline
```yaml
# .github/workflows/security.yml
name: Security Testing Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  security-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Static Analysis Security Testing (SAST)
        uses: github/super-linter@v4
        env:
          DEFAULT_BRANCH: main
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          
      - name: Dependency Vulnerability Scanning
        uses: securecodewarrior/github-action-add-sarif@v1
        with:
          sarif-file: 'security-scan-results.sarif'
          
      - name: Container Security Scanning
        run: |
          docker build -t devnous/api:${{ github.sha }} .
          docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
            aquasec/trivy image devnous/api:${{ github.sha }}
            
      - name: Dynamic Application Security Testing (DAST)
        run: |
          docker run --rm -t owasp/zap2docker-stable zap-baseline.py \
            -t http://localhost:8000
            
      - name: Infrastructure as Code Security
        uses: bridgecrewio/checkov-action@master
        with:
          directory: ./infrastructure/
          framework: kubernetes,dockerfile
```

### Penetration Testing

#### Regular Penetration Testing Schedule
```yaml
Internal Testing:
  - Frequency: Quarterly
  - Scope: Full infrastructure and applications
  - Methods: White-box, gray-box testing
  - Tools: Internal security team tools
  
External Testing:
  - Frequency: Annually
  - Scope: External-facing services
  - Methods: Black-box testing
  - Provider: Third-party security firm

Specialized Testing:
  - API security testing: Bi-annually
  - Social engineering testing: Annually
  - Physical security testing: Bi-annually
  - Wireless network testing: Annually

Post-Testing Process:
  1. Report review and validation
  2. Risk assessment and prioritization
  3. Remediation planning
  4. Implementation and verification
  5. Re-testing of critical findings
  6. Management reporting
```

---

## See Also

- [Configuration Reference](CONFIGURATION_REFERENCE.md)
- [Database Schema Reference](DATABASE_SCHEMA_REFERENCE.md)
- [API Quick Reference](API_QUICK_REFERENCE.md)
- [CLI Commands Reference](CLI_COMMANDS_REFERENCE.md)
- [Error Codes Reference](ERROR_CODES_REFERENCE.md)
- [Performance Benchmarks Reference](PERFORMANCE_BENCHMARKS_REFERENCE.md)
- [Deployment Reference](DEPLOYMENT_REFERENCE.md)
