#!/usr/bin/env python3
"""Accept only the exact documented high-confidence Bandit findings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_baseline(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def load_findings(path: Path) -> set[str]:
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        f"{result['filename']}:{result['line_number']}:{result['test_id']}"
        for result in report["results"]
    }


def compare(actual: set[str], expected: set[str]) -> list[str]:
    messages: list[str] = []
    new = sorted(actual - expected)
    resolved = sorted(expected - actual)
    if new:
        messages.append("New high-confidence Bandit findings:\n- " + "\n- ".join(new))
    if resolved:
        messages.append(
            "Baselined Bandit findings no longer reproduced; remove them from the baseline:\n- "
            + "\n- ".join(resolved)
        )
    return messages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()

    messages = compare(load_findings(args.report), load_baseline(args.baseline))
    if messages:
        print("\n\n".join(messages))
        return 1
    print("High-confidence Bandit findings match the explicit accepted baseline exactly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
