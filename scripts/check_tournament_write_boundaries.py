#!/usr/bin/env python3
"""Fail CI when a local gastos-project writer appears outside reviewed functions."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

PROTECTED_MODELS = {"Tournament", "TournamentOperationsLink"}
MUTATION_BUILDERS = {"insert", "update", "delete"}
PROTECTED_SQL = re.compile(
    r"\b(?:insert\s+into|update|delete\s+from)\s+"
    r"(?:(?:[a-z_][a-z0-9_]*)\.)?"
    r"(?:tournaments|tournament_operations_links|tournament_concepto_mappings)\b",
    re.IGNORECASE,
)

# Exact function-level exceptions are intentional debt, never whole-file exemptions.
ALLOWLIST = {
    (
        "src/samchat/assistant/tournament_application_domain.py",
        "create_local_tournament_from_projection",
    ),
    (
        "src/devnous/gastos/services/finance_training_seed_service.py",
        "generate_finance_training_dataset",
    ),
    (
        "src/devnous/gastos/services/finance_training_seed_service.py",
        "cleanup_finance_training_dataset",
    ),
    (
        "src/devnous/gastos/services/finance_training_seed_service.py",
        "_delete_finance_training_id_sets",
    ),
    (
        "src/samchat/budgets/service.py",
        "sync_budget_projects_from_partidas_workbook",
    ),
    ("src/devnous/gastos/routes/admin_routes.py", "link_tournament_to_operations"),
    ("src/devnous/gastos/routes/admin_routes.py", "update_tournament"),
    ("src/devnous/gastos/routes/admin_routes.py", "toggle_tournament"),
    ("src/devnous/gastos/routes/admin_routes.py", "delete_tournament"),
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    function: str
    rule: str
    target: str


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, source: str) -> None:
        self.path = path
        self.source = source
        self.model_aliases: set[str] = set()
        self.model_module_aliases: set[str] = set()
        self.function_stack: list[str] = []
        self.protected_vars_stack: list[set[str]] = []
        self.findings: list[Finding] = []

    @property
    def function(self) -> str:
        return self.function_stack[-1] if self.function_stack else "<module>"

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in {"devnous.gastos", "devnous.gastos.models"}:
            for item in node.names:
                if item.name == "models":
                    self.model_module_aliases.add(item.asname or item.name)
        for item in node.names:
            if item.name in PROTECTED_MODELS:
                self.model_aliases.add(item.asname or item.name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            if item.name == "devnous.gastos.models":
                self.model_module_aliases.add(item.asname or item.name)
        self.generic_visit(node)

    def _is_protected_model(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.model_aliases
        return (
            isinstance(node, ast.Attribute)
            and ast.unparse(node.value) in self.model_module_aliases
            and node.attr in PROTECTED_MODELS
        )

    def _contains_protected_model(self, node: ast.AST) -> bool:
        return any(self._is_protected_model(item) for item in ast.walk(node))

    def _contains_protected_select(self, node: ast.AST) -> bool:
        return any(
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id == "select"
            and any(self._is_protected_model(arg) for arg in item.args)
            for item in ast.walk(node)
        )

    def _contains_protected_session_get(self, node: ast.AST) -> bool:
        return any(
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr == "get"
            and bool(item.args)
            and self._is_protected_model(item.args[0])
            for item in ast.walk(node)
        )

    def _infer_protected_vars(self, node: ast.AST) -> set[str]:
        """Infer query results and ORM rows without tainting derived scalar values.

        A protected row may flow through ``result.scalar_one*()``.  It must not,
        however, taint every later value that merely reads one of its fields;
        doing so makes unrelated objects (for example an Expense whose display
        name came from a Tournament) look like Tournament rows.
        """
        protected: set[str] = set()
        assignments = [item for item in ast.walk(node) if isinstance(item, ast.Assign)]
        changed = True
        while changed:
            changed = False
            for assignment in assignments:
                target_names = {
                    target.id
                    for target in assignment.targets
                    if isinstance(target, ast.Name)
                }
                if not target_names or target_names <= protected:
                    continue
                value = assignment.value
                receiver_is_protected = (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and isinstance(value.func.value, ast.Name)
                    and value.func.value.id in protected
                    and value.func.attr
                    in {
                        "scalar_one",
                        "scalar_one_or_none",
                        "scalars",
                        "first",
                        "one",
                        "one_or_none",
                    }
                )
                derives_from_protected = (
                    self._contains_protected_select(value)
                    or self._contains_protected_session_get(value)
                    or (
                        isinstance(value, ast.Call)
                        and self._is_protected_model(value.func)
                    )
                    or receiver_is_protected
                )
                if derives_from_protected:
                    protected.update(target_names)
                    changed = True
        return protected

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        effective_body = list(node.body)
        if (
            effective_body
            and isinstance(effective_body[0], ast.Expr)
            and isinstance(effective_body[0].value, ast.Constant)
            and isinstance(effective_body[0].value.value, str)
        ):
            effective_body = effective_body[1:]
        # Quarantined handlers contain dormant legacy code after an unconditional
        # raise. Do not require an allowlist entry for unreachable sinks.
        if effective_body and isinstance(effective_body[0], ast.Raise):
            return
        self.function_stack.append(node.name)
        self.protected_vars_stack.append(self._infer_protected_vars(node))
        self.generic_visit(node)
        self.protected_vars_stack.pop()
        self.function_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _record(self, node: ast.AST, rule: str, target: str) -> None:
        self.findings.append(
            Finding(
                path=self.path,
                line=getattr(node, "lineno", 0),
                function=self.function,
                rule=rule,
                target=target,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_protected_model(node.func):
            self._record(node, "orm-constructor", ast.unparse(node.func))
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in MUTATION_BUILDERS
            and node.args
            and self._is_protected_model(node.args[0])
        ):
            self._record(node, "orm-mutation", ast.unparse(node.args[0]))
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in MUTATION_BUILDERS
            and self._contains_protected_model(node.func.value)
        ):
            self._record(node, "orm-table-mutation", ast.unparse(node.func.value))
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "delete"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and (
                node.args[0].id in {"tournament", "torneo", "existing_link"}
                or bool(
                    self.protected_vars_stack
                    and node.args[0].id in self.protected_vars_stack[-1]
                )
            )
        ):
            self._record(node, "session-delete", node.args[0].id)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and (
                    target.value.id in {"tournament", "torneo", "existing_link"}
                    or bool(
                        self.protected_vars_stack
                        and target.value.id in self.protected_vars_stack[-1]
                    )
                )
            ):
                self._record(node, "model-attribute-write", target.value.id)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and PROTECTED_SQL.search(node.value):
            self._record(node, "raw-sql-mutation", "protected tournament table")

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        static_text = "".join(
            item.value for item in node.values if isinstance(item, ast.Constant)
        )
        if PROTECTED_SQL.search(static_text):
            self._record(node, "dynamic-sql-mutation", "protected tournament table")
        # Do not visit the literal fragments again: they are constituents of this
        # dynamic SQL finding, not an additional raw-SQL sink.
        for item in node.values:
            if isinstance(item, ast.FormattedValue):
                self.visit(item.value)


def audit_source(source: str, *, path: str) -> list[Finding]:
    visitor = _Visitor(path, source)
    visitor.visit(ast.parse(source, filename=path))
    return [
        item for item in visitor.findings if (item.path, item.function) not in ALLOWLIST
    ]


def audit_tree(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted((root / "src").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        findings.extend(audit_source(path.read_text(encoding="utf-8"), path=relative))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    findings = audit_tree(Path(args.root).resolve())
    if args.json:
        print(json.dumps([asdict(item) for item in findings], indent=2))
    else:
        for item in findings:
            print(
                f"{item.path}:{item.line}: {item.rule}: "
                f"{item.target} in {item.function}"
            )
        if not findings:
            print("Local gastos-project write boundary inventory: PASS")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
