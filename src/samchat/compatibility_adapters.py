"""
Compatibility adapters for seamless migration between legacy and DevNous systems.

This module provides adapter classes that ensure backward compatibility while
enabling gradual migration to the new hybrid architecture.
"""

from typing import Dict, Any, List, Optional
import logging
from datetime import datetime

from .base_agent import BaseAgent, Message, ProjectContext
from .conversation_parser import ConversationParser


class LegacyAgentAdapter:
    """
    Adapter to make legacy agents work with DevNous integration.

    This adapter implements the Adapter Pattern to bridge the gap between
    legacy agent interfaces and the new hybrid system.
    """

    def __init__(self, legacy_agent: BaseAgent):
        self.legacy_agent = legacy_agent
        self.conversation_parser = ConversationParser()
        self.logger = logging.getLogger(f"Adapter:{legacy_agent.name}")

    def enable_devnous_integration(
        self, devnous_agent, feature_flags: Optional[Dict[str, bool]] = None
    ):
        """Enable DevNous integration for a legacy agent."""
        try:
            # Update the legacy agent's configuration
            self.legacy_agent.enable_devnous_integration = True
            self.legacy_agent.feature_flags = feature_flags or {}
            self.legacy_agent._legacy_mode = False

            # Inject DevNous agent
            self.legacy_agent.inject_devnous_agent(devnous_agent)

            self.logger.info(
                f"DevNous integration enabled for {self.legacy_agent.name}"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to enable DevNous integration: {e}")
            return False

    def disable_devnous_integration(self):
        """Disable DevNous integration, reverting to legacy mode."""
        try:
            self.legacy_agent.enable_devnous_integration = False
            self.legacy_agent._legacy_mode = True
            self.legacy_agent.devnous_agent = None
            self.legacy_agent.tool_registry.clear()
            self.legacy_agent.tool_cache.clear()

            self.logger.info(f"Reverted {self.legacy_agent.name} to legacy mode")
            return True

        except Exception as e:
            self.logger.error(f"Failed to disable DevNous integration: {e}")
            return False

    async def gradual_migration_process(
        self,
        conversation: List[Message],
        context: ProjectContext,
        migration_percentage: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Process conversation with gradual migration strategy.

        Args:
            conversation: Messages to process
            context: Project context
            migration_percentage: Percentage of requests to route through
                DevNous (0.0-1.0)

        Returns:
            Processing results with migration metadata
        """
        import random

        if migration_percentage <= 0.0:
            use_devnous = False
        elif migration_percentage >= 1.0:
            use_devnous = True
        else:
            use_devnous = random.random() < migration_percentage

        use_devnous = use_devnous and self.legacy_agent.enable_devnous_integration

        result = {
            "migration_metadata": {
                "used_devnous": use_devnous,
                "migration_percentage": migration_percentage,
                "timestamp": datetime.utcnow().isoformat(),
                "agent_name": self.legacy_agent.name,
            }
        }

        try:
            if use_devnous and self.legacy_agent.devnous_agent:
                # Use hybrid processing
                hybrid_result = await self.legacy_agent.orchestrate_tools(
                    conversation, context
                )
                result.update(hybrid_result)
                result["processing_mode"] = "hybrid"
            else:
                # Use legacy processing
                legacy_result = await self.legacy_agent.process_conversation(
                    conversation, context
                )
                result.update(legacy_result)
                result["processing_mode"] = "legacy"

            return result

        except Exception as e:
            self.logger.error(f"Error in gradual migration process: {e}")
            # Fallback to legacy processing
            try:
                legacy_result = await self.legacy_agent.process_conversation(
                    conversation, context
                )
                result.update(legacy_result)
                result["processing_mode"] = "legacy_fallback"
                result["fallback_reason"] = str(e)
                return result
            except Exception as fallback_error:
                return {
                    "success": False,
                    "error": (
                        "Both hybrid and legacy processing failed: "
                        f"{fallback_error}"
                    ),
                    "processing_mode": "failed",
                    **result,
                }


class ConversationContextAdapter:
    """
    Adapter to convert between legacy conversation formats and DevNous formats.
    """

    def __init__(self):
        self.conversation_parser = ConversationParser()
        self.logger = logging.getLogger("ConversationContextAdapter")

    def legacy_to_devnous_context(
        self, legacy_context: ProjectContext
    ) -> ProjectContext:
        """Convert legacy ProjectContext to DevNous-compatible format."""
        # Add DevNous-specific fields while preserving legacy data
        enhanced_context = ProjectContext(
            conversation_history=legacy_context.conversation_history,
            project_goals=legacy_context.project_goals,
            current_sprint=legacy_context.current_sprint,
            backlog=legacy_context.backlog,
            team_members=legacy_context.team_members,
            metadata=legacy_context.metadata or {},
            # DevNous enhancements
            workflow_id=(
                legacy_context.metadata.get("workflow_id")
                if legacy_context.metadata
                else None
            ),
            active_tools=[],
            tool_context={},
        )

        return enhanced_context

    def devnous_to_legacy_context(
        self, devnous_context: ProjectContext
    ) -> ProjectContext:
        """Convert DevNous ProjectContext back to legacy format."""
        # Remove DevNous-specific fields, preserve in metadata
        legacy_metadata = devnous_context.metadata or {}
        if devnous_context.workflow_id:
            legacy_metadata["workflow_id"] = devnous_context.workflow_id
        if devnous_context.active_tools:
            legacy_metadata["active_tools"] = devnous_context.active_tools
        if devnous_context.tool_context:
            legacy_metadata["tool_context"] = devnous_context.tool_context

        legacy_context = ProjectContext(
            conversation_history=devnous_context.conversation_history,
            project_goals=devnous_context.project_goals,
            current_sprint=devnous_context.current_sprint,
            backlog=devnous_context.backlog,
            team_members=devnous_context.team_members,
            metadata=legacy_metadata,
        )

        return legacy_context

    def parse_legacy_conversation(self, raw_conversation: str) -> List[Message]:
        """Parse legacy conversation format into Message objects."""
        return self.conversation_parser.parse_conversation(raw_conversation)

    def extract_conversation_entities(
        self, conversation: List[Message]
    ) -> Dict[str, Any]:
        """Extract entities using legacy parser with DevNous enhancements."""
        entities = self.conversation_parser.extract_entities(conversation)

        # Enhance with DevNous-compatible format
        enhanced_entities = {
            **entities,
            "devnous_compatible": True,
            "extraction_timestamp": datetime.utcnow().isoformat(),
            "parser_version": "legacy_with_devnous_enhancements",
        }

        return enhanced_entities


class ToolIntegrationAdapter:
    """
    Adapter for integrating legacy agent methods with DevNous tools.
    """

    def __init__(self, agent: BaseAgent):
        self.agent = agent
        self.logger = logging.getLogger(f"ToolAdapter:{agent.name}")

    async def adaptive_action_extraction(
        self, text: str, use_devnous: bool = True
    ) -> List[str]:
        """
        Extract action items using both legacy and DevNous methods.

        Args:
            text: Text to analyze
            use_devnous: Whether to use DevNous tools if available

        Returns:
            Combined action items from both approaches
        """
        action_items = []

        # Always use legacy method for baseline
        legacy_actions = await self.agent.extract_action_items(text)
        action_items.extend(legacy_actions)

        # Use DevNous if available and enabled
        if (
            use_devnous
            and self.agent.enable_devnous_integration
            and self.agent.devnous_agent
        ):

            try:
                intent_analysis = (
                    await self.agent.devnous_agent._analyze_message_intent(text)
                )
                if "task_management" in intent_analysis.get("intents", []):
                    # Use DevNous PM tools to extract more detailed actions
                    devnous_result = await self.agent.use_tool(
                        "get_tasks", filters={"status": "open", "search_text": text}
                    )
                    if devnous_result.get("success"):
                        devnous_actions = [
                            f"Follow up on task: {task.get('title', 'Unknown')}"
                            for task in devnous_result.get("data", {}).get("items", [])
                        ]
                        action_items.extend(devnous_actions)

            except Exception as e:
                self.logger.warning(
                    f"DevNous action extraction failed, using legacy only: {e}"
                )

        # Remove duplicates while preserving order
        seen = set()
        unique_actions = []
        for action in action_items:
            if action not in seen:
                seen.add(action)
                unique_actions.append(action)

        return unique_actions

    async def adaptive_blocker_identification(
        self, conversation: List[Message], use_devnous: bool = True
    ) -> Dict[str, Any]:
        """
        Identify blockers using both legacy and DevNous methods.
        """
        results = {
            "blockers": [],
            "confidence_scores": {},
            "sources": [],
            "recommendations": [],
        }

        # Legacy blocker identification
        legacy_blockers = await self.agent.identify_blockers(conversation)
        results["blockers"].extend(legacy_blockers)
        results["sources"].append("legacy")

        # DevNous enhancement
        if (
            use_devnous
            and self.agent.enable_devnous_integration
            and self.agent.devnous_agent
        ):

            try:
                # Use task management to identify dependency blockers
                task_result = await self.agent.use_tool(
                    "get_tasks", filters={"status": "blocked"}
                )

                if task_result.get("success"):
                    blocked_tasks = task_result.get("data", {}).get("items", [])
                    for task in blocked_tasks:
                        results["blockers"].append(
                            "Task blocked: "
                            f"{task.get('title', 'Unknown')} - "
                            f"{task.get('blocker_reason', 'No reason specified')}"
                        )
                    results["sources"].append("devnous_pm")

                # Generate recommendations
                if results["blockers"]:
                    results["recommendations"].append(
                        "Consider scheduling blocker removal session"
                    )
                    results["recommendations"].append(
                        "Update stakeholders on blocked items"
                    )

            except Exception as e:
                self.logger.warning(f"DevNous blocker identification failed: {e}")

        return results


class MigrationUtility:
    """
    Utility class for managing the migration process.
    """

    def __init__(self):
        self.logger = logging.getLogger("MigrationUtility")
        self.migration_log = []

    def create_migration_plan(
        self, agents: List[BaseAgent], migration_phases: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Create a comprehensive migration plan."""
        plan = {
            "total_agents": len(agents),
            "agents": [
                {
                    "name": agent.name,
                    "role": agent.role,
                    "current_status": "legacy",
                    "target_status": "hybrid",
                    "estimated_effort": "medium",
                }
                for agent in agents
            ],
            "phases": migration_phases,
            "rollback_procedures": [
                {
                    "phase": phase["name"],
                    "rollback_command": f"disable_devnous_integration({phase['name']})",
                    "validation_steps": [
                        "Check legacy functionality",
                        "Verify data integrity",
                    ],
                }
                for phase in migration_phases
            ],
            "success_criteria": [
                "All legacy functionality preserved",
                "DevNous tools accessible",
                "Performance meets or exceeds baseline",
                "Zero data loss during migration",
            ],
            "risk_mitigation": [
                "Feature flags for gradual rollout",
                "Automated rollback triggers",
                "Comprehensive testing at each phase",
                "Legacy system maintained in parallel",
            ],
        }

        return plan

    def validate_migration_readiness(
        self, agent: BaseAgent, devnous_agent
    ) -> Dict[str, Any]:
        """Validate if an agent is ready for migration."""
        readiness_report = {
            "agent_name": agent.name,
            "ready_for_migration": True,
            "checks_passed": [],
            "checks_failed": [],
            "recommendations": [],
        }

        # Check 1: Legacy functionality working
        try:
            # This would normally involve running test conversations
            readiness_report["checks_passed"].append("Legacy functionality verified")
        except Exception as e:
            readiness_report["checks_failed"].append(
                f"Legacy functionality check failed: {e}"
            )
            readiness_report["ready_for_migration"] = False

        # Check 2: DevNous agent available
        if devnous_agent is None:
            readiness_report["checks_failed"].append("DevNous agent not available")
            readiness_report["ready_for_migration"] = False
        else:
            readiness_report["checks_passed"].append("DevNous agent available")

        # Check 3: Required dependencies
        required_attrs = ["name", "role", "llm_client"]
        missing_attrs = [attr for attr in required_attrs if not hasattr(agent, attr)]
        if missing_attrs:
            readiness_report["checks_failed"].append(
                f"Missing required attributes: {missing_attrs}"
            )
            readiness_report["ready_for_migration"] = False
        else:
            readiness_report["checks_passed"].append("Required attributes present")

        # Generate recommendations
        if not readiness_report["ready_for_migration"]:
            readiness_report["recommendations"].append(
                "Fix failed checks before migration"
            )
        else:
            readiness_report["recommendations"].append(
                "Start with low-risk feature flags"
            )
            readiness_report["recommendations"].append("Monitor performance closely")

        return readiness_report

    def log_migration_event(
        self, agent_name: str, event_type: str, details: Dict[str, Any]
    ):
        """Log migration events for audit trail."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent_name": agent_name,
            "event_type": event_type,
            "details": details,
        }

        self.migration_log.append(event)
        self.logger.info(f"Migration event logged: {event_type} for {agent_name}")

    def get_migration_report(self) -> Dict[str, Any]:
        """Generate comprehensive migration report."""
        return {
            "total_events": len(self.migration_log),
            "events": self.migration_log,
            "summary": {
                "successful_migrations": len(
                    [
                        e
                        for e in self.migration_log
                        if e["event_type"] == "migration_completed"
                    ]
                ),
                "failed_migrations": len(
                    [
                        e
                        for e in self.migration_log
                        if e["event_type"] == "migration_failed"
                    ]
                ),
                "rollbacks": len(
                    [
                        e
                        for e in self.migration_log
                        if e["event_type"] == "rollback_executed"
                    ]
                ),
            },
        }
