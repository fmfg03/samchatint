#!/usr/bin/env python3
"""Guard accepted SamChat fixes from being silently lost in later merges.

This is a lightweight release-spine check. It deliberately verifies durable
source/test markers for behaviors that have already been accepted by users and
then disappeared during branch/deploy churn.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RequiredText:
    id: str
    path: str
    needle: str
    reason: str


@dataclass(frozen=True)
class ForbiddenPattern:
    id: str
    path: str
    pattern: str
    reason: str


REQUIRED_TEXTS: tuple[RequiredText, ...] = (
    RequiredText(
        "ARG-001-PDF-PREVIEW-CONTAINER",
        "src/devnous/gastos/routes/user_routes.py",
        "quick_cfdi_pdf_preview",
        "Expense-report quick capture must keep the PDF preview container.",
    ),
    RequiredText(
        "ARG-001-PDF-PREVIEW-SCRIPT",
        "src/devnous/gastos/routes/user_routes.py",
        "render_pdf_file_preview_script",
        "Expense-report quick capture must keep the PDF preview script wired.",
    ),
    RequiredText(
        "ARG-002-TIP-ROUTE",
        "src/devnous/gastos/routes/user_routes.py",
        "propina_no_deducible",
        "Meal/restaurant tip capture must stay wired in expense-report routes.",
    ),
    RequiredText(
        "ARG-002-TIP-SCHEMA",
        "src/devnous/gastos/schema_guard.py",
        "expense_reports_propina_no_deducible_column",
        "Tip storage must remain schema-guarded.",
    ),
    RequiredText(
        "ARG-002-TIP-SERVICE",
        "src/devnous/gastos/services/expense_service.py",
        "propina_no_deducible",
        "Tip values must continue to flow into expense creation.",
    ),
    RequiredText(
        "ARG-003-NO-CFDI-DESCRIPTION-PREFILL-NOTICE",
        "src/devnous/gastos/routes/user_routes.py",
        "la descripcion la captura el usuario",
        "Quick capture must keep the user description as user-authored text.",
    ),
    RequiredText(
        "ARG-003-SOLICITUD-NO-CFDI-DESCRIPTION-PREFILL",
        "src/devnous/gastos/routes/solicitud_transferencia_ui.py",
        "capture/revise la descripcion",
        "Solicitud CFDI autofill must not overwrite the operational description.",
    ),
    RequiredText(
        "ARG-004-PROVIDER-FILTER",
        "src/devnous/gastos/routes/user_routes.py",
        "Por Proveedor",
        "Solicitudes consultation must keep the provider search filter.",
    ),
    RequiredText(
        "ARG-005-PAYMENT-RUN-IN-PROCESS",
        "src/devnous/gastos/routes/admin_routes.py",
        "En Proceso de Pago",
        "Payment run must keep the in-process payment state boundary.",
    ),
    RequiredText(
        "ARG-006-REFERENCIA-OPERACIONES",
        "src/devnous/gastos/routes/user_routes.py",
        "Referencia Operaciones",
        "Expense-report and document views must keep the operations reference visible.",
    ),
    RequiredText(
        "ARG-007-QUICK-EXPENSE-TESTS",
        "tests/unit/gastos/test_cfdi_quick_expense_totals.py",
        "propina_no_deducible",
        "Focused quick expense regression tests must remain present.",
    ),
    RequiredText(
        "ARG-007-SOLICITUD-AUTOFILL-TESTS",
        "tests/unit/gastos/test_solicitud_terceros_routes.py",
        "tipInput",
        "Focused solicitud autofill regression tests must remain present.",
    ),
    RequiredText(
        "ARG-DOC-RELEASE-SPINE",
        "docs/release/accepted-regressions.md",
        "SamChat accepted regression gate",
        "The human-readable accepted regression spine must remain documented.",
    ),
)


FORBIDDEN_PATTERNS: tuple[ForbiddenPattern, ...] = (
    ForbiddenPattern(
        "ARG-003-FORBID-SOLICITUD-CFDI-CONCEPT-OVERWRITE",
        "src/devnous/gastos/routes/solicitud_transferencia_ui.py",
        r"conceptInput\.value\s*=\s*payload\.concepto",
        "CFDI autofill must not overwrite the user-authored operational description.",
    ),
)


def _read(root: Path, relative_path: str) -> tuple[str | None, str | None]:
    path = root / relative_path
    if not path.is_file():
        return None, f"missing file: {relative_path}"
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        return None, f"file is not UTF-8 readable: {relative_path}"


def check(root: Path) -> dict[str, object]:
    failures: list[dict[str, str]] = []

    for item in REQUIRED_TEXTS:
        text, error = _read(root, item.path)
        if error:
            failures.append({"id": item.id, "path": item.path, "reason": error})
            continue
        assert text is not None
        if item.needle not in text:
            failures.append(
                {
                    "id": item.id,
                    "path": item.path,
                    "reason": item.reason,
                    "missing": item.needle,
                }
            )

    for item in FORBIDDEN_PATTERNS:
        text, error = _read(root, item.path)
        if error:
            failures.append({"id": item.id, "path": item.path, "reason": error})
            continue
        assert text is not None
        if re.search(item.pattern, text):
            failures.append(
                {
                    "id": item.id,
                    "path": item.path,
                    "reason": item.reason,
                    "forbidden_pattern": item.pattern,
                }
            )

    return {
        "schema_version": "samchat.accepted_regression_gate.v1",
        "valid": not failures,
        "required_markers": len(REQUIRED_TEXTS),
        "forbidden_patterns": len(FORBIDDEN_PATTERNS),
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repository root to inspect")
    args = parser.parse_args(argv)

    result = check(Path(args.root).resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
