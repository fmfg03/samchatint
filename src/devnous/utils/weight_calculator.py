"""
Centralized weight calculation utilities.

Consolidates duplicate weight calculation logic from across modules:
- context_aggregator.py
- consensus_algorithms.py
- samchat_pm_consensus.py
- context_aware_consensus.py
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..models import EmotionalState
from ..response.multi_agent_coordinator import AgentProfile

logger = logging.getLogger(__name__)


@dataclass
class WeightFactors:
    """Common weight factors for calculations."""

    expertise: float = 0.0
    reliability: float = 0.0
    temporal: float = 0.0
    emotional: float = 0.0
    evidence_strength: float = 0.0

    def total_weight(self) -> float:
        """Calculate total weighted score."""
        return (
            self.expertise * 0.3
            + self.reliability * 0.25
            + self.temporal * 0.15
            + self.emotional * 0.15
            + self.evidence_strength * 0.15
        )


class CommonWeightCalculator:
    """Centralized weight calculation for all modules."""

    @staticmethod
    def calculate_expertise_weight(
        agent_profile: Optional[AgentProfile],
        domain: str = "general",
        max_weight: float = 1.0,
    ) -> float:
        """Calculate expertise-based weight."""
        if not agent_profile:
            return 0.5  # Default neutral weight

        domain_expertise = agent_profile.expertise_domains.get(domain, 0.5)
        success_rate = agent_profile.historical_accuracy

        # Combine domain expertise with historical success
        expertise_weight = (domain_expertise * 0.7) + (success_rate * 0.3)
        return min(expertise_weight, max_weight)

    @staticmethod
    def calculate_emotional_weight(
        emotional_state: Optional[EmotionalState],
        emotional_intensity: float = 0.5,
        boost_engaged: bool = True,
    ) -> float:
        """Calculate emotional state weight adjustment."""
        if not emotional_state:
            return 1.0  # Neutral weight

        # Emotional state multipliers
        state_weights = {
            EmotionalState.ENGAGED: 1.2 if boost_engaged else 1.0,
            EmotionalState.PRODUCTIVE: 1.15,
            EmotionalState.FOCUSED: 1.1,
            EmotionalState.CALM: 1.0,
            EmotionalState.STRESSED: 0.9,
            EmotionalState.FRUSTRATED: 0.85,
            EmotionalState.OVERWHELMED: 0.8,
            EmotionalState.DISENGAGED: 0.7,
        }

        base_weight = state_weights.get(emotional_state, 1.0)

        # Adjust by intensity
        intensity_factor = 1.0 + (emotional_intensity - 0.5) * 0.2

        return base_weight * intensity_factor

    @staticmethod
    def calculate_temporal_weight(
        timestamp: datetime, decay_hours: float = 24.0, min_weight: float = 0.1
    ) -> float:
        """Calculate time-decay weight."""
        time_diff = datetime.utcnow() - timestamp
        hours_old = time_diff.total_seconds() / 3600

        if hours_old <= 0:
            return 1.0

        # Exponential decay
        decay_factor = math.exp(-hours_old / decay_hours)
        return max(decay_factor, min_weight)

    @staticmethod
    def calculate_reliability_weight(
        success_count: int,
        total_attempts: int,
        min_attempts: int = 5,
        default_weight: float = 0.5,
    ) -> float:
        """Calculate reliability-based weight."""
        if total_attempts < min_attempts:
            return default_weight

        reliability = success_count / total_attempts

        # Apply confidence interval adjustment for small sample sizes
        if total_attempts < 20:
            confidence_penalty = 1.0 - (0.2 * (20 - total_attempts) / 15)
            reliability *= confidence_penalty

        return reliability

    @staticmethod
    def calculate_evidence_strength(
        argument_text: str,
        has_data: bool = False,
        has_citations: bool = False,
        length_bonus: bool = True,
    ) -> float:
        """Calculate evidence strength weight."""
        base_weight = 0.5

        # Data and citations provide strong evidence
        if has_data:
            base_weight += 0.25
        if has_citations:
            base_weight += 0.2

        # Length indicates thoroughness (to a point)
        if length_bonus and argument_text:
            length = len(argument_text.split())
            if 50 <= length <= 200:  # Sweet spot
                base_weight += 0.1
            elif length > 200:  # Too verbose
                base_weight += 0.05

        return min(base_weight, 1.0)

    @staticmethod
    def combine_weights(weight_factors: WeightFactors) -> float:
        """Combine all weight factors into final score."""
        return weight_factors.total_weight()
