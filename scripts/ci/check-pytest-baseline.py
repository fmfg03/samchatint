#!/usr/bin/env python3
"""Accept only the exact, documented pytest failures already present in main."""

from __future__ import annotations

import argparse
from pathlib import Path
from xml.etree import ElementTree


def load_baseline(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def load_failures(path: Path) -> set[str]:
    root = ElementTree.parse(path).getroot()
    failures: set[str] = set()
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        failures.add(f"{case.attrib['classname']}::{case.attrib['name']}")
    return failures


def compare(actual: set[str], expected: set[str]) -> list[str]:
    messages: list[str] = []
    new = sorted(actual - expected)
    resolved = sorted(expected - actual)
    if new:
        messages.append("New unit failures:\n- " + "\n- ".join(new))
    if resolved:
        messages.append(
            "Baselined failures no longer reproduced; remove them from the baseline:\n- "
            + "\n- ".join(resolved)
        )
    return messages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()

    messages = compare(load_failures(args.junit), load_baseline(args.baseline))
    if messages:
        print("\n\n".join(messages))
        return 1
    print("Unit failures match the explicit accepted baseline exactly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
