#!/usr/bin/env python3
"""Run SamChat specialist benchmark seeds and emit JSON or Markdown."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from samchat.assistant.specialist_harness import PASS  # noqa: E402
from samchat.assistant.specialist_report import (  # noqa: E402
    build_benchmark_report,
    compact_report_dict,
    render_benchmark_report_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    report = build_benchmark_report()
    if args.format == "markdown":
        print(render_benchmark_report_markdown(report), end="")
    else:
        payload = compact_report_dict(report) if args.compact else report.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.status == PASS and report.side_effects_detected == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
