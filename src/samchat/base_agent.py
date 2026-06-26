from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Callable, Union
from dataclasses import dataclass
import asyncio
from enum import Enum
import os
import logging
from datetime import datetime

# Optional imports - will be None if packages not installed
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None


class LLMProvider(Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"


@dataclass
class Message:
    role: str
    content: str
    sender: Optional[str] = None
    timestamp: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ProjectContext:
    conversation_history: List[Message]
    project_goals: List[str]
    current_sprint: Optional[Dict[str, Any]] = None
    backlog: List[Dict[str, Any]] = None
    team_members: List[str] = None
    metadata: Optional[Dict[str, Any]] = None
    # DevNous integration fields
    workflow_id: Optional[str] = None
    active_tools: Optional[List[str]] = None
    tool_context: Optional[Dict[str, Any]] = None


class BaseAgent(ABC):
    def __init__(
        self,
        name: str,
        role: str,
        llm_provider: LLMProvider = LLMProvider.OPENAI,
        model: str = "gpt-4",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        enable_devnous_integration: bool = False,
        feature_flags: Optional[Dict[str, bool]] = None
    ):
        self.name = name
        self.role = role
        self.llm_provider = llm_provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.conversation_history: List[Message] = []
        self.llm_client = None  # Initialize lazily
        
        # DevNous integration - Strangler Fig Pattern
        self.enable_devnous_integration = enable_devnous_integration
        self.feature_flags = feature_flags or {}
        self.devnous_agent = None  # Will be injected if enabled
        self.tool_registry: Dict[str, Callable] = {}
        self.tool_cache: Dict[str, Any] = {}
        self.logger = logging.getLogger(f"{self.__class__.__name__}:{name}")
        
        # Legacy compatibility flag
        self._legacy_mode = not enable_devnous_integration

    def _get_llm_client(self):
        """Get LLM client, initializing lazily if needed."""
        if self.llm_client is None:
            self.llm_client = self._initialize_llm()
        return self.llm_client
    
    def _initialize_llm(self):
        if self.llm_provider == LLMProvider.OPENAI:
            if OpenAI is None:
                raise ImportError("OpenAI package not installed. Install with: pip install openai")
            return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        elif self.llm_provider == LLMProvider.ANTHROPIC:
            if Anthropic is None:
                raise ImportError("Anthropic package not installed. Install with: pip install anthropic")
            return Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        else:
            return None

    @abstractmethod
    def get_system_prompt(self) -> str:
        pass

    @abstractmethod
    async def process_conversation(
        self, 
        conversation: List[Message], 
        context: ProjectContext
    ) -> Dict[str, Any]:
        pass

    async def generate_response(
        self, 
        messages: List[Dict[str, str]], 
        system_prompt: Optional[str] = None
    ) -> str:
        if system_prompt is None:
            system_prompt = self.get_system_prompt()

        client = self._get_llm_client()

        if self.llm_provider == LLMProvider.OPENAI:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *messages
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            return response.choices[0].message.content

        elif self.llm_provider == LLMProvider.ANTHROPIC:
            formatted_messages = []
            for msg in messages:
                formatted_messages.append({
                    "role": "user" if msg["role"] == "user" else "assistant",
                    "content": msg["content"]
                })
            
            response = client.messages.create(
                model=self.model,
                system=system_prompt,
                messages=formatted_messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            return response.content[0].text

        else:
            return "LLM provider not implemented"

    async def extract_action_items(self, text: str) -> List[str]:
        prompt = f"""Extract all action items from the following text.
        Return them as a numbered list.

        Text: {text}

        Action Items:"""

        messages = [{"role": "user", "content": prompt}]
        response = await self.generate_response(messages)

        action_items: List[str] = []
        for line in response.split('\n'):
            if line.strip() and (line.strip()[0].isdigit() or line.strip().startswith('-')):
                action_items.append(line.strip())

        return action_items

    async def identify_blockers(self, conversation: List[Message]) -> List[str]:
        conv_text = "\n".join([f"{msg.sender}: {msg.content}" for msg in conversation])
        prompt = f"""Identify any blockers or impediments mentioned in this conversation.
        
        Conversation:
        {conv_text}
        
        Blockers:"""
        
        messages = [{"role": "user", "content": prompt}]
        response = await self.generate_response(messages)
        
        blockers: List[str] = []
        for line in response.split('\n'):
            if line.strip():
                blockers.append(line.strip())
        
        return blockers

    async def summarize_decisions(self, conversation: List[Message]) -> Dict[str, Any]:
        conv_text = "\n".join([f"{msg.sender}: {msg.content}" for msg in conversation])
        prompt = f"""Summarize the key decisions made in this conversation.
        Include who made the decision and what was decided.
        
        Conversation:
        {conv_text}
        
        Decisions:"""
        
        messages = [{"role": "user", "content": prompt}]
        response = await self.generate_response(messages)
        
        return {
            "summary": response,
            "timestamp": asyncio.get_event_loop().time()
        }

    # DevNous Tool Integration Methods
    
    def inject_devnous_agent(self, devnous_agent):
        """Inject DevNous agent for tool integration (Dependency Injection)."""
        if not self.enable_devnous_integration:
            self.logger.warning("DevNous integration disabled, injection ignored")
            return
        
        self.devnous_agent = devnous_agent
        self._register_devnous_tools()
        self.logger.info(f"DevNous agent injected into {self.name}")

    def _register_devnous_tools(self):
        """Register DevNous tools in the agent's tool registry."""
        if not self.devnous_agent:
            return
        
        # Memory tools
        self.tool_registry["memorize"] = self.devnous_agent.memorize_string
        self.tool_registry["get_conversation_history"] = self.devnous_agent.get_conversation_history
        self.tool_registry["load_team_info"] = self.devnous_agent.load_team_info
        
        # Chat tools
        self.tool_registry["send_message"] = self.devnous_agent.send_message
        self.tool_registry["process_message"] = self.devnous_agent.process_message
        
        # PM tools
        self.tool_registry["get_tasks"] = self.devnous_agent.get_tasks
        self.tool_registry["create_task"] = self.devnous_agent.create_task
        self.tool_registry["update_task"] = self.devnous_agent.update_task
        
        # Workflow tools
        self.tool_registry["start_workflow"] = self.devnous_agent.start_workflow
        self.tool_registry["update_workflow"] = self.devnous_agent.update_workflow_data
        self.tool_registry["get_workflow_state"] = self.devnous_agent.get_workflow_state
        self.tool_registry["end_workflow"] = self.devnous_agent.end_workflow
        
        self.logger.info(f"Registered {len(self.tool_registry)} DevNous tools")

    def is_feature_enabled(self, feature_name: str) -> bool:
        """Check if a feature is enabled via feature flags."""
        return self.feature_flags.get(feature_name, False)

    async def use_tool(
        self, 
        tool_name: str, 
        *args, 
        use_cache: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Use a registered tool with intelligent caching and error handling.
        
        Args:
            tool_name: Name of the tool to use
            *args: Positional arguments for the tool
            use_cache: Whether to use cached results
            **kwargs: Keyword arguments for the tool
            
        Returns:
            Tool execution result
        """
        try:
            if not self.enable_devnous_integration:
                return {
                    "success": False,
                    "error": "DevNous integration disabled",
                    "fallback_used": True
                }
            
            if tool_name not in self.tool_registry:
                self.logger.warning(f"Tool {tool_name} not registered")
                return {
                    "success": False,
                    "error": f"Tool {tool_name} not available"
                }
            
            # Check cache first
            cache_key = self._generate_cache_key(tool_name, args, kwargs)
            if use_cache and cache_key in self.tool_cache:
                cached_result = self.tool_cache[cache_key]
                if self._is_cache_valid(cached_result):
                    self.logger.debug(f"Using cached result for {tool_name}")
                    return cached_result["result"]
            
            # Execute tool
            tool_func = self.tool_registry[tool_name]
            result = await tool_func(*args, **kwargs)
            
            # Cache result
            if use_cache and result.get("success", False):
                self.tool_cache[cache_key] = {
                    "result": result,
                    "timestamp": datetime.utcnow().timestamp(),
                    "ttl": 300  # 5 minutes default TTL
                }
            
            self.logger.debug(f"Successfully executed tool {tool_name}")
            return result
            
        except Exception as e:
            self.logger.error(f"Error executing tool {tool_name}: {e}")
            return {
                "success": False,
                "error": str(e),
                "tool_name": tool_name
            }

    def _generate_cache_key(self, tool_name: str, args, kwargs) -> str:
        """Generate cache key for tool execution."""
        import hashlib
        content = f"{tool_name}:{str(args)}:{str(sorted(kwargs.items()))}"
        return hashlib.md5(content.encode()).hexdigest()

    def _is_cache_valid(self, cached_entry: Dict[str, Any]) -> bool:
        """Check if cached entry is still valid."""
        if "timestamp" not in cached_entry or "ttl" not in cached_entry:
            return False
        
        age = datetime.utcnow().timestamp() - cached_entry["timestamp"]
        return age < cached_entry["ttl"]

    async def analyze_conversation_intent(self, message: str) -> Dict[str, Any]:
        """Analyze conversation message to determine tool usage intent."""
        if not self.enable_devnous_integration or not self.devnous_agent:
            # Legacy fallback
            return {"intents": [], "confidence": 0.0, "legacy_mode": True}
        
        # Use DevNous agent's intent analysis
        return await self.devnous_agent._analyze_message_intent(message)

    async def orchestrate_tools(
        self, 
        conversation: List[Message], 
        context: ProjectContext
    ) -> Dict[str, Any]:
        """
        Orchestrate multiple tools based on conversation context.
        
        This method implements the hybrid approach, using both legacy
        and modern tool capabilities.
        """
        results = {
            "legacy_analysis": {},
            "devnous_analysis": {},
            "combined_insights": {},
            "tools_used": [],
            "success": True
        }
        
        try:
            # Always run legacy analysis for backward compatibility
            results["legacy_analysis"] = await self._legacy_process_conversation(
                conversation, context
            )
            
            # Run DevNous analysis if enabled
            if self.enable_devnous_integration and self.devnous_agent:
                devnous_result = await self.devnous_agent.process_conversation(
                    conversation, context
                )
                results["devnous_analysis"] = devnous_result
                results["tools_used"] = devnous_result.get("actions_taken", [])
                
                # Combine insights from both approaches
                results["combined_insights"] = self._combine_analyses(
                    results["legacy_analysis"],
                    results["devnous_analysis"]
                )
            
            return results
            
        except Exception as e:
            self.logger.error(f"Error in tool orchestration: {e}")
            results["success"] = False
            results["error"] = str(e)
            return results

    def _combine_analyses(
        self, 
        legacy_result: Dict[str, Any], 
        devnous_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Combine insights from legacy and DevNous analyses."""
        combined = {
            "action_items": [],
            "blockers": [],
            "decisions": [],
            "suggestions": [],
            "confidence_score": 0.0
        }
        
        # Merge action items
        legacy_actions = legacy_result.get("action_items", [])
        devnous_suggestions = devnous_result.get("suggestions", [])
        combined["action_items"] = list(set(legacy_actions + devnous_suggestions))
        
        # Merge blockers
        legacy_blockers = legacy_result.get("blockers", [])
        combined["blockers"] = legacy_blockers
        
        # Merge decisions
        legacy_decisions = legacy_result.get("decisions", {}).get("summary", "")
        combined["decisions"] = [legacy_decisions] if legacy_decisions else []
        
        # Calculate confidence score based on analysis overlap
        overlap_score = len(set(legacy_actions) & set(devnous_suggestions))
        total_items = len(legacy_actions) + len(devnous_suggestions)
        combined["confidence_score"] = overlap_score / total_items if total_items > 0 else 0.0
        
        return combined

    async def _legacy_process_conversation(
        self, 
        conversation: List[Message], 
        context: ProjectContext
    ) -> Dict[str, Any]:
        """
        Process conversation using legacy methods.
        
        This maintains backward compatibility while the system migrates.
        """
        # Delegate to the concrete implementation's process_conversation
        # but mark it as legacy processing
        result = await self.process_conversation(conversation, context)
        result["processing_mode"] = "legacy"
        return result

    def get_migration_status(self) -> Dict[str, Any]:
        """Get the current migration status of this agent."""
        return {
            "agent_name": self.name,
            "agent_role": self.role,
            "devnous_integration_enabled": self.enable_devnous_integration,
            "devnous_agent_injected": self.devnous_agent is not None,
            "tools_registered": len(self.tool_registry),
            "available_tools": list(self.tool_registry.keys()),
            "feature_flags": self.feature_flags,
            "legacy_mode": self._legacy_mode,
            "cache_entries": len(self.tool_cache)
        }
