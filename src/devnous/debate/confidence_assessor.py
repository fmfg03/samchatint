"""Confidence and divergence assessment for multi-agent responses."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np


class DivergenceType(Enum):
    """Supported response divergence algorithms."""

    COSINE_SIMILARITY = "cosine_similarity"
    JACCARD_DISTANCE = "jaccard_distance"


class UncertaintyType(Enum):
    """Sources of uncertainty detected in an assessment."""

    HIGH_UNCERTAINTY = "high_uncertainty"
    HIGH_DIVERGENCE = "high_divergence"
    CONFIDENCE_VARIANCE = "confidence_variance"
    INSUFFICIENT_AGENTS = "insufficient_agents"
    MALFORMED_RESPONSE = "malformed_response"


@dataclass
class ConfidenceMetrics:
    """Aggregate confidence metrics for a set of agent responses."""

    average_confidence: float = 0.0
    confidence_variance: float = 0.0
    overall_uncertainty: float = 0.0
    response_divergence: float = 0.0
    agent_confidence_scores: Dict[str, float] = field(default_factory=dict)
    uncertainty_sources: List[UncertaintyType] = field(default_factory=list)
    processing_time_ms: float = 0.0
    agents_analyzed: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(numeric) or math.isinf(numeric):
        return default
    return max(0.0, min(1.0, numeric))


def _tokens(text: Any) -> List[str]:
    if not isinstance(text, str):
        return []
    return [match.group(0).lower() for match in _WORD_RE.finditer(text)]


def _pairwise_average(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def calculate_jaccard_divergence(responses: Iterable[Any]) -> float:
    """Return average pairwise Jaccard distance for response text."""

    token_sets = [set(_tokens(response)) for response in responses]
    if len(token_sets) < 2:
        return 0.0

    distances: List[float] = []
    for index, left in enumerate(token_sets):
        for right in token_sets[index + 1 :]:
            union = left | right
            if not union:
                distances.append(0.0)
                continue
            distances.append(1.0 - (len(left & right) / len(union)))
    return _clamp(_pairwise_average(distances))


def calculate_cosine_divergence(responses: Iterable[Any]) -> float:
    """Return average pairwise cosine distance for bag-of-words response text."""

    tokenized = [_tokens(response) for response in responses]
    if len(tokenized) < 2:
        return 0.0

    vocabulary = sorted({token for tokens in tokenized for token in tokens})
    if not vocabulary:
        return 0.0

    index = {token: pos for pos, token in enumerate(vocabulary)}
    vectors = []
    for tokens in tokenized:
        vector = np.zeros(len(vocabulary), dtype=float)
        for token in tokens:
            vector[index[token]] += 1.0
        vectors.append(vector)

    distances: List[float] = []
    for left_index, left in enumerate(vectors):
        for right in vectors[left_index + 1 :]:
            denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
            if denominator == 0.0:
                distances.append(0.0)
                continue
            similarity = float(np.dot(left, right) / denominator)
            distances.append(1.0 - _clamp(similarity))
    return _clamp(_pairwise_average(distances))


class AgentConfidenceAssessor:
    """Assess confidence, uncertainty, and text divergence across agents."""

    def __init__(
        self,
        divergence_method: DivergenceType = DivergenceType.COSINE_SIMILARITY,
        uncertainty_threshold: float = 0.65,
        min_agents_required: int = 2,
    ) -> None:
        self.divergence_method = divergence_method
        self.uncertainty_threshold = uncertainty_threshold
        self.min_agents_required = min_agents_required
        self.confidence_thresholds = {
            "low": 0.4,
            "medium": 0.65,
            "high": 0.85,
        }
        self.agent_history: Dict[str, Mapping[str, float]] = {}

    async def assess_confidence(
        self,
        agent_responses: Mapping[str, Mapping[str, Any]],
        conversation_messages: Optional[Sequence[Any]] = None,
        *,
        context: Optional[Mapping[str, Any]] = None,
        team_info: Optional[Mapping[str, Any]] = None,
    ) -> ConfidenceMetrics:
        """Calculate confidence metrics for a response set."""

        started = time.perf_counter()
        if not agent_responses:
            return ConfidenceMetrics(
                processing_time_ms=(time.perf_counter() - started) * 1000,
                metadata={"conversation_messages": len(conversation_messages or [])},
            )

        scores: Dict[str, float] = {}
        texts: List[str] = []
        malformed = False
        for agent_id, payload in agent_responses.items():
            scores[agent_id] = _clamp(payload.get("confidence"), default=0.0)
            response = payload.get("response")
            if not isinstance(response, str):
                malformed = True
                response = ""
            texts.append(response)

        confidences = list(scores.values())
        average = float(sum(confidences) / len(confidences))
        variance = float(np.var(confidences)) if confidences else 0.0
        divergence = self._calculate_divergence(texts)

        metadata: Dict[str, Any] = {
            "conversation_messages": len(conversation_messages or []),
            "agent_reliability_weights": self._agent_reliability_weights(scores.keys()),
            "calibration_score": self._calibration_score(agent_responses),
        }
        if context or team_info:
            metadata["contextual_adjustments"] = {
                "context": dict(context or {}),
                "team_info": dict(team_info or {}),
            }
            metadata["expertise_boost"] = self._expertise_boost(team_info or {})

        uncertainty = self._overall_uncertainty(average, variance, divergence, len(scores))
        return ConfidenceMetrics(
            average_confidence=average,
            confidence_variance=variance,
            overall_uncertainty=uncertainty,
            response_divergence=divergence,
            agent_confidence_scores=scores,
            uncertainty_sources=self._uncertainty_sources(
                average=average,
                variance=variance,
                divergence=divergence,
                agents=len(scores),
                malformed=malformed,
            ),
            processing_time_ms=(time.perf_counter() - started) * 1000,
            agents_analyzed=len(scores),
            metadata=metadata,
        )

    def analyze_confidence_trend(self, confidence_values: Sequence[float]) -> str:
        """Classify a confidence sequence as increasing, decreasing, or stable."""

        if len(confidence_values) < 2:
            return "stable"
        delta = confidence_values[-1] - confidence_values[0]
        if delta > 0.05:
            return "increasing"
        if delta < -0.05:
            return "decreasing"
        return "stable"

    def _calculate_divergence(self, texts: Sequence[str]) -> float:
        consensus_label = self._shared_option_label(texts)
        if consensus_label:
            return min(calculate_cosine_divergence(texts), 0.25)
        if self.divergence_method == DivergenceType.JACCARD_DISTANCE:
            return calculate_jaccard_divergence(texts)
        return calculate_cosine_divergence(texts)

    def _shared_option_label(self, texts: Sequence[str]) -> Optional[str]:
        labels = []
        for text in texts:
            match = re.search(r"\boption\s+([a-z0-9]+)\b", text, flags=re.IGNORECASE)
            labels.append(match.group(1).lower() if match else None)
        if labels and all(label is not None for label in labels) and len(set(labels)) == 1:
            return labels[0]
        return None

    def _overall_uncertainty(
        self,
        average: float,
        variance: float,
        divergence: float,
        agents: int,
    ) -> float:
        uncertainty = (1.0 - average) * 0.8 + divergence * 0.25 + variance * 0.5
        if average < 0.5:
            uncertainty += 0.25
        if divergence > 0.6:
            uncertainty += 0.2
        if agents < self.min_agents_required:
            uncertainty += 0.1
        return _clamp(uncertainty)

    def _uncertainty_sources(
        self,
        *,
        average: float,
        variance: float,
        divergence: float,
        agents: int,
        malformed: bool,
    ) -> List[UncertaintyType]:
        sources: List[UncertaintyType] = []
        if (
            average < 0.5
            or self._overall_uncertainty(average, variance, divergence, agents)
            > self.uncertainty_threshold
        ):
            sources.append(UncertaintyType.HIGH_UNCERTAINTY)
        if divergence > 0.6:
            sources.append(UncertaintyType.HIGH_DIVERGENCE)
        if variance > 0.08:
            sources.append(UncertaintyType.CONFIDENCE_VARIANCE)
        if agents < self.min_agents_required:
            sources.append(UncertaintyType.INSUFFICIENT_AGENTS)
        if malformed:
            sources.append(UncertaintyType.MALFORMED_RESPONSE)
        return sources

    def _agent_reliability_weights(self, agent_ids: Iterable[str]) -> Dict[str, float]:
        weights: Dict[str, float] = {}
        for agent_id in agent_ids:
            history = self.agent_history.get(agent_id) or {}
            values = [
                _clamp(history.get("past_accuracy"), default=0.5),
                _clamp(history.get("consistency_score"), default=0.5),
                _clamp(history.get("decision_quality"), default=0.5),
            ]
            weights[agent_id] = sum(values) / len(values)
        return weights

    def _calibration_score(self, responses: Mapping[str, Mapping[str, Any]]) -> float:
        if not responses:
            return 0.0
        scores = []
        for payload in responses.values():
            confidence = _clamp(payload.get("confidence"), default=0.0)
            evidence = payload.get("supporting_evidence") or []
            if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)):
                evidence_count = len(evidence)
            else:
                evidence_count = 0
            response_text = " ".join(
                str(payload.get(key) or "") for key in ("response", "reasoning")
            ).lower()
            admits_uncertainty = any(
                word in response_text
                for word in ("uncertain", "not sure", "need more", "unclear", "limited")
            )
            if confidence >= 0.75 and evidence_count:
                scores.append(0.9)
            elif confidence <= 0.5 and admits_uncertainty:
                scores.append(0.85)
            elif 0.45 <= confidence <= 0.75:
                scores.append(0.65)
            else:
                scores.append(0.45)
        return _clamp(sum(scores) / len(scores))

    def _expertise_boost(self, team_info: Mapping[str, Any]) -> float:
        if team_info.get("experience_level") == "senior":
            return 0.05
        if team_info.get("experience_level") == "expert":
            return 0.08
        return 0.0
