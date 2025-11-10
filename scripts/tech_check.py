#!/usr/bin/env python3
"""
Tech Check - Quick CLI for Technology Updates Monitor

Usage:
    python scripts/tech_check.py              # Quick check
    python scripts/tech_check.py --full       # Full report
    python scripts/tech_check.py --notify     # Send notifications
    python scripts/tech_check.py --watch      # Watch mode (check every hour)
"""

import asyncio
import sys
import argparse
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from devnous.agents.tech_updates_monitor import TechUpdatesMonitor
from devnous.agents.tech_updates_notifier import TechUpdatesNotificationService


async def quick_check():
    """Quick status check."""
    print("🔍 Running quick tech check...\n")

    monitor = TechUpdatesMonitor()
    report = await monitor.check_all_updates()

    # Quick summary
    print(f"📦 Package Updates: {len(report.package_updates)}")
    print(f"   🚨 Critical: {report.critical_count}")
    print(f"   ⚠️  High: {report.high_count}")
    print(f"🤖 Model Updates: {len(report.model_updates)}")

    if report.critical_count > 0:
        print(f"\n⚠️  {report.critical_count} CRITICAL updates require immediate attention!")
        print("Run with --full for details")
        return 1

    print("\n✅ System is up to date")
    return 0


async def full_report():
    """Generate and display full report."""
    print("🔍 Generating full technology update report...\n")

    monitor = TechUpdatesMonitor()
    report = await monitor.check_all_updates()

    # Print full report
    monitor.print_report(report)

    # Save to file
    filepath = monitor.save_report(report, format="markdown")
    print(f"\n💾 Full report saved to: {filepath}")

    return 0


async def notify():
    """Check and send notifications."""
    print("🔍 Checking for updates and sending notifications...\n")

    monitor = TechUpdatesMonitor()
    report = await monitor.check_all_updates()

    # Send notifications
    notifier = TechUpdatesNotificationService()
    await notifier.notify_if_important(report, threshold="high")

    print("✅ Notification check complete")
    return 0


async def watch_mode(interval_hours: int = 1):
    """Watch mode - check periodically."""
    print(f"👀 Watch mode: checking every {interval_hours} hour(s)")
    print("Press Ctrl+C to stop\n")

    monitor = TechUpdatesMonitor()
    notifier = TechUpdatesNotificationService()

    try:
        while True:
            print(f"\n🔍 Checking updates at {asyncio.get_event_loop().time()}...")

            report = await monitor.check_all_updates()

            # Quick summary
            print(f"📦 {len(report.package_updates)} package updates")
            print(f"🤖 {len(report.model_updates)} model updates")

            # Notify if important
            if report.critical_count > 0 or report.high_count > 0:
                await notifier.notify_if_important(report, threshold="high")

            # Wait
            await asyncio.sleep(interval_hours * 3600)

    except KeyboardInterrupt:
        print("\n\n✋ Watch mode stopped")
        return 0


async def check_model(model_name: str):
    """Check if a specific model is used in codebase."""
    print(f"🔍 Searching for model: {model_name}\n")

    monitor = TechUpdatesMonitor()
    files = await monitor.check_codebase_usage(model_name)

    if files:
        print(f"Found in {len(files)} files:\n")
        for f in files:
            print(f"  📄 {f}")

        # Check if model is deprecated
        if "deprecated" in model_name or model_name in [
            "claude-3-5-sonnet-20241022",  # Old deprecated model
            "gpt-3.5-turbo-0301"
        ]:
            print(f"\n⚠️  WARNING: {model_name} is deprecated!")
            print("Consider migrating to a newer model")
            return 1
    else:
        print(f"✅ Model '{model_name}' not found in codebase")

    return 0


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Technology Updates Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/tech_check.py                    # Quick check
  python scripts/tech_check.py --full             # Full report
  python scripts/tech_check.py --notify           # Send notifications
  python scripts/tech_check.py --watch            # Watch mode
  python scripts/tech_check.py --check-model claude-3-opus-20240229
        """
    )

    parser.add_argument("--full", action="store_true",
                       help="Generate full report")
    parser.add_argument("--notify", action="store_true",
                       help="Send notifications via configured channels")
    parser.add_argument("--watch", action="store_true",
                       help="Watch mode: check periodically")
    parser.add_argument("--interval", type=int, default=1,
                       help="Watch mode interval in hours (default: 1)")
    parser.add_argument("--check-model", metavar="MODEL",
                       help="Check if specific model is used in codebase")
    parser.add_argument("--save", action="store_true",
                       help="Save report to file")

    args = parser.parse_args()

    # Route to appropriate function
    if args.check_model:
        exit_code = asyncio.run(check_model(args.check_model))
    elif args.full:
        exit_code = asyncio.run(full_report())
    elif args.notify:
        exit_code = asyncio.run(notify())
    elif args.watch:
        exit_code = asyncio.run(watch_mode(args.interval))
    else:
        exit_code = asyncio.run(quick_check())

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
