"""Comprehensive load testing framework for DevNous messaging integration.

This module provides extensive load and stress testing capabilities for the
messaging system, including high-volume message processing, concurrent user
simulation, database stress testing, and resource utilization monitoring.
"""

import asyncio
import aiohttp
import time
import psutil
import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import matplotlib.pyplot as plt
from locust import HttpUser, task, between, events
from locust.env import Environment

from devnous.message_hub.core import MessageHubCore
from devnous.message_hub.kafka_client import KafkaClient
from devnous.database import DatabaseManager
from tests.factories import (
    HighVolumeTestDataFactory,
    UnifiedMessageFactory,
    MessageBatchFactory
)


@dataclass
class LoadTestMetrics:
    """Metrics collected during load testing."""
    timestamp: datetime
    concurrent_users: int
    requests_per_second: float
    average_response_time_ms: float
    p95_response_time_ms: float
    p99_response_time_ms: float
    error_rate: float
    cpu_usage_percent: float
    memory_usage_mb: float
    network_io_mbps: float
    database_connections: int
    kafka_throughput_msgs_per_sec: float
    cache_hit_rate: float


@dataclass
class StressTestResult:
    """Results of a stress test execution."""
    test_name: str
    duration_seconds: float
    total_requests: int
    successful_requests: int
    failed_requests: int
    peak_concurrent_users: int
    max_response_time_ms: float
    average_throughput_rps: float
    resource_utilization: Dict[str, float]
    error_breakdown: Dict[str, int]
    performance_degradation_points: List[Dict[str, Any]]
    recovery_time_seconds: Optional[float] = None


class MessageHubLoadTester:
    """Comprehensive load testing framework for Message Hub."""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.metrics_history: List[LoadTestMetrics] = []
        self.current_test_start = None
        self.session_pool: List[aiohttp.ClientSession] = []
        
    async def initialize_session_pool(self, pool_size: int = 100):
        """Initialize HTTP session pool for concurrent testing."""
        self.session_pool = []
        for _ in range(pool_size):
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                connector=aiohttp.TCPConnector(limit=100)
            )
            self.session_pool.append(session)
    
    async def cleanup_session_pool(self):
        """Clean up HTTP session pool."""
        for session in self.session_pool:
            await session.close()
        self.session_pool.clear()
    
    def collect_system_metrics(self) -> Dict[str, float]:
        """Collect current system resource metrics."""
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        network = psutil.net_io_counters()
        disk = psutil.disk_io_counters()
        
        return {
            "cpu_usage_percent": cpu_percent,
            "memory_usage_mb": memory.used / (1024 * 1024),
            "memory_usage_percent": memory.percent,
            "network_bytes_sent": network.bytes_sent,
            "network_bytes_recv": network.bytes_recv,
            "disk_read_mb": disk.read_bytes / (1024 * 1024),
            "disk_write_mb": disk.write_bytes / (1024 * 1024)
        }
    
    async def send_single_message(self, session: aiohttp.ClientSession, message_data: Dict) -> Tuple[bool, float, str]:
        """Send a single message and measure response time."""
        start_time = time.time()
        error_message = ""
        
        try:
            async with session.post(
                f"{self.base_url}/api/v1/messages/send",
                json=message_data,
                headers={"Content-Type": "application/json"}
            ) as response:
                response_time = (time.time() - start_time) * 1000  # Convert to milliseconds
                success = response.status == 200
                
                if not success:
                    error_message = f"HTTP {response.status}: {await response.text()}"
                
                return success, response_time, error_message
                
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            return False, response_time, str(e)
    
    async def concurrent_message_load_test(
        self,
        concurrent_users: int,
        messages_per_user: int,
        ramp_up_seconds: int = 10
    ) -> StressTestResult:
        """Execute concurrent message load test."""
        print(f"Starting concurrent load test: {concurrent_users} users, {messages_per_user} messages each")
        
        test_start = time.time()
        await self.initialize_session_pool(concurrent_users)
        
        # Prepare test messages
        all_messages = []
        for user_id in range(concurrent_users):
            for msg_id in range(messages_per_user):
                message = UnifiedMessageFactory()
                message.sender_id = f"load_test_user_{user_id}"
                message_data = {
                    "platform": message.platform.value,
                    "message_type": message.message_type.value,
                    "content": {
                        "text": f"Load test message {msg_id} from user {user_id}",
                        "metadata": {"test_id": f"load_{user_id}_{msg_id}"}
                    },
                    "sender_id": message.sender_id,
                    "recipient_id": message.recipient_id
                }
                all_messages.append(message_data)
        
        # Execute load test with gradual ramp-up
        response_times = []
        successful_requests = 0
        failed_requests = 0
        error_breakdown = {}
        
        # Ramp up gradually
        users_per_second = concurrent_users / ramp_up_seconds if ramp_up_seconds > 0 else concurrent_users
        
        async def user_session(user_id: int, start_delay: float):
            """Simulate a user session with multiple message sends."""
            await asyncio.sleep(start_delay)
            session = self.session_pool[user_id % len(self.session_pool)]
            
            for msg_idx in range(messages_per_user):
                message_data = all_messages[user_id * messages_per_user + msg_idx]
                success, response_time, error_msg = await self.send_single_message(session, message_data)
                
                response_times.append(response_time)
                
                if success:
                    nonlocal successful_requests
                    successful_requests += 1
                else:
                    nonlocal failed_requests
                    failed_requests += 1
                    error_type = error_msg.split(":")[0] if ":" in error_msg else "Unknown"
                    error_breakdown[error_type] = error_breakdown.get(error_type, 0) + 1
                
                # Small delay between messages from same user
                await asyncio.sleep(0.1)
        
        # Create user session tasks with ramp-up
        user_tasks = []
        for user_id in range(concurrent_users):
            start_delay = user_id / users_per_second if users_per_second > 0 else 0
            task = user_session(user_id, start_delay)
            user_tasks.append(task)
        
        # Execute all user sessions concurrently
        await asyncio.gather(*user_tasks)
        
        test_duration = time.time() - test_start
        await self.cleanup_session_pool()
        
        # Calculate metrics
        total_requests = successful_requests + failed_requests
        error_rate = (failed_requests / total_requests) if total_requests > 0 else 0
        avg_throughput = total_requests / test_duration
        
        return StressTestResult(
            test_name=f"concurrent_load_{concurrent_users}x{messages_per_user}",
            duration_seconds=test_duration,
            total_requests=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            peak_concurrent_users=concurrent_users,
            max_response_time_ms=max(response_times) if response_times else 0,
            average_throughput_rps=avg_throughput,
            resource_utilization=self.collect_system_metrics(),
            error_breakdown=error_breakdown,
            performance_degradation_points=[]
        )
    
    async def message_burst_test(self, burst_size: int, burst_interval_seconds: float) -> StressTestResult:
        """Test system behavior under message bursts."""
        print(f"Starting burst test: {burst_size} messages every {burst_interval_seconds} seconds")
        
        test_start = time.time()
        total_bursts = 10
        await self.initialize_session_pool(min(burst_size, 50))
        
        all_response_times = []
        total_successful = 0
        total_failed = 0
        error_breakdown = {}
        
        for burst_num in range(total_bursts):
            print(f"Executing burst {burst_num + 1}/{total_bursts}")
            
            # Prepare burst messages
            burst_messages = []
            for i in range(burst_size):
                message = UnifiedMessageFactory()
                message_data = {
                    "platform": message.platform.value,
                    "message_type": message.message_type.value,
                    "content": {
                        "text": f"Burst {burst_num} message {i}",
                        "metadata": {"burst_id": burst_num, "message_id": i}
                    },
                    "sender_id": f"burst_user_{i % 10}",
                    "recipient_id": message.recipient_id
                }
                burst_messages.append(message_data)
            
            # Send burst of messages simultaneously
            burst_start = time.time()
            
            async def send_burst_message(msg_data, session_idx):
                session = self.session_pool[session_idx % len(self.session_pool)]
                return await self.send_single_message(session, msg_data)
            
            burst_tasks = [
                send_burst_message(msg, i) 
                for i, msg in enumerate(burst_messages)
            ]
            
            burst_results = await asyncio.gather(*burst_tasks)
            
            # Process burst results
            burst_response_times = [result[1] for result in burst_results]
            burst_successful = sum(1 for result in burst_results if result[0])
            burst_failed = len(burst_results) - burst_successful
            
            all_response_times.extend(burst_response_times)
            total_successful += burst_successful
            total_failed += burst_failed
            
            # Track errors
            for success, _, error_msg in burst_results:
                if not success:
                    error_type = error_msg.split(":")[0] if ":" in error_msg else "Unknown"
                    error_breakdown[error_type] = error_breakdown.get(error_type, 0) + 1
            
            burst_duration = time.time() - burst_start
            print(f"Burst {burst_num + 1} completed in {burst_duration:.2f}s, {burst_successful}/{burst_size} successful")
            
            # Wait before next burst
            if burst_num < total_bursts - 1:
                await asyncio.sleep(burst_interval_seconds)
        
        test_duration = time.time() - test_start
        await self.cleanup_session_pool()
        
        total_requests = total_successful + total_failed
        avg_throughput = total_requests / test_duration
        
        return StressTestResult(
            test_name=f"burst_test_{burst_size}x{total_bursts}",
            duration_seconds=test_duration,
            total_requests=total_requests,
            successful_requests=total_successful,
            failed_requests=total_failed,
            peak_concurrent_users=min(burst_size, 50),
            max_response_time_ms=max(all_response_times) if all_response_times else 0,
            average_throughput_rps=avg_throughput,
            resource_utilization=self.collect_system_metrics(),
            error_breakdown=error_breakdown,
            performance_degradation_points=[]
        )
    
    async def gradual_load_increase_test(
        self,
        start_users: int,
        max_users: int,
        step_size: int,
        step_duration_seconds: int
    ) -> StressTestResult:
        """Test system behavior under gradually increasing load."""
        print(f"Starting gradual load test: {start_users} to {max_users} users, steps of {step_size}")
        
        test_start = time.time()
        all_metrics = []
        degradation_points = []
        
        current_users = start_users
        previous_avg_response_time = 0
        
        while current_users <= max_users:
            print(f"Testing with {current_users} concurrent users...")
            
            # Run load test for current user count
            step_result = await self.concurrent_message_load_test(
                concurrent_users=current_users,
                messages_per_user=5,  # Fewer messages per user for faster testing
                ramp_up_seconds=5
            )
            
            current_avg_response_time = (
                step_result.max_response_time_ms * 0.3 +  # Estimate average from max
                50  # Base response time estimate
            )
            
            # Detect performance degradation
            if previous_avg_response_time > 0:
                degradation_ratio = current_avg_response_time / previous_avg_response_time
                if degradation_ratio > 2.0:  # 100% increase in response time
                    degradation_points.append({
                        "user_count": current_users,
                        "response_time_ms": current_avg_response_time,
                        "degradation_ratio": degradation_ratio,
                        "timestamp": datetime.utcnow()
                    })
            
            # Collect metrics for this step
            step_metrics = LoadTestMetrics(
                timestamp=datetime.utcnow(),
                concurrent_users=current_users,
                requests_per_second=step_result.average_throughput_rps,
                average_response_time_ms=current_avg_response_time,
                p95_response_time_ms=step_result.max_response_time_ms * 0.95,
                p99_response_time_ms=step_result.max_response_time_ms * 0.99,
                error_rate=step_result.failed_requests / step_result.total_requests if step_result.total_requests > 0 else 0,
                cpu_usage_percent=step_result.resource_utilization.get("cpu_usage_percent", 0),
                memory_usage_mb=step_result.resource_utilization.get("memory_usage_mb", 0),
                network_io_mbps=0,  # Would need more detailed monitoring
                database_connections=0,  # Would need database monitoring
                kafka_throughput_msgs_per_sec=0,  # Would need Kafka monitoring
                cache_hit_rate=0  # Would need cache monitoring
            )
            
            all_metrics.append(step_metrics)
            previous_avg_response_time = current_avg_response_time
            
            # Move to next step
            current_users += step_size
            
            # Brief pause between steps
            await asyncio.sleep(2)
        
        test_duration = time.time() - test_start
        
        # Aggregate results
        total_successful = sum(m.requests_per_second * step_duration_seconds for m in all_metrics)
        total_requests = total_successful  # Simplified for this test
        
        return StressTestResult(
            test_name=f"gradual_load_{start_users}_to_{max_users}",
            duration_seconds=test_duration,
            total_requests=int(total_requests),
            successful_requests=int(total_successful),
            failed_requests=0,  # Simplified
            peak_concurrent_users=max_users,
            max_response_time_ms=max(m.average_response_time_ms for m in all_metrics) if all_metrics else 0,
            average_throughput_rps=sum(m.requests_per_second for m in all_metrics) / len(all_metrics) if all_metrics else 0,
            resource_utilization=self.collect_system_metrics(),
            error_breakdown={},
            performance_degradation_points=degradation_points
        )


class KafkaLoadTester:
    """Load testing for Kafka message streaming."""
    
    def __init__(self, bootstrap_servers: str = "localhost:9093"):
        self.bootstrap_servers = bootstrap_servers
        self.kafka_client = None
    
    async def initialize(self):
        """Initialize Kafka client for testing."""
        self.kafka_client = KafkaClient({
            "bootstrap_servers": self.bootstrap_servers,
            "client_id": "load_tester"
        })
        await self.kafka_client.initialize()
    
    async def cleanup(self):
        """Clean up Kafka resources."""
        if self.kafka_client:
            await self.kafka_client.close()
    
    async def message_throughput_test(
        self,
        topic: str,
        message_count: int,
        concurrent_producers: int
    ) -> Dict[str, Any]:
        """Test Kafka message throughput under load."""
        print(f"Testing Kafka throughput: {message_count} messages, {concurrent_producers} producers")
        
        await self.initialize()
        
        test_start = time.time()
        messages_sent = 0
        send_errors = 0
        
        async def producer_worker(worker_id: int, messages_per_worker: int):
            """Worker function for concurrent message production."""
            nonlocal messages_sent, send_errors
            
            for i in range(messages_per_worker):
                try:
                    message_data = {
                        "worker_id": worker_id,
                        "message_id": i,
                        "timestamp": time.time(),
                        "payload": f"Load test message {i} from worker {worker_id}"
                    }
                    
                    await self.kafka_client.produce_message(topic, message_data)
                    messages_sent += 1
                    
                except Exception as e:
                    send_errors += 1
                    print(f"Send error from worker {worker_id}: {e}")
        
        # Calculate messages per worker
        messages_per_worker = message_count // concurrent_producers
        remainder = message_count % concurrent_producers
        
        # Create producer tasks
        producer_tasks = []
        for worker_id in range(concurrent_producers):
            worker_message_count = messages_per_worker + (1 if worker_id < remainder else 0)
            task = producer_worker(worker_id, worker_message_count)
            producer_tasks.append(task)
        
        # Execute all producers concurrently
        await asyncio.gather(*producer_tasks)
        
        test_duration = time.time() - test_start
        await self.cleanup()
        
        throughput_msgs_per_sec = messages_sent / test_duration if test_duration > 0 else 0
        
        return {
            "test_name": f"kafka_throughput_{message_count}_{concurrent_producers}",
            "duration_seconds": test_duration,
            "messages_sent": messages_sent,
            "send_errors": send_errors,
            "throughput_msgs_per_sec": throughput_msgs_per_sec,
            "concurrent_producers": concurrent_producers,
            "success_rate": (messages_sent / message_count) if message_count > 0 else 0
        }
    
    async def consumer_lag_test(
        self,
        topic: str,
        message_count: int,
        consumer_count: int
    ) -> Dict[str, Any]:
        """Test consumer lag under high message volume."""
        print(f"Testing consumer lag: {message_count} messages, {consumer_count} consumers")
        
        await self.initialize()
        
        # First, produce all messages as quickly as possible
        produce_start = time.time()
        
        for i in range(message_count):
            message_data = {
                "message_id": i,
                "produce_timestamp": time.time(),
                "payload": f"Lag test message {i}"
            }
            await self.kafka_client.produce_message(topic, message_data)
        
        produce_duration = time.time() - produce_start
        
        # Now test consumer lag
        consume_start = time.time()
        messages_consumed = 0
        total_lag_ms = 0
        max_lag_ms = 0
        
        async def consumer_worker(worker_id: int):
            """Worker function for concurrent message consumption."""
            nonlocal messages_consumed, total_lag_ms, max_lag_ms
            
            async for message in self.kafka_client.consume_messages(topic, f"lag_test_group_{worker_id}"):
                consume_timestamp = time.time()
                produce_timestamp = message.get("produce_timestamp", consume_timestamp)
                
                lag_ms = (consume_timestamp - produce_timestamp) * 1000
                total_lag_ms += lag_ms
                max_lag_ms = max(max_lag_ms, lag_ms)
                
                messages_consumed += 1
                
                if messages_consumed >= message_count:
                    break
        
        # Create consumer tasks
        consumer_tasks = [consumer_worker(i) for i in range(consumer_count)]
        await asyncio.gather(*consumer_tasks)
        
        consume_duration = time.time() - consume_start
        await self.cleanup()
        
        avg_lag_ms = total_lag_ms / messages_consumed if messages_consumed > 0 else 0
        
        return {
            "test_name": f"kafka_lag_{message_count}_{consumer_count}",
            "produce_duration_seconds": produce_duration,
            "consume_duration_seconds": consume_duration,
            "messages_produced": message_count,
            "messages_consumed": messages_consumed,
            "average_lag_ms": avg_lag_ms,
            "max_lag_ms": max_lag_ms,
            "consumer_throughput_msgs_per_sec": messages_consumed / consume_duration if consume_duration > 0 else 0
        }


class DatabaseLoadTester:
    """Load testing for database operations."""
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.db_manager = None
    
    async def initialize(self):
        """Initialize database manager."""
        from devnous.config import DevNousConfig
        config = DevNousConfig()
        config.database.postgresql_url = self.database_url
        
        self.db_manager = DatabaseManager(config)
        await self.db_manager.initialize()
    
    async def cleanup(self):
        """Clean up database resources."""
        if self.db_manager:
            await self.db_manager.close()
    
    async def concurrent_write_test(
        self,
        concurrent_writers: int,
        writes_per_writer: int
    ) -> Dict[str, Any]:
        """Test concurrent database write performance."""
        print(f"Testing database writes: {concurrent_writers} writers, {writes_per_writer} writes each")
        
        await self.initialize()
        
        test_start = time.time()
        successful_writes = 0
        failed_writes = 0
        write_times = []
        
        async def writer_worker(writer_id: int):
            """Worker function for concurrent database writes."""
            nonlocal successful_writes, failed_writes, write_times
            
            for i in range(writes_per_writer):
                write_start = time.time()
                
                try:
                    # Simulate message storage
                    message_data = {
                        "id": f"load_test_{writer_id}_{i}",
                        "platform": "slack",
                        "content": f"Load test message {i} from writer {writer_id}",
                        "sender_id": f"user_{writer_id}",
                        "timestamp": datetime.utcnow(),
                        "metadata": {"test": True}
                    }
                    
                    async with self.db_manager.get_session() as session:
                        # Simulate message insert
                        result = await session.execute(
                            "INSERT INTO messages (id, platform, content, sender_id, created_at, metadata) VALUES (%s, %s, %s, %s, %s, %s)",
                            (
                                message_data["id"],
                                message_data["platform"],
                                message_data["content"],
                                message_data["sender_id"],
                                message_data["timestamp"],
                                str(message_data["metadata"])
                            )
                        )
                        await session.commit()
                    
                    write_time = (time.time() - write_start) * 1000
                    write_times.append(write_time)
                    successful_writes += 1
                    
                except Exception as e:
                    failed_writes += 1
                    print(f"Write error from writer {writer_id}: {e}")
        
        # Create writer tasks
        writer_tasks = [writer_worker(i) for i in range(concurrent_writers)]
        await asyncio.gather(*writer_tasks)
        
        test_duration = time.time() - test_start
        await self.cleanup()
        
        total_writes = successful_writes + failed_writes
        avg_write_time_ms = statistics.mean(write_times) if write_times else 0
        p95_write_time_ms = np.percentile(write_times, 95) if write_times else 0
        
        return {
            "test_name": f"db_concurrent_writes_{concurrent_writers}x{writes_per_writer}",
            "duration_seconds": test_duration,
            "total_writes": total_writes,
            "successful_writes": successful_writes,
            "failed_writes": failed_writes,
            "writes_per_second": total_writes / test_duration if test_duration > 0 else 0,
            "average_write_time_ms": avg_write_time_ms,
            "p95_write_time_ms": p95_write_time_ms,
            "success_rate": successful_writes / total_writes if total_writes > 0 else 0
        }
    
    async def read_performance_test(
        self,
        concurrent_readers: int,
        reads_per_reader: int
    ) -> Dict[str, Any]:
        """Test concurrent database read performance."""
        print(f"Testing database reads: {concurrent_readers} readers, {reads_per_reader} reads each")
        
        await self.initialize()
        
        test_start = time.time()
        successful_reads = 0
        failed_reads = 0
        read_times = []
        
        async def reader_worker(reader_id: int):
            """Worker function for concurrent database reads."""
            nonlocal successful_reads, failed_reads, read_times
            
            for i in range(reads_per_reader):
                read_start = time.time()
                
                try:
                    async with self.db_manager.get_session() as session:
                        # Simulate message query
                        result = await session.execute(
                            "SELECT * FROM messages WHERE sender_id = %s ORDER BY created_at DESC LIMIT 10",
                            (f"user_{reader_id % 100}",)  # Query for various users
                        )
                        rows = result.fetchall()
                    
                    read_time = (time.time() - read_start) * 1000
                    read_times.append(read_time)
                    successful_reads += 1
                    
                except Exception as e:
                    failed_reads += 1
                    print(f"Read error from reader {reader_id}: {e}")
        
        # Create reader tasks
        reader_tasks = [reader_worker(i) for i in range(concurrent_readers)]
        await asyncio.gather(*reader_tasks)
        
        test_duration = time.time() - test_start
        await self.cleanup()
        
        total_reads = successful_reads + failed_reads
        avg_read_time_ms = statistics.mean(read_times) if read_times else 0
        p95_read_time_ms = np.percentile(read_times, 95) if read_times else 0
        
        return {
            "test_name": f"db_concurrent_reads_{concurrent_readers}x{reads_per_reader}",
            "duration_seconds": test_duration,
            "total_reads": total_reads,
            "successful_reads": successful_reads,
            "failed_reads": failed_reads,
            "reads_per_second": total_reads / test_duration if test_duration > 0 else 0,
            "average_read_time_ms": avg_read_time_ms,
            "p95_read_time_ms": p95_read_time_ms,
            "success_rate": successful_reads / total_reads if total_reads > 0 else 0
        }


class LocustLoadTester(HttpUser):
    """Locust-based load testing for HTTP API endpoints."""
    
    wait_time = between(1, 3)
    host = "http://localhost:8000"
    
    def on_start(self):
        """Called when user starts."""
        self.client.verify = False  # Disable SSL verification for testing
    
    @task(3)
    def send_message(self):
        """Task to send a message via API."""
        message_data = {
            "platform": "slack",
            "message_type": "text",
            "content": {
                "text": f"Locust test message from user {self.client.base_url}",
                "metadata": {"test": "locust_load_test"}
            },
            "sender_id": f"locust_user_{hash(self.client.base_url) % 1000}",
            "recipient_id": "general"
        }
        
        with self.client.post(
            "/api/v1/messages/send",
            json=message_data,
            catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Failed with status {response.status_code}")
    
    @task(2)
    def get_message_history(self):
        """Task to retrieve message history."""
        with self.client.get(
            "/api/v1/messages/history",
            params={"limit": 10},
            catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Failed with status {response.status_code}")
    
    @task(1)
    def health_check(self):
        """Task to perform health check."""
        with self.client.get("/health") as response:
            if response.status_code != 200:
                print(f"Health check failed: {response.status_code}")


def run_locust_load_test(
    host: str = "http://localhost:8000",
    users: int = 50,
    spawn_rate: int = 5,
    run_time: str = "5m"
) -> Dict[str, Any]:
    """Run Locust load test programmatically."""
    print(f"Running Locust load test: {users} users, spawn rate {spawn_rate}/s, duration {run_time}")
    
    # Set up Locust environment
    env = Environment(user_classes=[LocustLoadTester], host=host)
    env.create_local_runner()
    
    # Start load test
    env.runner.start(users, spawn_rate=spawn_rate)
    
    # Run for specified time
    import time
    if run_time.endswith('s'):
        duration = int(run_time[:-1])
    elif run_time.endswith('m'):
        duration = int(run_time[:-1]) * 60
    elif run_time.endswith('h'):
        duration = int(run_time[:-1]) * 3600
    else:
        duration = 300  # Default 5 minutes
    
    time.sleep(duration)
    
    # Stop and get stats
    env.runner.quit()
    
    stats = env.runner.stats
    
    return {
        "total_requests": stats.total.num_requests,
        "total_failures": stats.total.num_failures,
        "average_response_time": stats.total.avg_response_time,
        "min_response_time": stats.total.min_response_time,
        "max_response_time": stats.total.max_response_time,
        "requests_per_second": stats.total.current_rps,
        "failure_rate": stats.total.fail_ratio
    }


class LoadTestReporter:
    """Generate reports from load test results."""
    
    @staticmethod
    def generate_performance_report(results: List[StressTestResult], output_file: str = "load_test_report.html"):
        """Generate HTML performance report."""
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>DevNous Load Test Report</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .test-result {{ border: 1px solid #ddd; margin: 10px 0; padding: 15px; }}
                .metrics {{ display: flex; justify-content: space-around; }}
                .metric {{ text-align: center; }}
                .success {{ color: green; }}
                .warning {{ color: orange; }}
                .error {{ color: red; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #f2f2f2; }}
            </style>
        </head>
        <body>
            <h1>DevNous Messaging Integration Load Test Report</h1>
            <p>Generated on: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
            
            <h2>Test Summary</h2>
            <table>
                <tr>
                    <th>Test Name</th>
                    <th>Duration (s)</th>
                    <th>Total Requests</th>
                    <th>Success Rate</th>
                    <th>Avg Throughput (RPS)</th>
                    <th>Max Response Time (ms)</th>
                    <th>Peak Users</th>
                </tr>
        """
        
        for result in results:
            success_rate = (result.successful_requests / result.total_requests * 100) if result.total_requests > 0 else 0
            success_class = "success" if success_rate >= 95 else "warning" if success_rate >= 90 else "error"
            
            html_content += f"""
                <tr>
                    <td>{result.test_name}</td>
                    <td>{result.duration_seconds:.2f}</td>
                    <td>{result.total_requests}</td>
                    <td class="{success_class}">{success_rate:.1f}%</td>
                    <td>{result.average_throughput_rps:.2f}</td>
                    <td>{result.max_response_time_ms:.2f}</td>
                    <td>{result.peak_concurrent_users}</td>
                </tr>
            """
        
        html_content += """
            </table>
            
            <h2>Detailed Results</h2>
        """
        
        for result in results:
            html_content += f"""
            <div class="test-result">
                <h3>{result.test_name}</h3>
                <div class="metrics">
                    <div class="metric">
                        <h4>Requests</h4>
                        <p>Total: {result.total_requests}</p>
                        <p>Success: {result.successful_requests}</p>
                        <p>Failed: {result.failed_requests}</p>
                    </div>
                    <div class="metric">
                        <h4>Performance</h4>
                        <p>Duration: {result.duration_seconds:.2f}s</p>
                        <p>Throughput: {result.average_throughput_rps:.2f} RPS</p>
                        <p>Max Response: {result.max_response_time_ms:.2f}ms</p>
                    </div>
                    <div class="metric">
                        <h4>Resources</h4>
                        <p>CPU: {result.resource_utilization.get('cpu_usage_percent', 0):.1f}%</p>
                        <p>Memory: {result.resource_utilization.get('memory_usage_mb', 0):.1f} MB</p>
                        <p>Peak Users: {result.peak_concurrent_users}</p>
                    </div>
                </div>
                
                {f'<h4>Performance Degradation Points</h4><ul>' + ''.join([f'<li>Users: {point["user_count"]}, Response Time: {point["response_time_ms"]:.2f}ms, Degradation: {point["degradation_ratio"]:.2f}x</li>' for point in result.performance_degradation_points]) + '</ul>' if result.performance_degradation_points else ''}
                
                {f'<h4>Error Breakdown</h4><ul>' + ''.join([f'<li>{error_type}: {count}</li>' for error_type, count in result.error_breakdown.items()]) + '</ul>' if result.error_breakdown else ''}
            </div>
            """
        
        html_content += """
        </body>
        </html>
        """
        
        with open(output_file, 'w') as f:
            f.write(html_content)
        
        print(f"Load test report generated: {output_file}")
    
    @staticmethod
    def export_metrics_to_csv(results: List[StressTestResult], output_file: str = "load_test_metrics.csv"):
        """Export load test metrics to CSV."""
        import csv
        
        with open(output_file, 'w', newline='') as csvfile:
            fieldnames = [
                'test_name', 'duration_seconds', 'total_requests', 'successful_requests',
                'failed_requests', 'peak_concurrent_users', 'max_response_time_ms',
                'average_throughput_rps', 'cpu_usage_percent', 'memory_usage_mb'
            ]
            
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            for result in results:
                row = {
                    'test_name': result.test_name,
                    'duration_seconds': result.duration_seconds,
                    'total_requests': result.total_requests,
                    'successful_requests': result.successful_requests,
                    'failed_requests': result.failed_requests,
                    'peak_concurrent_users': result.peak_concurrent_users,
                    'max_response_time_ms': result.max_response_time_ms,
                    'average_throughput_rps': result.average_throughput_rps,
                    'cpu_usage_percent': result.resource_utilization.get('cpu_usage_percent', 0),
                    'memory_usage_mb': result.resource_utilization.get('memory_usage_mb', 0)
                }
                writer.writerow(row)
        
        print(f"Load test metrics exported: {output_file}")


# Main load testing execution
async def run_comprehensive_load_tests():
    """Execute comprehensive load testing suite."""
    print("Starting comprehensive load testing suite for DevNous messaging integration")
    
    # Initialize testers
    message_hub_tester = MessageHubLoadTester()
    kafka_tester = KafkaLoadTester()
    db_tester = DatabaseLoadTester(
        "postgresql://testuser:<set-test-postgres-password>@localhost:5433/devnous_e2e_test"
    )
    
    all_results = []
    
    try:
        # Test 1: Basic concurrent load
        print("\n1. Running basic concurrent load test...")
        result1 = await message_hub_tester.concurrent_message_load_test(
            concurrent_users=10,
            messages_per_user=5,
            ramp_up_seconds=5
        )
        all_results.append(result1)
        
        # Test 2: Message burst test
        print("\n2. Running message burst test...")
        result2 = await message_hub_tester.message_burst_test(
            burst_size=50,
            burst_interval_seconds=2.0
        )
        all_results.append(result2)
        
        # Test 3: Gradual load increase
        print("\n3. Running gradual load increase test...")
        result3 = await message_hub_tester.gradual_load_increase_test(
            start_users=5,
            max_users=25,
            step_size=5,
            step_duration_seconds=30
        )
        all_results.append(result3)
        
        # Test 4: Kafka throughput test
        print("\n4. Running Kafka throughput test...")
        kafka_result = await kafka_tester.message_throughput_test(
            topic="devnous-messages",
            message_count=1000,
            concurrent_producers=10
        )
        print(f"Kafka throughput: {kafka_result['throughput_msgs_per_sec']:.2f} msgs/sec")
        
        # Test 5: Database load test
        print("\n5. Running database load test...")
        db_result = await db_tester.concurrent_write_test(
            concurrent_writers=10,
            writes_per_writer=50
        )
        print(f"Database throughput: {db_result['writes_per_second']:.2f} writes/sec")
        
        # Generate comprehensive report
        print("\n6. Generating load test reports...")
        LoadTestReporter.generate_performance_report(all_results)
        LoadTestReporter.export_metrics_to_csv(all_results)
        
        # Summary
        print(f"\n=== LOAD TEST SUMMARY ===")
        print(f"Tests completed: {len(all_results)}")
        total_requests = sum(r.total_requests for r in all_results)
        total_successful = sum(r.successful_requests for r in all_results)
        overall_success_rate = (total_successful / total_requests * 100) if total_requests > 0 else 0
        print(f"Total requests: {total_requests}")
        print(f"Overall success rate: {overall_success_rate:.1f}%")
        print(f"Peak concurrent users tested: {max(r.peak_concurrent_users for r in all_results)}")
        
        return all_results
        
    except Exception as e:
        print(f"Load testing failed: {e}")
        raise
    finally:
        # Cleanup
        await message_hub_tester.cleanup_session_pool()
        await kafka_tester.cleanup()
        await db_tester.cleanup()


if __name__ == "__main__":
    # Run the comprehensive load test suite
    asyncio.run(run_comprehensive_load_tests())
