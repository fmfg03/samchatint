"""
Feature flags system for gradual rollout of DevNous integration.

This module provides a comprehensive feature flag system that supports
gradual migration from legacy to hybrid architecture.
"""

from typing import Dict, Any, List, Optional, Callable
import json
import os
import asyncio
import logging
from enum import Enum
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict


class FeatureFlagStatus(Enum):
    DISABLED = "disabled"
    ENABLED = "enabled"
    EXPERIMENT = "experiment"  # A/B testing
    ROLLOUT = "rollout"  # Gradual rollout with percentage


@dataclass
class FeatureFlag:
    name: str
    status: FeatureFlagStatus
    description: str
    rollout_percentage: float = 0.0  # 0.0 to 1.0
    target_users: List[str] = None
    target_agents: List[str] = None
    experiment_groups: Dict[str, float] = None  # Group name -> percentage
    created_at: datetime = None
    updated_at: datetime = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.target_users is None:
            self.target_users = []
        if self.target_agents is None:
            self.target_agents = []
        if self.experiment_groups is None:
            self.experiment_groups = {}
        if self.created_at is None:
            self.created_at = datetime.utcnow()
        if self.updated_at is None:
            self.updated_at = datetime.utcnow()
        if self.metadata is None:
            self.metadata = {}


class FeatureFlagManager:
    """
    Feature flag manager with support for gradual rollouts and A/B testing.
    """

    def __init__(
        self,
        config_file: Optional[str] = None,
        use_redis: bool = False,
        redis_client=None
    ):
        self.config_file = config_file or "feature_flags.json"
        self.use_redis = use_redis
        self.redis_client = redis_client
        self.flags: Dict[str, FeatureFlag] = {}
        self.logger = logging.getLogger("FeatureFlagManager")
        self._load_flags()

        # Migration-specific feature flags
        self._initialize_migration_flags()

    def _initialize_migration_flags(self):
        """Initialize feature flags specific to DevNous migration."""
        default_migration_flags = [
            FeatureFlag(
                name="devnous_integration",
                status=FeatureFlagStatus.DISABLED,
                description="Enable DevNous tool integration for agents",
                rollout_percentage=0.0
            ),
            FeatureFlag(
                name="memory_integration",
                status=FeatureFlagStatus.DISABLED,
                description="Enable DevNous memory tools integration",
                rollout_percentage=0.0
            ),
            FeatureFlag(
                name="workflow_integration",
                status=FeatureFlagStatus.DISABLED,
                description="Enable DevNous workflow tools integration",
                rollout_percentage=0.0
            ),
            FeatureFlag(
                name="pm_tools_integration",
                status=FeatureFlagStatus.DISABLED,
                description="Enable DevNous PM tools integration",
                rollout_percentage=0.0
            ),
            FeatureFlag(
                name="communication_analytics",
                status=FeatureFlagStatus.DISABLED,
                description="Enable enhanced communication analytics",
                rollout_percentage=0.0
            ),
            FeatureFlag(
                name="architecture_analysis",
                status=FeatureFlagStatus.DISABLED,
                description="Enable architecture analysis for developers",
                rollout_percentage=0.0
            ),
            FeatureFlag(
                name="auto_create_pm_tasks",
                status=FeatureFlagStatus.DISABLED,
                description="Auto-create PM tasks from conversations",
                rollout_percentage=0.0
            ),
            FeatureFlag(
                name="hybrid_processing",
                status=FeatureFlagStatus.DISABLED,
                description="Enable hybrid processing (legacy + DevNous)",
                rollout_percentage=0.0
            ),
            FeatureFlag(
                name="intelligent_caching",
                status=FeatureFlagStatus.DISABLED,
                description="Enable intelligent tool result caching",
                rollout_percentage=0.0
            ),
            FeatureFlag(
                name="cross_agent_collaboration",
                status=FeatureFlagStatus.DISABLED,
                description="Enable cross-agent collaboration workflows",
                rollout_percentage=0.0
            )
        ]

        for flag in default_migration_flags:
            if flag.name not in self.flags:
                self.flags[flag.name] = flag

    def _load_flags(self):
        """Load feature flags from configuration."""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    data = json.load(f)

                for flag_data in data.get("flags", []):
                    flag = FeatureFlag(
                        name=flag_data["name"],
                        status=FeatureFlagStatus(flag_data["status"]),
                        description=flag_data["description"],
                        rollout_percentage=flag_data.get("rollout_percentage", 0.0),
                        target_users=flag_data.get("target_users", []),
                        target_agents=flag_data.get("target_agents", []),
                        experiment_groups=flag_data.get("experiment_groups", {}),
                        created_at=datetime.fromisoformat(flag_data.get("created_at", datetime.utcnow().isoformat())),
                        updated_at=datetime.fromisoformat(flag_data.get("updated_at", datetime.utcnow().isoformat())),
                        metadata=flag_data.get("metadata", {})
                    )
                    self.flags[flag.name] = flag

        except Exception as e:
            self.logger.warning(f"Failed to load feature flags: {e}")

    def _save_flags(self):
        """Save feature flags to configuration."""
        try:
            data = {
                "flags": [
                    {
                        "name": flag.name,
                        "status": flag.status.value,
                        "description": flag.description,
                        "rollout_percentage": flag.rollout_percentage,
                        "target_users": flag.target_users,
                        "target_agents": flag.target_agents,
                        "experiment_groups": flag.experiment_groups,
                        "created_at": flag.created_at.isoformat(),
                        "updated_at": flag.updated_at.isoformat(),
                        "metadata": flag.metadata
                    }
                    for flag in self.flags.values()
                ]
            }

            with open(self.config_file, 'w') as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            self.logger.error(f"Failed to save feature flags: {e}")

    def is_enabled(
        self,
        flag_name: str,
        user_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Check if a feature flag is enabled for the given context.

        Args:
            flag_name: Name of the feature flag
            user_id: Optional user identifier
            agent_name: Optional agent name
            context: Optional additional context

        Returns:
            True if feature is enabled, False otherwise
        """
        if flag_name not in self.flags:
            return False

        flag = self.flags[flag_name]

        if flag.status == FeatureFlagStatus.DISABLED:
            return False

        if flag.status == FeatureFlagStatus.ENABLED:
            return True

        if flag.status == FeatureFlagStatus.ROLLOUT:
            return self._check_rollout(flag, user_id, agent_name, context)

        if flag.status == FeatureFlagStatus.EXPERIMENT:
            return self._check_experiment(flag, user_id, agent_name, context)

        return False

    def _check_rollout(
        self,
        flag: FeatureFlag,
        user_id: Optional[str],
        agent_name: Optional[str],
        context: Optional[Dict[str, Any]]
    ) -> bool:
        """Check if feature should be enabled for gradual rollout."""
        # Target users/agents have priority
        if user_id and user_id in flag.target_users:
            return True

        if agent_name and agent_name in flag.target_agents:
            return True

        # Use deterministic hash for consistent rollout
        import hashlib
        identifier = user_id or agent_name or "default"
        hash_value = int(hashlib.md5(f"{flag.name}:{identifier}".encode()).hexdigest(), 16)
        normalized_hash = (hash_value % 1000) / 1000.0

        return normalized_hash < flag.rollout_percentage

    def _check_experiment(
        self,
        flag: FeatureFlag,
        user_id: Optional[str],
        agent_name: Optional[str],
        context: Optional[Dict[str, Any]]
    ) -> bool:
        """Check if feature should be enabled for A/B testing."""
        # For experiments, we need to assign users to groups
        identifier = user_id or agent_name or "default"
        group = self._get_experiment_group(flag, identifier)

        if group in flag.experiment_groups:
            return flag.experiment_groups[group] > 0

        return False

    def _get_experiment_group(self, flag: FeatureFlag, identifier: str) -> str:
        """Assign identifier to an experiment group."""
        import hashlib
        hash_value = int(hashlib.md5(f"{flag.name}:group:{identifier}".encode()).hexdigest(), 16)

        if not flag.experiment_groups:
            return "default"

        # Simple group assignment based on hash
        groups = list(flag.experiment_groups.keys())
        group_index = hash_value % len(groups)
        return groups[group_index]

    def enable_flag(
        self,
        flag_name: str,
        rollout_percentage: float = 1.0,
        target_agents: Optional[List[str]] = None
    ):
        """Enable a feature flag with optional gradual rollout."""
        if flag_name not in self.flags:
            self.logger.warning(f"Feature flag {flag_name} not found")
            return

        flag = self.flags[flag_name]

        if rollout_percentage >= 1.0:
            flag.status = FeatureFlagStatus.ENABLED
        else:
            flag.status = FeatureFlagStatus.ROLLOUT
            flag.rollout_percentage = rollout_percentage

        if target_agents:
            flag.target_agents = target_agents

        flag.updated_at = datetime.utcnow()
        self._save_flags()

        self.logger.info(f"Enabled feature flag: {flag_name} (rollout: {rollout_percentage * 100}%)")

    def disable_flag(self, flag_name: str):
        """Disable a feature flag."""
        if flag_name not in self.flags:
            self.logger.warning(f"Feature flag {flag_name} not found")
            return

        flag = self.flags[flag_name]
        flag.status = FeatureFlagStatus.DISABLED
        flag.updated_at = datetime.utcnow()
        self._save_flags()

        self.logger.info(f"Disabled feature flag: {flag_name}")

    def get_flag_status(self, flag_name: str) -> Dict[str, Any]:
        """Get detailed status of a feature flag."""
        if flag_name not in self.flags:
            return {"error": f"Flag {flag_name} not found"}

        flag = self.flags[flag_name]
        return {
            "name": flag.name,
            "status": flag.status.value,
            "description": flag.description,
            "rollout_percentage": flag.rollout_percentage,
            "target_users": flag.target_users,
            "target_agents": flag.target_agents,
            "experiment_groups": flag.experiment_groups,
            "created_at": flag.created_at.isoformat(),
            "updated_at": flag.updated_at.isoformat(),
            "metadata": flag.metadata
        }

    def get_all_flags(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all feature flags."""
        return {
            name: self.get_flag_status(name)
            for name in self.flags.keys()
        }

    def create_migration_plan(self) -> Dict[str, Any]:
        """Create a migration plan using feature flags."""
        phases = [
            {
                "name": "Phase 1: Core Infrastructure",
                "duration_days": 14,
                "flags": [
                    {"name": "devnous_integration", "rollout": 0.1},
                    {"name": "intelligent_caching", "rollout": 0.2}
                ],
                "success_criteria": [
                    "No performance degradation",
                    "All legacy functionality preserved"
                ]
            },
            {
                "name": "Phase 2: Memory Integration",
                "duration_days": 7,
                "flags": [
                    {"name": "memory_integration", "rollout": 0.3},
                    {"name": "devnous_integration", "rollout": 0.3}
                ],
                "success_criteria": [
                    "Memory operations working correctly",
                    "Conversation history preserved"
                ]
            },
            {
                "name": "Phase 3: Workflow Tools",
                "duration_days": 10,
                "flags": [
                    {"name": "workflow_integration", "rollout": 0.5},
                    {"name": "devnous_integration", "rollout": 0.5}
                ],
                "success_criteria": [
                    "Workflow state tracking functional",
                    "Cross-agent collaboration working"
                ]
            },
            {
                "name": "Phase 4: PM Integration",
                "duration_days": 10,
                "flags": [
                    {"name": "pm_tools_integration", "rollout": 0.7},
                    {"name": "auto_create_pm_tasks", "rollout": 0.3}
                ],
                "success_criteria": [
                    "Task management integration working",
                    "No duplicate tasks created"
                ]
            },
            {
                "name": "Phase 5: Full Rollout",
                "duration_days": 7,
                "flags": [
                    {"name": "devnous_integration", "rollout": 1.0},
                    {"name": "hybrid_processing", "rollout": 1.0},
                    {"name": "communication_analytics", "rollout": 0.8},
                    {"name": "architecture_analysis", "rollout": 0.8}
                ],
                "success_criteria": [
                    "All agents fully migrated",
                    "Performance meets or exceeds baseline"
                ]
            }
        ]

        return {
            "total_phases": len(phases),
            "estimated_duration_days": sum(p["duration_days"] for p in phases),
            "phases": phases,
            "rollback_strategy": {
                "trigger_conditions": [
                    "Error rate > 5%",
                    "Performance degradation > 20%",
                    "User satisfaction score < 7/10"
                ],
                "rollback_procedure": [
                    "Disable all DevNous flags",
                    "Verify legacy functionality",
                    "Notify stakeholders"
                ]
            }
        }

    def execute_migration_phase(self, phase_name: str, phase_config: Dict[str, Any]):
        """Execute a migration phase by updating feature flags."""
        try:
            for flag_config in phase_config.get("flags", []):
                flag_name = flag_config["name"]
                rollout_percentage = flag_config["rollout"]

                self.enable_flag(flag_name, rollout_percentage)

            self.logger.info(f"Executed migration phase: {phase_name}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to execute migration phase {phase_name}: {e}")
            return False

    def rollback_migration(self):
        """Rollback migration by disabling all DevNous flags."""
        migration_flags = [
            "devnous_integration",
            "memory_integration",
            "workflow_integration",
            "pm_tools_integration",
            "communication_analytics",
            "architecture_analysis",
            "auto_create_pm_tasks",
            "hybrid_processing",
            "intelligent_caching",
            "cross_agent_collaboration"
        ]

        for flag_name in migration_flags:
            self.disable_flag(flag_name)

        self.logger.warning("Migration rollback executed - all DevNous flags disabled")


# Global feature flag manager instance
_feature_manager = None


def get_feature_manager() -> FeatureFlagManager:
    """Get the global feature flag manager instance."""
    global _feature_manager
    if _feature_manager is None:
        _feature_manager = FeatureFlagManager()
    return _feature_manager


def is_feature_enabled(
    flag_name: str,
    user_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None
) -> bool:
    """Convenience function to check if a feature is enabled."""
    return get_feature_manager().is_enabled(flag_name, user_id, agent_name, context)


def enable_feature(
    flag_name: str,
    rollout_percentage: float = 1.0,
    target_agents: Optional[List[str]] = None
):
    """Convenience function to enable a feature."""
    return get_feature_manager().enable_flag(flag_name, rollout_percentage, target_agents)


def disable_feature(flag_name: str):
    """Convenience function to disable a feature."""
    return get_feature_manager().disable_flag(flag_name)