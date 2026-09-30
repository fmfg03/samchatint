"""Deterministic, evidence-bound executive interpretation and what-if arithmetic.

Every calculation consumes the signed home snapshot. No new financial source,
causal classifier, approval or write owner is introduced.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .home import TZ, amount, format_money


class AnalysisError(ValueError):
    """The requested assumption or scenario cannot be verified."""


def normalized(value: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(c)
    )


def scenario_from_question(question: str, previous: dict | None = None) -> dict | None:
    """Parse only explicit percentages/days; never invent a missing assumption."""
    text = normalized(question)
    pct = re.search(r"(?<![\d.,-])(\d+(?:[.,]\d+)?)\s*(?:%|por ciento)", text)
    days = re.search(r"(?<![\d-])(\d+)\s*dias?\b", text)
    spec = dict(((previous or {}).get("scenario") or {}).get("assumptions") or {})
    if any(
        w in text
        for w in ("reduce", "reduci", "reduz", "reduccion", "recorta", "recorte")
    ):
        spec.update(kind="expense_reduction")
        spec.setdefault("basis", "observed_expense")
    elif any(w in text for w in ("aceler", "anticipar cob", "cobramos", "cobrar")):
        spec.update(kind="collection_acceleration")
        spec.pop("concept_id", None)
    elif any(w in text for w in ("difer", "difier", "reprogram", "pospon", "aplaz")):
        spec.update(kind="payment_delay")
        spec.pop("concept_id", None)
    elif not (spec and (pct or days)):
        return None
    if pct:
        spec["percent"] = pct.group(1).replace(",", ".")
    if days:
        spec["days"] = int(days.group(1))
    return spec


def calculate_scenario(snapshot: dict, spec: dict) -> dict:
    """Return a labelled hypothetical result, leaving all observed values intact."""
    spec = {k: v for k, v in spec.items() if v is not None}
    kind = spec.get("kind")
    if kind not in {"expense_reduction", "collection_acceleration", "payment_delay"}:
        raise AnalysisError(
            "Selecciona reducción de gasto, aceleración de cobro o diferimiento de pagos."
        )
    if kind != "expense_reduction" and spec.get("concept_id"):
        raise AnalysisError(
            "El concepto solo aplica a una reducción de gasto observado."
        )
    rows = snapshot["tournaments"]
    target = spec.get("tournament_id")
    if target:
        rows = [r for r in rows if r["id"] == target]
    if not rows:
        raise AnalysisError("El escenario requiere un torneo del contexto autorizado.")
    labels = ", ".join(r["name"] for r in rows)
    notes = ["Supuesto del usuario; no modifica registros ni constituye autorización."]
    if kind == "payment_delay":
        days = spec.get("days")
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 365:
            raise AnalysisError("Indica un diferimiento entre 0 y 365 días.")
        cut = datetime.fromisoformat(snapshot["as_of"]).astimezone(TZ).date()
        baseline = Decimal("0")
        result = Decimal("0")
        for row in rows:
            value = amount(row["values"].get("obligations"))
            evidence = row.get("payment_evidence") or []
            amounts = [amount(e.get("value")) for e in evidence]
            if (
                value is None
                or any(v is None for v in amounts)
                or sum(amounts, Decimal("0")) != value
            ):
                raise AnalysisError(
                    "Obligaciones sin detalle completo conciliado; no se simula el calendario."
                )
            baseline += value
            for item in evidence:
                try:
                    due = date.fromisoformat(item["date"]) + timedelta(days=days)
                except (ValueError, KeyError) as exc:
                    raise AnalysisError(
                        "Fecha programada sin evidencia verificable."
                    ) from exc
                if cut <= due <= cut + timedelta(days=30):
                    result += amount(item["value"])
        formula = "Σ obligaciones de la ventana actual que permanecen a 30 días tras diferir sus fechas"
        title = "Calendario hipotético de obligaciones"
        notes += [
            "Solo difiere las obligaciones actualmente visibles a 30 días; no reduce la deuda.",
            "Requiere acuerdo con beneficiarios; no demuestra disponibilidad bancaria.",
        ]
        effect = baseline - result
    else:
        percent = amount(spec.get("percent"))
        if percent is None or not 0 <= percent <= 100:
            raise AnalysisError("Indica un porcentaje explícito entre 0 y 100.")
        spec["percent"] = str(percent)
        field = "receivables" if kind == "collection_acceleration" else "actual"
        if kind == "expense_reduction":
            basis = spec.setdefault("basis", "observed_expense")
            if basis not in {"observed_expense", "future_expense"}:
                raise AnalysisError(
                    "Selecciona gasto observado o gasto futuro estimado."
                )
            if spec.get("concept_id"):
                if len(rows) != 1 or basis != "observed_expense":
                    raise AnalysisError(
                        "Un concepto requiere un torneo y base de gasto observado."
                    )
                evidence = rows[0].get("concepts") or {}
                found = next(
                    (
                        c
                        for c in evidence.get("rows", [])
                        if c["id"] == spec["concept_id"]
                    ),
                    None,
                )
                if evidence.get("status") != "available" or found is None:
                    raise AnalysisError(
                        "El concepto no tiene un desglose conciliado en este alcance."
                    )
                values = [amount(found["actual"])]
                labels += " · " + found["label"]
            elif basis == "future_expense":
                values = []
                for row in rows:
                    forecast, actual = amount(row["values"].get("forecast")), amount(
                        row["values"].get("actual")
                    )
                    values.append(
                        max(forecast - actual, Decimal("0"))
                        if forecast is not None and actual is not None
                        else None
                    )
                notes.append(
                    "Base reducible asumida: cierre mecánico menos ejercido. No acredita gasto discrecional ni compromisos cancelables."
                )
            else:
                values = [amount(r["values"].get(field)) for r in rows]
            title = "Gasto hipotético tras reducción"
            notes.append(
                "Contrafactual sobre gasto observado; el ejercido real no cambia."
                if basis == "observed_expense"
                else "Sensibilidad del gasto futuro estimado; no es un nuevo presupuesto ni un pronóstico causal."
            )
        else:
            values = [amount(r["values"].get(field)) for r in rows]
            title = "Saldo hipotético tras acelerar cobranza"
            notes += [
                "Se supone cobro aceptado del porcentaje indicado dentro de 30 días.",
                "No acredita un cobro real ni equivale a liquidez disponible; validar contratos y conciliación.",
            ]
        if any(v is None or v < 0 for v in values):
            raise AnalysisError(
                "Falta una base completa, no negativa y acreditada para este escenario."
            )
        baseline = sum(values, Decimal("0"))
        effect = (baseline * percent / 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        result = baseline - effect
        formula = "impacto = base × porcentaje / 100; resultado = base - impacto"
    return {
        "title": title,
        "scope_label": labels,
        "base": str(baseline),
        "effect": str(effect),
        "result": str(result),
        "unit": "MXN",
        "formula": formula,
        "assumptions": spec,
        "limits": notes,
        "snapshot_id": snapshot["snapshot_id"],
        "read_only": True,
    }


def deviations(snapshot: dict) -> tuple[list[dict], list[str]]:
    """Keep actual overspend separate from projected annual excess."""
    items, gaps = [], []
    for row in snapshot["tournaments"]:
        budget, actual = amount(row["values"].get("budget")), amount(
            row["values"].get("actual")
        )
        if budget is not None and actual is not None and actual > budget:
            items.append(
                {
                    "tournament": row["name"],
                    "tournament_id": row["id"],
                    "label": "Ejercido por encima del presupuesto anual",
                    "basis": "actual",
                    "amount": str(actual - budget),
                    "source": "Presupuesto aprobado y gastos del intervalo",
                }
            )
        elif budget is None or actual is None:
            gaps.append(f"{row['name']}: sin base conciliada para evaluar exceso real.")
        concepts = row.get("concepts") or {}
        if concepts.get("status") == "available":
            for item in concepts["rows"]:
                if amount(item["excess"]) > 0:
                    items.append(
                        {
                            "tournament": row["name"],
                            "tournament_id": row["id"],
                            "label": item["label"],
                            "basis": "concept",
                            "amount": item["excess"],
                            "source": "Desglose conciliado de presupuesto y gastos",
                        }
                    )
        else:
            gaps.append(
                f"{row['name']}: {concepts.get('gap') or 'sin detalle de conceptos conciliado.'}"
            )
        excess = amount(row["values"].get("deviation"))
        if excess is not None and excess > 0:
            items.append(
                {
                    "tournament": row["name"],
                    "tournament_id": row["id"],
                    "label": "Exceso estimado anual (mecánico)",
                    "basis": "forecast",
                    "amount": str(excess),
                    "source": "Cierre mecánico y presupuesto aprobado",
                }
            )
    items.sort(key=lambda i: (-amount(i["amount"]), i["tournament"], i["label"]))
    return items, gaps


def recommendations(snapshot: dict) -> list[dict]:
    """Explain the tradeoff of each recommendation; never authorize an action."""
    items, _ = deviations(snapshot)
    result = []
    for item in items:
        result.append(
            {
                "title": f"Revisar {item['label']} · {item['tournament']}",
                "evidence": f"{format_money(item['amount'])}; {item['source']}.",
                "recommendation": "Validar las partidas y distinguir gasto indispensable de gasto reducible antes de decidir un recorte.",
                "alternative": "Mantener el alcance y someter el excedente a la autoridad correspondiente.",
                "risk": "Recortar sin validar compromisos puede afectar operación o incumplir contratos.",
                "next_step": "Finanzas y responsable del torneo: contrastar partidas, compromisos y calendario.",
                "horizon": "Revisión esta semana",
                "impact": item["amount"],
            }
        )
    for item in snapshot["priorities"]:
        if item["kind"] == "obligation":
            result.insert(
                0,
                {
                    "title": item["title"],
                    "evidence": item["detail"],
                    "recommendation": "Conciliar disponibilidad bancaria y confirmar el calendario antes del vencimiento.",
                    "alternative": "Evaluar diferimiento negociado o aceleración de cobranza verificable.",
                    "risk": "Un pago programado no acredita caja; diferir sin acuerdo genera riesgo contractual.",
                    "next_step": "Tesorería: validar saldo y fechas con los responsables.",
                    "horizon": "Antes de la primera fecha programada",
                    "impact": item["impact"],
                },
            )
    if not result:
        result.append(
            {
                "title": "Completar evidencia antes de decidir",
                "evidence": "No hay una desviación cuantificable acreditada en este contexto.",
                "recommendation": "Validar cobertura y conciliación de presupuesto, gastos, pagos y cobranza.",
                "alternative": "Mantener el seguimiento del alcance cubierto, identificándolo como parcial.",
                "risk": "Ausencia de una alerta no demuestra ausencia de riesgo.",
                "next_step": "Finanzas y Tesorería: resolver las brechas materiales.",
                "horizon": "Próxima revisión",
                "impact": None,
            }
        )
    return result


def analyze(
    snapshot: dict,
    metric: dict,
    question: str,
    *,
    spec: dict | None = None,
    previous: dict | None = None,
) -> dict:
    """Business response: conclusion, facts, interpretation, advice, next step."""
    text = normalized(question)
    missing = list(metric["gaps"])
    facts = [
        f"{metric['label']}: {format_money(metric['value'])}. {metric['definition']}",
        f"Periodo: {metric['period']}. Corte: {snapshot['as_of']}.",
        f"Cobertura: {metric['coverage']['covered']} de {metric['coverage']['total']} torneos.",
    ]
    if metric["status"] == "partial":
        missing.append(
            "Se muestra el subtotal del alcance cubierto; no representa el total de la cartera."
        )
    interpretations, hypotheses, opinions = [], [], []
    recommendation, next_step = (
        "Validar cobertura y detalle antes de decidir.",
        "Contrastar con el responsable de la fuente.",
    )
    items, item_gaps = deviations(snapshot)
    advice = recommendations(snapshot)
    requested = spec or scenario_from_question(question, previous)
    scenario = None
    report = bool(
        re.search(r"\b(reporte|informe)\b.*\b(socios|consejo|pdf|excel)\b", text)
    )
    supported = (
        bool(
            re.search(
                r"explica|esto|fuente|significa|compara|definicion|cuanto|cifra|dato|monto|evidencia|corte|por que|cerrar|cierre|pasando|desviacion|harias|recom|alternativ|escenario|que pasa|mejor",
                text,
            )
        )
        or report
        or bool(requested)
    )
    scenario_edit = bool(
        requested
        and (
            (previous or {}).get("scenario")
            or re.search(r"escenario|supuesto|alternativa", text)
        )
    )
    operational_write = bool(
        re.search(
            r"\b(paga|pagar|aprueba|aprobar|elimina|eliminar|factura|solicitud|registro|documento|presupuesto)\b",
            text,
        )
    )
    prohibited = bool(
        re.search(
            r"\b(paga|pagar|aprueba|aprobar|elimina|eliminar|modifica|modificar)\b",
            text,
        )
    )
    prohibited = prohibited and (operational_write or not scenario_edit)
    conclusion = "Esta cifra describe el alcance y corte seleccionados; su causa requiere evidencia de detalle."
    if prohibited:
        supported = False
        conclusion = "Esta consulta no autoriza ni ejecuta cambios en operaciones."
    elif requested:
        try:
            scenario = calculate_scenario(snapshot, requested)
            conclusion = f"{scenario['title']}: {format_money(scenario['result'])}; impacto hipotético {format_money(scenario['effect'])}."
            facts += [
                f"Base: {format_money(scenario['base'])}. Fórmula: {scenario['formula']}."
            ]
            interpretations += scenario["limits"]
            recommendation = (
                "Validar factibilidad y compromisos antes de elegir esta alternativa."
            )
            next_step = "Corrige el porcentaje o los días para comparar otro supuesto en el mismo contexto."
        except AnalysisError as exc:
            missing.append(str(exc))
            conclusion = "El escenario no tiene una base o un supuesto verificable; no se calculó un resultado."
    elif "pasando" in text or "desviacion" in text:
        conclusion = (
            f"Se detectaron {len(items)} excesos por torneo, concepto o proyección; son bases distintas y no se suman."
            if items
            else "No hay exceso cuantificable acreditado; revisa la cobertura antes de concluir que todo está dentro de presupuesto."
        )
        facts += [
            f"{i['tournament']} · {i['label']}: {format_money(i['amount'])}. Fuente: {i['source']}."
            for i in items
        ]
        missing += item_gaps
    elif "cerrar" in text or "cierre" in text:
        forecast = next(m for m in snapshot["indicators"] if m["id"] == "forecast")
        conclusion = (
            f"Cierre mecánico del alcance cubierto: {format_money(forecast['value'])}."
        )
        facts.append(
            f"Cobertura de cierre: {forecast['coverage']['covered']}/{forecast['coverage']['total']} torneos."
        )
        missing += forecast["gaps"]
        interpretations.append(
            "Método canónico: máximo entre ejercido × días del año / días transcurridos y ejercido + pendiente documental por pagar. No suma comprometido indiscriminadamente ni predice causas."
        )
        for row in snapshot["tournaments"]:
            method = row.get("forecast_method") or {}
            facts.append(
                f"{row['name']}: cierre {format_money(row['values'].get('forecast'))}; días transcurridos {method.get('elapsed_days') or 'sin dato'}, días del año {method.get('total_days') or 'sin dato'}."
            )
        missing.append(
            "Falta validar estacionalidad, eventos extraordinarios y cancelabilidad de compromisos."
        )
    elif "harias" in text or "recom" in text or "alternativ" in text:
        conclusion = advice[0]["title"]
        opinions = [
            f"{r['title']}: {r['recommendation']} Alternativa: {r['alternative']} Riesgo: {r['risk']}"
            for r in advice[:3]
        ]
        facts += [r["evidence"] for r in advice[:3]]
        recommendation, next_step = advice[0]["recommendation"], advice[0]["next_step"]
    elif "compara" in text and ("period" in text or "anterior" in text):
        conclusion = "Comparación de periodos consecutivos de igual duración y misma base documental."
        if metric["id"] not in {"actual", "committed", "paid"}:
            missing.append(
                "Este indicador es anual, una proyección o un saldo actual; no tiene cohortes equivalentes acreditadas."
            )
        else:
            for row in snapshot["tournaments"]:
                prior = row.get("previous_period") or {}
                old, current = amount(
                    prior.get("values", {}).get(metric["id"])
                ), amount(row["values"].get(metric["id"]))
                if old is None or current is None:
                    missing += [
                        f"{row['name']}: {g}"
                        for g in prior.get("gaps", ["Sin base anterior comparable."])
                    ]
                else:
                    p = prior["period"]
                    facts.append(
                        f"{row['name']}: anterior {p['start']} a {p['end']}: {format_money(old)}; actual {format_money(current)}; diferencia {format_money(current - old)}."
                    )
                    interpretations.append(
                        "La variación entre periodos no demuestra su causa."
                    )
    elif "compara" in text:
        facts += [
            f"{r['name']}: {format_money(r['values'].get(metric['id']))}."
            for r in snapshot["tournaments"]
        ]
    elif "por que" in text or "explica" in text or "esto" in text:
        facts += [
            f"{i['tournament']} · partida {i['label']}: exceso {format_money(i['amount'])}."
            for i in items
            if i["basis"] == "concept"
        ]
        missing += item_gaps
        interpretations.append(
            "Las partidas conciliadas explican la composición del importe; no acreditan la causa económica de una desviación."
        )
        hypotheses.append(
            "Hipótesis por validar: cambios de volumen, precio o calendario. Requiere contratos, cantidades, tarifas y fechas comparables."
        )
    if not supported:
        conclusion = "La petición requiere una capacidad fuera del análisis ejecutivo autorizado. No se ejecutó ninguna acción."
    sections = [
        ("Conclusión", [conclusion]),
        ("Cifras y evidencia · Hechos", facts),
        ("Interpretación", interpretations or ["Una variación no prueba una causa."]),
        ("Hipótesis", hypotheses or ["Ninguna causa adicional acreditada."]),
        ("Opinión", opinions or ["Sin opinión adicional."]),
        ("Recomendación", [recommendation]),
        ("Siguiente paso", [next_step]),
        (
            "Brechas",
            sorted(set(missing))
            or ["Conciliación y validación de negocio pendientes."],
        ),
    ]
    return {
        "conclusion": conclusion,
        "facts": facts,
        "hypotheses": hypotheses,
        "opinions": opinions,
        "interpretation": interpretations,
        "recommendation": recommendation,
        "next_step": next_step,
        "missing_evidence": sorted(set(missing)),
        "deviations": items,
        "recommendations": advice,
        "scenario": scenario,
        "report_requested": report,
        "supported": supported,
        "assistant_message": "\n\n".join(
            title + "\n" + "\n".join(values) for title, values in sections
        ),
    }
