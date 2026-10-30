"""Read-only tournament accounting with explicit, evidenced attribution.

Bindings live in canonical budget-concept metadata. Nothing is inferred from
account names, and account-level balances require approved exclusive ownership.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any

from sqlalchemy import text

from samchat.accounting_historical.service import _find_latest_applied_import_run


def unavailable(reason: str) -> dict[str, Any]:
    """Keep missing evidence distinct from measured zero."""
    return {
        "source_status": "unavailable",
        "reason": reason,
        "source": "historical_accounting",
        "read_only": True,
        "periods": [],
        "accounts": [],
        "income": None,
        "expense": None,
        "closing_balances": None,
        "coverage_months": [],
        "gaps": [],
    }


def _binding_for_year(metadata: dict[str, Any], year: int) -> dict[str, Any]:
    """Select an explicit yearly binding; legacy remains compatible."""
    if "historical_accounting_bindings" in metadata:
        yearly = metadata["historical_accounting_bindings"]
        binding = yearly.get(str(year)) if isinstance(yearly, dict) else None
    else:
        binding = metadata.get("historical_accounting_binding")
    return binding if isinstance(binding, dict) else {}


def _shares_account(metadata: Any, *, year: int, company: str) -> bool:
    """Unknown legacy scope blocks exclusivity; yearly maps select one period."""
    if not isinstance(metadata, dict):
        return True
    if "historical_accounting_bindings" in metadata:
        yearly = metadata["historical_accounting_bindings"]
        if not isinstance(yearly, dict):
            return True
        if str(year) not in yearly:
            return False
    binding = _binding_for_year(metadata, year)
    return not binding.get("company_code") or binding["company_code"] == company


def _bindings(
    concepts: list[dict[str, Any]], *, tournament_id: str, edition_year: int
) -> tuple[list[dict[str, Any]], list[str]]:
    bindings: list[dict[str, Any]] = []
    gaps: list[str] = []
    seen = set()
    account_modes = defaultdict(set)
    for concept in concepts:
        if str(concept.get("tournament_id")) != tournament_id:
            raise PermissionError("Accounting concept escaped authorized tournament.")
        metadata = concept.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        binding = _binding_for_year(metadata, edition_year)
        code = str(concept.get("account_code") or "")
        mode = binding.get("attribution")
        kind = binding.get("metric_kind")
        valid = (
            binding.get("approved") is True
            and binding.get("evidence_ref")
            and binding.get("fiscal_year") == edition_year
            and binding.get("company_code")
            and code
            and mode in {"exclusive_account", "project", "cost_center"}
            and kind in {"expense", "income", "balance"}
            and binding.get("tax_basis") in {"includes_vat", "excludes_vat"}
            and binding.get("currency")
        )
        if mode == "project" and not binding.get("project_raw"):
            valid = False
        if mode == "cost_center" and not binding.get("cost_center_raw"):
            valid = False
        if kind == "balance" and mode != "exclusive_account":
            valid = False
        if not valid:
            gaps.append(str(concept.get("concept_name") or "Partida sin vínculo"))
            continue
        item = {**binding, "account_code": code}
        key = (
            item["company_code"],
            code,
            mode,
            (
                item.get("project_raw")
                if mode == "project"
                else item.get("cost_center_raw") if mode == "cost_center" else None
            ),
        )
        account_modes[(item["company_code"], code)].add(mode)
        if key in seen:
            prior = next(b for b in bindings if b["_key"] == key)
            if any(
                prior[k] != item[k] for k in ("metric_kind", "tax_basis", "currency")
            ):
                raise ValueError("Conflicting accounting bindings.")
            continue
        seen.add(key)
        bindings.append({**item, "_key": key})
    if any(len(modes) > 1 for modes in account_modes.values()):
        raise ValueError("Overlapping account and project attribution.")
    return bindings, gaps


_DOCUMENTARY_SCOPE_PATH = (
    Path(__file__).resolve().parents[3]
    / "config/tournament_accounting_documentary_scope_2026.json"
)


def _documentary_scope(tournament_id: str, edition_year: int) -> dict[str, Any] | None:
    if edition_year != 2026:
        return None
    scope = json.loads(_DOCUMENTARY_SCOPE_PATH.read_text(encoding="utf-8"))
    accounts = [a for a in scope["accounts"] if a["tournament_id"] == tournament_id]
    if not accounts:
        return None
    if (
        scope["schema_version"] != "samchat.tournament.documentary.v1"
        or scope["fiscal_year"] != edition_year
        or scope["company_code"] != "01"
        or scope["company_label"] != "PSP1705058S4"
        or len({a["account_code"] for a in accounts}) != len(accounts)
        or any(a["exclusive_attribution"] is not False for a in accounts)
    ):
        raise ValueError("Invalid documentary scope")
    return {**scope, "accounts": accounts}


def budget_actual_comparison(
    tournament_id: str, edition_year: int
) -> dict[str, Any] | None:
    """Declare the approved three-tournament comparison gap without inference."""
    if _documentary_scope(tournament_id, edition_year) is None:
        return None
    return {
        "status": "unavailable",
        "reason": (
            "Presupuesto con IVA, gasto fiscal y compromiso documental tienen "
            "bases distintas; falta acreditar una población comparable."
        ),
    }


def _documentary_money(value: Any) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Invalid documentary amount")
    return number


def _documentary_sources(
    sources: list[dict[str, Any]], scope: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Require the independent canonical admission; flags never grant admission."""
    families: dict[str, dict[str, Any]] = {}
    for source in sources:
        if (
            source["fiscal_year"] != scope["fiscal_year"]
            or source["company_code"] != scope["company_code"]
            or source["company_label"] != scope["company_label"]
            or source["validation_status"] != "OK"
            or not re.fullmatch(r"[0-9a-f]{64}", source.get("source_sha256") or "")
            or source["source_family"] in families
        ):
            raise ValueError("Unvalidated or conflicting documentary source")
        families[source["source_family"]] = source
    required = {"balanza", "poliza_header", "poliza_line", "explicit_manifest"}
    if not required.issubset(families):
        raise ValueError("Independent explicit admission missing")
    for family, expected in scope["original_source_hashes"].items():
        if families.get(family, {}).get("source_sha256") != expected:
            raise ValueError("Original documentary source changed")
    validation = families["explicit_manifest"].get("metadata")
    if not isinstance(validation, dict):
        raise ValueError("Independent admission metadata missing")
    if validation.get("reconciliation_status") != "reconciled_with_disclosed_precision":
        raise ValueError("Source reconciliation not admitted")
    for key in (
        "material_reconciliation_issues",
        "rounding_incidents",
        "cent_reconciliation_differences",
    ):
        if validation.get(key) != []:
            raise ValueError("Unresolved admission issue")
    for key in ("balance_months", "policy_months"):
        months = validation.get(key)
        limit = 8 if key == "balance_months" else 9
        if (
            not isinstance(months, list)
            or not months
            or any(type(m) is not int or not 1 <= m <= limit for m in months)
            or months != sorted(set(months))
        ):
            raise ValueError("Invalid admitted coverage")
    withheld = validation.get("withheld_months")
    if not isinstance(withheld, list):
        raise ValueError("Missing withheld coverage declaration")
    excluded = set()
    for item in withheld:
        month = item.get("month_num")
        if (
            type(month) is not int
            or not 1 <= month <= 9
            or month in excluded
            or not item.get("reason")
        ):
            raise ValueError("Invalid withheld coverage declaration")
        excluded.add(month)
    if (
        set(validation["balance_months"]) != set(range(1, 9)) - excluded
        or set(validation["policy_months"]) != set(range(1, 10)) - excluded
    ):
        raise ValueError("Undeclared coverage gap")
    return families, validation


def _documentary_rows(
    balances: list[dict[str, Any]],
    headers: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    *,
    families: dict[str, dict[str, Any]],
    validation: dict[str, Any],
    scope: dict[str, Any],
    run_id: str,
) -> None:
    """Check whole policies independently, including their unshown counterparties."""
    for rows, family, coverage in (
        (balances, "balanza", "balance_months"),
        (headers, "poliza_header", "policy_months"),
        (lines, "poliza_line", "policy_months"),
    ):
        if {r["fiscal_month"] for r in rows} != set(validation[coverage]):
            raise ValueError("Persisted coverage differs from admission")
        for row in rows:
            if (
                str(row["import_run_id"]) != run_id
                or row["fiscal_year"] != scope["fiscal_year"]
                or row["company_code"] != scope["company_code"]
                or row["company_label"] != scope["company_label"]
                or str(row["source_file_id"]) != str(families[family]["id"])
            ):
                raise ValueError("Documentary row escaped admitted scope")
    by_header = {str(h["id"]): h for h in headers}
    if len(by_header) != len(headers):
        raise ValueError("Duplicate policy header")
    totals: dict[str, list[Any]] = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
    seen = set()
    for line in lines:
        hid = str(line["policy_header_id"])
        if hid not in by_header or str(line["id"]) in seen:
            raise ValueError("Orphan or duplicate policy line")
        seen.add(str(line["id"]))
        header = by_header[hid]
        if (
            header["fiscal_month"] != line["fiscal_month"]
            or header["policy_id_natural"] != line["policy_id_natural"]
        ):
            raise ValueError("Inconsistent policy identity")
        debit = _documentary_money(line["debit_amount"])
        credit = _documentary_money(line["credit_amount"])
        if min(debit, credit) < 0:
            raise ValueError("Negative policy movement")
        totals[hid][0] += 1
        totals[hid][1] += debit
        totals[hid][2] += credit
    for hid, header in by_header.items():
        flags = header.get("quality_flags")
        if (
            not isinstance(flags, list)
            or any(f != "missing_summary_marker" for f in flags)
            or header["is_balanced"] is not True
            or totals[hid]
            != [
                header["line_count"],
                _documentary_money(header["total_debits"]),
                _documentary_money(header["total_credits"]),
            ]
            or totals[hid][1] != totals[hid][2]
        ):
            raise ValueError("Incomplete or materially flagged policy")
    seen = set()
    for row in balances:
        key = (row["fiscal_month"], row["account_code"])
        if key in seen:
            raise ValueError("Duplicate documentary balance")
        seen.add(key)
        for field in ("debits", "credits", "closing_balance"):
            _documentary_money(row[field])


async def build_tournament_documentary_accounting(
    session: Any, *, tournament_id: str, edition_year: int
) -> dict[str, Any]:
    """Project verified account facts for an already-authorized tournament.

    Catalogue membership conveys documentary visibility only. It does not attest
    project ownership, currency, tax basis, financial totals or cash movement.
    """
    result: dict[str, Any] = {
        "source_status": "unavailable",
        "source": "historical_accounting_documentary",
        "read_only": True,
        "company_code": "01",
        "fiscal_year": edition_year,
        "currency": None,
        "tax_basis": None,
        "accounts": [],
        "coverage": {
            "trial_balance_months": [],
            "policy_months": [],
            "withheld_months": [],
        },
        "gaps": [],
    }
    scope = _documentary_scope(tournament_id, edition_year)
    if scope is None:
        result["reason"] = (
            "Sin alcance documental aprobado para este torneo y ejercicio."
        )
        return result
    result["accounts"] = [
        {
            **a,
            "company_code": scope["company_code"],
            "currency": None,
            "tax_basis": None,
            "trial_balance_periods": [],
            "policy_periods": [],
        }
        for a in scope["accounts"]
    ]
    ready = (
        await session.execute(
            text(
                """
        SELECT to_regclass('historical_trial_balance_rows') IS NOT NULL
          AND to_regclass('historical_policy_headers') IS NOT NULL
          AND to_regclass('historical_policy_lines') IS NOT NULL
          AND to_regclass('historical_accounting_source_files') IS NOT NULL
          AND to_regclass('accounting_import_runs') IS NOT NULL AS ready
    """
            )
        )
    ).scalar_one()
    if not ready:
        result["reason"] = "La fuente histórica no está disponible."
        return result
    run = await _find_latest_applied_import_run(
        session, fiscal_year=edition_year, company_code=scope["company_code"]
    )
    if not run or not re.fullmatch(r"[0-9a-f]{64}", run.get("source_sha256") or ""):
        result["reason"] = "Sin importación aplicada con huella acreditada."
        return result
    params = {"run": run["id"], "year": edition_year, "company": scope["company_code"]}

    async def read(sql: str) -> list[dict[str, Any]]:
        return [
            dict(r) for r in (await session.execute(text(sql), params)).mappings().all()
        ]

    sources = await read(
        """
        SELECT id, fiscal_year, company_code, company_label, source_family,
               source_sha256, validation_status, metadata
        FROM historical_accounting_source_files
        WHERE import_run_id=:run AND fiscal_year=:year AND company_code=:company
    """
    )
    try:
        families, validation = _documentary_sources(sources, scope)
        balances = await read(
            """
            SELECT *, COALESCE(account_code_canonical, account_code_raw) AS account_code
            FROM historical_trial_balance_rows
            WHERE import_run_id=:run AND fiscal_year=:year AND company_code=:company
        """
        )
        headers = await read(
            """
            SELECT * FROM historical_policy_headers
            WHERE import_run_id=:run AND fiscal_year=:year AND company_code=:company
        """
        )
        lines = await read(
            """
            SELECT *, COALESCE(account_code_canonical, account_code_raw) AS account_code
            FROM historical_policy_lines
            WHERE import_run_id=:run AND fiscal_year=:year AND company_code=:company
        """
        )
        _documentary_rows(
            balances,
            headers,
            lines,
            families=families,
            validation=validation,
            scope=scope,
            run_id=str(run["id"]),
        )
    except (ValueError, InvalidOperation, TypeError, KeyError):
        result["reason"] = "La fuente documental requiere validación o conciliación."
        result["gaps"].append(result["reason"])
        return result
    result.update(
        source_status="partial",
        import_run_id=str(run["id"]),
        source_sha256=run["source_sha256"],
        coverage={
            "trial_balance_months": validation["balance_months"],
            "policy_months": validation["policy_months"],
            "withheld_months": validation["withheld_months"],
        },
    )
    result["gaps"] = [
        "Cobertura parcial; los meses retenidos no se completan con versiones anteriores.",
        "Septiembre tiene pólizas; no hay balanza oficial de septiembre.",
        "Moneda, base fiscal y atribución exclusiva no acreditadas; sin comparación presupuestal.",
    ]
    by_account = {a["account_code"]: a for a in result["accounts"]}
    for row in sorted(balances, key=lambda r: r["fiscal_month"]):
        account = by_account.get(row["account_code"])
        if account is not None and row["is_detail"]:
            account["trial_balance_periods"].append(
                {
                    "month": row["fiscal_month"],
                    **{
                        k: str(row[k]) for k in ("debits", "credits", "closing_balance")
                    },
                    "source_family": "balanza",
                    "source_sha256": families["balanza"]["source_sha256"],
                }
            )
    header_flags = {str(h["id"]): h["quality_flags"] for h in headers}
    monthly: dict[tuple[str, int], dict[str, Any]] = {}
    for line in lines:
        code, month = line["account_code"], line["fiscal_month"]
        if code not in by_account:
            continue
        bucket = monthly.setdefault(
            (code, month),
            {
                "debits": Decimal(0),
                "credits": Decimal(0),
                "quality_flags": set(),
            },
        )
        bucket["debits"] += _documentary_money(line["debit_amount"])
        bucket["credits"] += _documentary_money(line["credit_amount"])
        bucket["quality_flags"].update(header_flags[str(line["policy_header_id"])])
    for (code, month), bucket in sorted(monthly.items()):
        by_account[code]["policy_periods"].append(
            {
                "month": month,
                "debits": str(bucket["debits"]),
                "credits": str(bucket["credits"]),
                "registered_net": str(bucket["debits"] - bucket["credits"]),
                "quality_flags": sorted(bucket["quality_flags"]),
                "source_family": "poliza_line",
                "source_sha256": families["poliza_line"]["source_sha256"],
            }
        )
    return result


async def build_tournament_accounting(
    session: Any,
    *,
    tournament_id: str,
    edition_year: int,
    budget_version_id: str | None = None,
) -> dict[str, Any]:
    """Keep strict attributed totals separate from independent documentary facts."""
    result = await _build_strict_tournament_accounting(
        session,
        tournament_id=tournament_id,
        edition_year=edition_year,
        budget_version_id=budget_version_id,
    )
    result["documentary"] = await build_tournament_documentary_accounting(
        session,
        tournament_id=tournament_id,
        edition_year=edition_year,
    )
    return result


async def _build_strict_tournament_accounting(
    session: Any,
    *,
    tournament_id: str,
    edition_year: int,
    budget_version_id: str | None = None,
) -> dict[str, Any]:
    """Read only the accounts approved for an already-authorized tournament."""

    def unavailable_for_year(reason: str) -> dict[str, Any]:
        result = unavailable(reason)
        result["fiscal_year"] = edition_year
        return result

    concepts = (
        (
            await session.execute(
                text(
                    """
        SELECT bc.tournament_id::text AS tournament_id, bc.concept_name,
               bc.metadata, cc.codigo AS account_code
        FROM budget_concepts bc
        LEFT JOIN cuentas_contables cc ON cc.id = bc.cuenta_contable_id
            AND cc.activo = TRUE
        WHERE bc.tournament_id = :tournament_id AND bc.active = TRUE
    """
                ),
                {"tournament_id": tournament_id},
            )
        )
        .mappings()
        .all()
    )
    try:
        bindings, gaps = _bindings(
            [dict(r) for r in concepts],
            tournament_id=tournament_id,
            edition_year=edition_year,
        )
    except ValueError:
        return unavailable_for_year(
            "Vínculos contables incompatibles: requieren conciliación."
        )
    if not bindings:
        result = unavailable_for_year(
            "Falta acreditar empresa, cuenta y alcance contable del torneo."
        )
        result["gaps"] = gaps
        return result
    ready = (
        await session.execute(
            text(
                """
        SELECT to_regclass('historical_trial_balance_rows') IS NOT NULL
           AND to_regclass('historical_policy_lines') IS NOT NULL
           AND to_regclass('historical_accounting_source_files') IS NOT NULL AS ready
    """
            )
        )
    ).scalar_one()
    if not ready:
        return unavailable_for_year("La fuente histórica no está disponible.")
    periods: dict[int, dict[str, Decimal]] = defaultdict(
        lambda: {"income": Decimal(0), "expense": Decimal(0)}
    )
    accounts = []
    runs = {}
    for binding in bindings:
        company = binding["company_code"]
        if company not in runs:
            runs[company] = await _find_latest_applied_import_run(
                session, fiscal_year=edition_year, company_code=company
            )
        run = runs[company]
        if not run or not run.get("source_sha256"):
            gaps.append("Sin importación aplicada para una cuenta vinculada.")
            continue
        params = {
            "year": edition_year,
            "company": company,
            "run": run["id"],
            "account": binding["account_code"],
        }
        exclusive = binding["attribution"] == "exclusive_account"
        if exclusive:
            # A binding elsewhere prevents claiming the whole account as exclusive.
            other_concepts = (
                (
                    await session.execute(
                        text(
                            """
                SELECT bc.metadata
                FROM budget_concepts bc
                JOIN cuentas_contables cc ON cc.id = bc.cuenta_contable_id
                WHERE bc.active = TRUE
                  AND bc.tournament_id IS DISTINCT FROM :tournament_id
                  AND cc.codigo = :account
            """
                        ),
                        {**params, "tournament_id": tournament_id},
                    )
                )
                .mappings()
                .all()
            )
            shared = any(
                _shares_account(row.get("metadata"), year=edition_year, company=company)
                for row in other_concepts
            )
            if shared:
                gaps.append(
                    "Cuenta compartida: falta separación por proyecto "
                    "o centro de costo."
                )
                continue
            rows = (
                (
                    await session.execute(
                        text(
                            """
                SELECT tb.fiscal_month, tb.debits, tb.credits, tb.closing_balance
                FROM historical_trial_balance_rows tb
                JOIN historical_accounting_source_files sf ON sf.id = tb.source_file_id
                  AND sf.validation_status = 'OK' AND sf.source_sha256 IS NOT NULL
                WHERE tb.import_run_id = :run AND tb.fiscal_year = :year
                  AND tb.company_code = :company
                  AND COALESCE(tb.account_code_canonical, tb.account_code_raw)
                      = :account
                  AND tb.is_detail = TRUE
                ORDER BY tb.fiscal_month
            """
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
        else:
            column = (
                "project_raw"
                if binding["attribution"] == "project"
                else "cost_center_raw"
            )
            params["scope"] = binding[column]
            rows = (
                (
                    await session.execute(
                        text(
                            f"""
                SELECT pl.fiscal_month, SUM(pl.debit_amount) AS debits,
                       SUM(pl.credit_amount) AS credits, NULL AS closing_balance
                FROM historical_policy_lines pl
                JOIN historical_policy_headers ph ON ph.id = pl.policy_header_id
                JOIN historical_accounting_source_files sf ON sf.id = pl.source_file_id
                  AND sf.validation_status = 'OK' AND sf.source_sha256 IS NOT NULL
                WHERE pl.import_run_id = :run AND pl.fiscal_year = :year
                  AND pl.company_code = :company
                  AND COALESCE(pl.account_code_canonical, pl.account_code_raw)
                      = :account
                  AND pl.{column} = :scope AND ph.is_balanced = TRUE
                  AND COALESCE(ph.quality_flags, '[]'::jsonb) = '[]'::jsonb
                GROUP BY pl.fiscal_month ORDER BY pl.fiscal_month
            """
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
        months = [int(r["fiscal_month"]) for r in rows]
        if (
            not rows
            or len(months) != len(set(months))
            or any(m < 1 or m > 12 for m in months)
        ):
            gaps.append("Sin cobertura o con duplicados en una cuenta vinculada.")
            continue
        try:
            values = [
                Decimal(str(row[k]))
                for row in rows
                for k in ("debits", "credits", "closing_balance")
                if row.get(k) is not None
            ]
            if any(not value.is_finite() for value in values):
                raise ValueError("Invalid accounting amount")
        except (InvalidOperation, ValueError):
            gaps.append("Importes contables inválidos: requieren conciliación.")
            continue
        amount = Decimal(0)
        kind = binding["metric_kind"]
        for row in rows:
            if kind == "balance":
                continue
            net = Decimal(str(row["debits"])) - Decimal(str(row["credits"]))
            if kind == "income":
                net = -net
            periods[int(row["fiscal_month"])][kind] += net
            amount += net
        accounts.append(
            {
                "account_code": binding["account_code"],
                "company_code": company,
                "metric_kind": kind,
                "amount": str(amount) if kind != "balance" else None,
                "closing_balance": (
                    str(rows[-1]["closing_balance"]) if exclusive else None
                ),
                "closing_month": months[-1] if exclusive else None,
                "months": months,
                "attribution": binding["attribution"],
                "currency": binding["currency"],
                "tax_basis": binding["tax_basis"],
                "source_sha256": run["source_sha256"],
                "evidence_ref": binding["evidence_ref"],
            }
        )
    if not accounts:
        result = unavailable_for_year(
            "No hay importes atribuibles con cobertura acreditada."
        )
        result["gaps"] = gaps
        return result
    compatible = len({(a["currency"], a["tax_basis"]) for a in accounts}) == 1
    if not compatible:
        gaps.append("Moneda o base fiscal incompatible: no se agregan importes.")
    months = sorted(set(m for a in accounts for m in a["months"]))
    kind_months = {
        k: [set(a["months"]) for a in accounts if a["metric_kind"] == k]
        for k in ("income", "expense")
    }
    totals = {}
    for kind, sets in kind_months.items():
        consistent = bool(sets) and all(s == sets[0] for s in sets)
        totals[kind] = (
            str(sum((p[kind] for p in periods.values()), Decimal(0)))
            if compatible and consistent
            else None
        )
    result = {
        "source_status": (
            "partial"
            if gaps or any(len(a["months"]) < 12 for a in accounts)
            else "available"
        ),
        "source": "historical_accounting",
        "read_only": True,
        "fiscal_year": edition_year,
        "accounts": accounts,
        "coverage_months": months,
        "gaps": gaps,
        "income": totals["income"],
        "expense": totals["expense"],
        "closing_balances": None,
        "periods": (
            [
                {
                    "month": month,
                    **{
                        k: (
                            str(p[k])
                            if totals[k] is not None and month in kind_months[k][0]
                            else None
                        )
                        for k in ("income", "expense")
                    },
                }
                for month, p in sorted(periods.items())
            ]
            if compatible
            else []
        ),
        "currency": accounts[0]["currency"] if compatible else None,
        "tax_basis": accounts[0]["tax_basis"] if compatible else None,
    }

    result["comparison"] = {
        "variance": None,
        "budget": None,
        "reason": "Falta cobertura comparable y base fiscal acreditada.",
    }
    if (
        budget_version_id
        and result["expense"] is not None
        and not gaps
        and all(
            len(a["months"]) == 12 for a in accounts if a["metric_kind"] == "expense"
        )
    ):
        budget_rows = (
            (
                await session.execute(
                    text(
                        """
            SELECT l.id, l.budget_version_id, l.tournament_id,
                   l.budget_concept_id, l.phase, l.budget_amount, l.metadata
            FROM budget_lines l JOIN budget_versions v ON v.id = l.budget_version_id
            WHERE l.tournament_id = :tournament_id AND l.budget_version_id = :version_id
              AND v.edition_year = :year
              AND COALESCE(l.line_direction, 'expense') = 'expense'
        """
                    ),
                    {
                        "tournament_id": tournament_id,
                        "version_id": budget_version_id,
                        "year": edition_year,
                    },
                )
            )
            .mappings()
            .all()
        )
        from samchat.budgets.source_provenance import resolve_budget_line_source

        budget_sources = [resolve_budget_line_source(r) for r in budget_rows]
        basis_matches = bool(budget_sources) and all(
            source["status"] in {"declared", "verified"}
            and source["currency"] == result["currency"]
            and source["tax_basis"] == result["tax_basis"]
            for source in budget_sources
        )
        if basis_matches:
            total = sum(
                (Decimal(str(r["budget_amount"])) for r in budget_rows), Decimal(0)
            )
            result["comparison"] = {
                "budget": str(total),
                "variance": str(Decimal(result["expense"]) - total),
                "reason": None,
                "period": "full_year",
            }
    return result
