"""
Core SamChat system orchestrator.

This module provides the main SamChatSystem class that serves as the primary
entry point for the SamChat DevNous-based multi-agent system.
"""

from typing import List, Dict, Any, Optional, Union
import asyncio
import logging
from datetime import datetime
from dataclasses import dataclass

from .base_agent import BaseAgent, Message, ProjectContext, LLMProvider
from .agents import ProductOwnerAgent, ScrumMasterAgent, DeveloperAgent
from .conversation_parser import ConversationParser
from .compatibility_adapters import LegacyAgentAdapter, MigrationUtility
from .feature_flags import get_feature_manager


@dataclass
class SamChatConfig:
    """Configuration for SamChat system."""

    llm_provider: LLMProvider = LLMProvider.OPENAI
    model: str = "gpt-4"
    temperature: float = 0.7
    max_tokens: int = 2000
    enable_devnous_integration: bool = False
    feature_flags: Optional[Dict[str, bool]] = None
    log_level: str = "INFO"
    use_redis: bool = False
    migration_rollout_percentage: float = 1.0
    redis_client = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.migration_rollout_percentage <= 1.0:
            raise ValueError("migration_rollout_percentage must be between 0.0 and 1.0")


class SamChatSystem:
    """
    Main orchestrator for the SamChat multi-agent system.

    This class provides the primary interface for interacting with the
    DevNous-based project management conversation analysis system.
    """

    def __init__(
        self,
        config: Optional[SamChatConfig] = None,
        custom_agents: Optional[Dict[str, BaseAgent]] = None,
    ):
        """
        Initialize the SamChat system.

        Args:
            config: System configuration
            custom_agents: Optional custom agents to use instead of defaults
        """
        self.config = config or SamChatConfig()
        self.conversation_parser = ConversationParser()
        self.migration_utility = MigrationUtility()
        self.feature_manager = get_feature_manager()

        # Use module logger; configure in CLI/app entrypoints
        self.logger = logging.getLogger("SamChatSystem")

        # Initialize agents
        self.agents: Dict[str, BaseAgent] = {}
        self._initialize_agents(custom_agents)

        # Agent adapters for migration support
        self.agent_adapters: Dict[str, LegacyAgentAdapter] = {}
        self._initialize_adapters()

        # System state
        self.conversation_history: List[Message] = []
        self.project_context: Optional[ProjectContext] = None
        self.active_workflow_id: Optional[str] = None

        self.logger.info(f"SamChat system initialized with {len(self.agents)} agents")

    def _initialize_agents(self, custom_agents: Optional[Dict[str, BaseAgent]] = None):
        """Initialize the default set of agents."""
        if custom_agents:
            self.agents.update(custom_agents)
        else:
            # Create default agents with system configuration
            agent_config = {
                "llm_provider": self.config.llm_provider,
                "model": self.config.model,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
                "enable_devnous_integration": self.config.enable_devnous_integration,
                "feature_flags": self.config.feature_flags or {},
            }

            self.agents = {
                "product_owner": ProductOwnerAgent(**agent_config),
                "scrum_master": ScrumMasterAgent(**agent_config),
                "developer": DeveloperAgent(**agent_config),
            }

    def _initialize_adapters(self):
        """Initialize legacy adapters for each agent."""
        for name, agent in self.agents.items():
            self.agent_adapters[name] = LegacyAgentAdapter(agent)

    def add_agent(self, name: str, agent: BaseAgent):
        """Add a custom agent to the system."""
        self.agents[name] = agent
        self.agent_adapters[name] = LegacyAgentAdapter(agent)
        self.logger.info(f"Added agent: {name}")

    def remove_agent(self, name: str):
        """Remove an agent from the system."""
        if name in self.agents:
            del self.agents[name]
            del self.agent_adapters[name]
            self.logger.info(f"Removed agent: {name}")

    def get_agent(self, name: str) -> Optional[BaseAgent]:
        """Get an agent by name."""
        return self.agents.get(name)

    def list_agents(self) -> List[str]:
        """List all available agent names."""
        return list(self.agents.keys())

    def parse_conversation(self, raw_conversation: str) -> List[Message]:
        """
        Parse raw conversation text into structured messages.

        Args:
            raw_conversation: Raw conversation text

        Returns:
            List of parsed Message objects
        """
        return self.conversation_parser.parse_conversation(raw_conversation)

    def create_project_context(
        self,
        conversation_history: List[Message],
        project_goals: List[str],
        current_sprint: Optional[Dict[str, Any]] = None,
        backlog: Optional[List[Dict[str, Any]]] = None,
        team_members: Optional[List[str]] = None,
        workflow_id: Optional[str] = None,
    ) -> ProjectContext:
        """
        Create a project context for conversation analysis.

        Args:
            conversation_history: List of conversation messages
            project_goals: List of project goals
            current_sprint: Optional current sprint information
            backlog: Optional backlog items
            team_members: Optional team member list
            workflow_id: Optional DevNous workflow ID

        Returns:
            ProjectContext object
        """
        context = ProjectContext(
            conversation_history=conversation_history,
            project_goals=project_goals,
            current_sprint=current_sprint,
            backlog=backlog or [],
            team_members=team_members or [],
            workflow_id=workflow_id,
        )

        self.project_context = context
        self.active_workflow_id = workflow_id

        return context

    async def analyze_conversation(
        self,
        conversation: Union[str, List[Message]],
        context: Optional[ProjectContext] = None,
        target_agents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze a conversation using the specified agents.

        Args:
            conversation: Raw conversation string or list of Messages
            context: Optional project context
            target_agents: Optional list of agent names to use (defaults to all)

        Returns:
            Analysis results from all agents
        """
        # Parse conversation if it's raw text
        if isinstance(conversation, str):
            messages = self.parse_conversation(conversation)
        else:
            messages = conversation

        # Use provided context or create a default one
        if context is None:
            context = self.create_project_context(
                conversation_history=messages,
                project_goals=["Analyze project conversation"],
            )

        # Determine which agents to use
        agents_to_use = target_agents or list(self.agents.keys())

        # Run analysis with all specified agents
        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "conversation_metadata": {
                "message_count": len(messages),
                "participants": list(set(msg.sender for msg in messages if msg.sender)),
            },
            "agent_analyses": {},
            "combined_insights": {},
        }

        # Process with each agent
        agent_tasks = []
        for agent_name in agents_to_use:
            if agent_name in self.agents:
                agent = self.agents[agent_name]
                task = self._analyze_with_agent(agent_name, agent, messages, context)
                agent_tasks.append(task)

        # Execute all agent analyses concurrently
        agent_results = await asyncio.gather(*agent_tasks, return_exceptions=True)

        # Compile results
        for i, agent_name in enumerate(agents_to_use):
            if agent_name in self.agents:
                agent_result = agent_results[i]
                if isinstance(agent_result, Exception):
                    results["agent_analyses"][agent_name] = {
                        "error": str(agent_result),
                        "agent_name": agent_name,
                        "success": False,
                    }
                    self.logger.error(
                        f"Agent {agent_name} analysis failed: {agent_result}"
                    )
                else:
                    results["agent_analyses"][agent_name] = agent_result

        # Generate combined insights
        results["combined_insights"] = self._generate_combined_insights(
            results["agent_analyses"]
        )

        # Update conversation history
        self.conversation_history.extend(messages)

        return results

    async def _analyze_with_agent(
        self,
        agent_name: str,
        agent: BaseAgent,
        messages: List[Message],
        context: ProjectContext,
    ) -> Dict[str, Any]:
        """Analyze conversation with a specific agent."""
        try:
            # Use adapter for migration support if DevNous integration is enabled
            if self.config.enable_devnous_integration:
                adapter = self.agent_adapters[agent_name]
                result = await adapter.gradual_migration_process(
                    messages,
                    context,
                    migration_percentage=self.config.migration_rollout_percentage,
                )
            else:
                # Use legacy processing directly
                result = await agent.process_conversation(messages, context)

            result["success"] = True
            return result

        except Exception as e:
            self.logger.error(f"Error analyzing with agent {agent_name}: {e}")
            return {
                "agent": agent_name,
                "error": str(e),
                "success": False,
                "fallback_used": True,
            }

    def _generate_combined_insights(
        self, agent_analyses: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate combined insights from all agent analyses."""
        combined = {
            "all_action_items": [],
            "all_blockers": [],
            "all_decisions": [],
            "priority_items": [],
            "cross_agent_recommendations": [],
            "consensus_score": 0.0,
        }

        successful_analyses = [
            result for result in agent_analyses.values() if result.get("success", False)
        ]

        if not successful_analyses:
            return combined

        # Collect all action items
        for result in successful_analyses:
            action_items = result.get("action_items", [])
            combined["all_action_items"].extend(action_items)

        # Collect all blockers
        for result in successful_analyses:
            blockers = result.get("blockers", [])
            if isinstance(blockers, dict):
                blockers = blockers.get("blockers", [])
            combined["all_blockers"].extend(blockers)

        # Collect decisions
        for result in successful_analyses:
            decisions = result.get("decisions", {})
            if isinstance(decisions, dict) and "summary" in decisions:
                combined["all_decisions"].append(decisions["summary"])

        # Generate cross-agent recommendations
        combined["cross_agent_recommendations"] = (
            self._generate_cross_agent_recommendations(successful_analyses)
        )

        # Calculate consensus score based on overlapping insights
        combined["consensus_score"] = self._calculate_consensus_score(
            successful_analyses
        )

        # Remove duplicates
        combined["all_action_items"] = list(set(combined["all_action_items"]))
        combined["all_blockers"] = list(set(combined["all_blockers"]))

        return combined

    def _generate_cross_agent_recommendations(
        self, analyses: List[Dict[str, Any]]
    ) -> List[str]:
        """Generate recommendations that span multiple agent perspectives."""
        recommendations = []

        # Check for common themes
        action_counts = {}
        for analysis in analyses:
            for action in analysis.get("action_items", []):
                action_counts[action] = action_counts.get(action, 0) + 1

        # High-priority items mentioned by multiple agents
        high_priority_actions = [
            action for action, count in action_counts.items() if count > 1
        ]

        if high_priority_actions:
            recommendations.append(
                "High-priority items mentioned by multiple agents: "
                f"{len(high_priority_actions)} items"
            )

        # Check for blocker consensus
        blocker_mentions = sum(
            1
            for analysis in analyses
            if analysis.get("blockers") and len(analysis.get("blockers", [])) > 0
        )

        if blocker_mentions > 1:
            recommendations.append(
                "Multiple agents identified blockers - schedule blocker "
                "resolution session"
            )

        # Team coordination recommendations
        if len(analyses) >= 2:
            recommendations.append(
                "Cross-functional analysis complete - consider team alignment meeting"
            )

        return recommendations

    def _calculate_consensus_score(self, analyses: List[Dict[str, Any]]) -> float:
        """Calculate how much the agents agree on insights."""
        if len(analyses) < 2:
            return 1.0

        # Simple consensus based on overlapping action items
        all_actions = []
        for analysis in analyses:
            all_actions.extend(analysis.get("action_items", []))

        if not all_actions:
            return 0.0

        # Count overlaps
        action_counts = {}
        for action in all_actions:
            action_counts[action] = action_counts.get(action, 0) + 1

        overlapping_actions = sum(1 for count in action_counts.values() if count > 1)
        total_unique_actions = len(action_counts)

        return (
            overlapping_actions / total_unique_actions
            if total_unique_actions > 0
            else 0.0
        )

    def enable_devnous_integration(
        self,
        devnous_agent,
        feature_flags: Optional[Dict[str, bool]] = None,
        target_agents: Optional[List[str]] = None,
    ):
        """
        Enable DevNous integration for specified agents.

        Args:
            devnous_agent: DevNous agent instance to integrate
            feature_flags: Optional feature flags to enable
            target_agents: Optional list of agent names (defaults to all)
        """
        agents_to_migrate = target_agents or list(self.agents.keys())

        for agent_name in agents_to_migrate:
            if agent_name in self.agent_adapters:
                adapter = self.agent_adapters[agent_name]
                success = adapter.enable_devnous_integration(
                    devnous_agent, feature_flags
                )
                if success:
                    self.logger.info(f"DevNous integration enabled for {agent_name}")
                else:
                    self.logger.error(
                        f"Failed to enable DevNous integration for {agent_name}"
                    )

        # Update system config
        self.config.enable_devnous_integration = True
        if feature_flags:
            self.config.feature_flags = feature_flags

    def disable_devnous_integration(self, target_agents: Optional[List[str]] = None):
        """Disable DevNous integration for specified agents."""
        agents_to_revert = target_agents or list(self.agents.keys())

        for agent_name in agents_to_revert:
            if agent_name in self.agent_adapters:
                adapter = self.agent_adapters[agent_name]
                success = adapter.disable_devnous_integration()
                if success:
                    self.logger.info(f"DevNous integration disabled for {agent_name}")
                else:
                    self.logger.error(
                        f"Failed to disable DevNous integration for {agent_name}"
                    )

        self.config.enable_devnous_integration = False

    def get_migration_status(self) -> Dict[str, Any]:
        """Get the current migration status of all agents."""
        return {
            "system_config": {
                "devnous_integration_enabled": self.config.enable_devnous_integration,
                "feature_flags": self.config.feature_flags,
                "agent_count": len(self.agents),
            },
            "agent_statuses": {
                name: agent.get_migration_status()
                for name, agent in self.agents.items()
            },
            "feature_flags": self.feature_manager.get_all_flags(),
        }

    def create_migration_plan(self) -> Dict[str, Any]:
        """Create a comprehensive migration plan."""
        # Use the migration utility and feature manager to create plan
        migration_plan = self.migration_utility.create_migration_plan(
            list(self.agents.values()),
            self.feature_manager.create_migration_plan()["phases"],
        )

        feature_plan = self.feature_manager.create_migration_plan()

        return {
            "system_migration": migration_plan,
            "feature_rollout": feature_plan,
            "estimated_total_duration": feature_plan["estimated_duration_days"],
            "risk_assessment": "Medium - gradual rollout with fallback procedures",
            "success_criteria": [
                "All agents successfully migrated",
                "No performance degradation",
                "DevNous tools fully integrated",
                "Feature flags properly configured",
            ],
        }

    async def health_check(self) -> Dict[str, Any]:
        """Perform a comprehensive health check of the system."""
        health_report = {
            "timestamp": datetime.utcnow().isoformat(),
            "overall_status": "healthy",
            "agents": {},
            "feature_flags": {},
            "system_metrics": {},
        }

        # Check each agent
        for name, agent in self.agents.items():
            try:
                # Simple health check - try to generate a response
                test_messages = [{"role": "user", "content": "Health check"}]
                response = await agent.generate_response(test_messages)

                health_report["agents"][name] = {
                    "status": "healthy",
                    "response_length": len(response),
                    "migration_status": agent.get_migration_status(),
                }
            except Exception as e:
                health_report["agents"][name] = {"status": "unhealthy", "error": str(e)}
                health_report["overall_status"] = "degraded"

        # Check feature flags
        health_report["feature_flags"] = self.feature_manager.get_all_flags()

        # System metrics
        health_report["system_metrics"] = {
            "conversation_history_length": len(self.conversation_history),
            "active_workflow_id": self.active_workflow_id,
            "project_context_available": self.project_context is not None,
        }

        return health_report

    def get_system_info(self) -> Dict[str, Any]:
        """Get comprehensive system information."""
        return {
            "version": "0.1.0",
            "config": {
                "llm_provider": self.config.llm_provider.value,
                "model": self.config.model,
                "temperature": self.config.temperature,
                "devnous_integration": self.config.enable_devnous_integration,
            },
            "agents": {
                name: {
                    "name": agent.name,
                    "role": agent.role,
                    "llm_provider": agent.llm_provider.value,
                    "model": agent.model,
                }
                for name, agent in self.agents.items()
            },
            "capabilities": [
                "Multi-agent conversation analysis",
                "Project context management",
                "DevNous integration support",
                "Feature flag management",
                "Migration utilities",
                "Conversation parsing",
                "Cross-agent insights",
            ],
        }
