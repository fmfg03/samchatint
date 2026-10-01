"""Bind monetary assertions to typed reader values before any output is saved.

This is a fail-closed claim check, not an accounting calculator. It never derives
new totals or treats years, counts, references or arbitrary numeric fields as money.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

# Concrete monetary leaves in supported reader contracts. Deliberately exclude
# ids, folios, counts, dates, percentages and generic `value` fields.
MONEY_KEYS = frozenset(
    {
        "amount",
        "monto",
        "monto_total",
        "monto_pendiente",
        "total_pendiente",
        "total_pagado",
        "gasto_total",
        "budget_total",
        "total_budget",
        "actual_total",
        "total_actual",
        "debit_total",
        "credit_total",
        "debe",
        "haber",
        "difference",
        "approved_amount",
        "paid_amount",
        "pending_amount",
        "balance",
        "saldo",
        "presupuesto",
        "ejercido",
        "disponible",
        "ingresos",
        "egresos",
    }
)
COUNT_FIELDS = {
    "registros": {"registros", "total_registros"},
    "lineas": {"line_count"},
    "bloqueos": {"blocker_count", "blockers_count"},
    "polizas": {"polizas_count", "policy_count"},
    "documentos": {"document_count", "documentos_count", "pending_count"},
}
NUMBER = r"-?\d+(?:[.,]\d+)*"
MONEY = re.compile(
    rf"(?:\$\s*(?P<prefix>{NUMBER})|(?P<suffix>{NUMBER})\s*(?:mxn|usd|pesos?|dolares?)\b|"
    rf"\b(?:importe|monto|total|saldo|presupuesto|iva|impuestos?|pago|pagaron|pagado|gasto)\s*(?:(?:es|de|:|=|fue de|asciende a|suma)\s*)?(?P<label>{NUMBER}))"
)


def decimal_value(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    # Canonical numeric values use decimal point; prose grouping is handled by
    # the claim parser, never applied to source numeric types.
    try:
        number = Decimal(text)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def _prose_number(value: str) -> Decimal | None:
    if re.fullmatch(r"-?\d{1,3}(?:[,.]\d{3})+(?:[,.]\d{2})?", value):
        decimal = re.search(r"[,.](\d{2})$", value)
        digits = re.sub(r"[,.]", "", value)
        value = digits[:-2] + "." + digits[-2:] if decimal else digits
    else:
        value = value.replace(",", ".")
    return decimal_value(value)


def is_safe_clarification(message: str) -> bool:
    # Questions may select years or folios, but cannot smuggle an asserted amount.
    if not re.fullmatch(r"¿[^¿?!.]+\?", message) or MONEY.search(message):
        return False
    body = message[1:-1]
    if re.search(
        r"\b(?:se pago|se pagaron|aumento porque|el total es|confirmas que)\b", body
    ):
        return False
    if not re.match(
        r"(?:buscas|quieres|te refieres|que|cual|cuales|necesitas|puedes|debo|consulto|"
        r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|"
        r"noviembre|diciembre|folio|periodo|empresa)\b",
        body,
    ):
        return False
    # Remove explicit reference identifiers and years. Other numbers need a
    # specific clarification contract rather than being assumed harmless.
    references_removed = re.sub(r"\b[a-z]+[-/]\d+(?:[-/]\d+)*\b", "", body)
    references_removed = re.sub(r"\b20\d{2}\b", "", references_removed)
    return not re.search(r"\d", references_removed)


def validate_financial_claims(
    message: str, reads: list[tuple[str, Mapping[str, Any]]]
) -> tuple[bool, list[dict[str, str]]]:
    """Require each monetary claim to equal an explicit monetary source leaf.

    Only validated reader payloads may be supplied. Diagnostics bind each claim
    to its exact source path; no model-provided calculation is trusted.
    """
    # No exposed reader currently establishes allocated paid VAT. A successful
    # budget/expense read cannot be relabeled as a tax breakdown.
    if "iva" in message and re.search(r"\d", message):
        return False, []
    if re.search(
        r"\b(?:cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|"
        r"veinte|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|cien|mil|millon)"
        r"\b[^.!?]{0,40}\b(?:pesos|mxn|usd|dolares)\b",
        message,
    ):
        return False, []
    values: dict[Decimal, list[str]] = {}
    counts: dict[tuple[str, Decimal], str] = {}

    def visit(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                for noun, keys in COUNT_FIELDS.items():
                    if key in keys:
                        count = decimal_value(child)
                        if count is not None:
                            counts[(noun, count)] = child_path
                if key in MONEY_KEYS:
                    number = decimal_value(child)
                    if number is not None:
                        values.setdefault(number, []).append(child_path)
                if key in {
                    "summary",
                    "totals",
                    "rows",
                    "items",
                    "documentos",
                    "gastos",
                    "expenses",
                    "documents",
                    "breakdown",
                    "monthly_buckets",
                    "expected_income",
                    "candidates",
                    "blockers",
                    "reports",
                } and isinstance(child, (Mapping, list)):
                    visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    for tool, payload in reads:
        if tool == "assistant_canonical_query":
            visit(payload.get("data", {}), tool + ".data")
        elif tool == "assistant_finance_read":
            visit(payload.get("payload", {}), tool + ".payload")
        else:
            visit(payload, tool)
    bindings = []
    for match in MONEY.finditer(message):
        raw = next(value for value in match.groupdict().values() if value is not None)
        number = _prose_number(raw)
        if number not in values:
            return False, []
        paths = values[number]
        # A row amount cannot be relabeled a population total. No implicit sum.
        if "total" in match.group() or re.search(
            r"\btotal\b", message[max(0, match.start() - 24) : match.start()]
        ):
            paths = [
                path
                for path in paths
                if "[" not in path
                and ("total" in path.rsplit(".", 1)[-1] or ".totals." in path)
            ]
        if not paths:
            return False, []
        bindings.append({"claim": match.group(), "source_path": paths[0]})
    # Fail closed on numeric assertions outside the monetary grammar. A bare
    # number or an unfamiliar monetary phrase must not bypass source binding.
    remainder = MONEY.sub(" ", message)
    count_matches = list(
        re.finditer(
            r"\b(\d+)\s+(registros|lineas|bloqueos|polizas|documentos)\b", remainder
        )
    )
    for match in count_matches:
        key = (match.group(2), Decimal(match.group(1)))
        if key not in counts:
            return False, []
        bindings.append({"claim": match.group(), "source_path": counts[key]})
    remainder = re.sub(
        r"\b\d+\s+(?:registros|lineas|bloqueos|polizas|documentos)\b", " ", remainder
    )
    remainder = re.sub(r"\b[a-z]+[-/]?\d+(?:[-/]\d+)*\b", " ", remainder)
    remainder = re.sub(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", " ", remainder)
    remainder = re.sub(
        r"\b(?:folio|referencia|ano|ejercicio|enero|febrero|marzo|abril|mayo|junio|"
        r"julio|agosto|septiembre|octubre|noviembre|diciembre)\s+\d+\b",
        " ",
        remainder,
    )
    if re.search(r"\d", remainder):
        return False, []
    # No deterministic derivation contract exists for comparative percentages.
    if re.search(r"\d[\d.,]*\s*%", message):
        return False, []
    return True, bindings
