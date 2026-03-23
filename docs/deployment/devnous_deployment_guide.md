# DevNous Database Deployment and Operations Guide

This comprehensive guide covers the deployment, operation, and maintenance of the DevNous database system with PostgreSQL, including backup strategies, performance optimization, and disaster recovery procedures.

Important:

- This is a standalone DevNous database operations guide.
- Database names, roles, scripts, and maintenance paths in this document are not the current production defaults for the live `sam.chat` deployment in this repository.
- For the active runtime/install split, see:
  - `docs/install_matrix.md`

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Initial Deployment](#initial-deployment)
3. [Database Configuration](#database-configuration)
4. [Connection Pooling Setup](#connection-pooling-setup)
5. [Backup and Recovery](#backup-and-recovery)
6. [Monitoring and Maintenance](#monitoring-and-maintenance)
7. [Performance Tuning](#performance-tuning)
8. [Disaster Recovery](#disaster-recovery)
9. [Security Configuration](#security-configuration)
10. [Troubleshooting](#troubleshooting)

## System Architecture

### Core Components

- **PostgreSQL 14+**: Primary database engine
- **PgBouncer**: Connection pooling and management
- **pg_basebackup**: Physical backups
- **pg_dump/pg_restore**: Logical backups
- **WAL-E/pgBackRest**: Continuous archiving (recommended for production)

### Database Schema Structure

The DevNous system includes the following main components:

- **Core Tables**: teams, users, sessions, conversations, messages
- **Task Management**: projects, tasks, task_comments, task_attachments
- **Workflow Engine**: workflows, workflow_executions, workflow_state_snapshots
- **Session Memory**: session_memory (partitioned by team_id)
- **Chat Integration**: chat_channels, channel_members, message_routing_logs
- **Audit System**: audit_logs (partitioned by date), session_memory_versions
- **Monitoring**: system_metrics, query_performance, connection_pool_stats

## Initial Deployment

### Prerequisites

```bash
# Install PostgreSQL 14+
sudo apt update
sudo apt install postgresql-14 postgresql-contrib-14

# Install PgBouncer
sudo apt install pgbouncer

# Install additional tools
sudo apt install postgresql-14-pg-stat-statements
sudo apt install postgresql-14-pg-trgm
```

### Step 1: Database Setup

```sql
-- Connect as postgres superuser
sudo -u postgres psql

-- Create database and user
CREATE DATABASE devnous;
CREATE USER devnous_app WITH ENCRYPTED PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE devnous TO devnous_app;

-- Connect to devnous database
\c devnous

-- Run schema setup
\i /path/to/devnous_schema.sql
\i /path/to/devnous_indexes.sql
\i /path/to/devnous_audit_functions.sql
\i /path/to/devnous_partitioning.sql
\i /path/to/devnous_migrations.sql
\i /path/to/devnous_backup_recovery.sql
\i /path/to/devnous_connection_pooling.sql
```

### Step 2: Initial Configuration

```sql
-- Set environment configuration
SELECT configure_environment('production'); -- or 'development', 'staging'

-- Create initial backup configuration
SELECT create_backup_configuration(
    'daily_full_backup',
    'full',
    '0 2 * * *', -- Daily at 2 AM
    30,          -- 30 day retention
    '/backups/devnous',
    4            -- 4 parallel jobs
);

-- Set up connection pool
SELECT configure_connection_pool(
    'devnous_app_pool',
    'devnous',
    'session',  -- pool mode
    25,         -- pool size
    100,        -- max client connections
    50          -- max db connections
);
```

## Database Configuration

### PostgreSQL Configuration (/etc/postgresql/14/main/postgresql.conf)

```ini
# Memory settings
shared_buffers = 256MB                  # 25% of RAM for smaller systems
effective_cache_size = 1GB              # 75% of RAM
work_mem = 4MB                          # Per-operation memory
maintenance_work_mem = 64MB             # Maintenance operations

# Connection settings
max_connections = 200                   # Adjust based on connection pooling
superuser_reserved_connections = 3

# WAL settings for replication and PITR
wal_level = replica
max_wal_senders = 3
wal_keep_size = 1GB
archive_mode = on
archive_command = 'cp %p /var/lib/postgresql/archive/%f'

# Logging
log_destination = 'stderr,syslog'
log_line_prefix = '%t [%p]: [%l-1] user=%u,db=%d,app=%a,client=%h '
log_min_duration_statement = 5000       # Log queries > 5 seconds
log_checkpoints = on
log_connections = on
log_disconnections = on
log_lock_waits = on

# Performance monitoring
shared_preload_libraries = 'pg_stat_statements'
pg_stat_statements.track = all
pg_stat_statements.max = 10000

# Autovacuum tuning
autovacuum_max_workers = 3
autovacuum_naptime = 20s
autovacuum_vacuum_threshold = 50
autovacuum_analyze_threshold = 50
autovacuum_vacuum_scale_factor = 0.1
autovacuum_analyze_scale_factor = 0.05
```

### pg_hba.conf Configuration

```
# TYPE  DATABASE        USER            ADDRESS                 METHOD
local   all             postgres                                peer
local   all             all                                     peer
host    devnous         devnous_app     127.0.0.1/32           scram-sha-256
host    devnous         devnous_app     ::1/128                scram-sha-256
host    replication     replicator      192.168.1.0/24         scram-sha-256

# Connection pooler
host    devnous         devnous_app     127.0.0.1/32           scram-sha-256
```

## Connection Pooling Setup

### PgBouncer Configuration

Generate the configuration:

```sql
-- Generate PgBouncer configuration
SELECT generate_pgbouncer_config();
```

Save the output to `/etc/pgbouncer/pgbouncer.ini` and create userlist:

```bash
# Create userlist file
echo '"devnous_app" "SCRAM-SHA-256$4096:salt$hash"' > /etc/pgbouncer/userlist.txt

# Start PgBouncer
sudo systemctl start pgbouncer
sudo systemctl enable pgbouncer
```

### Connection Pool Monitoring

Set up automated monitoring:

```bash
# Create cron job for pool statistics
echo "*/5 * * * * postgres psql devnous -c \"SELECT capture_pool_statistics();\"" >> /etc/crontab

# Weekly pool maintenance
echo "0 3 * * 0 postgres psql devnous -c \"SELECT maintain_connection_pools();\"" >> /etc/crontab
```

## Backup and Recovery

### Automated Backup Setup

```sql
-- Configure different backup types
SELECT create_backup_configuration(
    'hourly_incremental',
    'incremental',
    '0 * * * *',    -- Every hour
    7,              -- 7 day retention
    '/backups/incremental',
    2
);

SELECT create_backup_configuration(
    'weekly_logical',
    'logical',
    '0 4 * * 0',    -- Weekly Sunday 4 AM
    60,             -- 60 day retention
    '/backups/logical',
    1
);
```

### Backup Execution Scripts

Create `/usr/local/bin/devnous_backup.sh`:

```bash
#!/bin/bash
set -e

BACKUP_TYPE=${1:-full}
CONFIG_NAME=${2:-daily_full_backup}
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="/var/log/postgresql/backup_${TIMESTAMP}.log"

echo "Starting $BACKUP_TYPE backup at $(date)" >> $LOG_FILE

case $BACKUP_TYPE in
  "logical")
    sudo -u postgres psql devnous -c "SELECT perform_logical_backup('$CONFIG_NAME');" >> $LOG_FILE 2>&1
    ;;
  "physical"|"full")
    sudo -u postgres psql devnous -c "SELECT perform_physical_backup('$CONFIG_NAME');" >> $LOG_FILE 2>&1
    ;;
  *)
    echo "Unknown backup type: $BACKUP_TYPE" >> $LOG_FILE
    exit 1
    ;;
esac

echo "Backup completed at $(date)" >> $LOG_FILE

# Cleanup old logs (keep 30 days)
find /var/log/postgresql -name "backup_*.log" -mtime +30 -delete
```

### Backup Scheduling

```bash
# Add to crontab
0 2 * * * /usr/local/bin/devnous_backup.sh full daily_full_backup
0 */6 * * * /usr/local/bin/devnous_backup.sh physical hourly_incremental
0 4 * * 0 /usr/local/bin/devnous_backup.sh logical weekly_logical

# Backup validation
0 6 * * * postgres psql devnous -c "SELECT * FROM check_backup_health();"
```

## Monitoring and Maintenance

### Daily Health Checks

Create `/usr/local/bin/devnous_health_check.sh`:

```bash
#!/bin/bash
HEALTH_LOG="/var/log/postgresql/health_$(date +%Y%m%d).log"

echo "=== DevNous Health Check - $(date) ===" >> $HEALTH_LOG

# Database integrity
sudo -u postgres psql devnous -t -c "SELECT check_name, status, details FROM validate_database_integrity();" >> $HEALTH_LOG

# Connection pool health
sudo -u postgres psql devnous -t -c "SELECT pool_name, status, utilization_percent FROM get_pool_health_status();" >> $HEALTH_LOG

# Backup health
sudo -u postgres psql devnous -t -c "SELECT check_name, status, details FROM check_backup_health();" >> $HEALTH_LOG

# Partition health
sudo -u postgres psql devnous -t -c "SELECT table_family, total_partitions, recommendations FROM partition_health_check();" >> $HEALTH_LOG

# Disk space check
df -h /var/lib/postgresql >> $HEALTH_LOG
df -h /backups >> $HEALTH_LOG

echo "=== End Health Check ===" >> $HEALTH_LOG
```

### Automated Maintenance Tasks

```bash
# Daily partition maintenance
0 1 * * * postgres psql devnous -c "SELECT perform_partition_maintenance();"

# Weekly statistics update
0 2 * * 0 postgres psql devnous -c "ANALYZE;"

# Monthly cleanup
0 3 1 * * postgres psql devnous -c "SELECT cleanup_old_backups(); SELECT archive_old_audit_logs(24);"

# Connection leak detection
*/30 * * * * postgres psql devnous -c "SELECT session_id, duration_minutes FROM detect_connection_leaks(30);"
```

## Performance Tuning

### Index Maintenance

```sql
-- Monthly index analysis
SELECT 
    schemaname,
    tablename,
    indexname,
    pg_size_pretty(pg_total_relation_size(indexrelid)) as index_size,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes 
ORDER BY pg_total_relation_size(indexrelid) DESC;

-- Find unused indexes
SELECT 
    schemaname,
    tablename,
    indexname,
    pg_size_pretty(pg_total_relation_size(indexrelid)) as index_size
FROM pg_stat_user_indexes 
WHERE idx_scan = 0 
    AND pg_total_relation_size(indexrelid) > 1048576; -- > 1MB
```

### Query Performance Monitoring

```sql
-- Top slow queries
SELECT 
    query,
    calls,
    total_time,
    mean_time,
    rows
FROM pg_stat_statements 
WHERE calls > 100
ORDER BY mean_time DESC 
LIMIT 20;

-- Lock monitoring
SELECT 
    blocked_locks.pid AS blocked_pid,
    blocked_activity.usename AS blocked_user,
    blocking_locks.pid AS blocking_pid,
    blocking_activity.usename AS blocking_user,
    blocked_activity.query AS blocked_statement,
    blocking_activity.query AS current_statement_in_blocking_process
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
    AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
    AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
    AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
    AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
    AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
    AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
    AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
    AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
    AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
    AND blocking_locks.pid != blocked_locks.pid
JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;
```

## Disaster Recovery

### Recovery Time Objective (RTO): 4 hours
### Recovery Point Objective (RPO): 1 hour

### Disaster Recovery Procedures

1. **Assessment and Communication**
   ```bash
   # Get DR checklist
   sudo -u postgres psql devnous -c "SELECT create_dr_checklist();"
   ```

2. **Point-in-Time Recovery**
   ```sql
   -- Find recovery points
   SELECT * FROM list_recovery_points(7);
   
   -- Generate recovery plan
   SELECT simulate_point_in_time_recovery('2025-08-29 10:30:00'::timestamptz);
   ```

3. **Full Recovery Process**
   ```bash
   # Stop services
   sudo systemctl stop application_services
   sudo systemctl stop pgbouncer
   sudo systemctl stop postgresql
   
   # Backup current state
   sudo mv /var/lib/postgresql/14/main /var/lib/postgresql/14/main_damaged_$(date +%Y%m%d_%H%M%S)
   
   # Restore from backup
   sudo -u postgres pg_basebackup -D /var/lib/postgresql/14/main -Ft -z -P
   
   # Configure recovery
   sudo -u postgres cat > /var/lib/postgresql/14/main/recovery.conf << 'EOF'
   restore_command = 'cp /var/lib/postgresql/archive/%f %p'
   recovery_target_time = '2025-08-29 10:30:00'
   EOF
   
   # Start recovery
   sudo systemctl start postgresql
   
   # Monitor recovery
   sudo -u postgres tail -f /var/log/postgresql/postgresql-14-main.log
   ```

## Security Configuration

### SSL/TLS Configuration

```ini
# postgresql.conf
ssl = on
ssl_cert_file = '/etc/ssl/certs/postgresql.crt'
ssl_key_file = '/etc/ssl/private/postgresql.key'
ssl_ca_file = '/etc/ssl/certs/ca-certificates.crt'
ssl_prefer_server_ciphers = on
ssl_ciphers = 'ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384'
```

### User Management

```sql
-- Create monitoring user
CREATE USER monitoring WITH PASSWORD 'secure_monitoring_password';
GRANT CONNECT ON DATABASE devnous TO monitoring;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO monitoring;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO monitoring;

-- Create backup user
CREATE USER backup_user WITH REPLICATION PASSWORD 'secure_backup_password';

-- Audit user access
SELECT 
    usename,
    datname,
    COUNT(*),
    MAX(backend_start) as last_connection
FROM pg_stat_activity 
WHERE usename IS NOT NULL
GROUP BY usename, datname
ORDER BY last_connection DESC;
```

## Troubleshooting

### Common Issues and Solutions

#### High Connection Count
```sql
-- Check connections
SELECT count(*) as connection_count, state, usename 
FROM pg_stat_activity 
GROUP BY state, usename 
ORDER BY connection_count DESC;

-- Terminate idle connections
SELECT terminate_problematic_connections(30, 15, false);
```

#### Slow Queries
```sql
-- Current slow queries
SELECT pid, now() - pg_stat_activity.query_start AS duration, query 
FROM pg_stat_activity 
WHERE (now() - pg_stat_activity.query_start) > interval '5 minutes'
  AND state = 'active';

-- Kill slow query
SELECT pg_terminate_backend(pid);
```

#### Lock Issues
```sql
-- Check for locks
SELECT * FROM pg_locks WHERE NOT granted;

-- Detailed lock analysis
SELECT 
    locktype,
    database,
    relation::regclass,
    page,
    tuple,
    virtualxid,
    transactionid,
    mode,
    granted
FROM pg_locks;
```

#### Partition Issues
```sql
-- Check partition health
SELECT * FROM partition_health_check();

-- Create missing partitions
SELECT maintain_message_partitions();
SELECT maintain_audit_partitions();
```

### Emergency Contacts and Procedures

1. **Database Emergency**: Contact DBA team
2. **Security Incident**: Follow security incident response plan
3. **Data Corruption**: Immediate backup and recovery procedures
4. **Performance Issues**: Escalate to performance team

### Log Locations

- PostgreSQL logs: `/var/log/postgresql/postgresql-14-main.log`
- PgBouncer logs: `/var/log/pgbouncer/pgbouncer.log`
- Backup logs: `/var/log/postgresql/backup_*.log`
- Health check logs: `/var/log/postgresql/health_*.log`
- Application logs: Check application-specific log locations

---

This deployment guide provides a comprehensive foundation for operating the DevNous database system with high availability, performance, and reliability. Regular review and updates of these procedures are recommended as the system evolves.
