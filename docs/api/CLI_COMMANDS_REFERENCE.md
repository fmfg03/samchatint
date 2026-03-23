# CLI Commands Reference

**Version**: 1.0.0  
**Last Updated**: 2024-01-15  
**Target**: Developers, DevOps Engineers, System Administrators

## Overview

This document provides comprehensive reference for all CLI commands and tools available in the SamChat/DevNous system. Includes command-line utilities, management scripts, and development tools.

Important:

- The only package console script currently exposed by this repository is `samchat`.
- Many `devnous ...` examples below describe a separate or planned CLI surface and should be read as legacy/reference material, not as the current production bootstrap for `sam.chat`.
- Current install/runtime matrix:
  - `docs/install_matrix.md`

## Table of Contents

- [Installation and Setup](#installation-and-setup)
- [Core CLI Tools](#core-cli-tools)
- [Project Management Commands](#project-management-commands)
- [Context Commands](#context-commands)
- [Testing Commands](#testing-commands)
- [Database Commands](#database-commands)
- [Deployment Commands](#deployment-commands)
- [Monitoring Commands](#monitoring-commands)
- [Development Utilities](#development-utilities)
- [Configuration Management](#configuration-management)
- [Troubleshooting Commands](#troubleshooting-commands)

---

## Installation and Setup

### System Requirements
- Python 3.9+
- PostgreSQL 14+
- Redis 6+
- Node.js 18+ (for web components)
- Docker 20+ (for containerized deployment)

### Installation
```bash
# Clone repository
git clone https://github.com/company/samchat.git
cd samchat

# Install Python dependencies
pip install -r requirements.txt

# Optional profiles
# pip install -r requirements-test.txt
# pip install -r requirements-docs.txt
pip install -r requirements-dev.txt

# Install CLI tools
pip install -e .

# Verify installation
samchat info
```

Note:

- The current package exposes the `samchat` console script.
- The `devnous` CLI examples below document a separate/legacy command surface and should not be treated as the production bootstrap for `sam.chat`.

### Environment Setup
```bash
# Create environment file
cp .env.example .env

# Start the secondary DevNous API surface
uvicorn devnous.api:app --reload --host 0.0.0.0 --port 8000
```

---

## Core CLI Tools

### devnous

Main CLI tool for system management and operations.

#### Usage
```bash
devnous [GLOBAL_OPTIONS] COMMAND [COMMAND_OPTIONS] [ARGS]
```

#### Global Options
```bash
--config, -c        Configuration file path (default: .env)
--verbose, -v       Verbose output
--quiet, -q         Quiet mode (errors only)
--debug             Enable debug mode
--profile           Profile command execution
--no-color          Disable colored output
--format            Output format: json, yaml, table (default: table)
```

#### Available Commands
```bash
# System Management
devnous server          # Server management
devnous db              # Database operations
devnous cache           # Cache management
devnous config          # Configuration management

# Core Features
devnous memory          # Memory management
devnous chat            # Chat processing
devnous pm              # Project management
devnous workflow        # Workflow management
devnous context         # Context detection
devnous debate          # Debate system

# Monitoring and Maintenance
devnous monitor         # Monitoring operations
devnous logs            # Log management
devnous health          # Health checks
devnous metrics         # Metrics collection

# Development and Testing
devnous test            # Test runner
devnous lint            # Code linting
devnous migrate         # Database migrations
devnous seed            # Data seeding
```

### samchat

Legacy CLI tool for SamChat-specific operations.

#### Usage
```bash
samchat [OPTIONS] COMMAND [ARGS]
```

#### Available Commands
```bash
samchat agents          # Agent management
samchat conversations   # Conversation processing
samchat integrations    # External integrations
samchat migrate         # Migration utilities
```

---

## Project Management Commands

### `/pm:*` Commands

Project management commands available in the Claude Code PM system.

#### `/pm:create-task`
**Purpose**: Create a new task in the project management system
**Usage**: `/pm:create-task`
**Interactive**: Prompts for task details
**Options**:
- `--title, -t`: Task title
- `--description, -d`: Task description
- `--priority, -p`: Priority (low, medium, high, critical)
- `--assignee, -a`: Assignee username
- `--labels, -l`: Comma-separated labels
- `--due-date`: Due date (YYYY-MM-DD)

```bash
# Interactive mode
/pm:create-task

# With parameters
/pm:create-task --title "Fix authentication bug" --priority high --assignee john_doe
```

#### `/pm:list-tasks`
**Purpose**: List and filter project tasks
**Usage**: `/pm:list-tasks [OPTIONS]`
**Options**:
- `--status, -s`: Filter by status (todo, in_progress, done, blocked)
- `--assignee, -a`: Filter by assignee
- `--priority, -p`: Filter by priority
- `--project`: Filter by project
- `--limit, -l`: Number of tasks to show (default: 20)
- `--format`: Output format (table, json, csv)

```bash
# List all tasks
/pm:list-tasks

# Filter active tasks for specific user
/pm:list-tasks --status in_progress --assignee jane_smith

# Export to CSV
/pm:list-tasks --format csv --limit 100 > tasks.csv
```

#### `/pm:update-task`
**Purpose**: Update existing task
**Usage**: `/pm:update-task TASK_ID [OPTIONS]`
**Options**:
- `--status`: New status
- `--priority`: New priority
- `--assignee`: New assignee
- `--progress`: Progress percentage (0-100)
- `--hours`: Actual hours worked

```bash
# Update task status
/pm:update-task TASK-123 --status done --hours 6.5

# Reassign task
/pm:update-task TASK-456 --assignee new_developer --priority high
```

#### `/pm:task-stats`
**Purpose**: Generate task statistics and reports
**Usage**: `/pm:task-stats [OPTIONS]`
**Options**:
- `--team`: Team identifier
- `--period`: Time period (day, week, month, quarter)
- `--user`: Specific user stats
- `--export`: Export format (json, csv, pdf)

```bash
# Team statistics for current month
/pm:task-stats --team dev-team --period month

# Individual user report
/pm:task-stats --user john_doe --period week --export pdf
```

#### `/pm:burndown`
**Purpose**: Generate burndown charts and sprint analytics
**Usage**: `/pm:burndown [OPTIONS]`
**Options**:
- `--sprint`: Sprint identifier
- `--team`: Team identifier
- `--format`: Chart format (ascii, png, svg)
- `--period`: Days to analyze

```bash
# Current sprint burndown
/pm:burndown --sprint current --format ascii

# Team burndown chart
/pm:burndown --team backend --format png --period 14
```

#### `/pm:sync-jira`
**Purpose**: Synchronize with Jira integration
**Usage**: `/pm:sync-jira [OPTIONS]`
**Options**:
- `--project`: Jira project key
- `--direction`: Sync direction (pull, push, both)
- `--dry-run`: Preview changes without applying
- `--force`: Force sync despite conflicts

```bash
# Pull updates from Jira
/pm:sync-jira --project DEV --direction pull

# Dry run sync
/pm:sync-jira --project DEV --direction both --dry-run
```

#### `/pm:github-sync`
**Purpose**: Synchronize with GitHub issues and projects
**Usage**: `/pm:github-sync [OPTIONS]`
**Options**:
- `--repo`: Repository name (org/repo)
- `--milestone`: Milestone filter
- `--labels`: Label filter
- `--state`: Issue state (open, closed, all)

```bash
# Sync open issues from specific repo
/pm:github-sync --repo company/backend --state open

# Sync milestone issues
/pm:github-sync --repo company/frontend --milestone "Sprint 23"
```

---

## Context Commands

### `/context:*` Commands

Context management and analysis commands.

#### `/context:create`
**Purpose**: Create initial project context documentation
**Usage**: `/context:create [OPTIONS]`
**Options**:
- `--force`: Overwrite existing context
- `--template`: Context template to use
- `--include`: Components to include (architecture, dependencies, patterns)

```bash
# Create comprehensive context
/context:create

# Force recreate with specific template
/context:create --force --template enterprise
```

#### `/context:update`
**Purpose**: Update existing context with recent changes
**Usage**: `/context:update [OPTIONS]`
**Options**:
- `--incremental`: Only update changed components
- `--since`: Update changes since date/commit
- `--components`: Specific components to update

```bash
# Incremental update
/context:update --incremental

# Update since specific date
/context:update --since "2024-01-01"
```

#### `/context:prime`
**Purpose**: Load context into current conversation
**Usage**: `/context:prime [OPTIONS]`
**Options**:
- `--sections`: Specific sections to load
- `--summary`: Load only summary information
- `--depth`: Context depth (shallow, normal, deep)

```bash
# Load full context
/context:prime

# Load architecture summary only
/context:prime --sections architecture --summary
```

#### `/context:analyze`
**Purpose**: Analyze current project context and patterns
**Usage**: `/context:analyze [OPTIONS]`
**Options**:
- `--type`: Analysis type (complexity, patterns, dependencies)
- `--output`: Output format (report, json, metrics)
- `--export`: Export results to file

```bash
# Full context analysis
/context:analyze --type all --output report

# Complexity analysis only
/context:analyze --type complexity --export complexity_report.json
```

---

## Testing Commands

### `/testing:*` Commands

Test configuration and execution commands.

#### `/testing:prime`
**Purpose**: Configure testing setup for the project
**Usage**: `/testing:prime [OPTIONS]`
**Options**:
- `--framework`: Test framework (pytest, unittest, jest)
- `--config`: Configuration template
- `--coverage`: Enable coverage reporting

```bash
# Auto-detect and configure testing
/testing:prime

# Configure with specific framework
/testing:prime --framework pytest --coverage
```

#### `/testing:run`
**Purpose**: Execute tests with intelligent analysis
**Usage**: `/testing:run [TARGET] [OPTIONS]`
**Options**:
- `--parallel, -p`: Run tests in parallel
- `--coverage`: Generate coverage report
- `--format`: Output format (detailed, summary, json)
- `--failed-only`: Run only previously failed tests
- `--pattern`: Test pattern to match

```bash
# Run all tests
/testing:run

# Run specific test file with coverage
/testing:run tests/test_api.py --coverage

# Run tests matching pattern
/testing:run --pattern "*integration*" --parallel
```

#### `/testing:coverage`
**Purpose**: Generate and analyze test coverage
**Usage**: `/testing:coverage [OPTIONS]`
**Options**:
- `--format`: Coverage format (html, xml, json, terminal)
- `--threshold`: Minimum coverage threshold
- `--exclude`: Files/directories to exclude
- `--report`: Generate detailed report

```bash
# Generate HTML coverage report
/testing:coverage --format html

# Check coverage threshold
/testing:coverage --threshold 80 --format terminal
```

#### `/testing:benchmark`
**Purpose**: Run performance benchmarks
**Usage**: `/testing:benchmark [OPTIONS]`
**Options**:
- `--suite`: Benchmark suite to run
- `--iterations`: Number of iterations
- `--baseline`: Compare against baseline
- `--export`: Export results

```bash
# Run all benchmarks
/testing:benchmark

# Compare against baseline
/testing:benchmark --baseline main --export benchmark_results.json
```

---

## Database Commands

### `devnous db` Commands

Database management and operations.

#### `devnous db init`
**Purpose**: Initialize database schema and base data
**Usage**: `devnous db init [OPTIONS]`
**Options**:
- `--schema-only`: Create schema without data
- `--force`: Force initialization (drops existing)
- `--seed`: Load seed data
- `--env`: Environment configuration

```bash
# Initialize with seed data
devnous db init --seed

# Force reinitialize
devnous db init --force --env production
```

#### `devnous db migrate`
**Purpose**: Run database migrations
**Usage**: `devnous db migrate [OPTIONS]`
**Options**:
- `--target`: Target migration version
- `--dry-run`: Preview migration without applying
- `--rollback`: Rollback to previous version
- `--list`: List available migrations

```bash
# Run pending migrations
devnous db migrate

# Dry run migrations
devnous db migrate --dry-run

# Rollback last migration
devnous db migrate --rollback
```

#### `devnous db seed`
**Purpose**: Seed database with sample or test data
**Usage**: `devnous db seed [OPTIONS]`
**Options**:
- `--dataset`: Dataset to load (sample, test, production)
- `--tables`: Specific tables to seed
- `--clean`: Clean existing data first
- `--size`: Dataset size (small, medium, large)

```bash
# Load sample data
devnous db seed --dataset sample

# Clean and load test data
devnous db seed --dataset test --clean --size large
```

#### `devnous db backup`
**Purpose**: Create database backup
**Usage**: `devnous db backup [OPTIONS]`
**Options**:
- `--output`: Output file path
- `--format`: Backup format (sql, custom, tar)
- `--compress`: Compress backup
- `--schema-only`: Backup schema only

```bash
# Full compressed backup
devnous db backup --output backup_$(date +%Y%m%d).sql.gz --compress

# Schema-only backup
devnous db backup --schema-only --output schema.sql
```

#### `devnous db restore`
**Purpose**: Restore database from backup
**Usage**: `devnous db restore BACKUP_FILE [OPTIONS]`
**Options**:
- `--clean`: Clean database before restore
- `--data-only`: Restore data only
- `--schema-only`: Restore schema only
- `--tables`: Specific tables to restore

```bash
# Full restore
devnous db restore backup_20240115.sql.gz --clean

# Restore specific tables
devnous db restore backup.sql --tables core.users,core.teams
```

#### `devnous db status`
**Purpose**: Check database status and health
**Usage**: `devnous db status [OPTIONS]`
**Options**:
- `--connections`: Show connection information
- `--size`: Show database size information
- `--performance`: Show performance metrics
- `--json`: Output in JSON format

```bash
# Full status check
devnous db status --connections --size --performance

# JSON output for monitoring
devnous db status --json
```

#### `devnous db query`
**Purpose**: Execute SQL queries against the database
**Usage**: `devnous db query [SQL] [OPTIONS]`
**Options**:
- `--file`: SQL file to execute
- `--format`: Output format (table, csv, json)
- `--output`: Output file
- `--explain`: Show query execution plan

```bash
# Execute query
devnous db query "SELECT COUNT(*) FROM core.users WHERE is_active = true"

# Execute from file
devnous db query --file analysis.sql --format csv --output results.csv
```

---

## Deployment Commands

### Container Management

#### `devnous server` Commands

Server lifecycle management.

```bash
# Start development server
devnous server start --dev --port 8000

# Start production server
devnous server start --env production --workers 4

# Stop server gracefully
devnous server stop --graceful

# Restart server
devnous server restart --zero-downtime

# Server status
devnous server status --detailed
```

#### Docker Commands

```bash
# Build Docker images
docker-compose -f docker-compose.production.yml build

# Start full stack
docker-compose -f docker-compose.production.yml up -d

# Scale services
docker-compose -f docker-compose.production.yml up -d --scale debate-orchestrator=3

# Check service status
docker-compose -f docker-compose.production.yml ps

# View logs
docker-compose -f docker-compose.production.yml logs -f debate-orchestrator
```

#### Kubernetes Commands

```bash
# Apply configurations
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmaps.yaml
kubectl apply -f k8s/deployments.yaml

# Check deployment status
kubectl get deployments -n devnous-messaging

# Scale deployment
kubectl scale deployment debate-orchestrator --replicas=5 -n devnous-messaging

# Rolling update
kubectl rollout restart deployment/devnous-orchestrator -n devnous-messaging

# Check rollout status
kubectl rollout status deployment/devnous-orchestrator -n devnous-messaging

# View pod logs
kubectl logs -f deployment/debate-orchestrator -n devnous-messaging
```

### Configuration Management

#### `devnous config` Commands

```bash
# View current configuration
devnous config show

# Edit configuration
devnous config edit

# Validate configuration
devnous config validate

# Generate configuration template
devnous config generate --env production --output production.env

# Migrate configuration
devnous config migrate --from .env.old --to .env

# Environment-specific config
devnous config load --env staging
devnous config load --file infrastructure/config/debate-system.staging.env
```

---

## Monitoring Commands

### Health and Metrics

#### `devnous health` Commands

```bash
# System health check
devnous health check

# Component-specific health
devnous health check --component database
devnous health check --component redis
devnous health check --component debate-system

# Continuous health monitoring
devnous health monitor --interval 30 --alert-threshold warning

# Export health report
devnous health report --format json --output health_report.json
```

#### `devnous metrics` Commands

```bash
# Collect current metrics
devnous metrics collect

# View metrics for specific component
devnous metrics show --component api --period 1h

# Export metrics
devnous metrics export --format prometheus --output metrics.txt

# Start metrics server
devnous metrics server --port 9090

# Generate metrics dashboard
devnous metrics dashboard --generate --output dashboard.json
```

### Log Management

#### `devnous logs` Commands

```bash
# View recent logs
devnous logs tail --lines 100

# Follow logs in real-time
devnous logs follow --component debate-orchestrator

# Filter logs by level
devnous logs show --level error --since "1 hour ago"

# Search logs
devnous logs search "authentication failed" --since today

# Export logs
devnous logs export --since "2024-01-15" --output logs_20240115.txt

# Log rotation
devnous logs rotate --keep 30 --compress
```

### Alerting

#### Alert Management Commands

```bash
# List active alerts
devnous alerts list --status active

# Create alert rule
devnous alerts create --name "High CPU" --condition "cpu_usage > 80" --severity warning

# Test alert rule
devnous alerts test --rule "High CPU" --dry-run

# Acknowledge alert
devnous alerts ack ALERT_ID --message "Investigating issue"

# Silence alert
devnous alerts silence ALERT_ID --duration 1h --reason "Maintenance window"

# Alert history
devnous alerts history --since "1 week ago" --format table
```

---

## Development Utilities

### Code Quality

#### Linting and Formatting

```bash
# Run all linters
devnous lint

# Specific linters
devnous lint --tool black --check
devnous lint --tool isort --diff
devnous lint --tool flake8 --max-line-length 100
devnous lint --tool mypy --strict

# Auto-fix issues
devnous lint --fix --tools black,isort

# Pre-commit hooks
devnous lint install-hooks
devnous lint run-hooks --all-files
```

#### Security Scanning

```bash
# Security audit
devnous security audit

# Dependency vulnerability check
devnous security check-deps

# Secret scanning
devnous security scan-secrets --exclude .git

# Generate security report
devnous security report --format html --output security_report.html
```

### Performance Analysis

#### Profiling Commands

```bash
# Profile API endpoints
devnous profile api --duration 60s --output profile_api.json

# Memory profiling
devnous profile memory --component debate-orchestrator

# Database query profiling
devnous profile db --slow-queries --duration 300s

# Load testing
devnous load-test --target http://localhost:8000 --users 100 --duration 5m

# Benchmark comparison
devnous benchmark --compare baseline --output benchmark_comparison.html
```

---

## Troubleshooting Commands

### Diagnostic Tools

#### `devnous debug` Commands

```bash
# System diagnostics
devnous debug system --full

# Component diagnostics
devnous debug component --name debate-orchestrator

# Network diagnostics
devnous debug network --check-external

# Performance diagnostics
devnous debug performance --component api --duration 60s

# Generate debug report
devnous debug report --include-logs --output debug_report.zip
```

#### Connection Testing

```bash
# Test database connection
devnous debug connection database

# Test Redis connection
devnous debug connection redis

# Test external API connections
devnous debug connection openai
devnous debug connection anthropic

# Test webhook endpoints
devnous debug webhook --platform slack --test-url

# Network connectivity
devnous debug ping --host api.devnous.example.com --port 443
```

#### Resource Analysis

```bash
# CPU and memory usage
devnous debug resources --component all

# Database connection pool
devnous debug resources --component database --pools

# Cache usage analysis
devnous debug resources --component redis --memory-usage

# Disk space analysis
devnous debug resources --disk-usage --threshold 80

# Thread analysis
devnous debug resources --threads --component debate-system
```

### Recovery Commands

#### Service Recovery

```bash
# Restart failed components
devnous recover restart --component debate-orchestrator

# Clear stuck processes
devnous recover clear-stuck --timeout 300s

# Reset component state
devnous recover reset --component debate-system --confirm

# Rollback deployment
devnous recover rollback --deployment debate-orchestrator --version previous

# Emergency stop
devnous recover emergency-stop --component all --reason "Security incident"
```

#### Data Recovery

```bash
# Recover corrupted cache
devnous recover cache --rebuild

# Fix database inconsistencies
devnous recover database --check-integrity --fix

# Recover from backup
devnous recover restore --backup backup_20240115.sql.gz --point-in-time "2024-01-15 14:30:00"

# Clean orphaned data
devnous recover cleanup --orphaned-records --dry-run

# Rebuild indexes
devnous recover indexes --rebuild --analyze
```

---

## Command Examples and Workflows

### Development Workflow

```bash
# Daily development setup
/context:prime
/testing:prime
devnous server start --dev

# Before committing changes
devnous lint --fix
/testing:run --coverage
devnous security scan-secrets

# Code review preparation
/context:update --incremental
devnous debug performance --baseline
```

### Deployment Workflow

```bash
# Pre-deployment checks
devnous config validate --env production
devnous health check --all-components
devnous db migrate --dry-run

# Deploy to staging
kubectl apply -f k8s/ --namespace devnous-staging
devnous health monitor --env staging --timeout 300s

# Production deployment
kubectl apply -f k8s/ --namespace devnous-production
devnous monitor deployment --zero-downtime
```

### Incident Response Workflow

```bash
# Initial assessment
devnous health check --critical-only
devnous logs search "error" --since "10 minutes ago"
devnous debug system --quick

# Deep investigation
devnous debug performance --full-report
devnous metrics show --period 2h --anomalies
devnous alerts history --severity critical

# Recovery actions
devnous recover restart --failed-components
devnous db status --connections --performance
devnous health monitor --continuous --alert-threshold critical
```

### Performance Optimization Workflow

```bash
# Performance baseline
devnous benchmark --baseline --export baseline.json
devnous profile api --duration 300s

# Optimization and testing
devnous config tune --optimize performance
devnous test performance --compare baseline

# Validation
devnous benchmark --compare baseline --export optimized.json
devnous metrics export --period 1h --format dashboard
```

---

## Command Reference Quick Lookup

### Most Used Commands
```bash
# System status
devnous health check
devnous server status

# Development
/context:prime
/testing:run
devnous lint --fix

# Database
devnous db migrate
devnous db status

# Monitoring
devnous logs follow
devnous metrics show
devnous alerts list

# Deployment
kubectl get deployments -n devnous-messaging
docker-compose ps
```

### Emergency Commands
```bash
# Emergency stop
devnous recover emergency-stop --component all

# Quick health check
devnous health check --critical-only --timeout 30s

# Immediate log analysis
devnous logs search "error|critical" --since "5 minutes ago" --tail 50

# Resource check
devnous debug resources --component all --alert-threshold 90

# Database emergency check
devnous db status --connections --json
```

---

## See Also

- [Configuration Reference](CONFIGURATION_REFERENCE.md)
- [Database Schema Reference](DATABASE_SCHEMA_REFERENCE.md)
- [API Quick Reference](API_QUICK_REFERENCE.md)
- [Error Codes Reference](ERROR_CODES_REFERENCE.md)
- [Performance Benchmarks Reference](PERFORMANCE_BENCHMARKS_REFERENCE.md)
- [Security Configuration Reference](SECURITY_CONFIGURATION_REFERENCE.md)
- [Deployment Reference](DEPLOYMENT_REFERENCE.md)
