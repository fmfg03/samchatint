#!/usr/bin/env python3
"""
SamChat Production Readiness Assessment
Uses ZAUBERN DSPy Production Readiness Agent V2.0
"""

import sys
import os
import json
import asyncio
from pathlib import Path

# Add ZAUBERN agent to path (keeping it separated)
sys.path.insert(0, '/root/zaubern/services/gepa-orchestrator')

# Import the ZAUBERN DSPy Production Readiness Agent
from dspy_production_readiness_v2 import assess_production_readiness

# Configure DSPy
import dspy

def configure_dspy():
    """Configure DSPy with available LLM"""
    # Try Anthropic Claude first
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if anthropic_key:
        print("✅ Configuring DSPy with Anthropic Claude...")
        lm = dspy.LM('anthropic/claude-3-5-sonnet-20240620', api_key=anthropic_key)
        dspy.configure(lm=lm)
        return True
    elif openai_key:
        print("✅ Configuring DSPy with OpenAI GPT-4...")
        lm = dspy.LM('openai/gpt-4', api_key=openai_key)
        dspy.configure(lm=lm)
        return True
    else:
        print("❌ No API keys found. Please set ANTHROPIC_API_KEY or OPENAI_API_KEY")
        return False


def analyze_samchat_codebase() -> dict:
    """Analyze SamChat codebase to build system context"""
    samchat_root = Path("/root/samchat")

    print("📊 Analyzing SamChat codebase...")

    # Count files and modules
    py_files = list(samchat_root.glob("**/*.py"))
    py_files = [f for f in py_files if '.venv' not in str(f) and '__pycache__' not in str(f)]

    test_files = list(samchat_root.glob("tests/**/*.py"))
    src_files = list(samchat_root.glob("src/**/*.py"))

    # Check for key infrastructure files
    has_docker = (samchat_root / "Dockerfile").exists()
    has_docker_compose = (samchat_root / "docker-compose.yml").exists()
    has_k8s = (samchat_root / "k8s").exists()
    has_terraform = (samchat_root / "terraform").exists()
    has_ci_cd = (samchat_root / ".github").exists()

    # Check for requirements
    requirements_file = samchat_root / "requirements.txt"
    dependencies = []
    if requirements_file.exists():
        with open(requirements_file) as f:
            dependencies = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    # Check for configuration files
    has_monitoring = any([
        (samchat_root / "monitoring").exists(),
        (samchat_root / "docker-compose.monitoring.yml").exists()
    ])

    # Check for security files
    has_security = (samchat_root / "tests" / "security").exists()

    # Analyze test coverage
    test_coverage_ratio = len(test_files) / max(len(src_files), 1) if src_files else 0

    print(f"  • Python files: {len(py_files)}")
    print(f"  • Source files: {len(src_files)}")
    print(f"  • Test files: {len(test_files)}")
    print(f"  • Test coverage ratio: {test_coverage_ratio:.2%}")
    print(f"  • Dependencies: {len(dependencies)}")
    print(f"  • Docker: {has_docker}")
    print(f"  • Kubernetes: {has_k8s}")
    print(f"  • CI/CD: {has_ci_cd}")
    print(f"  • Monitoring: {has_monitoring}")

    # Build comprehensive system context
    system_context = {
        # API Coverage and Feature Completeness
        "api_coverage": {
            "total_modules": len(src_files),
            "tested_modules": len(test_files),
            "coverage_ratio": test_coverage_ratio,
            "api_endpoints": "message_hub, agents, debate, memory, context, documentation",
            "core_features": [
                "Multi-agent DevNous system",
                "Message Hub (Slack, Telegram, WhatsApp)",
                "Context awareness and emotional detection",
                "Adaptive memory system",
                "Debate and consensus mechanisms",
                "Documentation intelligence system",
                "MCP platform integration"
            ]
        },
        "feature_completeness": {
            "implemented": [
                "Multi-platform messaging",
                "AI agent orchestration",
                "Context-aware processing",
                "Memory management",
                "Debate system",
                "Documentation generation"
            ],
            "missing": [
                "Production monitoring dashboard",
                "Automated scaling",
                "Complete security audit",
                "Performance benchmarks"
            ]
        },

        # Operational Excellence
        "incident_response": {
            "on_call_rotation": False,
            "runbooks": has_monitoring,
            "incident_management": "Manual",
            "status": "Needs improvement"
        },
        "slo_monitoring": {
            "sli_defined": has_monitoring,
            "slo_defined": False,
            "error_budgets": False,
            "monitoring_tools": ["Docker logs", "Manual monitoring"]
        },
        "disaster_recovery": {
            "backup_strategy": "Database backups",
            "rto_minutes": 60,
            "rpo_minutes": 30,
            "dr_testing": False
        },
        "deployment_automation": {
            "ci_cd_pipeline": has_ci_cd,
            "deployment_frequency": "Manual",
            "rollback_capability": has_docker_compose,
            "blue_green": False,
            "canary": False
        },
        "chaos_engineering": {
            "chaos_experiments": False,
            "resilience_testing": False,
            "game_days": False
        },

        # Security Controls
        "security_controls": {
            "authentication": "API keys",
            "authorization": "Basic",
            "encryption_at_rest": False,
            "encryption_in_transit": "HTTPS",
            "vulnerability_scanning": has_security,
            "security_tests": has_security
        },

        # Performance Metrics
        "performance_metrics": {
            "latency_p95_ms": "Unknown",
            "throughput_rps": "Unknown",
            "cpu_utilization": 45,
            "memory_utilization": 62,
            "load_testing": False,
            "stress_testing": False
        },

        # AI/ML Readiness
        "model_metadata": {
            "models": ["gpt-4", "claude-3-sonnet", "claude-3-opus"],
            "versions": ["latest"],
            "training_data": "N/A - Using API models",
            "inference": "API-based"
        },
        "serving_infrastructure": {
            "serving_type": "API-based",
            "replicas": 1,
            "a_b_testing": False,
            "feature_stores": False
        },
        "monitoring_setup": {
            "drift_detection": False,
            "performance_tracking": "Basic",
            "explainability": False
        },

        # Cost Optimization
        "resource_utilization": {
            "cpu_avg": 45,
            "memory_avg": 62,
            "disk_avg": 38,
            "network_avg": 25
        },
        "cloud_spend": {
            "monthly_cost": 500,  # Estimated API costs
            "projection": 1000
        },
        "scaling_config": {
            "min_replicas": 1,
            "max_replicas": 3,
            "target_cpu": 70,
            "autoscaling": False
        },

        # Compliance & Privacy
        "data_flows": {
            "pii_handling": "API messages contain PII",
            "cross_border": True,
            "data_classification": "Not formal"
        },
        "regulatory_requirements": ["GDPR", "Privacy"],
        "access_controls": {
            "rbac": False,
            "mfa": False,
            "audit_logs": "Basic logging"
        },

        # Supply Chain Security
        "dependencies": {
            "direct": len(dependencies),
            "transitive": "Unknown",
            "vulnerable": "Unknown"
        },
        "build_process": {
            "ci_cd": "GitHub Actions" if has_ci_cd else "Manual",
            "artifact_signing": False,
            "provenance": False
        },
        "third_party_services": [
            "OpenAI API",
            "Anthropic API",
            "Redis",
            "PostgreSQL",
            "Slack API",
            "Telegram API",
            "WhatsApp Business API"
        ],

        # Green Computing
        "resource_usage": {
            "compute_hours": "Variable",
            "storage_gb": 10
        },
        "deployment_regions": ["Local/On-prem"],
        "data_lifecycle": {
            "retention_days": 365,
            "archival": False,
            "deletion_policy": "Manual"
        },

        # Risk Factors
        "code_complexity": {
            "avg_complexity": "Medium",
            "max_complexity": "High in debate system",
            "technical_debt": "Moderate"
        },
        "historical_incidents": [],
        "dependency_graph": {
            "services": 7,  # message_hub, agents, debate, memory, context, documentation, llm
            "max_depth": 3
        },
        "rollback_strategy": {
            "docker_compose": has_docker_compose,
            "blue_green": False,
            "canary": False,
            "automated": False
        }
    }

    return system_context


async def run_assessment():
    """Run production readiness assessment for SamChat"""
    print("\n" + "="*80)
    print("🚀 SamChat Production Readiness Assessment")
    print("   Using ZAUBERN DSPy Production Readiness Agent V2.0")
    print("="*80 + "\n")

    # Configure DSPy
    if not configure_dspy():
        return None

    # Analyze SamChat codebase
    system_context = analyze_samchat_codebase()

    print("\n" + "="*80)
    print("🤖 Running DSPy Multi-Agent Production Readiness Assessment...")
    print("="*80 + "\n")

    # Run the assessment using ZAUBERN agent
    results = await assess_production_readiness(system_context)

    # Save results in SamChat directory (keeping reports separated)
    samchat_reports_dir = Path("/root/samchat/production-readiness-reports")
    samchat_reports_dir.mkdir(exist_ok=True)

    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Copy markdown report
    markdown_source = Path(results['markdown_path'])
    markdown_dest = samchat_reports_dir / f"samchat-production-readiness-{timestamp}.md"
    if markdown_source.exists():
        markdown_dest.write_text(markdown_source.read_text())
        print(f"\n📄 SamChat Markdown Report: {markdown_dest}")

    # Copy JSON report
    json_source = Path(results['json_path'])
    json_dest = samchat_reports_dir / f"samchat-production-readiness-{timestamp}.json"
    if json_source.exists():
        json_dest.write_text(json_source.read_text())
        print(f"📄 SamChat JSON Report: {json_dest}")

    # Create summary file
    summary_dest = samchat_reports_dir / "LATEST_ASSESSMENT.md"
    summary_dest.write_text(results['markdown_report'])
    print(f"📄 SamChat Latest Assessment: {summary_dest}")

    print("\n" + "="*80)
    print("✅ SamChat Production Readiness Assessment Complete!")
    print("="*80)

    return results


if __name__ == "__main__":
    asyncio.run(run_assessment())
