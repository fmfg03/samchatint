"""
Unit tests for AgentConfidenceAssessor - agent divergence detection and uncertainty analysis.

Tests cover:
- Agent confidence scoring and analysis
- Response divergence detection algorithms
- Uncertainty quantification methods
- Confidence variance calculations
- Performance and accuracy metrics
- Edge cases and error handling
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, List, Any

from devnous.debate.confidence_assessor import (
    AgentConfidenceAssessor,
    ConfidenceMetrics,
    DivergenceType,
    UncertaintyType
)
from devnous.models import ConversationMessage


@pytest.mark.asyncio
class TestAgentConfidenceAssessor:
    """Test suite for AgentConfidenceAssessor component."""
    
    async def test_assessor_initialization(self):
        """Test proper initialization of confidence assessor."""
        assessor = AgentConfidenceAssessor()
        
        assert assessor.divergence_method == DivergenceType.COSINE_SIMILARITY
        assert len(assessor.confidence_thresholds) > 0
        assert assessor.uncertainty_threshold > 0.0
        assert assessor.min_agents_required >= 2

    async def test_assess_confidence_basic(
        self,
        confidence_assessor,
        sample_agent_responses,
        sample_conversation_messages
    ):
        """Test basic confidence assessment functionality."""
        confidence_metrics = await confidence_assessor.assess_confidence(
            sample_agent_responses,
            sample_conversation_messages
        )
        
        assert isinstance(confidence_metrics, ConfidenceMetrics)
        assert 0.0 <= confidence_metrics.average_confidence <= 1.0
        assert 0.0 <= confidence_metrics.confidence_variance <= 1.0
        assert 0.0 <= confidence_metrics.overall_uncertainty <= 1.0
        assert 0.0 <= confidence_metrics.response_divergence <= 1.0
        assert confidence_metrics.processing_time_ms > 0
        assert confidence_metrics.agents_analyzed > 0

    async def test_high_confidence_consensus(self, confidence_assessor):
        """Test detection of high confidence consensus."""
        high_confidence_responses = {
            'agent_1': {
                'response': 'Option A is the clear choice based on all criteria',
                'confidence': 0.95,
                'reasoning': 'Comprehensive analysis supports this decision',
                'priority_score': 0.9
            },
            'agent_2': {
                'response': 'I strongly agree with Option A for the same reasons',
                'confidence': 0.92,
                'reasoning': 'Analysis confirms this is the optimal solution',
                'priority_score': 0.88
            },
            'agent_3': {
                'response': 'Option A is definitely the best approach',
                'confidence': 0.90,
                'reasoning': 'All factors point to this conclusion',
                'priority_score': 0.85
            }
        }
        
        confidence_metrics = await confidence_assessor.assess_confidence(
            high_confidence_responses
        )
        
        assert confidence_metrics.average_confidence > 0.9
        assert confidence_metrics.confidence_variance < 0.1
        assert confidence_metrics.overall_uncertainty < 0.2
        assert confidence_metrics.response_divergence < 0.3

    async def test_high_divergence_detection(self, confidence_assessor):
        """Test detection of high agent divergence."""
        divergent_responses = {
            'agent_1': {
                'response': 'Option A is the only viable solution for our needs',
                'confidence': 0.85,
                'reasoning': 'Technical requirements favor this approach',
                'priority_score': 0.9
            },
            'agent_2': {
                'response': 'Option B is clearly superior in all aspects',
                'confidence': 0.88,
                'reasoning': 'Business benefits are much better with B',
                'priority_score': 0.92
            },
            'agent_3': {
                'response': 'Option C offers the best long-term value',
                'confidence': 0.80,
                'reasoning': 'Strategic advantages make this the right choice',
                'priority_score': 0.85
            }
        }
        
        confidence_metrics = await confidence_assessor.assess_confidence(
            divergent_responses
        )
        
        assert confidence_metrics.response_divergence > 0.6
        assert confidence_metrics.overall_uncertainty > 0.4
        assert len(set(confidence_metrics.agent_confidence_scores.values())) > 1

    async def test_uncertainty_quantification(self, confidence_assessor):
        """Test uncertainty quantification methods."""
        uncertain_responses = {
            'agent_1': {
                'response': 'I think Option A might be better, but not sure',
                'confidence': 0.45,
                'reasoning': 'Limited information makes this difficult',
                'priority_score': 0.5
            },
            'agent_2': {
                'response': 'Could be Option B, need more analysis',
                'confidence': 0.40,
                'reasoning': 'Unclear requirements complicate the decision',
                'priority_score': 0.45
            },
            'agent_3': {
                'response': 'Several options seem viable',
                'confidence': 0.35,
                'reasoning': 'Multiple factors need consideration',
                'priority_score': 0.4
            }
        }
        
        confidence_metrics = await confidence_assessor.assess_confidence(
            uncertain_responses
        )
        
        assert confidence_metrics.average_confidence < 0.5
        assert confidence_metrics.overall_uncertainty > 0.7
        assert UncertaintyType.HIGH_UNCERTAINTY in confidence_metrics.uncertainty_sources

    async def test_confidence_variance_calculation(self, confidence_assessor):
        """Test confidence variance calculation accuracy."""
        varied_confidence_responses = {
            'agent_1': {'confidence': 0.9, 'response': 'High confidence response'},
            'agent_2': {'confidence': 0.3, 'response': 'Low confidence response'},
            'agent_3': {'confidence': 0.6, 'response': 'Medium confidence response'}
        }
        
        confidence_metrics = await confidence_assessor.assess_confidence(
            varied_confidence_responses
        )
        
        # Manual variance calculation for verification
        confidences = [0.9, 0.3, 0.6]
        mean_confidence = sum(confidences) / len(confidences)
        expected_variance = sum((c - mean_confidence) ** 2 for c in confidences) / len(confidences)
        
        assert abs(confidence_metrics.confidence_variance - expected_variance) < 0.01
        assert confidence_metrics.average_confidence == mean_confidence

    async def test_response_similarity_analysis(self, confidence_assessor):
        """Test response similarity analysis algorithms."""
        # Similar responses
        similar_responses = {
            'agent_1': {
                'response': 'Database optimization with indexing is the best approach',
                'confidence': 0.8
            },
            'agent_2': {
                'response': 'Database indexing and optimization will solve the problem',
                'confidence': 0.85
            },
            'agent_3': {
                'response': 'Optimizing database performance through indexing',
                'confidence': 0.75
            }
        }
        
        # Dissimilar responses  
        dissimilar_responses = {
            'agent_1': {
                'response': 'Use machine learning for predictive analytics',
                'confidence': 0.8
            },
            'agent_2': {
                'response': 'Implement microservices architecture pattern',
                'confidence': 0.85
            },
            'agent_3': {
                'response': 'Focus on user experience design improvements',
                'confidence': 0.75
            }
        }
        
        similar_metrics = await confidence_assessor.assess_confidence(similar_responses)
        dissimilar_metrics = await confidence_assessor.assess_confidence(dissimilar_responses)
        
        # Similar responses should have lower divergence
        assert similar_metrics.response_divergence < dissimilar_metrics.response_divergence

    async def test_contextual_confidence_adjustment(
        self,
        confidence_assessor,
        sample_agent_responses
    ):
        """Test contextual confidence adjustments."""
        context = {
            'deadline_pressure': 'high',
            'team_expertise': 'expert',
            'decision_complexity': 'high',
            'stakeholder_alignment': 'low'
        }
        
        team_info = {
            'experience_level': 'senior',
            'domain_expertise': ['architecture', 'performance'],
            'collaboration_history': {'successful_decisions': 15}
        }
        
        base_metrics = await confidence_assessor.assess_confidence(sample_agent_responses)
        
        contextual_metrics = await confidence_assessor.assess_confidence(
            sample_agent_responses,
            context=context,
            team_info=team_info
        )
        
        # Context should influence confidence assessment
        assert 'contextual_adjustments' in contextual_metrics.metadata
        
        # High expertise should generally increase confidence
        if team_info['experience_level'] == 'senior':
            adjustment_factor = contextual_metrics.metadata.get('expertise_boost', 0)
            assert adjustment_factor >= 0

    async def test_confidence_trend_analysis(self, confidence_assessor):
        """Test confidence trend analysis over time."""
        time_series_responses = []
        
        # Simulate confidence trend over multiple assessments
        base_time = datetime.utcnow()
        confidence_trend = [0.3, 0.4, 0.6, 0.7, 0.8]  # Increasing confidence
        
        for i, confidence in enumerate(confidence_trend):
            responses = {
                f'agent_{j}': {
                    'response': f'Response {i} from agent {j}',
                    'confidence': confidence + (j * 0.05),  # Slight agent variation
                    'timestamp': base_time + timedelta(minutes=i * 5)
                }
                for j in range(3)
            }
            time_series_responses.append(responses)
        
        trend_metrics = []
        for responses in time_series_responses:
            metrics = await confidence_assessor.assess_confidence(responses)
            trend_metrics.append(metrics)
        
        # Should detect increasing confidence trend
        confidence_values = [m.average_confidence for m in trend_metrics]
        trend_direction = confidence_assessor.analyze_confidence_trend(confidence_values)
        
        assert trend_direction == 'increasing'
        assert confidence_values[-1] > confidence_values[0]

    async def test_agent_reliability_scoring(self, confidence_assessor):
        """Test agent reliability scoring based on historical performance."""
        historical_data = {
            'agent_1': {
                'past_accuracy': 0.85,
                'consistency_score': 0.90,
                'decision_quality': 0.80
            },
            'agent_2': {
                'past_accuracy': 0.70,
                'consistency_score': 0.75,
                'decision_quality': 0.65
            },
            'agent_3': {
                'past_accuracy': 0.95,
                'consistency_score': 0.88,
                'decision_quality': 0.92
            }
        }
        
        responses = {
            agent_id: {
                'response': f'Response from {agent_id}',
                'confidence': 0.8
            }
            for agent_id in historical_data.keys()
        }
        
        # Configure assessor with historical data
        confidence_assessor.agent_history = historical_data
        
        confidence_metrics = await confidence_assessor.assess_confidence(responses)
        
        # Should weight agent_3 higher due to better historical performance
        reliability_weights = confidence_metrics.metadata.get('agent_reliability_weights', {})
        assert reliability_weights.get('agent_3', 0) > reliability_weights.get('agent_2', 0)

    async def test_cross_validation_confidence(self, confidence_assessor):
        """Test cross-validation of confidence assessments."""
        responses = {
            'agent_1': {
                'response': 'Technical solution A with database optimization',
                'confidence': 0.8,
                'reasoning': 'Performance benefits are significant'
            },
            'agent_2': {
                'response': 'Business solution B with cost optimization',
                'confidence': 0.75,
                'reasoning': 'Budget constraints favor this approach'
            },
            'agent_3': {
                'response': 'Hybrid approach combining A and B elements',
                'confidence': 0.85,
                'reasoning': 'Balances technical and business needs'
            }
        }
        
        # Test with different assessment methods
        cosine_assessor = AgentConfidenceAssessor(divergence_method=DivergenceType.COSINE_SIMILARITY)
        jaccard_assessor = AgentConfidenceAssessor(divergence_method=DivergenceType.JACCARD_DISTANCE)
        
        cosine_metrics = await cosine_assessor.assess_confidence(responses)
        jaccard_metrics = await jaccard_assessor.assess_confidence(responses)
        
        # Different methods should produce broadly similar results
        confidence_diff = abs(cosine_metrics.average_confidence - jaccard_metrics.average_confidence)
        assert confidence_diff < 0.2  # Should be within 20%

    async def test_confidence_calibration(self, confidence_assessor):
        """Test confidence calibration accuracy."""
        # Test well-calibrated confidence
        well_calibrated_responses = {
            'agent_1': {
                'response': 'Clear technical solution with proven track record',
                'confidence': 0.9,  # High confidence
                'supporting_evidence': ['benchmark_data', 'case_studies']
            },
            'agent_2': {
                'response': 'Uncertain about the best approach, need more data',
                'confidence': 0.4,  # Appropriately low confidence  
                'supporting_evidence': ['incomplete_analysis']
            }
        }
        
        confidence_metrics = await confidence_assessor.assess_confidence(
            well_calibrated_responses
        )
        
        calibration_score = confidence_metrics.metadata.get('calibration_score', 0.5)
        assert calibration_score > 0.6  # Well-calibrated should score higher

    async def test_edge_cases_and_error_handling(self, confidence_assessor):
        """Test edge cases and error handling."""
        # Test with single agent (minimum boundary)
        single_agent_responses = {
            'agent_1': {
                'response': 'Only response available',
                'confidence': 0.7
            }
        }
        
        single_metrics = await confidence_assessor.assess_confidence(single_agent_responses)
        assert single_metrics.agents_analyzed == 1
        assert single_metrics.response_divergence == 0.0  # No divergence with single agent
        
        # Test with empty responses
        empty_responses = {}
        empty_metrics = await confidence_assessor.assess_confidence(empty_responses)
        assert empty_metrics.agents_analyzed == 0
        assert empty_metrics.average_confidence == 0.0
        
        # Test with malformed responses
        malformed_responses = {
            'agent_1': {
                'response': None,
                'confidence': 'invalid'  # Invalid type
            },
            'agent_2': {
                # Missing response field
                'confidence': 0.5
            }
        }
        
        # Should handle gracefully without crashing
        malformed_metrics = await confidence_assessor.assess_confidence(malformed_responses)
        assert isinstance(malformed_metrics, ConfidenceMetrics)

    async def test_performance_with_many_agents(self, confidence_assessor):
        """Test performance with large number of agents."""
        many_agent_responses = {}
        
        # Generate responses from 50 agents
        for i in range(50):
            many_agent_responses[f'agent_{i}'] = {
                'response': f'Response from agent {i} with analysis and recommendations',
                'confidence': 0.5 + (i % 10) * 0.05,  # Varied confidence levels
                'reasoning': f'Agent {i} reasoning based on available data'
            }
        
        start_time = datetime.utcnow()
        confidence_metrics = await confidence_assessor.assess_confidence(many_agent_responses)
        processing_time = (datetime.utcnow() - start_time).total_seconds() * 1000
        
        assert confidence_metrics.agents_analyzed == 50
        assert processing_time < 5000  # Should complete in under 5 seconds
        assert confidence_metrics.processing_time_ms > 0

    async def test_confidence_metrics_serialization(self, confidence_assessor, sample_agent_responses):
        """Test ConfidenceMetrics can be serialized for caching."""
        confidence_metrics = await confidence_assessor.assess_confidence(sample_agent_responses)
        
        # Should be able to convert to dict and back
        metrics_dict = confidence_metrics.__dict__
        assert 'average_confidence' in metrics_dict
        assert 'response_divergence' in metrics_dict
        assert 'processing_time_ms' in metrics_dict
        
        # Check all required fields are present
        required_fields = [
            'average_confidence', 'confidence_variance', 'overall_uncertainty',
            'response_divergence', 'agent_confidence_scores', 'processing_time_ms',
            'agents_analyzed'
        ]
        for field in required_fields:
            assert hasattr(confidence_metrics, field)


@pytest.mark.asyncio
class TestConfidenceMetrics:
    """Test suite for ConfidenceMetrics data structure."""
    
    def test_confidence_metrics_creation(self):
        """Test ConfidenceMetrics object creation."""
        metrics = ConfidenceMetrics(
            average_confidence=0.75,
            confidence_variance=0.15,
            overall_uncertainty=0.3,
            response_divergence=0.4,
            agent_confidence_scores={'agent_1': 0.8, 'agent_2': 0.7},
            processing_time_ms=85.5,
            agents_analyzed=2
        )
        
        assert metrics.average_confidence == 0.75
        assert metrics.confidence_variance == 0.15
        assert metrics.overall_uncertainty == 0.3
        assert metrics.response_divergence == 0.4
        assert len(metrics.agent_confidence_scores) == 2
        assert metrics.processing_time_ms == 85.5
        assert metrics.agents_analyzed == 2
    
    def test_confidence_metrics_defaults(self):
        """Test ConfidenceMetrics default values."""
        metrics = ConfidenceMetrics()
        
        assert metrics.average_confidence == 0.0
        assert metrics.confidence_variance == 0.0
        assert metrics.overall_uncertainty == 0.0
        assert metrics.response_divergence == 0.0
        assert len(metrics.agent_confidence_scores) == 0
        assert metrics.processing_time_ms == 0.0
        assert metrics.agents_analyzed == 0
    
    def test_confidence_metrics_validation(self):
        """Test ConfidenceMetrics value validation."""
        metrics = ConfidenceMetrics(
            average_confidence=0.8,
            confidence_variance=0.2,
            overall_uncertainty=0.4,
            response_divergence=0.6,
            agents_analyzed=5
        )
        
        # All scores should be in valid ranges
        assert 0.0 <= metrics.average_confidence <= 1.0
        assert metrics.confidence_variance >= 0.0
        assert 0.0 <= metrics.overall_uncertainty <= 1.0
        assert 0.0 <= metrics.response_divergence <= 1.0
        assert metrics.agents_analyzed >= 0


@pytest.mark.asyncio
class TestDivergenceAlgorithms:
    """Test suite for response divergence algorithms."""
    
    async def test_cosine_similarity_divergence(self):
        """Test cosine similarity divergence calculation."""
        from devnous.debate.confidence_assessor import calculate_cosine_divergence
        
        # Similar responses should have low divergence
        similar_responses = [
            "database optimization and performance tuning",
            "database performance optimization and tuning",
            "optimize database performance with tuning"
        ]
        
        # Dissimilar responses should have high divergence
        dissimilar_responses = [
            "database optimization strategies",
            "machine learning algorithms", 
            "user interface design principles"
        ]
        
        similar_divergence = calculate_cosine_divergence(similar_responses)
        dissimilar_divergence = calculate_cosine_divergence(dissimilar_responses)
        
        assert similar_divergence < dissimilar_divergence
        assert 0.0 <= similar_divergence <= 1.0
        assert 0.0 <= dissimilar_divergence <= 1.0
    
    async def test_jaccard_distance_divergence(self):
        """Test Jaccard distance divergence calculation."""
        from devnous.debate.confidence_assessor import calculate_jaccard_divergence
        
        responses_with_overlap = [
            "system architecture and design patterns",
            "design patterns and system architecture", 
            "architecture patterns for system design"
        ]
        
        responses_no_overlap = [
            "database normalization techniques",
            "frontend user experience",
            "network security protocols"
        ]
        
        overlap_divergence = calculate_jaccard_divergence(responses_with_overlap)
        no_overlap_divergence = calculate_jaccard_divergence(responses_no_overlap)
        
        assert overlap_divergence < no_overlap_divergence
        assert 0.0 <= overlap_divergence <= 1.0
        assert 0.0 <= no_overlap_divergence <= 1.0