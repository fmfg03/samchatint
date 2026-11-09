"""
Evidence Bus Persistent Storage Layer
Replaces in-memory storage with PostgreSQL backend for production readiness
"""

import asyncio
import asyncpg
import json
import time
import uuid
import hashlib
import hmac
from typing import Dict, List, Any, Optional, Union
from datetime import datetime, timezone
import logging
from contextlib import asynccontextmanager
import os

logger = logging.getLogger(__name__)

class PersistentStorageError(Exception):
    """Custom exception for persistent storage operations"""
    pass

class DatabaseConnectionPool:
    """Manages PostgreSQL connection pool for Evidence Bus"""

    def __init__(self, database_url: str, min_connections: int = 5, max_connections: int = 50):
        self.database_url = database_url
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.pool = None

    async def initialize(self):
        """Initialize the connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=self.min_connections,
                max_size=self.max_connections,
                command_timeout=30,
                server_settings={
                    'application_name': 'evidence-bus',
                    'search_path': 'evidence_bus,public'
                }
            )
            logger.info(f"Database connection pool initialized: {self.min_connections}-{self.max_connections} connections")

            # Test connection
            async with self.pool.acquire() as conn:
                result = await conn.fetchval("SELECT 'connection_test'")
                logger.info(f"Database connection test: {result}")

        except Exception as e:
            logger.error(f"Failed to initialize database connection pool: {e}")
            raise PersistentStorageError(f"Database initialization failed: {e}")

    async def close(self):
        """Close the connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("Database connection pool closed")

    @asynccontextmanager
    async def get_connection(self):
        """Get a database connection from the pool"""
        if not self.pool:
            raise PersistentStorageError("Database pool not initialized")

        async with self.pool.acquire() as connection:
            yield connection

class PersistentEventStore:
    """Persistent storage implementation for Evidence Bus events"""

    def __init__(self, db_pool: DatabaseConnectionPool):
        self.db_pool = db_pool
        self._metrics_cache = {}
        self._last_metrics_flush = time.time()

    async def store_event(self, event_data: Dict[str, Any], verified: bool = False,
                         audit_trail_id: Optional[str] = None) -> str:
        """Store an event in persistent storage"""
        try:
            event_id = str(uuid.uuid4())

            async with self.db_pool.get_connection() as conn:
                # Insert event into main events table
                await conn.execute("""
                    INSERT INTO events (
                        event_id, timestamp, event_type, service_id, tenant_id,
                        trace_id, session_id, user_id, event_data, metadata,
                        signature, hmac_signature, verification_status,
                        audit_trail_id, ingestion_latency_ms
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                """,
                    event_id,
                    datetime.now(timezone.utc),
                    event_data.get('type', 'unknown'),
                    event_data.get('service_id', 'unknown'),
                    event_data.get('tenant', 'default'),
                    event_data.get('trace_id'),
                    event_data.get('session_id'),
                    event_data.get('user_id'),
                    json.dumps(event_data),
                    json.dumps(event_data.get('metadata', {})),
                    event_data.get('signature'),
                    event_data.get('eis_sig'),
                    'verified' if verified else 'pending',
                    audit_trail_id,
                    event_data.get('ingestion_latency_ms')
                )

                logger.debug(f"Event stored in persistent storage: {event_id}")
                return event_id

        except Exception as e:
            logger.error(f"Failed to store event: {e}")
            raise PersistentStorageError(f"Event storage failed: {e}")

    async def get_events(self, event_type: Optional[str] = None, tenant: Optional[str] = None,
                        limit: int = 100, verified_only: bool = True) -> List[Dict[str, Any]]:
        """Retrieve events from persistent storage with filtering"""
        try:
            query = """
                SELECT
                    event_id, timestamp, event_type, service_id, tenant_id,
                    trace_id, session_id, user_id, event_data, verification_status,
                    audit_trail_id, created_at
                FROM events
                WHERE ($1::text IS NULL OR event_type = $1)
                  AND ($2::text IS NULL OR tenant_id = $2)
                  AND ($3::boolean IS FALSE OR verification_status = 'verified')
                ORDER BY timestamp DESC
                LIMIT $4
            """

            async with self.db_pool.get_connection() as conn:
                rows = await conn.fetch(query, event_type, tenant, verified_only, limit)

                events = []
                for row in rows:
                    event = {
                        "received_at": row['created_at'].timestamp(),
                        "verified": row['verification_status'] == 'verified',
                        "event": {
                            "event_id": str(row['event_id']),
                            "type": row['event_type'],
                            "service_id": row['service_id'],
                            "tenant": row['tenant_id'],
                            "timestamp": row['timestamp'].isoformat(),
                            **json.loads(row['event_data'])
                        }
                    }
                    if row['audit_trail_id']:
                        event["audit_trail_id"] = row['audit_trail_id']
                    events.append(event)

                logger.debug(f"Retrieved {len(events)} events from persistent storage")
                return events

        except Exception as e:
            logger.error(f"Failed to retrieve events: {e}")
            raise PersistentStorageError(f"Event retrieval failed: {e}")

    async def get_canary_trips(self, tenant: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent canary trips for investigation"""
        try:
            query = """
                SELECT
                    timestamp, event_data, tenant_id, created_at
                FROM events
                WHERE event_type = 'canary.trip.v1'
                  AND verification_status = 'verified'
                  AND ($1::text IS NULL OR tenant_id = $1)
                ORDER BY timestamp DESC
                LIMIT $2
            """

            async with self.db_pool.get_connection() as conn:
                rows = await conn.fetch(query, tenant, limit)

                trips = []
                for row in rows:
                    event_data = json.loads(row['event_data'])
                    trip = {
                        "received_at": row['created_at'].timestamp(),
                        "tenant": row['tenant_id'],
                        "route": event_data.get('route'),
                        "valid": event_data.get('valid'),
                        "meta": event_data.get('meta'),
                        "sample": event_data.get('sample', '')[:100] + "..." if len(event_data.get('sample', '')) > 100 else event_data.get('sample', '')
                    }
                    trips.append(trip)

                return trips

        except Exception as e:
            logger.error(f"Failed to retrieve canary trips: {e}")
            raise PersistentStorageError(f"Canary trip retrieval failed: {e}")

    async def update_metric(self, metric_key: str, increment: int = 1,
                          service_id: Optional[str] = None) -> int:
        """Update real-time metrics in persistent storage"""
        try:
            async with self.db_pool.get_connection() as conn:
                result = await conn.fetchval(
                    "SELECT update_realtime_metric($1, $2, $3)",
                    metric_key, increment, service_id
                )
                return result
        except Exception as e:
            logger.error(f"Failed to update metric {metric_key}: {e}")
            raise PersistentStorageError(f"Metric update failed: {e}")

    async def get_metrics(self) -> Dict[str, int]:
        """Get current metrics from persistent storage"""
        try:
            async with self.db_pool.get_connection() as conn:
                rows = await conn.fetch("SELECT * FROM get_realtime_metrics()")

                metrics = {}
                for row in rows:
                    metrics[row['metric_key']] = row['metric_value']

                # Ensure all expected metrics exist
                expected_metrics = [
                    'events_received', 'events_verified', 'events_failed',
                    'canary_trips', 'canary_seeds'
                ]
                for metric in expected_metrics:
                    if metric not in metrics:
                        metrics[metric] = 0

                return metrics

        except Exception as e:
            logger.error(f"Failed to retrieve metrics: {e}")
            raise PersistentStorageError(f"Metrics retrieval failed: {e}")

    async def clear_events(self) -> bool:
        """Clear all events from storage (admin operation)"""
        try:
            async with self.db_pool.get_connection() as conn:
                async with conn.transaction():
                    # Clear events
                    await conn.execute("TRUNCATE TABLE events CASCADE")

                    # Reset metrics
                    await conn.execute("TRUNCATE TABLE realtime_metrics")

                    # Reset verification queue
                    await conn.execute("TRUNCATE TABLE event_verification_queue")

                    logger.warning("All events cleared from persistent storage")
                    return True

        except Exception as e:
            logger.error(f"Failed to clear events: {e}")
            raise PersistentStorageError(f"Event clearing failed: {e}")

    async def get_health_status(self) -> Dict[str, Any]:
        """Get system health status from persistent storage"""
        try:
            async with self.db_pool.get_connection() as conn:
                # Get basic statistics
                stats = await conn.fetchrow("""
                    SELECT
                        COUNT(*) as total_events,
                        COUNT(DISTINCT service_id) as active_services,
                        COUNT(DISTINCT tenant_id) as active_tenants,
                        AVG(ingestion_latency_ms) as avg_latency_ms,
                        COUNT(*) FILTER (WHERE verification_status = 'failed') as failed_verifications,
                        (COUNT(*) FILTER (WHERE verification_status = 'verified')::DECIMAL / NULLIF(COUNT(*), 0) * 100) as verification_rate_percent
                    FROM events
                    WHERE timestamp > NOW() - INTERVAL '1 hour'
                """)

                return {
                    "status": "healthy",
                    "service": "evidence-bus",
                    "timestamp": time.time(),
                    "total_events_last_hour": stats['total_events'],
                    "active_services": stats['active_services'],
                    "active_tenants": stats['active_tenants'],
                    "avg_latency_ms": float(stats['avg_latency_ms']) if stats['avg_latency_ms'] else 0,
                    "failed_verifications": stats['failed_verifications'],
                    "verification_rate_percent": float(stats['verification_rate_percent']) if stats['verification_rate_percent'] else 0
                }

        except Exception as e:
            logger.error(f"Failed to get health status: {e}")
            return {
                "status": "unhealthy",
                "service": "evidence-bus",
                "timestamp": time.time(),
                "error": str(e)
            }

class PersistentAegisProcessor:
    """Persistent storage for AEGIS audit trails"""

    def __init__(self, db_pool: DatabaseConnectionPool):
        self.db_pool = db_pool

    async def store_audit_trail(self, trajectory_id: str, model_version: str,
                               inference_path: str, blamed_agents: List[str],
                               error_modes: List[str], confidences: Dict[str, float] = None,
                               human_reviewable: bool = False,
                               calibration_validated: bool = False,
                               inference_time_ms: Optional[int] = None) -> str:
        """Store AEGIS audit trail in persistent storage"""
        try:
            async with self.db_pool.get_connection() as conn:
                attribution_id = await conn.fetchval(
                    "SELECT store_aegis_audit_trail($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                    trajectory_id, model_version, inference_path,
                    json.dumps(blamed_agents), json.dumps(error_modes),
                    json.dumps(confidences or {}), human_reviewable,
                    calibration_validated, inference_time_ms
                )

                logger.info(f"AEGIS audit trail stored: {attribution_id}")
                return attribution_id

        except Exception as e:
            logger.error(f"Failed to store AEGIS audit trail: {e}")
            raise PersistentStorageError(f"AEGIS audit trail storage failed: {e}")

    async def get_audit_trail(self, trajectory_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve AEGIS audit trail by trajectory ID"""
        try:
            async with self.db_pool.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM get_aegis_audit_trail($1)",
                    trajectory_id
                )

                if row:
                    return {
                        "attribution_id": row['attribution_id'],
                        "model_version": row['model_version'],
                        "inference_path": row['inference_path'],
                        "blamed_agents": json.loads(row['blamed_agents']),
                        "error_modes": json.loads(row['error_modes']),
                        "confidences": json.loads(row['confidences']),
                        "decision_timestamp": row['decision_timestamp'].isoformat(),
                        "human_reviewable": row['human_reviewable'],
                        "calibration_validated": row['calibration_validated']
                    }

                return None

        except Exception as e:
            logger.error(f"Failed to retrieve AEGIS audit trail: {e}")
            raise PersistentStorageError(f"AEGIS audit trail retrieval failed: {e}")

    async def get_statistics(self) -> Dict[str, Any]:
        """Get AEGIS statistics from persistent storage"""
        try:
            async with self.db_pool.get_connection() as conn:
                stats = await conn.fetchrow("SELECT * FROM get_aegis_statistics()")

                return {
                    "total_audit_trails": stats['total_audit_trails'],
                    "recent_trails_24h": stats['recent_trails_24h'],
                    "avg_inference_time_ms": float(stats['avg_inference_time_ms']) if stats['avg_inference_time_ms'] else 0,
                    "human_reviewable_count": stats['human_reviewable_count'],
                    "calibration_validated_count": stats['calibration_validated_count'],
                    "fast_path_percentage": float(stats['fast_path_percentage']) if stats['fast_path_percentage'] else 0,
                    "slow_path_percentage": float(stats['slow_path_percentage']) if stats['slow_path_percentage'] else 0,
                    "supported_event_types": [
                        "aegis_trajectory_replayed",
                        "aegis_trajectory_manipulated",
                        "aegis_dataset_sample_generated",
                        "aegis_dataset_generation_completed",
                        "aegis_attribution_inference_completed",
                        "aegis_training_phase_completed",
                        "aegis_model_calibrated",
                        "aegis_promotion_gate_evaluated"
                    ],
                    "processor_version": "2.0.0"
                }

        except Exception as e:
            logger.error(f"Failed to get AEGIS statistics: {e}")
            raise PersistentStorageError(f"AEGIS statistics retrieval failed: {e}")

# Factory function to create storage instances
async def create_persistent_storage(database_url: Optional[str] = None) -> tuple[PersistentEventStore, PersistentAegisProcessor]:
    """Create persistent storage instances with initialized connection pool"""

    if not database_url:
        database_url = (os.getenv("EVIDENCE_BUS_DATABASE_URL") or "").strip()
    if not database_url:
        raise PersistentStorageError("EVIDENCE_BUS_DATABASE_URL is required for persistent storage")

    # Initialize database connection pool
    db_pool = DatabaseConnectionPool(database_url)
    await db_pool.initialize()

    # Create storage instances
    event_store = PersistentEventStore(db_pool)
    aegis_processor = PersistentAegisProcessor(db_pool)

    logger.info("Persistent storage initialized successfully")
    return event_store, aegis_processor

# Global storage instances (initialized in main.py)
persistent_event_store = None
persistent_aegis_processor = None
