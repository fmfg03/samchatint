from typing import List, Dict, Any, Optional
import json
import asyncio
import logging
from datetime import datetime

from .base_agent import BaseAgent, Message, ProjectContext
from .compatibility_adapters import ToolIntegrationAdapter


class ProductOwnerAgent(BaseAgent):
    def __init__(self, name: str = "Product Owner", **kwargs):
        super().__init__(name=name, role="Product Owner", **kwargs)
        self.tool_adapter = ToolIntegrationAdapter(self)
        self.logger = logging.getLogger(f"ProductOwnerAgent:{name}")

        # DevNous-enhanced capabilities
        self.story_templates = {
            "epic": "Epic: {title}\nAs a {user_type}, I want {high_level_goal} so that {business_value}",
            "story": "As a {user_type}, I want {feature} so that {benefit}",
            "task": "Task: {description}\nAcceptance Criteria: {criteria}"
        }

    def get_system_prompt(self) -> str:
        return """You are a Product Owner in an Agile/Scrum team. Your responsibilities include:
        - Extracting and prioritizing requirements from conversations
        - Creating and maintaining user stories
        - Defining acceptance criteria
        - Managing the product backlog
        - Ensuring the team understands the product vision
        - Making decisions about feature priorities

        Analyze conversations to identify:
        1. User requirements and feature requests
        2. Business value and priorities
        3. Acceptance criteria for features
        4. Dependencies and constraints
        5. Stakeholder concerns

        Format your outputs as structured data including user stories, priorities, and acceptance criteria."""

    async def process_conversation(
        self,
        conversation: List[Message],
        context: ProjectContext
    ) -> Dict[str, Any]:
        """Enhanced conversation processing with DevNous tool integration."""

        # Use hybrid processing if DevNous is available
        if self.enable_devnous_integration and self.devnous_agent:
            return await self._enhanced_process_conversation(conversation, context)
        else:
            return await self._legacy_process_conversation(conversation, context)

    async def _enhanced_process_conversation(
        self,
        conversation: List[Message],
        context: ProjectContext
    ) -> Dict[str, Any]:
        """Enhanced processing using DevNous tools."""
        conv_text = "\n".join([f"{msg.sender}: {msg.content}" for msg in conversation])

        # Analyze conversation intent
        intent_analysis = await self.analyze_conversation_intent(conv_text)

        # Initialize results structure
        results = {
            "agent": self.name,
            "role": self.role,
            "processing_mode": "enhanced",
            "timestamp": datetime.utcnow().isoformat(),
            "conversation": conversation,
            "analysis": {},
            "devnous_insights": {},
            "action_items": [],
            "decisions": {},
            "recommendations": []
        }

        try:
            # Use DevNous memory tools to store conversation context
            if self.is_feature_enabled("memory_integration"):
                memory_result = await self.use_tool(
                    "memorize",
                    f"po_conversation_{context.current_sprint.get('id', 'default') if context.current_sprint else 'default'}",
                    conv_text,
                    ttl=3600  # 1 hour
                )
                if memory_result.get("success"):
                    results["devnous_insights"]["conversation_stored"] = True

            # Enhanced user story extraction using PM tools
            if "task_management" in intent_analysis.get("intents", []):
                await self._extract_user_stories_enhanced(conv_text, results, context)

            # Use workflow tools if workflow context is available
            if context.workflow_id:
                workflow_result = await self.use_tool("get_workflow_state", context.workflow_id)
                if workflow_result.get("success"):
                    results["devnous_insights"]["workflow_context"] = workflow_result.get("data")

            # Enhanced action item extraction
            enhanced_actions = await self.tool_adapter.adaptive_action_extraction(conv_text)
            results["action_items"] = enhanced_actions

            # Legacy analysis for comparison
            legacy_analysis = await self._get_legacy_analysis(conv_text, conversation)
            results["analysis"] = legacy_analysis

            # Generate product owner specific recommendations
            results["recommendations"] = await self._generate_po_recommendations(
                results, context, intent_analysis
            )

        except Exception:
            self.logger.exception("Enhanced processing failed, falling back to legacy")
            return await self._legacy_process_conversation(conversation, context)

        return results

    async def _legacy_process_conversation(
        self,
        conversation: List[Message],
        context: ProjectContext
    ) -> Dict[str, Any]:
        """Legacy conversation processing (preserved for backward compatibility)."""
        conv_text = "\n".join([f"{msg.sender}: {msg.content}" for msg in conversation])

        prompt = f"""Analyze this project conversation and extract:
        1. User stories (in format: As a [user], I want [feature] so that [benefit])
        2. Feature priorities (High/Medium/Low)
        3. Acceptance criteria for each feature
        4. Any constraints or dependencies mentioned

        Conversation:
        {conv_text}

        Provide the analysis in JSON format."""

        messages = [{"role": "user", "content": prompt}]
        response = await self.generate_response(messages)

        try:
            analysis = json.loads(response)
        except (json.JSONDecodeError, TypeError) as e:
            self.logger.exception("ProductOwner legacy analysis JSON parse failed")
            analysis = {
                "raw_analysis": response,
                "user_stories": [],
                "priorities": {},
                "acceptance_criteria": {},
                "constraints": []
            }

        return {
            "agent": self.name,
            "role": self.role,
            "processing_mode": "legacy",
            "analysis": analysis,
            "action_items": await self.extract_action_items(conv_text),
            "decisions": await self.summarize_decisions(conversation)
        }

    async def _extract_user_stories_enhanced(
        self,
        conv_text: str,
        results: Dict[str, Any],
        context: ProjectContext
    ):
        """Extract user stories using DevNous PM tools."""
        try:
            # Check for existing related stories
            existing_stories = await self.use_tool(
                "get_tasks",
                filters={
                    "type": "story",
                    "status": ["open", "in_progress"],
                    "search_text": conv_text[:100]  # Search first 100 chars
                }
            )

            if existing_stories.get("success"):
                related_stories = existing_stories.get("data", {}).get("items", [])
                results["devnous_insights"]["related_existing_stories"] = len(related_stories)

                # Generate insights about story relationships
                if related_stories:
                    results["devnous_insights"]["story_relationships"] = [
                        f"Related to existing story: {story.get('title', 'Unknown')}"
                        for story in related_stories[:3]  # Top 3 related
                    ]

        except Exception:
            self.logger.warning("Enhanced story extraction failed", exc_info=True)

    async def _get_legacy_analysis(
        self,
        conv_text: str,
        conversation: List[Message]
    ) -> Dict[str, Any]:
        """Get legacy analysis for comparison."""
        prompt = f"""Extract user stories, priorities, and acceptance criteria from:
        {conv_text}

        Format as JSON with keys: user_stories, priorities, acceptance_criteria, constraints"""

        messages = [{"role": "user", "content": prompt}]
        response = await self.generate_response(messages)

        try:
            return json.loads(response)
        except (json.JSONDecodeError, TypeError):
            self.logger.exception("ProductOwner legacy analysis (PO) JSON parse failed")
            return {
                "raw_analysis": response,
                "user_stories": [],
                "priorities": {},
                "acceptance_criteria": {},
                "constraints": []
            }

    async def _generate_po_recommendations(
        self,
        results: Dict[str, Any],
        context: ProjectContext,
        intent_analysis: Dict[str, Any]
    ) -> List[str]:
        """Generate Product Owner specific recommendations."""
        recommendations = []

        # Story prioritization recommendations
        if "task_management" in intent_analysis.get("intents", []):
            recommendations.append("Consider story mapping session for complex features")
            recommendations.append("Review acceptance criteria with development team")

        # Workflow recommendations
        if context.workflow_id and results["devnous_insights"].get("workflow_context"):
            workflow_status = results["devnous_insights"]["workflow_context"].get("status")
            if workflow_status == "blocked":
                recommendations.append("Prioritize removing workflow blockers")

        # Backlog management
        if context.backlog and len(context.backlog) > 20:
            recommendations.append("Consider backlog grooming session - backlog getting large")

        # Team coordination
        if len(set(msg.sender for msg in results.get("conversation", []))) > 5:
            recommendations.append("Large discussion - consider follow-up with key stakeholders")

        return recommendations

    def create_user_story(
        self,
        user_type: str,
        feature: str,
        benefit: str,
        acceptance_criteria: List[str] = None,
        priority: str = "Medium"
    ) -> Dict[str, Any]:
        """Legacy user story creation (preserved for backward compatibility)."""
        return {
            "story": f"As a {user_type}, I want {feature} so that {benefit}",
            "acceptance_criteria": acceptance_criteria or [],
            "priority": priority,
            "status": "New",
            "story_points": None
        }

    async def create_user_story_enhanced(
        self,
        user_type: str,
        feature: str,
        benefit: str,
        acceptance_criteria: List[str] = None,
        priority: str = "Medium",
        context: Optional[ProjectContext] = None,
        auto_create_tasks: bool = False
    ) -> Dict[str, Any]:
        """Enhanced user story creation with DevNous integration."""

        # Create base story
        story = self.create_user_story(user_type, feature, benefit, acceptance_criteria, priority)

        enhanced_story = {
            **story,
            "created_by": self.name,
            "created_at": datetime.utcnow().isoformat(),
            "devnous_enhanced": True,
            "metadata": {}
        }

        if not self.enable_devnous_integration or not self.devnous_agent:
            return enhanced_story

        try:
            # Check for similar stories using PM tools
            similar_stories_result = await self.use_tool(
                "get_tasks",
                filters={
                    "type": "story",
                    "search_text": f"{user_type} {feature}"
                }
            )

            if similar_stories_result.get("success"):
                similar_count = len(similar_stories_result.get("data", {}).get("items", []))
                enhanced_story["metadata"]["similar_stories_found"] = similar_count

                if similar_count > 0:
                    enhanced_story["metadata"]["warning"] = "Similar stories exist - review for duplication"

            # Create the story in the PM system if enabled
            if auto_create_tasks and self.is_feature_enabled("auto_create_pm_tasks"):
                from devnous.models import TaskCreate, TaskPriority

                task_priority = TaskPriority.HIGH if priority == "High" else TaskPriority.MEDIUM
                task_data = TaskCreate(
                    title=f"Story: {feature}",
                    description=enhanced_story["story"],
                    priority=task_priority,
                    type="story",
                    metadata={
                        "user_type": user_type,
                        "benefit": benefit,
                        "acceptance_criteria": acceptance_criteria or [],
                        "created_by_agent": self.name
                    }
                )

                create_result = await self.use_tool("create_task", task_data)
                if create_result.get("success"):
                    enhanced_story["pm_task_id"] = create_result.get("data", {}).get("id")
                    enhanced_story["metadata"]["auto_created_in_pm"] = True

            # Store in memory for future reference
            if self.is_feature_enabled("memory_integration"):
                story_key = f"story_{user_type}_{feature}".replace(" ", "_").lower()
                await self.use_tool("memorize", story_key, json.dumps(enhanced_story))

        except Exception as e:
            self.logger.warning(f"Enhanced story creation partially failed: {e}")
            enhanced_story["metadata"]["enhancement_errors"] = [str(e)]

        return enhanced_story

    async def prioritize_backlog(
        self,
        backlog_items: List[Dict[str, Any]],
        criteria: Optional[Dict[str, float]] = None
    ) -> List[Dict[str, Any]]:
        """Enhanced backlog prioritization using DevNous tools."""

        if not self.enable_devnous_integration or not self.devnous_agent:
            # Legacy prioritization - simple sorting by priority field
            priority_order = {"High": 3, "Medium": 2, "Low": 1}
            return sorted(
                backlog_items,
                key=lambda x: priority_order.get(x.get("priority", "Medium"), 2),
                reverse=True
            )

        try:
            # Use DevNous tools to analyze dependencies and priorities
            enhanced_items = []

            for item in backlog_items:
                enhanced_item = {**item}

                # Check dependencies using PM tools
                if item.get("pm_task_id"):
                    task_result = await self.use_tool(
                        "get_tasks",
                        filters={"id": item["pm_task_id"]}
                    )

                    if task_result.get("success"):
                        task_data = task_result.get("data", {}).get("items", [])
                        if task_data:
                            task = task_data[0]
                            enhanced_item["dependencies"] = task.get("dependencies", [])
                            enhanced_item["blocked_by"] = task.get("blocked_by", [])

                # Calculate enhanced priority score
                priority_score = self._calculate_priority_score(enhanced_item, criteria)
                enhanced_item["calculated_priority_score"] = priority_score

                enhanced_items.append(enhanced_item)

            # Sort by calculated priority score
            return sorted(
                enhanced_items,
                key=lambda x: x.get("calculated_priority_score", 0),
                reverse=True
            )

        except Exception as e:
            self.logger.error(f"Enhanced prioritization failed: {e}, using legacy method")
            # Fallback to legacy prioritization
            priority_order = {"High": 3, "Medium": 2, "Low": 1}
            return sorted(
                backlog_items,
                key=lambda x: priority_order.get(x.get("priority", "Medium"), 2),
                reverse=True
            )

    def _calculate_priority_score(
        self,
        item: Dict[str, Any],
        criteria: Optional[Dict[str, float]] = None
    ) -> float:
        """Calculate enhanced priority score for backlog items."""
        default_criteria = {
            "business_value": 0.4,
            "urgency": 0.3,
            "effort": 0.2,  # Lower effort = higher priority
            "dependencies": 0.1  # Fewer dependencies = higher priority
        }

        criteria = criteria or default_criteria
        score = 0.0

        # Business value (1-10 scale)
        business_value = item.get("business_value", 5)
        score += (business_value / 10) * criteria["business_value"]

        # Urgency based on priority
        urgency_map = {"High": 10, "Medium": 5, "Low": 1}
        urgency = urgency_map.get(item.get("priority", "Medium"), 5)
        score += (urgency / 10) * criteria["urgency"]

        # Effort (inverse - lower effort is better)
        effort = item.get("story_points", 5)  # Default to medium effort
        effort_score = max(0, (10 - effort) / 10)  # Invert so lower effort = higher score
        score += effort_score * criteria["effort"]

        # Dependencies (inverse - fewer dependencies is better)
        dependency_count = len(item.get("dependencies", []))
        dependency_score = max(0, (10 - dependency_count) / 10)
        score += dependency_score * criteria["dependencies"]

        return score


class ScrumMasterAgent(BaseAgent):
    def __init__(self, name: str = "Scrum Master", **kwargs):
        super().__init__(name=name, role="Scrum Master", **kwargs)
        self.tool_adapter = ToolIntegrationAdapter(self)
        self.logger = logging.getLogger(f"ScrumMasterAgent:{name}")

        # DevNous-enhanced capabilities
        self.sprint_metrics = {
            "velocity": 0,
            "burndown_rate": 0,
            "blocker_resolution_time": 0,
            "team_satisfaction": 0
        }

    def get_system_prompt(self) -> str:
        return """You are a Scrum Master facilitating an Agile team. Your responsibilities include:
        - Identifying and removing blockers
        - Facilitating team communication and collaboration
        - Tracking sprint progress and velocity
        - Organizing and summarizing daily standups, sprint planning, and retrospectives
        - Ensuring the team follows Scrum practices
        - Managing team dynamics and conflicts

        Analyze conversations to identify:
        1. Team blockers and impediments
        2. Sprint progress updates
        3. Team collaboration issues
        4. Process improvements needed
        5. Action items from meetings

        Format your outputs to track sprint health, team velocity, and action items."""

    async def process_conversation(
        self,
        conversation: List[Message],
        context: ProjectContext
    ) -> Dict[str, Any]:
        """Enhanced conversation processing with DevNous workflow integration."""

        # Use hybrid processing if DevNous is available
        if self.enable_devnous_integration and self.devnous_agent:
            return await self._enhanced_process_conversation(conversation, context)
        else:
            return await self._legacy_process_conversation(conversation, context)

    async def _enhanced_process_conversation(
        self,
        conversation: List[Message],
        context: ProjectContext
    ) -> Dict[str, Any]:
        """Enhanced processing using DevNous workflow and communication tools."""
        conv_text = "\n".join([f"{msg.sender}: {msg.content}" for msg in conversation])

        # Analyze conversation intent
        intent_analysis = await self.analyze_conversation_intent(conv_text)

        # Initialize results structure
        results = {
            "agent": self.name,
            "role": self.role,
            "processing_mode": "enhanced",
            "timestamp": datetime.utcnow().isoformat(),
            "analysis": {},
            "devnous_insights": {},
            "blockers": [],
            "action_items": [],
            "decisions": {},
            "recommendations": [],
            "sprint_health": "Unknown"
        }

        try:
            # Enhanced blocker identification using both legacy and DevNous tools
            enhanced_blockers = await self.tool_adapter.adaptive_blocker_identification(
                conversation, use_devnous=True
            )
            results["blockers"] = enhanced_blockers["blockers"]
            results["devnous_insights"]["blocker_analysis"] = enhanced_blockers

            # Use workflow tools to check sprint progress
            if context.workflow_id:
                workflow_state = await self.use_tool("get_workflow_state", context.workflow_id)
                if workflow_state.get("success"):
                    workflow_data = workflow_state.get("data", {})
                    results["devnous_insights"]["workflow_state"] = workflow_data
                    results["sprint_health"] = self._assess_sprint_health(workflow_data)

            # Track team communication patterns
            if self.is_feature_enabled("communication_analytics"):
                comm_insights = await self._analyze_team_communication(conversation)
                results["devnous_insights"]["communication_patterns"] = comm_insights

            # Enhanced action item extraction
            enhanced_actions = await self.tool_adapter.adaptive_action_extraction(conv_text)
            results["action_items"] = enhanced_actions

            # Generate Scrum Master specific recommendations
            results["recommendations"] = await self._generate_sm_recommendations(
                results, context, intent_analysis
            )

            # Legacy analysis for comparison
            legacy_analysis = await self._get_legacy_sm_analysis(conv_text, conversation)
            results["analysis"] = legacy_analysis

        except Exception:
            self.logger.exception("Enhanced processing failed, falling back to legacy")
            return await self._legacy_process_conversation(conversation, context)

        return results

    async def _legacy_process_conversation(
        self,
        conversation: List[Message],
        context: ProjectContext
    ) -> Dict[str, Any]:
        """Legacy conversation processing (preserved for backward compatibility)."""
        conv_text = "\n".join([f"{msg.sender}: {msg.content}" for msg in conversation])

        prompt = f"""As a Scrum Master, analyze this team conversation and identify:
        1. Current blockers or impediments
        2. Progress updates on tasks
        3. Team collaboration issues or conflicts
        4. Process improvements suggested
        5. Action items and who owns them
        6. Sprint health indicators

        Conversation:
        {conv_text}

        Provide the analysis in JSON format."""

        messages = [{"role": "user", "content": prompt}]
        response = await self.generate_response(messages)

        try:
            analysis = json.loads(response)
        except (json.JSONDecodeError, TypeError) as e:
            self.logger.exception("ScrumMaster legacy analysis JSON parse failed")
            analysis = {
                "raw_analysis": response,
                "blockers": [],
                "progress_updates": [],
                "collaboration_issues": [],
                "process_improvements": [],
                "sprint_health": "Unknown"
            }

        blockers = await self.identify_blockers(conversation)

        return {
            "agent": self.name,
            "role": self.role,
            "processing_mode": "legacy",
            "analysis": analysis,
            "blockers": blockers,
            "action_items": await self.extract_action_items(conv_text),
            "decisions": await self.summarize_decisions(conversation)
        }

    def _assess_sprint_health(self, workflow_data: Dict[str, Any]) -> str:
        """Assess sprint health based on workflow state."""
        status = workflow_data.get("status", "unknown")
        progress = workflow_data.get("progress_percentage", 0)

        if status == "blocked":
            return "At Risk - Blockers Present"
        elif progress < 25:
            return "Healthy - Early Sprint"
        elif progress < 50:
            return "Healthy - On Track"
        elif progress < 75:
            return "Good - Mid Sprint"
        elif progress < 90:
            return "Good - Nearing Completion"
        else:
            return "Excellent - Sprint Nearly Complete"

    async def _analyze_team_communication(self, conversation: List[Message]) -> Dict[str, Any]:
        """Analyze team communication patterns."""
        senders = [msg.sender for msg in conversation if msg.sender]
        unique_senders = set(senders)

        # Calculate communication distribution
        sender_counts = {}
        for sender in senders:
            sender_counts[sender] = sender_counts.get(sender, 0) + 1

        # Identify dominant speakers (more than 40% of messages)
        total_messages = len(senders)
        dominant_speakers = [
            sender for sender, count in sender_counts.items()
            if count / total_messages > 0.4
        ]

        # Check for balanced participation
        participation_balance = "balanced" if len(dominant_speakers) == 0 else "imbalanced"

        return {
            "unique_participants": len(unique_senders),
            "total_messages": total_messages,
            "message_distribution": sender_counts,
            "dominant_speakers": dominant_speakers,
            "participation_balance": participation_balance,
            "engagement_score": min(len(unique_senders) / max(1, total_messages / 3), 1.0)
        }

    async def _generate_sm_recommendations(
        self,
        results: Dict[str, Any],
        context: ProjectContext,
        intent_analysis: Dict[str, Any]
    ) -> List[str]:
        """Generate Scrum Master specific recommendations."""
        recommendations = []

        # Blocker recommendations
        if len(results["blockers"]) > 0:
            recommendations.append(f"Address {len(results['blockers'])} identified blockers in next standup")
            recommendations.append("Consider dedicating time for blocker resolution")

        # Communication recommendations
        comm_insights = results["devnous_insights"].get("communication_patterns", {})
        if comm_insights.get("participation_balance") == "imbalanced":
            recommendations.append("Facilitate more balanced team participation")

        if comm_insights.get("engagement_score", 1.0) < 0.5:
            recommendations.append("Consider breaking down discussion into smaller groups")

        # Workflow recommendations
        workflow_state = results["devnous_insights"].get("workflow_state", {})
        if workflow_state.get("status") == "blocked":
            recommendations.append("Escalate workflow blockers to leadership")

        # Sprint health recommendations
        if "At Risk" in results.get("sprint_health", ""):
            recommendations.append("Schedule risk mitigation session")
            recommendations.append("Consider sprint scope adjustment")

        return recommendations

    async def _get_legacy_sm_analysis(
        self,
        conv_text: str,
        conversation: List[Message]
    ) -> Dict[str, Any]:
        """Get legacy analysis for comparison."""
        prompt = f"""Analyze for blockers, progress updates, team issues, improvements:
        {conv_text}

        Format as JSON with keys: blockers, progress_updates, collaboration_issues, process_improvements, sprint_health"""

        messages = [{"role": "user", "content": prompt}]
        response = await self.generate_response(messages)

        try:
            return json.loads(response)
        except (json.JSONDecodeError, TypeError):
            self.logger.exception("ScrumMaster legacy SM analysis JSON parse failed")
            return {
                "raw_analysis": response,
                "blockers": [],
                "progress_updates": [],
                "collaboration_issues": [],
                "process_improvements": [],
                "sprint_health": "Unknown"
            }

    def generate_standup_summary(self, updates: List[Dict[str, str]]) -> str:
        summary = "Daily Standup Summary:\n\n"
        for update in updates:
            summary += f"**{update.get('member', 'Unknown')}:**\n"
            summary += f"- Yesterday: {update.get('yesterday', 'No update')}\n"
            summary += f"- Today: {update.get('today', 'No update')}\n"
            summary += f"- Blockers: {update.get('blockers', 'None')}\n\n"
        return summary


class DeveloperAgent(BaseAgent):
    def __init__(self, name: str = "Developer", **kwargs):
        super().__init__(name=name, role="Developer", **kwargs)
        self.tool_adapter = ToolIntegrationAdapter(self)
        self.logger = logging.getLogger(f"DeveloperAgent:{name}")

        # DevNous-enhanced capabilities
        self.technical_patterns = {
            "architecture": ["microservices", "monolith", "serverless", "api", "database"],
            "frameworks": ["react", "angular", "vue", "spring", "django", "flask"],
            "deployment": ["docker", "kubernetes", "aws", "azure", "gcp", "ci/cd"],
            "testing": ["unit test", "integration test", "e2e", "tdd", "bdd"]
        }

    def get_system_prompt(self) -> str:
        return """You are a Senior Developer in an Agile team. Your responsibilities include:
        - Identifying technical requirements and constraints
        - Estimating development effort and complexity
        - Proposing technical solutions and architectures
        - Identifying technical debt and risks
        - Breaking down features into technical tasks
        - Reviewing code quality concerns

        Analyze conversations to identify:
        1. Technical requirements and specifications
        2. Architecture decisions and patterns
        3. Technical dependencies and integrations
        4. Development effort estimates
        5. Technical risks and debt
        6. Code quality and testing requirements

        Format your outputs to include technical tasks, estimates, and architectural decisions."""

    async def process_conversation(
        self,
        conversation: List[Message],
        context: ProjectContext
    ) -> Dict[str, Any]:
        """Enhanced conversation processing with DevNous PM tool integration."""

        # Use hybrid processing if DevNous is available
        if self.enable_devnous_integration and self.devnous_agent:
            return await self._enhanced_process_conversation(conversation, context)
        else:
            return await self._legacy_process_conversation(conversation, context)

    async def _enhanced_process_conversation(
        self,
        conversation: List[Message],
        context: ProjectContext
    ) -> Dict[str, Any]:
        """Enhanced processing using DevNous PM and workflow tools."""
        conv_text = "\n".join([f"{msg.sender}: {msg.content}" for msg in conversation])

        # Analyze conversation intent
        intent_analysis = await self.analyze_conversation_intent(conv_text)

        # Initialize results structure
        results = {
            "agent": self.name,
            "role": self.role,
            "processing_mode": "enhanced",
            "timestamp": datetime.utcnow().isoformat(),
            "analysis": {},
            "devnous_insights": {},
            "action_items": [],
            "decisions": {},
            "recommendations": [],
            "technical_assessment": {}
        }

        try:
            # Technical pattern analysis
            tech_patterns = self._analyze_technical_patterns(conv_text)
            results["technical_assessment"]["patterns_detected"] = tech_patterns

            # Enhanced task breakdown using PM tools
            if "task_management" in intent_analysis.get("intents", []):
                await self._analyze_existing_tasks(conv_text, results, context)

            # Architecture and dependency analysis
            if self.is_feature_enabled("architecture_analysis"):
                arch_insights = await self._analyze_architecture_implications(conv_text)
                results["devnous_insights"]["architecture"] = arch_insights

            # Enhanced action item extraction with technical focus
            enhanced_actions = await self.tool_adapter.adaptive_action_extraction(conv_text)
            results["action_items"] = enhanced_actions

            # Generate developer specific recommendations
            results["recommendations"] = await self._generate_dev_recommendations(
                results, context, intent_analysis
            )

            # Legacy analysis for comparison
            legacy_analysis = await self._get_legacy_dev_analysis(conv_text, conversation)
            results["analysis"] = legacy_analysis

        except Exception:
            self.logger.exception("Enhanced processing failed, falling back to legacy")
            return await self._legacy_process_conversation(conversation, context)

        return results

    async def _legacy_process_conversation(
        self,
        conversation: List[Message],
        context: ProjectContext
    ) -> Dict[str, Any]:
        """Legacy conversation processing (preserved for backward compatibility)."""
        conv_text = "\n".join([f"{msg.sender}: {msg.content}" for msg in conversation])

        prompt = f"""As a Senior Developer, analyze this conversation and extract:
        1. Technical requirements and specifications
        2. Proposed technical solutions or architectures
        3. Development tasks that need to be done
        4. Technical dependencies or integrations needed
        5. Estimated effort (in story points or hours)
        6. Technical risks or debt identified
        7. Testing requirements

        Conversation:
        {conv_text}

        Provide the analysis in JSON format."""

        messages = [{"role": "user", "content": prompt}]
        response = await self.generate_response(messages)

        try:
            analysis = json.loads(response)
        except (json.JSONDecodeError, TypeError) as e:
            self.logger.exception("Developer legacy analysis JSON parse failed")
            analysis = {
                "raw_analysis": response,
                "technical_requirements": [],
                "proposed_solutions": [],
                "development_tasks": [],
                "dependencies": [],
                "estimates": {},
                "technical_risks": [],
                "testing_requirements": []
            }

        return {
            "agent": self.name,
            "role": self.role,
            "processing_mode": "legacy",
            "analysis": analysis,
            "action_items": await self.extract_action_items(conv_text),
            "decisions": await self.summarize_decisions(conversation)
        }

    def _analyze_technical_patterns(self, text: str) -> Dict[str, List[str]]:
        """Analyze conversation for technical patterns and keywords."""
        detected_patterns = {}
        text_lower = text.lower()

        for category, patterns in self.technical_patterns.items():
            detected = [pattern for pattern in patterns if pattern in text_lower]
            if detected:
                detected_patterns[category] = detected

        return detected_patterns

    async def _analyze_existing_tasks(
        self,
        conv_text: str,
        results: Dict[str, Any],
        context: ProjectContext
    ):
        """Analyze existing technical tasks using PM tools."""
        try:
            # Get technical tasks from PM system
            tech_tasks = await self.use_tool(
                "get_tasks",
                filters={
                    "type": ["task", "bug", "technical_debt"],
                    "status": ["open", "in_progress"],
                    "assignee": context.team_members[0] if context.team_members else None
                }
            )

            if tech_tasks.get("success"):
                tasks = tech_tasks.get("data", {}).get("items", [])
                results["devnous_insights"]["existing_technical_tasks"] = len(tasks)

                # Analyze task complexity distribution
                complexity_dist = {}
                for task in tasks:
                    complexity = task.get("story_points", 1)
                    complexity_dist[str(complexity)] = complexity_dist.get(str(complexity), 0) + 1

                results["devnous_insights"]["task_complexity_distribution"] = complexity_dist

        except Exception as e:
            self.logger.warning(f"Technical task analysis failed: {e}")

    async def _analyze_architecture_implications(self, conv_text: str) -> Dict[str, Any]:
        """Analyze architectural implications of the conversation."""
        implications = {
            "scalability_concerns": [],
            "integration_points": [],
            "technology_decisions": [],
            "risk_factors": []
        }

        # Simple pattern matching - in real implementation, this could use ML
        if "scale" in conv_text.lower() or "performance" in conv_text.lower():
            implications["scalability_concerns"].append("Performance/scalability discussed")

        if "api" in conv_text.lower() or "integration" in conv_text.lower():
            implications["integration_points"].append("API/integration requirements mentioned")

        tech_patterns = self._analyze_technical_patterns(conv_text)
        if tech_patterns:
            implications["technology_decisions"] = list(tech_patterns.keys())

        return implications

    async def _generate_dev_recommendations(
        self,
        results: Dict[str, Any],
        context: ProjectContext,
        intent_analysis: Dict[str, Any]
    ) -> List[str]:
        """Generate Developer specific recommendations."""
        recommendations = []

        # Technical pattern recommendations
        tech_patterns = results["technical_assessment"].get("patterns_detected", {})
        if "testing" in tech_patterns:
            recommendations.append("Ensure comprehensive test coverage for discussed features")

        if "deployment" in tech_patterns:
            recommendations.append("Consider CI/CD pipeline implications")

        # Task complexity recommendations
        task_insights = results["devnous_insights"]
        complexity_dist = task_insights.get("task_complexity_distribution", {})
        high_complexity_tasks = sum(int(k) * v for k, v in complexity_dist.items() if int(k) > 5)

        if high_complexity_tasks > 0:
            recommendations.append("Break down complex tasks for better estimation")

        # Architecture recommendations
        arch_insights = task_insights.get("architecture", {})
        if arch_insights.get("scalability_concerns"):
            recommendations.append("Document scalability decisions and assumptions")

        return recommendations

    async def _get_legacy_dev_analysis(
        self,
        conv_text: str,
        conversation: List[Message]
    ) -> Dict[str, Any]:
        """Get legacy analysis for comparison."""
        prompt = f"""Extract technical requirements, solutions, tasks, dependencies, estimates, risks:
        {conv_text}

        Format as JSON with keys: technical_requirements, proposed_solutions, development_tasks, dependencies, estimates, technical_risks, testing_requirements"""

        messages = [{"role": "user", "content": prompt}]
        response = await self.generate_response(messages)

        try:
            return json.loads(response)
        except (json.JSONDecodeError, TypeError):
            self.logger.exception("Developer legacy dev analysis JSON parse failed")
            return {
                "raw_analysis": response,
                "technical_requirements": [],
                "proposed_solutions": [],
                "development_tasks": [],
                "dependencies": [],
                "estimates": {},
                "technical_risks": [],
                "testing_requirements": []
            }

    async def estimate_story_points(self, task_description: str) -> int:
        prompt = f"""Estimate the story points (1, 2, 3, 5, 8, 13) for this task:
        {task_description}

        Consider complexity, effort, and uncertainty.
        Return only the number."""

        messages = [{"role": "user", "content": prompt}]
        response = await self.generate_response(messages)

        try:
            return int(response.strip())
        except (ValueError, TypeError):
            self.logger.warning("Estimate parsing failed; defaulting to 5 story points")
            return 5

    async def break_down_feature(self, feature_description: str) -> List[Dict[str, Any]]:
        prompt = f"""Break down this feature into technical tasks:
        {feature_description}

        For each task provide:
        1. Task name
        2. Description
        3. Technical approach
        4. Dependencies
        5. Estimated hours

        Format as JSON array."""

        messages = [{"role": "user", "content": prompt}]
        response = await self.generate_response(messages)

        try:
            return json.loads(response)
        except (json.JSONDecodeError, TypeError):
            self.logger.exception("Feature breakdown JSON parse failed")
            return []
