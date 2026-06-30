"""
Common agent response and coordination patterns.

Consolidates duplicate agent handling logic from:
- multi_agent_coordinator.py
- samchat_pm_consensus.py
- debate management modules
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .result_patterns import GenerationResult, ResultFactory
from .weight_calculator import CommonWeightCalculator, WeightFactors

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    """Standardized agent response format."""

    agent_id: str
    content: str
    confidence: float
    reasoning: str = ""
    timestamp: datetime = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
        if self.metadata is None:
            self.metadata = {}


@dataclass
class AgentCoordinationConfig:
    """Configuration for agent coordination."""

    timeout_seconds: float = 30.0
    min_responses: int = 1
    max_responses: int = 5
    require_consensus: bool = False
    consensus_threshold: float = 0.7
    enable_fallback: bool = True
    parallel_execution: bool = True


class CommonAgentCoordinator:
    """Common utilities for agent coordination."""

    def __init__(self, config: Optional[AgentCoordinationConfig] = None):
        self.config = config or AgentCoordinationConfig()
        self.weight_calculator = CommonWeightCalculator()

    async def coordinate_responses(
        self,
        agents: List[str],
        prompt: str,
        response_generator: Callable[[str, str], Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> GenerationResult:
        """Coordinate responses from multiple agents."""
        start_time = time.time()

        try:
            if self.config.parallel_execution:
                responses = await self._gather_parallel_responses(
                    agents, prompt, response_generator, context
                )
            else:
                responses = await self._gather_sequential_responses(
                    agents, prompt, response_generator, context
                )

            if not responses:
                return ResultFactory.failure("No agent responses received")

            # Process and rank responses
            ranked_responses = self._rank_responses(responses, context)

            # Select best response or build consensus
            if self.config.require_consensus:
                final_result = await self._build_consensus(ranked_responses, context)
            else:
                final_result = self._select_best_response(ranked_responses)

            execution_time = (time.time() - start_time) * 1000

            return GenerationResult(
                status=final_result["status"],
                success=final_result["success"],
                message=final_result["message"],
                content=final_result["content"],
                confidence=final_result["confidence"],
                alternatives=final_result.get("alternatives", []),
                generation_method="multi_agent_coordination",
                execution_time_ms=execution_time,
                metadata={
                    "participating_agents": agents,
                    "total_responses": len(responses),
                    "consensus_data": final_result.get("consensus_data", {}),
                },
            )

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            logger.error(f"Error coordinating agent responses: {e}")
            return GenerationResult(
                status="failure",
                success=False,
                message="Agent coordination failed",
                error=str(e),
                execution_time_ms=execution_time,
            )

    async def _gather_parallel_responses(
        self,
        agents: List[str],
        prompt: str,
        response_generator: Callable,
        context: Optional[Dict[str, Any]],
    ) -> List[AgentResponse]:
        """Gather responses from agents in parallel."""
        tasks = []
        for agent_id in agents:
            task = asyncio.create_task(
                self._get_single_agent_response(
                    agent_id, prompt, response_generator, context
                )
            )
            tasks.append(task)

        # Wait for responses with timeout
        try:
            responses = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self.config.timeout_seconds,
            )

            # Filter out exceptions and failed responses
            valid_responses = []
            for response in responses:
                if isinstance(response, AgentResponse):
                    valid_responses.append(response)
                elif isinstance(response, Exception):
                    logger.warning(f"Agent response failed: {response}")

            return valid_responses

        except asyncio.TimeoutError:
            logger.warning("Agent response timeout exceeded")
            # Return any completed responses
            completed_responses = []
            for task in tasks:
                if task.done() and not task.exception():
                    completed_responses.append(task.result())
            return completed_responses

    async def _gather_sequential_responses(
        self,
        agents: List[str],
        prompt: str,
        response_generator: Callable,
        context: Optional[Dict[str, Any]],
    ) -> List[AgentResponse]:
        """Gather responses from agents sequentially."""
        responses = []

        for agent_id in agents:
            try:
                response = await asyncio.wait_for(
                    self._get_single_agent_response(
                        agent_id, prompt, response_generator, context
                    ),
                    timeout=self.config.timeout_seconds / len(agents),
                )
                responses.append(response)

            except Exception as e:
                logger.warning(f"Agent {agent_id} failed: {e}")
                continue

        return responses

    async def _get_single_agent_response(
        self,
        agent_id: str,
        prompt: str,
        response_generator: Callable,
        context: Optional[Dict[str, Any]],
    ) -> AgentResponse:
        """Get response from a single agent."""
        try:
            # Call the provided response generator
            raw_response = await response_generator(agent_id, prompt)

            # Normalize response format
            if isinstance(raw_response, dict):
                content = raw_response.get("content", str(raw_response))
                confidence = raw_response.get("confidence", 0.5)
                reasoning = raw_response.get("reasoning", "")
                metadata = raw_response.get("metadata", {})
            else:
                content = str(raw_response)
                confidence = 0.5
                reasoning = ""
                metadata = {}

            return AgentResponse(
                agent_id=agent_id,
                content=content,
                confidence=confidence,
                reasoning=reasoning,
                metadata=metadata,
            )

        except Exception as e:
            logger.error(f"Error getting response from agent {agent_id}: {e}")
            raise

    def _rank_responses(
        self, responses: List[AgentResponse], context: Optional[Dict[str, Any]]
    ) -> List[Tuple[AgentResponse, float]]:
        """Rank responses by calculated weights."""
        ranked = []

        for response in responses:
            # Calculate weight factors
            weight_factors = WeightFactors(
                expertise=self.weight_calculator.calculate_expertise_weight(
                    agent_profile=(
                        context.get("agent_profiles", {}).get(response.agent_id)
                        if context
                        else None
                    )
                ),
                reliability=response.confidence,
                temporal=self.weight_calculator.calculate_temporal_weight(
                    response.timestamp
                ),
                emotional=self.weight_calculator.calculate_emotional_weight(
                    context.get("emotional_state") if context else None
                ),
                evidence_strength=self.weight_calculator.calculate_evidence_strength(
                    response.reasoning or response.content
                ),
            )

            total_weight = weight_factors.total_weight()
            ranked.append((response, total_weight))

        # Sort by weight (highest first)
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    def _select_best_response(
        self, ranked_responses: List[Tuple[AgentResponse, float]]
    ) -> Dict[str, Any]:
        """Select the best single response."""
        if not ranked_responses:
            return {
                "status": "failure",
                "success": False,
                "message": "No valid responses to select from",
                "content": "",
                "confidence": 0.0,
            }

        best_response, weight = ranked_responses[0]
        alternatives = [
            resp.content for resp, _ in ranked_responses[1:3]
        ]  # Top 2 alternatives

        return {
            "status": "success",
            "success": True,
            "message": "Best response selected",
            "content": best_response.content,
            "confidence": min(best_response.confidence * weight, 1.0),
            "alternatives": alternatives,
        }

    async def _build_consensus(
        self,
        ranked_responses: List[Tuple[AgentResponse, float]],
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build consensus from multiple responses."""
        if not ranked_responses:
            return self._select_best_response([])

        # For now, implement simple weighted consensus
        # This could be enhanced with more sophisticated consensus algorithms

        total_weight = sum(weight for _, weight in ranked_responses)
        if total_weight == 0:
            return self._select_best_response(ranked_responses)

        # Check if top responses are similar enough for consensus
        top_responses = ranked_responses[:3]  # Consider top 3
        similarity_threshold = self.config.consensus_threshold

        # Simple similarity check based on content length and key terms
        if len(top_responses) > 1:
            consensus_score = self._calculate_consensus_score(top_responses)

            if consensus_score >= similarity_threshold:
                # Build consensus response
                consensus_content = self._merge_responses(top_responses)
                consensus_confidence = (
                    sum(resp.confidence * weight for resp, weight in top_responses)
                    / total_weight
                )

                return {
                    "status": "success",
                    "success": True,
                    "message": "Consensus reached",
                    "content": consensus_content,
                    "confidence": consensus_confidence,
                    "consensus_data": {
                        "score": consensus_score,
                        "participating_responses": len(top_responses),
                    },
                }

        # Fallback to best single response
        return self._select_best_response(ranked_responses)

    def _calculate_consensus_score(
        self, responses: List[Tuple[AgentResponse, float]]
    ) -> float:
        """Calculate similarity score between responses."""
        if len(responses) < 2:
            return 1.0

        # Simple consensus scoring based on content similarity
        # This is a placeholder - could be enhanced with NLP similarity measures
        contents = [resp.content.lower() for resp, _ in responses]

        # Count common words
        all_words = set()
        word_counts = []

        for content in contents:
            words = set(content.split())
            all_words.update(words)
            word_counts.append(words)

        if not all_words:
            return 0.0

        # Calculate Jaccard similarity average
        similarities = []
        for i in range(len(word_counts)):
            for j in range(i + 1, len(word_counts)):
                intersection = len(word_counts[i] & word_counts[j])
                union = len(word_counts[i] | word_counts[j])
                similarity = intersection / union if union > 0 else 0.0
                similarities.append(similarity)

        return sum(similarities) / len(similarities) if similarities else 0.0

    def _merge_responses(self, responses: List[Tuple[AgentResponse, float]]) -> str:
        """Merge multiple responses into consensus."""
        # Simple merging - take the longest response as base
        # This could be enhanced with more sophisticated text merging

        sorted_by_length = sorted(
            responses, key=lambda x: len(x[0].content), reverse=True
        )
        base_response = sorted_by_length[0][0].content

        # Add unique insights from other responses
        merged = base_response
        base_words = set(base_response.lower().split())

        for response, weight in sorted_by_length[1:]:
            response_words = set(response.content.lower().split())
            unique_words = response_words - base_words

            # If there are significant unique insights, append them
            if len(unique_words) > len(response_words) * 0.3:  # 30% unique content
                merged += f"\n\nAdditionally: {response.content}"

        return merged
