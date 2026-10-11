"""Read-only tournament accounting with explicit, evidenced attribution.

Bindings live in canonical budget-concept metadata. Nothing is inferred from
account names, and account-level balances require approved exclusive ownership.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
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


async def build_tournament_accounting(
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
            SELECT l.budget_amount, l.metadata
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
        basis_matches = bool(budget_rows) and all(
            (r.get("metadata") or {}).get("upload_source", {}).get("currency")
            == result["currency"]
            and (r.get("metadata") or {}).get("upload_source", {}).get("tax_basis")
            == result["tax_basis"]
            for r in budget_rows
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
