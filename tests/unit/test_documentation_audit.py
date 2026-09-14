from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT = Path("scripts/audit_documentation.py")
SPEC = spec_from_file_location("documentation_audit", SCRIPT)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_missing_local_markdown_link_reports_line_and_target(tmp_path):
    source = tmp_path / "README.md"
    source.write_text("# Test\n[broken](missing.md)\n", encoding="utf-8")
    assert MODULE.link_findings(source, source.read_text(), tmp_path) == [
        {"line": 2, "target": "missing.md", "status": "missing"}
    ]


def test_external_and_anchor_links_are_not_treated_as_local_paths(tmp_path):
    source = tmp_path / "README.md"
    source.write_text("[web](https://sam.chat) [anchor](#x)\n", encoding="utf-8")
    assert MODULE.link_findings(source, source.read_text(), tmp_path) == []


def test_missing_entrypoint_command_path_is_reported(tmp_path):
    source = tmp_path / "README.md"
    source.write_text("```bash\npython missing.py\n```\n", encoding="utf-8")
    assert MODULE.command_findings(source, source.read_text(), tmp_path)[0]["target"] == "missing.py"


def test_risky_claims_require_pending_human_assignment(tmp_path):
    source = tmp_path / "README.md"
    source.write_text("# Old\n99+ agents and GDPR compliant\n", encoding="utf-8")
    classification, disposition, owner = MODULE.classify(source, source.read_text(), tmp_path)
    assert (classification, disposition, owner) == (
        "MISLEADING_OR_UNSAFE", "escalate to a named human owner", "PENDING_HUMAN_ASSIGNMENT"
    )
