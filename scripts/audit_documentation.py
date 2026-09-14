#!/usr/bin/env python3
"""Read-only inventory and safety checks for tracked Markdown documentation."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANON = {
    "SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md",
    "SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md",
    "SAMCHAT_CODEBASE_SWEEP_REPORT_2026-09-10.md",
}
RISK_PATTERNS = (
    r"\bsamchat(?:\s+|-)+mcp\b", r"\bmcp_platform_launcher\.py\b", r"\b99\+",
    r"\b(?:GDPR|SOC ?2|ISO ?27001|HIPAA|PCI-DSS)\s+(?:compliant|certified)",
    r"\b(?:ROI|adoption|cost reduction).{0,20}\b\d+%", r"\b(?:your-org|example\.com)\b",
)
LINK = re.compile(r"(?<!!)\[[^]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
FENCE = re.compile(r"```(?:bash|sh|shell|console)\s*\n(.*?)```", re.I | re.S)


def tracked_markdown(root: Path = ROOT) -> list[Path]:
    output = subprocess.check_output(["git", "-C", str(root), "ls-files", "*.md"], text=True)
    return [root / item for item in output.splitlines() if item]


def _local_target(source: Path, target: str, root: Path) -> Path | None:
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return None
    value = target.split("#", 1)[0]
    return (source.parent / value).resolve() if value else source


def link_findings(path: Path, text: str, root: Path = ROOT) -> list[dict[str, Any]]:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        for target in LINK.findall(line):
            local = _local_target(path, target, root)
            if local is not None and not local.exists():
                findings.append({"line": number, "target": target, "status": "missing"})
    return findings


def command_findings(path: Path, text: str, root: Path = ROOT) -> list[dict[str, Any]]:
    findings = []
    for block in FENCE.findall(text):
        for command in block.splitlines():
            command = command.strip()
            if not command or command.startswith("#"):
                continue
            for token in re.findall(r"(?<![\w.-])(?:[\w./-]+\.(?:py|yml|yaml|txt)|[\w./-]+/)", command):
                candidate = (root / token.rstrip("/")).resolve()
                if token.startswith(("http", "/")) or "<" in token:
                    continue
                if not candidate.exists():
                    findings.append({"command": command, "target": token, "status": "missing"})
    return findings


def classify(path: Path, text: str, root: Path = ROOT) -> tuple[str, str, str]:
    relative = path.relative_to(root).as_posix()
    if path.name in CANON:
        return "CURRENT_CANONICAL", "keep", "Canon governance"
    if any(re.search(pattern, text, re.I) for pattern in RISK_PATTERNS):
        return "MISLEADING_OR_UNSAFE", "escalate to a named human owner", "PENDING_HUMAN_ASSIGNMENT"
    if (relative.startswith("artifacts/") or "/artifacts/" in relative
            or relative.startswith("docs/release/") or "/release/" in relative
            or "2025" in path.name):
        return "HISTORICAL_EVIDENCE", "add historical/staleness banner", "PENDING_HUMAN_ASSIGNMENT"
    if "/sprints/" in relative or "roadmap" in relative:
        return "PLANNING_ONLY", "keep", "PENDING_HUMAN_ASSIGNMENT"
    return "UNKNOWN_REQUIRES_OWNER", "escalate to a named human owner", "PENDING_HUMAN_ASSIGNMENT"


def inventory(root: Path = ROOT) -> list[dict[str, Any]]:
    records = []
    for path in tracked_markdown(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
        classification, disposition, owner = classify(path, text)
        records.append({
            "path": path.relative_to(root).as_posix(), "title": title,
            "owner": owner, "classification": classification, "disposition": disposition,
            "local_link_findings": link_findings(path, text, root),
            "entrypoint_command_findings": command_findings(path, text, root)
            if path.relative_to(root).as_posix() in {"README.md", "docs/README.md"} else [],
        })
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--baseline", type=Path, default=ROOT / "docs/audits/documentation-known-findings.json")
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()
    records = inventory()
    if args.output:
        args.output.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    findings = sorted(f"{r['path']}:{item.get('line', '?')}:{item.get('target', '')}:{item.get('command', '')}" for r in records for item in r['local_link_findings'] + r['entrypoint_command_findings'])
    if args.write_baseline:
        args.baseline.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else []
    new = sorted(set(findings) - set(baseline))
    if args.check and new:
        print("\n".join(new)); return 1
    print(json.dumps({"markdown_files": len(records), "findings": len(findings), "new_findings": len(new)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
