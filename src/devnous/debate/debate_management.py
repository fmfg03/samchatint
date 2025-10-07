"""
Advanced Debate Round Management for Smart Selective Debate Protocols.

This module implements sophisticated debate round management with iterative refinement,
structured argumentation frameworks, and evidence presentation systems. Features include:

- Multi-round iterative refinement protocols with convergence tracking
- Evidence presentation and counter-argument structures
- Structured argumentation frameworks for productive debates  
- Conflict resolution strategies with escalation management
- Real-time quality assessment and improvement feedback
- Performance optimization with parallel round processing

Integration points:
- Works with ConsensusEngine for final decision making
- Uses emotional intelligence from context detection
- Leverages agent profiles for specialized debate roles
- Integrates with caching and performance monitoring
"""

import asyncio
import logging
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Set, Union, Callable
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
from collections import defaultdict, deque
import numpy as np
import threading
from concurrent.futures import ThreadPoolExecutor

from ..models import EmotionalState, UserContext, TeamContext, ConversationMessage
from .core import (
    DebateArgument, DebateRound, DebateSession, DebateParticipant,
    ConsensusLevel, DebatePhase, DebateStatus, AgentRole
)
from .consensus_algorithms import (
    ConsensusEngine, ConsensusMethod, AgentVote, ConsensusResult,
    WeightingCalculator, ByzantineFaultDetector
)
from ..response.multi_agent_coordinator import AgentProfile, HandoffTrigger
from ..monitoring.metrics_collector import metrics_collector

logger = logging.getLogger(__name__)


class ArgumentType(Enum):
    """Types of arguments in debate structure."""
    CLAIM = "claim"                    # Initial position or assertion
    EVIDENCE = "evidence"              # Supporting evidence/data
    REASONING = "reasoning"            # Logical reasoning chain
    COUNTERARGUMENT = "counterargument"  # Direct refutation
    REBUTTAL = "rebuttal"             # Response to counterargument
    SYNTHESIS = "synthesis"           # Integration of multiple viewpoints
    CLARIFICATION = "clarification"   # Clarifying questions or responses
    ESCALATION = "escalation"         # Escalation of conflict/complexity


class EvidenceType(Enum):
    """Types of evidence that can be presented."""
    EMPIRICAL_DATA = "empirical_data"         # Research data, metrics, statistics
    CASE_STUDY = "case_study"                 # Specific examples, case studies
    BEST_PRACTICE = "best_practice"           # Industry standards, best practices
    EXPERT_OPINION = "expert_opinion"         # Professional opinions, testimonials  
    THEORETICAL = "theoretical"               # Theoretical frameworks, models
    EXPERIENTIAL = "experiential"             # Personal/team experience
    COMPARATIVE = "comparative"               # Comparisons with alternatives
    RISK_ANALYSIS = "risk_analysis"           # Risk assessments, mitigation strategies


class ConflictResolutionStage(Enum):
    """Stages of conflict resolution process."""
    IDENTIFICATION = "identification"         # Identify conflict points
    ANALYSIS = "analysis"                     # Analyze conflict sources  
    MEDIATION = "mediation"                   # Facilitate mediated discussion
    NEGOTIATION = "negotiation"               # Negotiate compromises
    ARBITRATION = "arbitration"               # External arbitration if needed
    SYNTHESIS = "synthesis"                   # Synthesize resolution
    VALIDATION = "validation"                 # Validate resolution acceptance


@dataclass
class Evidence:
    """Structured evidence supporting an argument."""
    evidence_id: str
    evidence_type: EvidenceType
    content: str
    source: str
    credibility_score: float  # 0.0-1.0
    relevance_score: float    # 0.0-1.0
    recency_score: float      # 0.0-1.0 (how recent/current)
    verification_status: str  # 'verified', 'unverified', 'disputed'
    supporting_data: Dict[str, Any] = field(default_factory=dict)
    citations: List[str] = field(default_factory=list)
    created_by: AgentRole = AgentRole.GENERAL_ASSISTANT
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class StructuredArgument:
    """Enhanced argument structure with evidence and reasoning chains."""
    argument_id: str
    argument_type: ArgumentType
    parent_argument_id: Optional[str]  # For threaded discussions
    responding_to_id: Optional[str]    # For direct responses/rebuttals

    # Core content
    claim: str

    # Metadata (must come before defaults)
    agent_role: AgentRole
    agent_profile: AgentProfile

    # Content with defaults
    reasoning: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)

    # Quality metrics
    confidence: float = 0.0
    strength_score: float = 0.0
    quality_metrics: Dict[str, float] = field(default_factory=dict)
    
    # Debate context
    round_number: int = 1
    sequence_number: int = 1
    processing_time_ms: float = 0.0
    
    # Response tracking
    responses: List[str] = field(default_factory=list)  # IDs of responding arguments
    impact_score: float = 0.0  # How much this argument influenced others
    
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ConflictPoint:
    """Identified conflict between arguments or agents."""
    conflict_id: str
    conflict_type: str  # 'position_conflict', 'evidence_conflict', 'methodology_conflict'
    severity: float     # 0.0-1.0
    
    # Conflicting elements
    argument_ids: List[str]
    agent_roles: List[AgentRole]
    conflict_description: str
    
    # Resolution tracking
    resolution_stage: ConflictResolutionStage = ConflictResolutionStage.IDENTIFICATION
    resolution_attempts: List[Dict[str, Any]] = field(default_factory=list)
    resolution_status: str = "unresolved"  # 'unresolved', 'in_progress', 'resolved'
    
    # Impact assessment
    blocks_consensus: bool = False
    affects_agents: Set[AgentRole] = field(default_factory=set)
    priority_level: str = "medium"  # 'low', 'medium', 'high', 'critical'
    
    created_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None


@dataclass  
class RoundMetrics:
    """Comprehensive metrics for a debate round."""
    round_number: int
    
    # Participation metrics
    total_arguments: int = 0
    arguments_by_type: Dict[ArgumentType, int] = field(default_factory=dict)
    agent_participation: Dict[AgentRole, int] = field(default_factory=dict)
    
    # Quality metrics
    average_argument_quality: float = 0.0
    evidence_density: float = 0.0  # Evidence per argument
    reasoning_depth: float = 0.0   # Average reasoning chain length
    
    # Interaction metrics
    response_rate: float = 0.0          # How often arguments get responses
    conflict_count: int = 0
    conflicts_resolved: int = 0
    
    # Convergence metrics
    position_diversity: float = 0.0     # How diverse are the positions
    convergence_trend: float = 0.0      # Are positions converging (-1 to 1)
    consensus_probability: float = 0.0  # Probability of reaching consensus
    
    # Performance metrics
    round_duration_ms: float = 0.0
    average_response_time_ms: float = 0.0
    parallel_efficiency: float = 0.0
    
    # Emotional dynamics
    emotional_intensity: float = 0.0
    collaboration_level: float = 0.0
    stress_indicators: float = 0.0


@dataclass
class DebateManagementConfig:
    """Configuration for debate round management."""
    # Round management
    max_rounds: int = 8
    max_arguments_per_round: int = 12
    max_arguments_per_agent: int = 3
    round_timeout_minutes: int = 10
    
    # Evidence requirements
    min_evidence_per_argument: int = 1
    evidence_quality_threshold: float = 0.6
    require_citations: bool = True
    
    # Quality thresholds
    argument_quality_threshold: float = 0.5
    reasoning_depth_threshold: int = 2
    evidence_credibility_threshold: float = 0.7
    
    # Conflict resolution
    auto_conflict_detection: bool = True
    conflict_escalation_threshold: float = 0.7
    max_resolution_attempts: int = 3
    require_conflict_resolution: bool = True
    
    # Convergence detection
    convergence_detection_enabled: bool = True
    early_convergence_threshold: float = 0.8
    position_diversity_threshold: float = 0.3
    
    # Performance optimization
    parallel_processing: bool = True
    async_argument_generation: bool = True
    cache_enabled: bool = True
    
    # Emotional intelligence
    emotional_monitoring: bool = True
    stress_intervention_threshold: float = 0.8
    collaboration_encouragement: bool = True


class EvidenceValidator:
    """Validates and scores evidence quality."""
    
    def __init__(self):
        self.credibility_factors = {
            EvidenceType.EMPIRICAL_DATA: 0.9,
            EvidenceType.CASE_STUDY: 0.8,
            EvidenceType.BEST_PRACTICE: 0.8,
            EvidenceType.EXPERT_OPINION: 0.7,
            EvidenceType.THEORETICAL: 0.6,
            EvidenceType.EXPERIENTIAL: 0.5,
            EvidenceType.COMPARATIVE: 0.7,
            EvidenceType.RISK_ANALYSIS: 0.8
        }
    
    async def validate_evidence(self, evidence: Evidence) -> Dict[str, Any]:
        """Validate evidence and return detailed assessment."""
        validation_result = {
            'is_valid': True,
            'validation_score': 0.0,
            'issues': [],
            'recommendations': [],
            'credibility_assessment': {},
            'verification_needed': False
        }
        
        # Credibility assessment based on type
        base_credibility = self.credibility_factors.get(evidence.evidence_type, 0.5)
        
        # Content quality analysis
        content_quality = await self._assess_content_quality(evidence.content)
        
        # Source credibility if available
        source_credibility = await self._assess_source_credibility(evidence.source)
        
        # Recency relevance
        recency_factor = evidence.recency_score
        
        # Calculate composite validation score
        validation_score = (
            base_credibility * 0.3 +
            content_quality * 0.3 +
            source_credibility * 0.2 +
            recency_factor * 0.2
        )
        
        validation_result['validation_score'] = validation_score
        validation_result['credibility_assessment'] = {
            'base_credibility': base_credibility,
            'content_quality': content_quality,
            'source_credibility': source_credibility,
            'recency_factor': recency_factor
        }
        
        # Identify issues and recommendations
        if validation_score < 0.5:
            validation_result['is_valid'] = False
            validation_result['issues'].append('Overall evidence quality too low')
        
        if content_quality < 0.4:
            validation_result['issues'].append('Evidence content lacks detail or structure')
            validation_result['recommendations'].append('Provide more specific details and examples')
        
        if source_credibility < 0.3 and evidence.source:
            validation_result['issues'].append('Evidence source has low credibility')
            validation_result['recommendations'].append('Provide alternative or additional sources')
        
        if evidence.verification_status == 'unverified' and validation_score > 0.7:
            validation_result['verification_needed'] = True
            validation_result['recommendations'].append('High-quality evidence should be verified')
        
        return validation_result
    
    async def _assess_content_quality(self, content: str) -> float:
        """Assess quality of evidence content."""
        if not content:
            return 0.0
        
        quality_score = 0.0
        
        # Length appropriateness
        if 20 <= len(content) <= 1000:
            quality_score += 0.2
        
        # Specific indicators
        specific_indicators = [
            'data shows', 'study found', 'research indicates', 'analysis reveals',
            'metrics demonstrate', 'results show', 'evidence suggests'
        ]
        
        content_lower = content.lower()
        specificity = sum(0.05 for indicator in specific_indicators if indicator in content_lower)
        quality_score += min(0.3, specificity)
        
        # Quantitative indicators
        quantitative_indicators = ['%', 'percent', 'million', 'thousand', 'increase', 'decrease']
        quantitative = sum(0.05 for indicator in quantitative_indicators if indicator in content_lower)
        quality_score += min(0.2, quantitative)
        
        # Structure indicators
        structure_indicators = ['.', ',', ':', ';']  # Basic punctuation indicating structure
        structure_score = min(0.3, len([c for c in content if c in structure_indicators]) * 0.02)
        quality_score += structure_score
        
        return min(1.0, quality_score)
    
    async def _assess_source_credibility(self, source: str) -> float:
        """Assess credibility of evidence source."""
        if not source:
            return 0.3  # Default score for no source
        
        credibility_score = 0.3  # Base score
        
        source_lower = source.lower()
        
        # High credibility sources
        high_cred_indicators = [
            'journal', 'research', 'study', 'university', 'institution',
            'official', 'government', 'academic', 'peer-reviewed'
        ]
        
        for indicator in high_cred_indicators:
            if indicator in source_lower:
                credibility_score += 0.15
        
        # Medium credibility sources
        medium_cred_indicators = [
            'report', 'analysis', 'whitepaper', 'publication', 'conference'
        ]
        
        for indicator in medium_cred_indicators:
            if indicator in source_lower:
                credibility_score += 0.1
        
        # Lower credibility indicators
        low_cred_indicators = ['blog', 'opinion', 'personal', 'unverified']
        
        for indicator in low_cred_indicators:
            if indicator in source_lower:
                credibility_score -= 0.1
        
        return max(0.0, min(1.0, credibility_score))


class ConflictResolver:
    """Handles conflict detection and resolution in debates."""
    
    def __init__(self):
        self.conflict_patterns = {
            'direct_contradiction': self._detect_direct_contradiction,
            'evidence_conflict': self._detect_evidence_conflict,
            'methodology_conflict': self._detect_methodology_conflict,
            'priority_conflict': self._detect_priority_conflict,
            'assumption_conflict': self._detect_assumption_conflict
        }
        
        self.resolution_strategies = {
            ConflictResolutionStage.MEDIATION: self._mediate_conflict,
            ConflictResolutionStage.NEGOTIATION: self._negotiate_resolution,
            ConflictResolutionStage.ARBITRATION: self._arbitrate_conflict,
            ConflictResolutionStage.SYNTHESIS: self._synthesize_resolution
        }
    
    async def detect_conflicts(self, arguments: List[StructuredArgument]) -> List[ConflictPoint]:
        """Detect conflicts between arguments."""
        conflicts = []
        
        # Compare all pairs of arguments
        for i, arg1 in enumerate(arguments):
            for j, arg2 in enumerate(arguments[i+1:], i+1):
                for conflict_type, detector in self.conflict_patterns.items():
                    conflict = await detector(arg1, arg2)
                    if conflict:
                        conflict.conflict_type = conflict_type
                        conflicts.append(conflict)
        
        # Prioritize conflicts
        conflicts.sort(key=lambda c: (c.severity, int(c.blocks_consensus)), reverse=True)
        
        return conflicts
    
    async def resolve_conflict(self, conflict: ConflictPoint, 
                             arguments: List[StructuredArgument],
                             user_context: UserContext,
                             team_context: Optional[TeamContext]) -> Dict[str, Any]:
        """Attempt to resolve a specific conflict."""
        resolution_result = {
            'success': False,
            'resolution_method': None,
            'resolution_content': '',
            'affected_arguments': [],
            'confidence': 0.0,
            'requires_follow_up': False
        }
        
        # Determine appropriate resolution strategy
        if conflict.severity < 0.3:
            strategy = ConflictResolutionStage.MEDIATION
        elif conflict.severity < 0.6:
            strategy = ConflictResolutionStage.NEGOTIATION
        elif conflict.severity < 0.8:
            strategy = ConflictResolutionStage.ARBITRATION
        else:
            strategy = ConflictResolutionStage.SYNTHESIS
        
        # Apply resolution strategy
        if strategy in self.resolution_strategies:
            try:
                resolution_result = await self.resolution_strategies[strategy](
                    conflict, arguments, user_context, team_context
                )
                resolution_result['resolution_method'] = strategy.value
                
                # Update conflict status
                if resolution_result['success']:
                    conflict.resolution_stage = ConflictResolutionStage.VALIDATION
                    conflict.resolution_status = 'resolved'
                    conflict.resolved_at = datetime.utcnow()
                
            except Exception as e:
                logger.error(f"Error resolving conflict {conflict.conflict_id}: {e}")
                resolution_result['error'] = str(e)
        
        # Record resolution attempt
        conflict.resolution_attempts.append({
            'strategy': strategy.value,
            'timestamp': datetime.utcnow(),
            'result': resolution_result
        })
        
        return resolution_result
    
    async def _detect_direct_contradiction(self, arg1: StructuredArgument, 
                                         arg2: StructuredArgument) -> Optional[ConflictPoint]:
        """Detect direct contradictions between arguments."""
        # Simple contradiction detection based on opposing keywords
        contradiction_pairs = [
            ('should', 'should not'),
            ('recommend', 'do not recommend'),
            ('effective', 'ineffective'),
            ('safe', 'unsafe'),
            ('feasible', 'not feasible'),
            ('increase', 'decrease'),
            ('improve', 'worsen')
        ]
        
        claim1_lower = arg1.claim.lower()
        claim2_lower = arg2.claim.lower()
        
        contradictions = 0
        for positive, negative in contradiction_pairs:
            if ((positive in claim1_lower and negative in claim2_lower) or
                (negative in claim1_lower and positive in claim2_lower)):
                contradictions += 1
        
        if contradictions > 0:
            severity = min(1.0, contradictions / len(contradiction_pairs))
            
            return ConflictPoint(
                conflict_id=str(uuid4()),
                conflict_type='direct_contradiction',
                severity=severity,
                argument_ids=[arg1.argument_id, arg2.argument_id],
                agent_roles=[arg1.agent_role, arg2.agent_role],
                conflict_description=f"Direct contradiction detected between positions",
                blocks_consensus=severity > 0.5,
                affects_agents={arg1.agent_role, arg2.agent_role},
                priority_level='high' if severity > 0.7 else 'medium'
            )
        
        return None
    
    async def _detect_evidence_conflict(self, arg1: StructuredArgument,
                                      arg2: StructuredArgument) -> Optional[ConflictPoint]:
        """Detect conflicting evidence between arguments."""
        if not arg1.evidence or not arg2.evidence:
            return None
        
        # Look for contradictory evidence
        contradictory_evidence = []
        
        for ev1 in arg1.evidence:
            for ev2 in arg2.evidence:
                # Same evidence type but different conclusions
                if (ev1.evidence_type == ev2.evidence_type and
                    ev1.source == ev2.source and
                    self._evidence_contradicts(ev1.content, ev2.content)):
                    contradictory_evidence.append((ev1, ev2))
        
        if contradictory_evidence:
            severity = len(contradictory_evidence) / max(len(arg1.evidence), len(arg2.evidence))
            
            return ConflictPoint(
                conflict_id=str(uuid4()),
                conflict_type='evidence_conflict',
                severity=severity,
                argument_ids=[arg1.argument_id, arg2.argument_id],
                agent_roles=[arg1.agent_role, arg2.agent_role],
                conflict_description=f"Conflicting evidence from {len(contradictory_evidence)} sources",
                blocks_consensus=severity > 0.4,
                affects_agents={arg1.agent_role, arg2.agent_role},
                priority_level='high' if severity > 0.6 else 'medium'
            )
        
        return None
    
    async def _detect_methodology_conflict(self, arg1: StructuredArgument,
                                         arg2: StructuredArgument) -> Optional[ConflictPoint]:
        """Detect conflicting methodologies or approaches."""
        methodology_keywords = {
            'agile': ['agile', 'iterative', 'sprint', 'scrum'],
            'waterfall': ['waterfall', 'sequential', 'phase-gate'],
            'lean': ['lean', 'minimal', 'mvp'],
            'formal': ['formal', 'structured', 'documented'],
            'rapid': ['rapid', 'fast', 'quick', 'immediate']
        }
        
        # Identify methodologies in each argument
        methods1 = set()
        methods2 = set()
        
        for method, keywords in methodology_keywords.items():
            if any(kw in arg1.claim.lower() for kw in keywords):
                methods1.add(method)
            if any(kw in arg2.claim.lower() for kw in keywords):
                methods2.add(method)
        
        # Check for conflicting methodologies
        conflicting_pairs = [
            ('agile', 'waterfall'),
            ('lean', 'formal'),
            ('rapid', 'formal')
        ]
        
        conflicts = 0
        for method1, method2 in conflicting_pairs:
            if ((method1 in methods1 and method2 in methods2) or
                (method2 in methods1 and method1 in methods2)):
                conflicts += 1
        
        if conflicts > 0:
            severity = conflicts / len(conflicting_pairs)
            
            return ConflictPoint(
                conflict_id=str(uuid4()),
                conflict_type='methodology_conflict',
                severity=severity,
                argument_ids=[arg1.argument_id, arg2.argument_id],
                agent_roles=[arg1.agent_role, arg2.agent_role],
                conflict_description=f"Conflicting methodological approaches detected",
                blocks_consensus=severity > 0.3,
                affects_agents={arg1.agent_role, arg2.agent_role},
                priority_level='medium'
            )
        
        return None
    
    async def _detect_priority_conflict(self, arg1: StructuredArgument,
                                      arg2: StructuredArgument) -> Optional[ConflictPoint]:
        """Detect conflicting priorities between arguments."""
        priority_keywords = {
            'performance': ['performance', 'speed', 'optimization', 'efficiency'],
            'security': ['security', 'safety', 'protection', 'vulnerability'],
            'cost': ['cost', 'budget', 'expense', 'economic'],
            'quality': ['quality', 'reliability', 'robust', 'stable'],
            'time': ['time', 'deadline', 'schedule', 'urgent']
        }
        
        # Count priority mentions in each argument
        priorities1 = {}
        priorities2 = {}
        
        for priority, keywords in priority_keywords.items():
            count1 = sum(1 for kw in keywords if kw in arg1.claim.lower())
            count2 = sum(1 for kw in keywords if kw in arg2.claim.lower())
            
            if count1 > 0:
                priorities1[priority] = count1
            if count2 > 0:
                priorities2[priority] = count2
        
        # Check for conflicting priorities
        if priorities1 and priorities2:
            top_priority1 = max(priorities1.items(), key=lambda x: x[1])[0]
            top_priority2 = max(priorities2.items(), key=lambda x: x[1])[0]
            
            # Some priorities naturally conflict
            conflicting_priorities = [
                ('performance', 'security'),
                ('cost', 'quality'),
                ('time', 'quality'),
                ('performance', 'cost')
            ]
            
            for p1, p2 in conflicting_priorities:
                if ((top_priority1 == p1 and top_priority2 == p2) or
                    (top_priority1 == p2 and top_priority2 == p1)):
                    
                    return ConflictPoint(
                        conflict_id=str(uuid4()),
                        conflict_type='priority_conflict',
                        severity=0.6,
                        argument_ids=[arg1.argument_id, arg2.argument_id],
                        agent_roles=[arg1.agent_role, arg2.agent_role],
                        conflict_description=f"Conflicting priorities: {top_priority1} vs {top_priority2}",
                        blocks_consensus=True,
                        affects_agents={arg1.agent_role, arg2.agent_role},
                        priority_level='high'
                    )
        
        return None
    
    async def _detect_assumption_conflict(self, arg1: StructuredArgument,
                                        arg2: StructuredArgument) -> Optional[ConflictPoint]:
        """Detect conflicting assumptions between arguments."""
        if not arg1.assumptions or not arg2.assumptions:
            return None
        
        # Compare assumptions for contradictions
        conflicting_assumptions = []
        
        for assumption1 in arg1.assumptions:
            for assumption2 in arg2.assumptions:
                if self._assumptions_conflict(assumption1, assumption2):
                    conflicting_assumptions.append((assumption1, assumption2))
        
        if conflicting_assumptions:
            severity = len(conflicting_assumptions) / max(len(arg1.assumptions), len(arg2.assumptions))
            
            return ConflictPoint(
                conflict_id=str(uuid4()),
                conflict_type='assumption_conflict',
                severity=severity,
                argument_ids=[arg1.argument_id, arg2.argument_id],
                agent_roles=[arg1.agent_role, arg2.agent_role],
                conflict_description=f"Conflicting underlying assumptions",
                blocks_consensus=severity > 0.5,
                affects_agents={arg1.agent_role, arg2.agent_role},
                priority_level='medium' if severity > 0.3 else 'low'
            )
        
        return None
    
    def _evidence_contradicts(self, evidence1: str, evidence2: str) -> bool:
        """Check if two pieces of evidence contradict each other."""
        # Simple contradiction detection
        contradiction_indicators = [
            ('increases', 'decreases'),
            ('improves', 'worsens'),
            ('successful', 'failed'),
            ('effective', 'ineffective'),
            ('higher', 'lower')
        ]
        
        ev1_lower = evidence1.lower()
        ev2_lower = evidence2.lower()
        
        for pos, neg in contradiction_indicators:
            if ((pos in ev1_lower and neg in ev2_lower) or
                (neg in ev1_lower and pos in ev2_lower)):
                return True
        
        return False
    
    def _assumptions_conflict(self, assumption1: str, assumption2: str) -> bool:
        """Check if two assumptions conflict."""
        # Simple assumption conflict detection
        assumption1_lower = assumption1.lower()
        assumption2_lower = assumption2.lower()
        
        # Look for direct negations or opposite statements
        if ('not' in assumption1_lower) != ('not' in assumption2_lower):
            # One has negation, one doesn't - check if they're about the same thing
            clean1 = assumption1_lower.replace('not ', '')
            clean2 = assumption2_lower.replace('not ', '')
            
            # Simple word overlap check
            words1 = set(clean1.split())
            words2 = set(clean2.split())
            overlap = len(words1.intersection(words2))
            
            if overlap >= min(len(words1), len(words2)) * 0.5:
                return True
        
        return False
    
    async def _mediate_conflict(self, conflict: ConflictPoint,
                              arguments: List[StructuredArgument],
                              user_context: UserContext,
                              team_context: Optional[TeamContext]) -> Dict[str, Any]:
        """Mediate a conflict through structured dialogue."""
        resolution = {
            'success': True,
            'resolution_content': '',
            'affected_arguments': conflict.argument_ids,
            'confidence': 0.7,
            'requires_follow_up': False
        }
        
        # Generate mediated response
        conflicting_args = [arg for arg in arguments if arg.argument_id in conflict.argument_ids]
        
        if len(conflicting_args) >= 2:
            arg1, arg2 = conflicting_args[0], conflicting_args[1]
            
            resolution['resolution_content'] = f"""
I see there's a disagreement between {arg1.agent_role.value} and {arg2.agent_role.value} regarding this issue.

{arg1.agent_role.value} position: {arg1.claim[:100]}...
{arg2.agent_role.value} position: {arg2.claim[:100]}...

Let me help find common ground:
1. Both positions seem to prioritize the project's success
2. The disagreement appears to be about approach rather than goals
3. Perhaps we can explore a hybrid approach that incorporates both perspectives

Suggested next steps:
- Clarify the specific points of disagreement
- Identify shared objectives
- Explore compromise solutions
- Consider piloting different approaches
"""
            
        return resolution
    
    async def _negotiate_resolution(self, conflict: ConflictPoint,
                                  arguments: List[StructuredArgument],
                                  user_context: UserContext,
                                  team_context: Optional[TeamContext]) -> Dict[str, Any]:
        """Negotiate a resolution through compromise."""
        resolution = {
            'success': True,
            'resolution_content': '',
            'affected_arguments': conflict.argument_ids,
            'confidence': 0.6,
            'requires_follow_up': True
        }
        
        resolution['resolution_content'] = f"""
This conflict requires negotiation between the involved parties. Here's a structured approach:

**Conflict Summary:** {conflict.conflict_description}
**Severity:** {conflict.severity:.2f}

**Negotiation Framework:**
1. **Position Clarification**: Each party clarifies their core position
2. **Interest Identification**: What underlying needs drive each position?  
3. **Option Generation**: Brainstorm multiple possible solutions
4. **Criteria Development**: Agree on evaluation criteria for solutions
5. **Solution Selection**: Choose the best option using agreed criteria

**Potential Compromise Areas:**
- Implementation timeline flexibility
- Resource allocation adjustments  
- Scope modifications
- Risk mitigation strategies

This will require follow-up discussion with the involved agents.
"""
        
        return resolution
    
    async def _arbitrate_conflict(self, conflict: ConflictPoint,
                                arguments: List[StructuredArgument], 
                                user_context: UserContext,
                                team_context: Optional[TeamContext]) -> Dict[str, Any]:
        """Arbitrate conflict through expert decision."""
        resolution = {
            'success': True,
            'resolution_content': '',
            'affected_arguments': conflict.argument_ids,
            'confidence': 0.8,
            'requires_follow_up': False
        }
        
        # Find the most expert agent involved
        conflicting_args = [arg for arg in arguments if arg.argument_id in conflict.argument_ids]
        expert_arg = max(conflicting_args, 
                        key=lambda a: a.agent_profile.technical_expertise_level)
        
        resolution['resolution_content'] = f"""
**Arbitration Decision**

After reviewing the conflict between positions, I'm making an executive decision based on:
- Technical expertise levels of involved agents
- Quality of evidence presented  
- Alignment with project objectives
- Risk assessment

**Decision:** The position presented by {expert_arg.agent_role.value} will be adopted as the primary approach.

**Rationale:**
- Highest technical expertise level ({expert_arg.agent_profile.technical_expertise_level:.2f})
- Strongest evidence quality ({expert_arg.quality_metrics.get('evidence_strength', 0.5):.2f})
- Best alignment with current project context

**Implementation:** This decision is final for this debate session but can be revisited in future discussions if new evidence emerges.
"""
        
        return resolution
    
    async def _synthesize_resolution(self, conflict: ConflictPoint,
                                   arguments: List[StructuredArgument],
                                   user_context: UserContext,
                                   team_context: Optional[TeamContext]) -> Dict[str, Any]:
        """Synthesize a new position that resolves the conflict."""
        resolution = {
            'success': True,
            'resolution_content': '',
            'affected_arguments': conflict.argument_ids,
            'confidence': 0.9,
            'requires_follow_up': False
        }
        
        conflicting_args = [arg for arg in arguments if arg.argument_id in conflict.argument_ids]
        
        # Extract key elements from conflicting arguments
        all_claims = [arg.claim for arg in conflicting_args]
        all_evidence = []
        for arg in conflicting_args:
            all_evidence.extend(arg.evidence)
        
        resolution['resolution_content'] = f"""
**Synthesized Resolution**

Instead of choosing between conflicting positions, I'm proposing a synthesized approach that integrates the best elements from each:

**Integrated Position:**
Drawing from the insights of {', '.join(arg.agent_role.value for arg in conflicting_args)}, 
we can develop a comprehensive solution that:

1. **Combines Strengths**: Takes the strongest elements from each position
2. **Mitigates Weaknesses**: Addresses the limitations of individual approaches  
3. **Balances Trade-offs**: Finds optimal balance between competing priorities
4. **Reduces Risks**: Incorporates risk mitigation from multiple perspectives

**Synthesis Framework:**
- Primary approach based on strongest evidence
- Secondary elements from alternative positions
- Implementation phases to test different approaches
- Continuous evaluation and adjustment process

This synthesis resolves the conflict by creating a superior solution that neither original position could achieve alone.
"""
        
        return resolution


class DebateRoundManager:
    """
    Advanced debate round management with iterative refinement and structured argumentation.
    
    Features:
    - Multi-round iterative refinement with quality improvement
    - Structured evidence presentation and validation  
    - Conflict detection and resolution
    - Real-time convergence tracking
    - Performance optimization with parallel processing
    - Emotional intelligence integration for productive debates
    """
    
    def __init__(self, 
                 consensus_engine: ConsensusEngine,
                 config: Optional[DebateManagementConfig] = None):
        
        self.consensus_engine = consensus_engine
        self.config = config or DebateManagementConfig()
        
        # Core components
        self.evidence_validator = EvidenceValidator()
        self.conflict_resolver = ConflictResolver()
        
        # State management
        self.active_rounds: Dict[str, DebateRound] = {}
        self.round_history: deque = deque(maxlen=1000)
        self.performance_metrics = {
            'total_rounds_managed': 0,
            'average_round_duration_ms': 0.0,
            'conflicts_detected': 0,
            'conflicts_resolved': 0,
            'early_convergence_rate': 0.0,
            'quality_improvement_rate': 0.0
        }
        self._metrics_lock = threading.Lock()
        
        # Performance optimization
        self.thread_pool = ThreadPoolExecutor(max_workers=4)
        self.argument_cache = {}
        
        logger.info("DebateRoundManager initialized with advanced features")
    
    async def manage_debate_round(self,
                                session: DebateSession,
                                round_number: int,
                                previous_arguments: List[StructuredArgument],
                                participant_profiles: Dict[AgentRole, AgentProfile]) -> RoundMetrics:
        """
        Manage a single debate round with full lifecycle management.
        
        Args:
            session: Current debate session
            round_number: Round number being processed
            previous_arguments: Arguments from previous rounds
            participant_profiles: Agent profiles for participants
            
        Returns:
            RoundMetrics with comprehensive round analysis
        """
        round_start = time.time()
        round_id = f"{session.session_id}_round_{round_number}"
        
        try:
            with self._metrics_lock:
                self.performance_metrics['total_rounds_managed'] += 1
            
            logger.info(f"Managing debate round {round_number} for session {session.session_id}")
            
            # Initialize round
            current_round = DebateRound(
                round_number=round_number,
                phase=DebatePhase.ARGUMENT_EXCHANGE,
                started_at=datetime.utcnow()
            )
            self.active_rounds[round_id] = current_round
            
            # Generate structured arguments
            new_arguments = await self._generate_round_arguments(
                session, current_round, previous_arguments, participant_profiles
            )
            
            # Validate evidence quality
            await self._validate_round_evidence(new_arguments)
            
            # Detect and resolve conflicts
            conflicts = await self._detect_and_resolve_conflicts(
                new_arguments, previous_arguments, session.user_context, session.team_context
            )
            
            # Calculate round metrics
            round_metrics = await self._calculate_round_metrics(
                current_round, new_arguments, conflicts, participant_profiles
            )
            
            # Check for convergence
            convergence_analysis = await self._analyze_convergence(
                new_arguments, previous_arguments, round_metrics
            )
            
            # Update session state
            current_round.arguments = [self._convert_to_debate_argument(arg) for arg in new_arguments]
            current_round.consensus_score = convergence_analysis.get('consensus_score', 0.0)
            current_round.quality_metrics = round_metrics.__dict__
            current_round.completed_at = datetime.utcnow()
            
            # Performance tracking
            round_duration = (time.time() - round_start) * 1000
            round_metrics.round_duration_ms = round_duration
            
            # Store in history
            self.round_history.append({
                'session_id': session.session_id,
                'round_number': round_number,
                'duration_ms': round_duration,
                'arguments_count': len(new_arguments),
                'conflicts_detected': len(conflicts),
                'consensus_score': current_round.consensus_score,
                'timestamp': datetime.utcnow()
            })
            
            # Cleanup
            if round_id in self.active_rounds:
                del self.active_rounds[round_id]
            
            logger.info(
                f"Round {round_number} completed: {len(new_arguments)} arguments, "
                f"{len(conflicts)} conflicts, consensus_score={current_round.consensus_score:.3f}, "
                f"duration={round_duration:.1f}ms"
            )
            
            return round_metrics
            
        except Exception as e:
            logger.error(f"Error managing debate round {round_number}: {e}")
            raise
        finally:
            # Cleanup
            if round_id in self.active_rounds:
                del self.active_rounds[round_id]
    
    async def _generate_round_arguments(self,
                                      session: DebateSession,
                                      current_round: DebateRound,
                                      previous_arguments: List[StructuredArgument],
                                      participant_profiles: Dict[AgentRole, AgentProfile]) -> List[StructuredArgument]:
        """Generate structured arguments for the current round."""
        arguments = []
        
        # Determine argument types needed for this round
        if current_round.round_number == 1:
            primary_type = ArgumentType.CLAIM
        elif current_round.round_number <= 3:
            primary_type = ArgumentType.EVIDENCE
        else:
            primary_type = ArgumentType.REASONING
        
        # Generate arguments in parallel if enabled
        if self.config.parallel_processing:
            tasks = []
            for agent_role, profile in participant_profiles.items():
                task = self._generate_agent_argument(
                    agent_role, profile, session, current_round,
                    previous_arguments, primary_type
                )
                tasks.append(task)
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, StructuredArgument):
                    arguments.append(result)
                elif isinstance(result, Exception):
                    logger.warning(f"Error generating argument: {result}")
        
        else:
            # Sequential generation
            for agent_role, profile in participant_profiles.items():
                try:
                    argument = await self._generate_agent_argument(
                        agent_role, profile, session, current_round,
                        previous_arguments, primary_type
                    )
                    if argument:
                        arguments.append(argument)
                except Exception as e:
                    logger.warning(f"Error generating argument for {agent_role.value}: {e}")
        
        return arguments
    
    async def _generate_agent_argument(self,
                                     agent_role: AgentRole,
                                     agent_profile: AgentProfile,
                                     session: DebateSession,
                                     current_round: DebateRound,
                                     previous_arguments: List[StructuredArgument],
                                     argument_type: ArgumentType) -> Optional[StructuredArgument]:
        """Generate a structured argument from a specific agent."""
        start_time = time.time()
        
        try:
            # Build context for argument generation
            context = await self._build_argument_context(
                agent_role, session, previous_arguments, argument_type
            )
            
            # Generate argument content
            argument_content = await self._generate_argument_content(
                agent_role, agent_profile, context, argument_type
            )
            
            if not argument_content:
                return None
            
            # Create structured argument
            argument = StructuredArgument(
                argument_id=str(uuid4()),
                argument_type=argument_type,
                claim=argument_content.get('claim', ''),
                reasoning=argument_content.get('reasoning', []),
                assumptions=argument_content.get('assumptions', []),
                agent_role=agent_role,
                agent_profile=agent_profile,
                round_number=current_round.round_number,
                sequence_number=len(current_round.arguments) + 1,
                processing_time_ms=(time.time() - start_time) * 1000
            )
            
            # Generate evidence
            if argument_content.get('evidence_requirements'):
                evidence_list = await self._generate_evidence(
                    argument_content['evidence_requirements'], 
                    agent_role, 
                    context
                )
                argument.evidence = evidence_list
            
            # Calculate initial quality scores
            argument.quality_metrics = await self._assess_argument_quality(argument)
            argument.strength_score = argument.quality_metrics.get('overall_strength', 0.0)
            argument.confidence = min(1.0, agent_profile.technical_expertise_level * 
                                   argument.quality_metrics.get('evidence_strength', 0.5))
            
            return argument
            
        except Exception as e:
            logger.error(f"Error generating argument for {agent_role.value}: {e}")
            return None
    
    async def _build_argument_context(self,
                                    agent_role: AgentRole,
                                    session: DebateSession,
                                    previous_arguments: List[StructuredArgument],
                                    argument_type: ArgumentType) -> Dict[str, Any]:
        """Build context for argument generation."""
        context = {
            'session_topic': session.topic,
            'round_number': len(session.rounds) + 1,
            'argument_type': argument_type,
            'agent_role': agent_role,
            'user_emotional_state': session.user_context.emotional_state,
            'team_context': session.team_context.__dict__ if session.team_context else None,
            'previous_arguments_summary': []
        }
        
        # Summarize relevant previous arguments
        relevant_args = [
            arg for arg in previous_arguments
            if (arg.agent_role == agent_role or 
                any(agent_role.value in response for response in arg.responses))
        ]
        
        for arg in relevant_args[-3:]:  # Last 3 relevant arguments
            context['previous_arguments_summary'].append({
                'agent': arg.agent_role.value,
                'claim': arg.claim[:100] + '...',
                'type': arg.argument_type.value,
                'strength': arg.strength_score
            })
        
        return context
    
    async def _generate_argument_content(self,
                                       agent_role: AgentRole,
                                       agent_profile: AgentProfile,
                                       context: Dict[str, Any],
                                       argument_type: ArgumentType) -> Optional[Dict[str, Any]]:
        """Generate argument content based on agent profile and context."""
        
        # This would integrate with actual LLM API in production
        # For now, return simulated content based on agent role and type
        
        if agent_role == AgentRole.TECHNICAL_EXPERT:
            return await self._generate_technical_argument(agent_profile, context, argument_type)
        elif agent_role == AgentRole.PROJECT_MANAGER:
            return await self._generate_pm_argument(agent_profile, context, argument_type)
        elif agent_role == AgentRole.WELLNESS_COACH:
            return await self._generate_wellness_argument(agent_profile, context, argument_type)
        else:
            return await self._generate_general_argument(agent_profile, context, argument_type)
    
    async def _generate_technical_argument(self,
                                         agent_profile: AgentProfile,
                                         context: Dict[str, Any],
                                         argument_type: ArgumentType) -> Dict[str, Any]:
        """Generate technical expert argument."""
        topic = context.get('session_topic', 'technical issue')
        
        if argument_type == ArgumentType.CLAIM:
            claim = f"From a technical perspective, {topic} requires a systematic approach focusing on architecture, scalability, and maintainability."
            reasoning = [
                "Technical solutions must consider long-term maintainability",
                "Scalability requirements will grow with system usage",
                "Architecture decisions have lasting impact on team productivity"
            ]
            assumptions = [
                "The system will need to handle increased load over time",
                "The development team has appropriate technical expertise",
                "Quality and performance are higher priorities than speed of delivery"
            ]
            evidence_requirements = ["technical_benchmarks", "architecture_patterns", "performance_data"]
            
        elif argument_type == ArgumentType.EVIDENCE:
            claim = f"Technical analysis supports a robust implementation approach for {topic}."
            reasoning = [
                "Industry benchmarks show similar approaches succeed",
                "Performance testing validates the proposed solution",
                "Code quality metrics indicate maintainable architecture"
            ]
            assumptions = ["Historical data is representative of future performance"]
            evidence_requirements = ["performance_metrics", "benchmark_comparisons", "case_studies"]
            
        else:  # REASONING
            claim = f"The technical reasoning behind {topic} is sound based on established engineering principles."
            reasoning = [
                "Follows established software engineering best practices",
                "Incorporates lessons learned from similar implementations",
                "Balances technical debt with feature delivery requirements"
            ]
            assumptions = ["Engineering best practices are applicable to this context"]
            evidence_requirements = ["best_practice_guidelines", "engineering_standards"]
        
        return {
            'claim': claim,
            'reasoning': reasoning,
            'assumptions': assumptions,
            'evidence_requirements': evidence_requirements
        }
    
    async def _generate_pm_argument(self,
                                  agent_profile: AgentProfile,
                                  context: Dict[str, Any],
                                  argument_type: ArgumentType) -> Dict[str, Any]:
        """Generate project manager argument."""
        topic = context.get('session_topic', 'project issue')
        
        if argument_type == ArgumentType.CLAIM:
            claim = f"From a project management perspective, {topic} needs clear scope definition, timeline planning, and risk management."
            reasoning = [
                "Project success depends on clear deliverable definitions",
                "Timeline management prevents scope creep and delays",
                "Risk identification enables proactive mitigation strategies"
            ]
            assumptions = [
                "Stakeholders will provide clear requirements",
                "Team capacity is accurately estimated",
                "External dependencies are manageable"
            ]
            evidence_requirements = ["project_metrics", "timeline_analysis", "risk_assessment"]
            
        elif argument_type == ArgumentType.EVIDENCE:
            claim = f"Project data supports a structured approach to {topic}."
            reasoning = [
                "Historical project metrics show structured approaches succeed",
                "Risk analysis indicates manageable project complexity",
                "Resource allocation aligns with project timeline"
            ]
            assumptions = ["Past project performance predicts future results"]
            evidence_requirements = ["project_history", "resource_analysis", "timeline_data"]
            
        else:  # REASONING
            claim = f"Project management principles support the proposed approach to {topic}."
            reasoning = [
                "Aligns with established project management frameworks",
                "Incorporates stakeholder feedback and requirements",
                "Balances scope, timeline, and quality constraints"
            ]
            assumptions = ["Project management frameworks are applicable"]
            evidence_requirements = ["pm_frameworks", "stakeholder_analysis"]
        
        return {
            'claim': claim,
            'reasoning': reasoning,
            'assumptions': assumptions,
            'evidence_requirements': evidence_requirements
        }
    
    async def _generate_wellness_argument(self,
                                        agent_profile: AgentProfile,
                                        context: Dict[str, Any],
                                        argument_type: ArgumentType) -> Dict[str, Any]:
        """Generate wellness coach argument."""
        topic = context.get('session_topic', 'team issue')
        emotional_state = context.get('user_emotional_state', EmotionalState.NEUTRAL)
        
        if argument_type == ArgumentType.CLAIM:
            claim = f"From a team wellness perspective, {topic} should prioritize sustainable practices and team member well-being."
            reasoning = [
                "Sustainable practices prevent burnout and maintain long-term productivity",
                "Team well-being directly impacts quality of work and collaboration",
                "Stress management improves decision-making and creativity"
            ]
            assumptions = [
                "Team members' well-being affects overall project success",
                "Sustainable pace is more effective than intensive sprints",
                f"Current emotional state ({emotional_state.value}) impacts team dynamics"
            ]
            evidence_requirements = ["wellness_metrics", "productivity_studies", "stress_indicators"]
            
        elif argument_type == ArgumentType.EVIDENCE:
            claim = f"Research supports prioritizing team wellness in addressing {topic}."
            reasoning = [
                "Studies show correlation between team wellness and project success",
                "Stress reduction improves problem-solving capabilities", 
                "Sustainable practices lead to higher quality outcomes"
            ]
            assumptions = ["Research findings apply to this team context"]
            evidence_requirements = ["research_studies", "wellness_data", "performance_correlation"]
            
        else:  # REASONING
            claim = f"Wellness considerations provide valuable insight for {topic}."
            reasoning = [
                "Considers human factors in technical and project decisions",
                "Promotes long-term sustainability over short-term gains",
                "Integrates emotional intelligence with practical solutions"
            ]
            assumptions = ["Human factors significantly influence project outcomes"]
            evidence_requirements = ["human_factors_research", "emotional_intelligence_studies"]
        
        return {
            'claim': claim,
            'reasoning': reasoning,
            'assumptions': assumptions,
            'evidence_requirements': evidence_requirements
        }
    
    async def _generate_general_argument(self,
                                       agent_profile: AgentProfile,
                                       context: Dict[str, Any],
                                       argument_type: ArgumentType) -> Dict[str, Any]:
        """Generate general argument for other agent types."""
        topic = context.get('session_topic', 'issue')
        
        if argument_type == ArgumentType.CLAIM:
            claim = f"Addressing {topic} requires a balanced approach considering multiple perspectives and stakeholder needs."
            reasoning = [
                "Multiple perspectives provide comprehensive understanding",
                "Stakeholder needs must be balanced for optimal outcomes",
                "Collaborative approaches tend to produce better solutions"
            ]
            assumptions = [
                "All stakeholders want the project to succeed",
                "Different perspectives can be reconciled",
                "Collaborative solutions are feasible"
            ]
            evidence_requirements = ["stakeholder_analysis", "collaborative_outcomes", "multi_perspective_benefits"]
            
        elif argument_type == ArgumentType.EVIDENCE:
            claim = f"Evidence supports a collaborative approach to {topic}."
            reasoning = [
                "Case studies show collaborative approaches succeed",
                "Stakeholder feedback indicates support for balanced solutions",
                "Multi-disciplinary teams produce innovative solutions"
            ]
            assumptions = ["Historical evidence applies to current situation"]
            evidence_requirements = ["case_studies", "stakeholder_feedback", "collaboration_research"]
            
        else:  # REASONING
            claim = f"Logical analysis supports addressing {topic} through systematic evaluation."
            reasoning = [
                "Systematic evaluation reduces bias and oversight",
                "Structured analysis improves decision quality",
                "Evidence-based approaches are more reliable"
            ]
            assumptions = ["Systematic approaches are superior to ad-hoc solutions"]
            evidence_requirements = ["analysis_frameworks", "decision_quality_research"]
        
        return {
            'claim': claim,
            'reasoning': reasoning,
            'assumptions': assumptions,
            'evidence_requirements': evidence_requirements
        }
    
    async def _generate_evidence(self,
                               requirements: List[str],
                               agent_role: AgentRole,
                               context: Dict[str, Any]) -> List[Evidence]:
        """Generate evidence based on requirements."""
        evidence_list = []
        
        for requirement in requirements:
            evidence = Evidence(
                evidence_id=str(uuid4()),
                evidence_type=self._map_requirement_to_evidence_type(requirement),
                content=await self._generate_evidence_content(requirement, agent_role, context),
                source=f"Internal {agent_role.value} knowledge base",
                credibility_score=0.7 + (agent_role == AgentRole.TECHNICAL_EXPERT) * 0.2,
                relevance_score=0.8,
                recency_score=0.9,
                verification_status='unverified',
                created_by=agent_role
            )
            evidence_list.append(evidence)
        
        return evidence_list
    
    def _map_requirement_to_evidence_type(self, requirement: str) -> EvidenceType:
        """Map evidence requirements to evidence types."""
        mapping = {
            'technical_benchmarks': EvidenceType.EMPIRICAL_DATA,
            'performance_data': EvidenceType.EMPIRICAL_DATA,
            'case_studies': EvidenceType.CASE_STUDY,
            'best_practice': EvidenceType.BEST_PRACTICE,
            'research_studies': EvidenceType.EMPIRICAL_DATA,
            'expert_opinion': EvidenceType.EXPERT_OPINION,
            'risk_assessment': EvidenceType.RISK_ANALYSIS
        }
        
        for key, evidence_type in mapping.items():
            if key in requirement:
                return evidence_type
        
        return EvidenceType.THEORETICAL  # Default
    
    async def _generate_evidence_content(self,
                                       requirement: str,
                                       agent_role: AgentRole,
                                       context: Dict[str, Any]) -> str:
        """Generate evidence content based on requirement."""
        # Simplified evidence generation
        if 'benchmark' in requirement:
            return f"Industry benchmarks show that similar approaches achieve 85-95% success rates in comparable contexts."
        elif 'performance' in requirement:
            return f"Performance testing indicates 40-60% improvement in key metrics when following this approach."
        elif 'case_study' in requirement:
            return f"Case study analysis of 15 similar projects shows consistent positive outcomes with this methodology."
        elif 'research' in requirement:
            return f"Research studies from leading institutions demonstrate significant benefits of this approach."
        elif 'risk' in requirement:
            return f"Risk analysis indicates manageable risk levels (2.3/10) with proper mitigation strategies."
        else:
            return f"Analysis shows strong support for this approach based on established principles and practices."
    
    async def _validate_round_evidence(self, arguments: List[StructuredArgument]):
        """Validate evidence quality for all arguments in the round."""
        for argument in arguments:
            for evidence in argument.evidence:
                try:
                    validation_result = await self.evidence_validator.validate_evidence(evidence)
                    
                    # Update evidence scores based on validation
                    evidence.credibility_score = validation_result['validation_score']
                    
                    if not validation_result['is_valid']:
                        logger.warning(
                            f"Low quality evidence detected: {evidence.evidence_id} "
                            f"(score: {validation_result['validation_score']:.2f})"
                        )
                        
                except Exception as e:
                    logger.error(f"Error validating evidence {evidence.evidence_id}: {e}")
    
    async def _detect_and_resolve_conflicts(self,
                                          new_arguments: List[StructuredArgument],
                                          previous_arguments: List[StructuredArgument],
                                          user_context: UserContext,
                                          team_context: Optional[TeamContext]) -> List[ConflictPoint]:
        """Detect and resolve conflicts in arguments."""
        all_arguments = new_arguments + previous_arguments
        
        # Detect conflicts
        conflicts = await self.conflict_resolver.detect_conflicts(all_arguments)
        
        with self._metrics_lock:
            self.performance_metrics['conflicts_detected'] += len(conflicts)
        
        # Resolve conflicts if enabled
        if self.config.require_conflict_resolution and conflicts:
            resolved_conflicts = []
            
            for conflict in conflicts:
                if conflict.severity >= self.config.conflict_escalation_threshold:
                    try:
                        resolution_result = await self.conflict_resolver.resolve_conflict(
                            conflict, all_arguments, user_context, team_context
                        )
                        
                        if resolution_result['success']:
                            resolved_conflicts.append(conflict)
                            logger.info(f"Resolved conflict {conflict.conflict_id} using {resolution_result['resolution_method']}")
                        
                    except Exception as e:
                        logger.error(f"Error resolving conflict {conflict.conflict_id}: {e}")
            
            with self._metrics_lock:
                self.performance_metrics['conflicts_resolved'] += len(resolved_conflicts)
        
        return conflicts
    
    async def _calculate_round_metrics(self,
                                     current_round: DebateRound,
                                     arguments: List[StructuredArgument],
                                     conflicts: List[ConflictPoint],
                                     participant_profiles: Dict[AgentRole, AgentProfile]) -> RoundMetrics:
        """Calculate comprehensive metrics for the debate round."""
        metrics = RoundMetrics(round_number=current_round.round_number)
        
        if not arguments:
            return metrics
        
        # Basic participation metrics
        metrics.total_arguments = len(arguments)
        
        # Arguments by type
        for arg in arguments:
            arg_type = arg.argument_type
            metrics.arguments_by_type[arg_type] = metrics.arguments_by_type.get(arg_type, 0) + 1
        
        # Agent participation
        for arg in arguments:
            agent_role = arg.agent_role
            metrics.agent_participation[agent_role] = metrics.agent_participation.get(agent_role, 0) + 1
        
        # Quality metrics
        quality_scores = [arg.quality_metrics.get('overall_strength', 0.0) for arg in arguments]
        metrics.average_argument_quality = sum(quality_scores) / len(quality_scores)
        
        # Evidence metrics
        total_evidence = sum(len(arg.evidence) for arg in arguments)
        metrics.evidence_density = total_evidence / len(arguments)
        
        # Reasoning depth
        reasoning_lengths = [len(arg.reasoning) for arg in arguments]
        metrics.reasoning_depth = sum(reasoning_lengths) / len(reasoning_lengths)
        
        # Interaction metrics
        response_count = sum(len(arg.responses) for arg in arguments)
        metrics.response_rate = response_count / len(arguments) if len(arguments) > 0 else 0.0
        
        # Conflict metrics
        metrics.conflict_count = len(conflicts)
        metrics.conflicts_resolved = len([c for c in conflicts if c.resolution_status == 'resolved'])
        
        # Position diversity (simplified)
        unique_claims = len(set(arg.claim[:50] for arg in arguments))  # First 50 chars as proxy
        metrics.position_diversity = unique_claims / len(arguments)
        
        # Performance metrics
        processing_times = [arg.processing_time_ms for arg in arguments]
        metrics.average_response_time_ms = sum(processing_times) / len(processing_times)
        
        # Emotional dynamics (simplified)
        if any(participant_profiles):
            empathy_levels = [profile.emotional_intelligence_level for profile in participant_profiles.values()]
            metrics.collaboration_level = sum(empathy_levels) / len(empathy_levels)
        
        return metrics
    
    async def _analyze_convergence(self,
                                 new_arguments: List[StructuredArgument],
                                 previous_arguments: List[StructuredArgument],
                                 round_metrics: RoundMetrics) -> Dict[str, Any]:
        """Analyze convergence patterns in arguments."""
        convergence_analysis = {
            'consensus_score': 0.0,
            'convergence_trend': 0.0,
            'early_convergence_detected': False,
            'convergence_indicators': []
        }
        
        if not new_arguments:
            return convergence_analysis
        
        # Simple consensus scoring based on position similarity
        position_similarity = self._calculate_position_similarity(new_arguments)
        convergence_analysis['consensus_score'] = position_similarity
        
        # Check for early convergence
        if (position_similarity >= self.config.early_convergence_threshold and
            round_metrics.position_diversity <= self.config.position_diversity_threshold):
            
            convergence_analysis['early_convergence_detected'] = True
            convergence_analysis['convergence_indicators'].append('high_position_similarity')
            convergence_analysis['convergence_indicators'].append('low_position_diversity')
            
            with self._metrics_lock:
                total_rounds = self.performance_metrics['total_rounds_managed']
                current_rate = self.performance_metrics['early_convergence_rate']
                self.performance_metrics['early_convergence_rate'] = (
                    (current_rate * (total_rounds - 1) + 1) / total_rounds
                )
        
        # Trend analysis (if we have previous rounds)
        if previous_arguments:
            previous_similarity = self._calculate_position_similarity(previous_arguments)
            convergence_analysis['convergence_trend'] = position_similarity - previous_similarity
        
        return convergence_analysis
    
    def _calculate_position_similarity(self, arguments: List[StructuredArgument]) -> float:
        """Calculate similarity between argument positions."""
        if len(arguments) < 2:
            return 1.0
        
        # Simple word-based similarity calculation
        all_claims = [arg.claim.lower() for arg in arguments]
        
        total_similarity = 0.0
        comparison_count = 0
        
        for i, claim1 in enumerate(all_claims):
            for claim2 in all_claims[i+1:]:
                words1 = set(claim1.split())
                words2 = set(claim2.split())
                
                if words1 or words2:
                    intersection = len(words1.intersection(words2))
                    union = len(words1.union(words2))
                    similarity = intersection / union if union > 0 else 0.0
                    total_similarity += similarity
                    comparison_count += 1
        
        return total_similarity / comparison_count if comparison_count > 0 else 0.0
    
    async def _assess_argument_quality(self, argument: StructuredArgument) -> Dict[str, float]:
        """Assess comprehensive quality of an argument."""
        quality_metrics = {}
        
        # Claim quality
        claim_quality = self._assess_claim_quality(argument.claim)
        quality_metrics['claim_quality'] = claim_quality
        
        # Reasoning quality
        reasoning_quality = self._assess_reasoning_quality(argument.reasoning)
        quality_metrics['reasoning_quality'] = reasoning_quality
        
        # Evidence quality
        if argument.evidence:
            evidence_scores = [ev.credibility_score for ev in argument.evidence]
            evidence_quality = sum(evidence_scores) / len(evidence_scores)
        else:
            evidence_quality = 0.0
        quality_metrics['evidence_strength'] = evidence_quality
        
        # Structure quality
        structure_quality = self._assess_structure_quality(argument)
        quality_metrics['structure_quality'] = structure_quality
        
        # Overall quality
        overall_quality = (
            claim_quality * 0.3 +
            reasoning_quality * 0.3 +
            evidence_quality * 0.25 +
            structure_quality * 0.15
        )
        quality_metrics['overall_strength'] = overall_quality
        
        return quality_metrics
    
    def _assess_claim_quality(self, claim: str) -> float:
        """Assess quality of argument claim."""
        if not claim:
            return 0.0
        
        quality_score = 0.0
        
        # Length appropriateness
        if 20 <= len(claim) <= 200:
            quality_score += 0.3
        
        # Clarity indicators
        clear_indicators = ['specifically', 'clearly', 'precisely', 'exactly']
        if any(indicator in claim.lower() for indicator in clear_indicators):
            quality_score += 0.2
        
        # Action-oriented language
        action_indicators = ['recommend', 'propose', 'suggest', 'should', 'must']
        if any(indicator in claim.lower() for indicator in action_indicators):
            quality_score += 0.2
        
        # Specificity indicators
        if any(char.isdigit() for char in claim):  # Contains numbers
            quality_score += 0.1
        
        # Professional tone
        if claim[0].isupper() and claim.endswith('.'):
            quality_score += 0.2
        
        return min(1.0, quality_score)
    
    def _assess_reasoning_quality(self, reasoning: List[str]) -> float:
        """Assess quality of reasoning chain."""
        if not reasoning:
            return 0.0
        
        quality_score = 0.0
        
        # Quantity factor
        reasoning_count = len(reasoning)
        if reasoning_count >= 2:
            quality_score += 0.3
        
        # Logical connectors
        logical_connectors = ['because', 'therefore', 'consequently', 'since', 'thus']
        connector_count = sum(
            1 for reason in reasoning
            for connector in logical_connectors
            if connector in reason.lower()
        )
        quality_score += min(0.4, connector_count * 0.1)
        
        # Depth indicators
        depth_indicators = ['analysis', 'research', 'evidence', 'study', 'data']
        depth_count = sum(
            1 for reason in reasoning
            for indicator in depth_indicators
            if indicator in reason.lower()
        )
        quality_score += min(0.3, depth_count * 0.1)
        
        return min(1.0, quality_score)
    
    def _assess_structure_quality(self, argument: StructuredArgument) -> float:
        """Assess structural quality of argument."""
        quality_score = 0.5  # Base score
        
        # Has all key components
        if argument.claim:
            quality_score += 0.2
        if argument.reasoning:
            quality_score += 0.2
        if argument.evidence:
            quality_score += 0.2
        
        # Completeness bonus
        if argument.claim and argument.reasoning and argument.evidence:
            quality_score += 0.1
        
        return min(1.0, quality_score)
    
    def _convert_to_debate_argument(self, structured_arg: StructuredArgument) -> DebateArgument:
        """Convert StructuredArgument to DebateArgument for compatibility."""
        return DebateArgument(
            id=structured_arg.argument_id,
            participant_id=structured_arg.agent_role.value,
            phase=DebatePhase.ARGUMENT_EXCHANGE,
            round_number=structured_arg.round_number,
            content=structured_arg.claim,
            supporting_evidence=[ev.content for ev in structured_arg.evidence],
            confidence=structured_arg.confidence,
            quality_score=structured_arg.quality_metrics.get('overall_strength', 0.0),
            processing_time_ms=structured_arg.processing_time_ms
        )
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get comprehensive performance metrics."""
        with self._metrics_lock:
            metrics = dict(self.performance_metrics)
        
        # Add derived metrics
        if metrics['conflicts_detected'] > 0:
            metrics['conflict_resolution_rate'] = metrics['conflicts_resolved'] / metrics['conflicts_detected']
        else:
            metrics['conflict_resolution_rate'] = 0.0
        
        # Add recent performance trends
        if len(self.round_history) >= 5:
            recent_rounds = list(self.round_history)[-5:]
            metrics['recent_average_duration_ms'] = sum(r['duration_ms'] for r in recent_rounds) / len(recent_rounds)
            metrics['recent_average_arguments'] = sum(r['arguments_count'] for r in recent_rounds) / len(recent_rounds)
        
        return metrics
    
    async def shutdown(self):
        """Gracefully shutdown the debate round manager."""
        logger.info("Shutting down debate round manager...")
        
        # Complete any active rounds
        for round_id in list(self.active_rounds.keys()):
            logger.warning(f"Terminating active round {round_id}")
            del self.active_rounds[round_id]
        
        # Shutdown thread pool
        self.thread_pool.shutdown(wait=True)
        
        logger.info("Debate round manager shutdown completed")


# Export main classes for integration
__all__ = [
    'DebateRoundManager',
    'ArgumentType',
    'EvidenceType',
    'ConflictResolutionStage',
    'Evidence',
    'StructuredArgument',
    'ConflictPoint',
    'RoundMetrics',
    'DebateManagementConfig',
    'EvidenceValidator',
    'ConflictResolver'
]