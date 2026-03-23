# SamChat + DevNous Hybrid Migration Guide

## Overview

This guide outlines the complete migration strategy for integrating SamChat agents with the DevNous tool system, creating a robust hybrid architecture that preserves existing functionality while adding powerful new capabilities.

Important:

- This guide documents a hybrid or transitional architecture, not the current production bootstrap for the live `sam.chat` deployment in this repository.
- References to injected `DevNousAgent` flows and hybrid toggles should be read as integration patterns or migration material.
- For the active runtime/install split, see:
  - `docs/install_matrix.md`

## Architecture Overview

### Before Migration: Legacy SamChat
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ ProductOwner    │    │ ScrumMaster     │    │ Developer       │
│ Agent           │    │ Agent           │    │ Agent           │
├─────────────────┤    ├─────────────────┤    ├─────────────────┤
│ - Story Creation│    │ - Blocker ID    │    │ - Tech Analysis │
│ - Prioritization│    │ - Sprint Health │    │ - Task Breakdown│
│ - Requirements  │    │ - Team Metrics  │    │ - Estimation    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌─────────────────┐
                    │ Conversation    │
                    │ Parser          │
                    └─────────────────┘
```

### After Migration: Hybrid Architecture
```
┌─────────────────────────────────────────────────────────────────┐
│                    Enhanced BaseAgent                           │
├─────────────────────────────────────────────────────────────────┤
│ • DevNous Tool Integration  • Feature Flags  • Caching         │
│ • Backward Compatibility   • Error Handling  • Monitoring      │
└─────────────────────────────────────────────────────────────────┘
         │                       │                       │
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ ProductOwner+   │    │ ScrumMaster+    │    │ Developer+      │
│ - Legacy Funcs  │    │ - Legacy Funcs  │    │ - Legacy Funcs  │
│ + PM Tools      │    │ + Workflow      │    │ + Architecture  │
│ + Memory        │    │ + Communication │    │ + PM Integration│
│ + Auto Tasks    │    │ + Analytics     │    │ + Pattern Match │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌─────────────────┐
                    │ DevNous Agent   │
                    │ Orchestration   │
                    └─────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Memory Tools    │    │ Workflow Tools  │    │ PM Tools        │
│ - Redis Cache   │    │ - State Machine │    │ - Jira/GitHub   │
│ - PostgreSQL    │    │ - Versioning    │    │ - Task Mgmt     │
│ - History       │    │ - Notifications │    │ - Conflict Res  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Migration Strategy: Strangler Fig Pattern

We use the Strangler Fig pattern to gradually replace legacy functionality while maintaining full backward compatibility.

### Phase 1: Foundation (Week 1-2)
**Goal**: Establish hybrid infrastructure without changing behavior

```python
# Enable basic DevNous integration
from samchat.feature_flags import enable_feature

# Start with 10% of agents
enable_feature("devnous_integration", rollout_percentage=0.1)
enable_feature("intelligent_caching", rollout_percentage=0.2)
```

**Success Criteria**:
- [ ] No performance regression
- [ ] All existing functionality preserved  
- [ ] DevNous tools accessible for enabled agents
- [ ] Error rates < 1%

### Phase 2: Memory Integration (Week 3)
**Goal**: Add persistent memory capabilities

```python
# Enable memory tools for selected agents
enable_feature("memory_integration", rollout_percentage=0.3)
enable_feature("devnous_integration", rollout_percentage=0.3)
```

**New Capabilities**:
- Conversation history persistence
- Team information caching
- Context-aware responses

### Phase 3: Workflow Tools (Week 4-5)
**Goal**: Add workflow management and orchestration

```python
# Enable workflow capabilities
enable_feature("workflow_integration", rollout_percentage=0.5)
enable_feature("cross_agent_collaboration", rollout_percentage=0.3)
```

**New Capabilities**:
- Sprint workflow tracking
- Cross-agent collaboration
- State management

### Phase 4: PM Integration (Week 6-7)
**Goal**: Integrate with project management systems

```python
# Enable PM tools
enable_feature("pm_tools_integration", rollout_percentage=0.7)
enable_feature("auto_create_pm_tasks", rollout_percentage=0.3)
```

**New Capabilities**:
- Jira/GitHub integration
- Automated task creation
- Dependency tracking

### Phase 5: Full Rollout (Week 8)
**Goal**: Complete migration with enhanced analytics

```python
# Full rollout
enable_feature("devnous_integration", rollout_percentage=1.0)
enable_feature("hybrid_processing", rollout_percentage=1.0)
enable_feature("communication_analytics", rollout_percentage=0.8)
enable_feature("architecture_analysis", rollout_percentage=0.8)
```

## Implementation Guide

### 1. Setting Up the Hybrid System

#### Install Dependencies
```bash
pip install -r requirements.txt
# Ensure both SamChat and DevNous dependencies are available
```

#### Initialize Agents with DevNous Integration
```python
from samchat.agents import ProductOwnerAgent, ScrumMasterAgent, DeveloperAgent
from devnous.devnous_agent import DevNousAgent
from samchat.feature_flags import FeatureFlagManager

# Initialize feature flags
flag_manager = FeatureFlagManager()

# Create DevNous agent
devnous_agent = DevNousAgent()
await devnous_agent.initialize()

# Create enhanced SamChat agents
po_agent = ProductOwnerAgent(
    enable_devnous_integration=True,
    feature_flags={
        "memory_integration": True,
        "pm_tools_integration": True,
        "auto_create_pm_tasks": False  # Start conservatively
    }
)

# Inject DevNous capabilities
po_agent.inject_devnous_agent(devnous_agent)
```

#### Backward Compatibility
```python
from samchat.compatibility_adapters import LegacyAgentAdapter

# Wrap existing agents for gradual migration
legacy_agent = ProductOwnerAgent()  # Existing agent
adapter = LegacyAgentAdapter(legacy_agent)

# Enable DevNous gradually
adapter.enable_devnous_integration(
    devnous_agent,
    feature_flags={"memory_integration": True}
)

# Process with gradual migration (50% DevNous, 50% legacy)
result = await adapter.gradual_migration_process(
    conversation, 
    context,
    migration_percentage=0.5
)
```

### 2. Feature Flag Configuration

Create a `feature_flags.json` file:
```json
{
  "flags": [
    {
      "name": "devnous_integration",
      "status": "rollout",
      "description": "Enable DevNous tool integration",
      "rollout_percentage": 0.1,
      "target_agents": ["ProductOwner_Test"],
      "created_at": "2023-01-01T00:00:00Z",
      "metadata": {
        "phase": 1,
        "owner": "migration_team"
      }
    }
  ]
}
```

### 3. Enhanced Agent Usage

#### ProductOwner Agent with DevNous
```python
# Enhanced user story creation
story = await po_agent.create_user_story_enhanced(
    user_type="customer",
    feature="password reset",
    benefit="recover access easily",
    context=project_context,
    auto_create_tasks=True  # Creates PM tasks automatically
)

# Enhanced backlog prioritization
prioritized_backlog = await po_agent.prioritize_backlog(
    backlog_items,
    criteria={
        "business_value": 0.5,
        "urgency": 0.3,
        "effort": 0.2
    }
)
```

#### ScrumMaster Agent with DevNous
```python
# Enhanced conversation processing with workflow tracking
result = await sm_agent.process_conversation(conversation, context)

# Includes workflow insights
workflow_health = result["devnous_insights"]["workflow_state"]
communication_patterns = result["devnous_insights"]["communication_patterns"]

# Sprint health assessment
print(f"Sprint Health: {result['sprint_health']}")
print(f"Blockers: {len(result['blockers'])}")
```

#### Developer Agent with DevNous
```python
# Enhanced technical analysis
result = await dev_agent.process_conversation(conversation, context)

# Technical pattern detection
patterns = result["technical_assessment"]["patterns_detected"]
architecture_insights = result["devnous_insights"]["architecture"]

# Integration with PM tools
task_analysis = result["devnous_insights"]["existing_technical_tasks"]
```

### 4. Migration Monitoring

#### Health Check Dashboard
```python
def get_migration_dashboard():
    agents = [po_agent, sm_agent, dev_agent]
    
    dashboard = {
        "migration_status": {},
        "feature_flags": flag_manager.get_all_flags(),
        "performance_metrics": {},
        "error_rates": {}
    }
    
    for agent in agents:
        status = agent.get_migration_status()
        dashboard["migration_status"][agent.name] = status
    
    return dashboard
```

#### Rollback Procedures
```python
def emergency_rollback():
    """Emergency rollback to legacy mode."""
    try:
        flag_manager.rollback_migration()
        
        # Verify all agents are in legacy mode
        for agent in agents:
            assert agent._legacy_mode
            
        logging.info("Emergency rollback completed successfully")
        return True
        
    except Exception as e:
        logging.error(f"Rollback failed: {e}")
        return False
```

## Testing Strategy

### 1. Integration Tests
```python
# Test both legacy and hybrid functionality
async def test_backward_compatibility():
    """Ensure legacy functionality still works."""
    legacy_agent = ProductOwnerAgent()
    
    # Should work exactly as before
    story = legacy_agent.create_user_story(
        "user", "login", "access system"
    )
    assert story["story"] == "As a user, I want login so that access system"

async def test_hybrid_functionality():
    """Test enhanced capabilities."""
    hybrid_agent = ProductOwnerAgent(enable_devnous_integration=True)
    hybrid_agent.inject_devnous_agent(devnous_agent)
    
    result = await hybrid_agent.process_conversation(messages, context)
    assert result["processing_mode"] == "enhanced"
    assert "devnous_insights" in result
```

### 2. Performance Testing
```python
async def test_performance_comparison():
    """Compare legacy vs hybrid performance."""
    import time
    
    # Legacy performance
    start = time.time()
    legacy_result = await legacy_agent.process_conversation(messages, context)
    legacy_time = time.time() - start
    
    # Hybrid performance
    start = time.time()
    hybrid_result = await hybrid_agent.process_conversation(messages, context)
    hybrid_time = time.time() - start
    
    # Hybrid should not be more than 50% slower
    assert hybrid_time < legacy_time * 1.5
```

### 3. Error Resilience Testing
```python
async def test_devnous_failure_recovery():
    """Test graceful degradation when DevNous fails."""
    # Simulate DevNous failure
    hybrid_agent.devnous_agent = None
    
    # Should fall back to legacy processing
    result = await hybrid_agent.process_conversation(messages, context)
    assert result["processing_mode"] == "legacy"
    assert "error" not in result
```

## Monitoring and Observability

### 1. Key Metrics
- **Migration Progress**: % of agents using DevNous tools
- **Performance**: Response time legacy vs hybrid
- **Reliability**: Error rates and fallback frequency
- **Feature Adoption**: Usage of enhanced capabilities

### 2. Logging
```python
import logging

# Configure structured logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Agent-specific loggers
po_logger = logging.getLogger("ProductOwnerAgent")
sm_logger = logging.getLogger("ScrumMasterAgent")
dev_logger = logging.getLogger("DeveloperAgent")
```

### 3. Alerting
```python
# Alert conditions
ALERT_CONDITIONS = {
    "error_rate_threshold": 0.05,  # 5%
    "performance_degradation": 0.5,  # 50% slower
    "devnous_unavailable_duration": 300  # 5 minutes
}

async def check_system_health():
    """Monitor system health and trigger alerts."""
    dashboard = get_migration_dashboard()
    
    # Check error rates
    for agent_name, metrics in dashboard["performance_metrics"].items():
        if metrics["error_rate"] > ALERT_CONDITIONS["error_rate_threshold"]:
            await send_alert(f"High error rate for {agent_name}")
```

## Rollback Strategy

### Automated Rollback Triggers
```python
ROLLBACK_TRIGGERS = [
    "error_rate > 5%",
    "response_time > 2x baseline",
    "devnous_unavailable > 10 minutes"
]

async def check_rollback_conditions():
    """Check if automatic rollback should be triggered."""
    dashboard = get_migration_dashboard()
    
    for condition in ROLLBACK_TRIGGERS:
        if evaluate_condition(condition, dashboard):
            logging.warning(f"Rollback trigger: {condition}")
            return await emergency_rollback()
    
    return False
```

### Manual Rollback
```python
def manual_rollback(reason: str):
    """Manually trigger rollback with reason."""
    logging.warning(f"Manual rollback initiated: {reason}")
    
    # Disable all DevNous features
    flag_manager.rollback_migration()
    
    # Verify rollback
    success = verify_legacy_mode()
    
    if success:
        logging.info("Rollback completed successfully")
    else:
        logging.error("Rollback verification failed")
    
    return success
```

## Success Criteria

### Phase Completion Criteria

#### Phase 1: Foundation
- [ ] DevNous integration working for 10% of agents
- [ ] No functionality regression
- [ ] Tool caching operational
- [ ] Performance within 20% of baseline

#### Phase 2: Memory Integration  
- [ ] Conversation history persistence working
- [ ] Team information caching functional
- [ ] Memory tools integrated successfully
- [ ] No data loss incidents

#### Phase 3: Workflow Tools
- [ ] Workflow state tracking operational
- [ ] Cross-agent collaboration working
- [ ] Sprint health monitoring functional
- [ ] Workflow notifications working

#### Phase 4: PM Integration
- [ ] Jira/GitHub integration working
- [ ] Task creation/updates functional
- [ ] Dependency tracking operational
- [ ] No duplicate tasks created

#### Phase 5: Full Rollout
- [ ] 100% agent migration completed
- [ ] All enhanced features operational
- [ ] Performance meets or exceeds baseline
- [ ] User satisfaction maintained/improved

### Overall Success Metrics
- **Zero** data loss during migration
- **< 1%** error rate increase
- **< 20%** performance degradation
- **100%** backward compatibility maintained
- **> 80%** enhanced feature adoption

## Troubleshooting Guide

### Common Issues

#### 1. DevNous Agent Not Initializing
```python
# Check DevNous agent status
health_status = await devnous_agent.get_health_status()
print(f"DevNous Status: {health_status}")

# Common fixes:
# - Check database connections
# - Verify Redis connectivity
# - Check API credentials
```

#### 2. Feature Flags Not Working
```python
# Debug feature flags
flag_status = flag_manager.get_all_flags()
print(json.dumps(flag_status, indent=2))

# Reset flags if needed
flag_manager.disable_flag("problematic_flag")
```

#### 3. Tool Integration Failures
```python
# Check tool registry
print(f"Registered tools: {agent.tool_registry.keys()}")

# Test individual tools
result = await agent.use_tool("get_tasks")
print(f"Tool result: {result}")
```

#### 4. Performance Issues
```python
# Check cache usage
print(f"Cache entries: {len(agent.tool_cache)}")
print(f"Cache hit rate: {calculate_cache_hit_rate()}")

# Clear cache if needed
agent.tool_cache.clear()
```

### Recovery Procedures

#### 1. Partial Migration Failure
```python
async def recover_from_partial_failure():
    # Identify failed agents
    failed_agents = []
    for agent in all_agents:
        status = agent.get_migration_status()
        if not status["devnous_agent_injected"]:
            failed_agents.append(agent)
    
    # Retry migration for failed agents
    for agent in failed_agents:
        try:
            agent.inject_devnous_agent(devnous_agent)
        except Exception as e:
            logging.error(f"Recovery failed for {agent.name}: {e}")
```

#### 2. Data Consistency Issues
```python
async def verify_data_consistency():
    """Verify data consistency between legacy and DevNous systems."""
    inconsistencies = []
    
    # Check conversation history
    legacy_history = get_legacy_conversation_history()
    devnous_history = await devnous_agent.get_conversation_history()
    
    if len(legacy_history) != devnous_history.total:
        inconsistencies.append("Conversation history mismatch")
    
    return inconsistencies
```

## Best Practices

### 1. Migration Execution
- **Start Small**: Begin with 10% rollout
- **Monitor Closely**: Watch metrics during each phase
- **Communicate**: Keep stakeholders informed
- **Document**: Record all decisions and changes

### 2. Feature Flag Management
- **Conservative**: Start with conservative settings
- **Targeted**: Use target agents for testing
- **Gradual**: Increase rollout percentage slowly
- **Reversible**: Always maintain rollback capability

### 3. Error Handling
- **Graceful Degradation**: Fall back to legacy on errors
- **Comprehensive Logging**: Log all migration events
- **Quick Recovery**: Have fast rollback procedures
- **User Communication**: Inform users of any issues

### 4. Performance Optimization
- **Intelligent Caching**: Use tool caching effectively
- **Async Processing**: Leverage async capabilities
- **Resource Management**: Monitor memory and CPU usage
- **Load Testing**: Test under realistic loads

## Conclusion

This hybrid migration approach ensures that the powerful capabilities of DevNous are integrated with SamChat while maintaining full backward compatibility and minimizing risk. The strangler fig pattern allows for gradual migration with the ability to rollback at any point.

The key to success is careful monitoring, conservative rollout percentages, and maintaining the ability to quickly revert to legacy functionality if needed.

For additional support or questions about the migration process, consult the troubleshooting guide or reach out to the development team.
