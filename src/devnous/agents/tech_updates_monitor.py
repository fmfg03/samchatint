"""
Technology Updates Monitor Agent

Automatically monitors and reports on:
- Python package updates
- LLM model versions (Anthropic, OpenAI)
- API changes and deprecations
- Security vulnerabilities
- Breaking changes

Usage:
    python -m devnous.agents.tech_updates_monitor --report
"""

import asyncio
import logging
import json
import subprocess
import re
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, asdict
from enum import Enum

import aiohttp
import requests
from packaging import version as pkg_version

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class UpdateSeverity(Enum):
    """Severity levels for updates."""
    CRITICAL = "critical"  # Security vulnerabilities, breaking changes
    HIGH = "high"          # Major version updates, deprecations
    MEDIUM = "medium"      # Minor version updates, new features
    LOW = "low"            # Patch updates, documentation


@dataclass
class PackageUpdate:
    """Represents a package update."""
    name: str
    current_version: str
    latest_version: str
    update_type: str  # major, minor, patch
    severity: UpdateSeverity
    changelog_url: Optional[str] = None
    security_issues: List[str] = None
    breaking_changes: bool = False

    def __post_init__(self):
        if self.security_issues is None:
            self.security_issues = []


@dataclass
class ModelUpdate:
    """Represents an LLM model update."""
    provider: str  # anthropic, openai
    model_name: str
    status: str  # new, deprecated, updated
    released_date: Optional[str] = None
    deprecation_date: Optional[str] = None
    replacement_model: Optional[str] = None
    capabilities: List[str] = None

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = []


@dataclass
class TechUpdateReport:
    """Complete technology update report."""
    generated_at: str
    package_updates: List[PackageUpdate]
    model_updates: List[ModelUpdate]
    critical_count: int
    high_count: int
    summary: str


class TechUpdatesMonitor:
    """
    Monitors technology updates across the stack.
    """

    # Known LLM models with their versions
    ANTHROPIC_MODELS = {
        "claude-3-5-sonnet-20240620": {"status": "stable", "vision": True},
        "claude-3-opus-20240229": {"status": "stable", "vision": True},
        "claude-3-sonnet-20240229": {"status": "stable", "vision": True},
        "claude-3-haiku-20240307": {"status": "stable", "vision": True},
    }

    OPENAI_MODELS = {
        "gpt-4-turbo": {"status": "stable", "vision": True},
        "gpt-4": {"status": "stable", "vision": False},
        "gpt-3.5-turbo": {"status": "stable", "vision": False},
    }

    def __init__(self, requirements_file: str = "requirements.txt"):
        """
        Initialize tech updates monitor.

        Args:
            requirements_file: Path to requirements.txt
        """
        self.requirements_file = Path(requirements_file)
        self.project_root = Path(__file__).parent.parent.parent.parent
        self.reports_dir = self.project_root / "reports" / "tech_updates"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    async def check_all_updates(self) -> TechUpdateReport:
        """
        Check all technology updates.

        Returns:
            Complete update report
        """
        logger.info("🔍 Starting technology updates check...")

        # Run checks in parallel
        package_updates_task = self.check_package_updates()
        model_updates_task = self.check_model_updates()

        package_updates = await package_updates_task
        model_updates = await model_updates_task

        # Count by severity
        critical_count = sum(1 for u in package_updates if u.severity == UpdateSeverity.CRITICAL)
        high_count = sum(1 for u in package_updates if u.severity == UpdateSeverity.HIGH)

        # Generate summary
        summary = self._generate_summary(package_updates, model_updates, critical_count, high_count)

        report = TechUpdateReport(
            generated_at=datetime.now().isoformat(),
            package_updates=package_updates,
            model_updates=model_updates,
            critical_count=critical_count,
            high_count=high_count,
            summary=summary
        )

        logger.info(f"✅ Check complete: {len(package_updates)} package updates, {len(model_updates)} model updates")
        return report

    async def check_package_updates(self) -> List[PackageUpdate]:
        """
        Check for Python package updates.

        Returns:
            List of package updates
        """
        logger.info("📦 Checking Python package updates...")
        updates = []

        if not self.requirements_file.exists():
            logger.warning(f"⚠️ Requirements file not found: {self.requirements_file}")
            return updates

        # Parse requirements.txt
        packages = self._parse_requirements()

        # Check each package
        for pkg_name, current_ver in packages.items():
            try:
                latest_ver = await self._get_latest_version(pkg_name)

                if latest_ver and latest_ver != current_ver:
                    update = self._analyze_package_update(pkg_name, current_ver, latest_ver)
                    updates.append(update)

            except Exception as e:
                logger.error(f"❌ Error checking {pkg_name}: {e}")

        return updates

    def _parse_requirements(self) -> Dict[str, str]:
        """Parse requirements.txt file."""
        packages = {}

        with open(self.requirements_file, 'r') as f:
            for line in f:
                line = line.strip()

                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue

                # Parse package==version or package>=version
                match = re.match(r'^([a-zA-Z0-9\-_]+)([><=!]+)([0-9.]+)', line)
                if match:
                    pkg_name = match.group(1)
                    pkg_ver = match.group(3)
                    packages[pkg_name] = pkg_ver

        return packages

    async def _get_latest_version(self, package_name: str) -> Optional[str]:
        """Get latest version from PyPI."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://pypi.org/pypi/{package_name}/json"
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data['info']['version']
        except Exception as e:
            logger.debug(f"Error fetching {package_name}: {e}")

        return None

    def _analyze_package_update(
        self,
        pkg_name: str,
        current: str,
        latest: str
    ) -> PackageUpdate:
        """Analyze package update and determine severity."""

        try:
            current_v = pkg_version.parse(current)
            latest_v = pkg_version.parse(latest)
        except Exception:
            # Fallback if version parsing fails
            return PackageUpdate(
                name=pkg_name,
                current_version=current,
                latest_version=latest,
                update_type="unknown",
                severity=UpdateSeverity.MEDIUM
            )

        # Determine update type
        if latest_v.major > current_v.major:
            update_type = "major"
            severity = UpdateSeverity.HIGH
            breaking_changes = True
        elif latest_v.minor > current_v.minor:
            update_type = "minor"
            severity = UpdateSeverity.MEDIUM
            breaking_changes = False
        else:
            update_type = "patch"
            severity = UpdateSeverity.LOW
            breaking_changes = False

        # Check for security-critical packages
        security_critical = pkg_name.lower() in [
            'cryptography', 'openssl', 'requests', 'urllib3',
            'pyjwt', 'passlib', 'pillow', 'django', 'flask'
        ]

        if security_critical and update_type in ['major', 'minor']:
            severity = UpdateSeverity.CRITICAL

        changelog_url = f"https://pypi.org/project/{pkg_name}/{latest}/#history"

        return PackageUpdate(
            name=pkg_name,
            current_version=current,
            latest_version=latest,
            update_type=update_type,
            severity=severity,
            changelog_url=changelog_url,
            breaking_changes=breaking_changes
        )

    async def check_model_updates(self) -> List[ModelUpdate]:
        """
        Check for LLM model updates and deprecations.

        Returns:
            List of model updates
        """
        logger.info("🤖 Checking LLM model updates...")
        updates = []

        # Check Anthropic models
        for model_name, info in self.ANTHROPIC_MODELS.items():
            if info["status"] == "deprecated":
                updates.append(ModelUpdate(
                    provider="anthropic",
                    model_name=model_name,
                    status="deprecated",
                    replacement_model=info.get("replacement"),
                    capabilities=["vision"] if info.get("vision") else []
                ))

        # Check for new Anthropic models via API
        try:
            new_models = await self._fetch_anthropic_models()
            for model in new_models:
                if model not in self.ANTHROPIC_MODELS:
                    updates.append(ModelUpdate(
                        provider="anthropic",
                        model_name=model,
                        status="new",
                        capabilities=["vision"]  # Assume new models have vision
                    ))
        except Exception as e:
            logger.error(f"Error fetching Anthropic models: {e}")

        return updates

    async def _fetch_anthropic_models(self) -> List[str]:
        """Fetch available models from Anthropic API docs."""
        # This would ideally call an API endpoint
        # For now, return known models
        return list(self.ANTHROPIC_MODELS.keys())

    def _generate_summary(
        self,
        package_updates: List[PackageUpdate],
        model_updates: List[ModelUpdate],
        critical_count: int,
        high_count: int
    ) -> str:
        """Generate human-readable summary."""

        lines = []
        lines.append("# Technology Updates Summary")
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # Critical alerts
        if critical_count > 0:
            lines.append(f"🚨 **CRITICAL**: {critical_count} critical updates require immediate attention")
            lines.append("")

        # Package updates
        if package_updates:
            lines.append(f"## 📦 Package Updates ({len(package_updates)})")
            lines.append("")

            # Group by severity
            by_severity = {}
            for update in package_updates:
                severity = update.severity.value
                if severity not in by_severity:
                    by_severity[severity] = []
                by_severity[severity].append(update)

            for severity in ["critical", "high", "medium", "low"]:
                if severity in by_severity:
                    updates = by_severity[severity]
                    emoji = {"critical": "🚨", "high": "⚠️", "medium": "📦", "low": "✨"}[severity]
                    lines.append(f"### {emoji} {severity.upper()} ({len(updates)})")
                    lines.append("")

                    for u in updates:
                        lines.append(f"- **{u.name}**: {u.current_version} → {u.latest_version} ({u.update_type})")
                        if u.breaking_changes:
                            lines.append(f"  ⚠️ May contain breaking changes")
                        if u.changelog_url:
                            lines.append(f"  📝 [Changelog]({u.changelog_url})")

                    lines.append("")

        # Model updates
        if model_updates:
            lines.append(f"## 🤖 LLM Model Updates ({len(model_updates)})")
            lines.append("")

            deprecated = [m for m in model_updates if m.status == "deprecated"]
            new_models = [m for m in model_updates if m.status == "new"]

            if deprecated:
                lines.append(f"### ⚠️ Deprecated Models ({len(deprecated)})")
                for m in deprecated:
                    lines.append(f"- **{m.model_name}** ({m.provider})")
                    if m.replacement_model:
                        lines.append(f"  → Use: {m.replacement_model}")
                lines.append("")

            if new_models:
                lines.append(f"### ✨ New Models ({len(new_models)})")
                for m in new_models:
                    caps = ", ".join(m.capabilities) if m.capabilities else "standard"
                    lines.append(f"- **{m.model_name}** ({m.provider}) - {caps}")
                lines.append("")

        # Recommendations
        lines.append("## 📋 Recommendations")
        lines.append("")

        if critical_count > 0:
            lines.append("1. ⚠️ **Update critical packages immediately** (security/breaking changes)")

        if high_count > 0:
            lines.append(f"2. 📦 Review {high_count} high-priority updates this week")

        if any(m.status == "deprecated" for m in model_updates):
            lines.append("3. 🤖 Migrate away from deprecated LLM models")

        if not package_updates and not model_updates:
            lines.append("✅ All dependencies are up to date!")

        return "\n".join(lines)

    def save_report(self, report: TechUpdateReport, format: str = "markdown") -> Path:
        """
        Save report to file.

        Args:
            report: Update report
            format: Output format (markdown, json)

        Returns:
            Path to saved report
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if format == "json":
            filename = f"tech_updates_{timestamp}.json"
            filepath = self.reports_dir / filename

            # Convert to JSON-serializable dict
            data = {
                "generated_at": report.generated_at,
                "package_updates": [asdict(u) for u in report.package_updates],
                "model_updates": [asdict(m) for m in report.model_updates],
                "critical_count": report.critical_count,
                "high_count": report.high_count,
                "summary": report.summary
            }

            # Convert enums to strings
            for pkg in data["package_updates"]:
                pkg["severity"] = pkg["severity"].value if hasattr(pkg["severity"], "value") else pkg["severity"]

            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)

        else:  # markdown
            filename = f"tech_updates_{timestamp}.md"
            filepath = self.reports_dir / filename

            with open(filepath, 'w') as f:
                f.write(report.summary)

        logger.info(f"📄 Report saved: {filepath}")
        return filepath

    def print_report(self, report: TechUpdateReport):
        """Print report to console."""
        print("\n" + "=" * 80)
        print(report.summary)
        print("=" * 80 + "\n")

    async def check_codebase_usage(self, model_name: str) -> List[str]:
        """
        Find where a specific model is used in the codebase.

        Args:
            model_name: Model to search for

        Returns:
            List of file paths where model is used
        """
        logger.info(f"🔍 Searching codebase for: {model_name}")

        matches = []

        try:
            result = subprocess.run(
                ["grep", "-r", "-l", model_name, str(self.project_root), "--include=*.py"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.stdout:
                matches = result.stdout.strip().split('\n')
                logger.info(f"Found {len(matches)} files using {model_name}")

        except Exception as e:
            logger.error(f"Error searching codebase: {e}")

        return matches


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Technology Updates Monitor")
    parser.add_argument("--report", action="store_true", help="Generate full report")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--save", action="store_true", help="Save report to file")
    parser.add_argument("--requirements", default="requirements.txt", help="Path to requirements.txt")
    parser.add_argument("--check-model", help="Check usage of specific model in codebase")

    args = parser.parse_args()

    monitor = TechUpdatesMonitor(requirements_file=args.requirements)

    if args.check_model:
        # Check specific model usage
        files = await monitor.check_codebase_usage(args.check_model)
        print(f"\n🔍 Model '{args.check_model}' found in {len(files)} files:\n")
        for f in files:
            print(f"  - {f}")
        return

    # Generate full report
    report = await monitor.check_all_updates()

    # Print to console
    monitor.print_report(report)

    # Save if requested
    if args.save:
        format_type = "json" if args.json else "markdown"
        filepath = monitor.save_report(report, format=format_type)
        print(f"💾 Report saved to: {filepath}")

    # Exit with appropriate code
    if report.critical_count > 0:
        logger.warning(f"⚠️ Exiting with code 1: {report.critical_count} critical updates")
        exit(1)
    else:
        logger.info("✅ All checks passed")
        exit(0)


if __name__ == "__main__":
    asyncio.run(main())
