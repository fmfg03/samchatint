"""Verify Treasury-certified bank statements for the scoped Direction report."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
DAY = timedelta(days=1)
FIELDS = ("opening", "origins_total", "applications_total", "closing")
SOURCE_UNAVAILABLE = "Pendiente de una fuente bancaria acreditada para este alcance."
NO_COVERAGE = "Falta cobertura bancaria continua hasta el cierre seleccionado."
NO_OPENING = "Falta saldo de apertura bancario acreditado para el inicio seleccionado."
INVALID_SOURCE = "La evidencia bancaria requiere revisión de Tesorería."

# The account hash is a server-side join key. Never include it or the original
# statement data in a report, even within a signed context.
_READ_STATEMENTS = text("""
    WITH RECURSIVE eligible AS MATERIALIZED (
        SELECT c.*
        FROM public.direction_bank_statement_certifications c
        JOIN public.client_executive_portfolios p
          ON p.id = c.portfolio_id AND p.active
        WHERE c.portfolio_id = ANY(CAST(:portfolio_ids AS uuid[]))
          AND c.tournament_ids @> CAST(:tournament_ids AS uuid[])
          AND c.tournament_ids <@ CAST(:tournament_ids AS uuid[])
          AND cardinality(c.tournament_ids) = :tournament_count
          AND NOT EXISTS (
              SELECT 1 FROM unnest(c.tournament_ids) AS tournament(id)
              WHERE NOT EXISTS (
                  SELECT 1 FROM public.client_executive_portfolio_tournaments pt
                  WHERE pt.portfolio_id = p.id AND pt.tournament_id = tournament.id
                    AND pt.active
              )
          )
          AND c.coverage_start <= :end_date
          AND c.revoked_at IS NULL AND c.certified_at <= statement_timestamp()
    ), needed(id) AS (
        SELECT id FROM eligible WHERE coverage_end >= :prior_day
        UNION
        SELECT earlier.id FROM needed n
        JOIN eligible later ON later.id = n.id
        JOIN eligible earlier ON earlier.account_id = later.account_id
          AND earlier.account_fingerprint = later.account_fingerprint
          AND earlier.portfolio_id = later.portfolio_id
          AND earlier.company_id = later.company_id
          AND earlier.coverage_end = later.coverage_start - 1
        WHERE later.opening_balance IS NULL
    )
    SELECT c.*, r.source_sha256 AS live_file_sha,
           NOT EXISTS (
               SELECT 1 FROM public.direction_bank_statement_certifications other
               WHERE other.account_fingerprint = c.account_fingerprint
                 AND other.coverage_start <= c.coverage_end
                 AND other.coverage_end >= c.coverage_start
                 AND other.revoked_at IS NULL
                 AND other.certified_at <= statement_timestamp()
                 AND (other.account_id, other.company_id, other.currency,
                      other.portfolio_id, other.tournament_ids) IS DISTINCT FROM
                     (c.account_id, c.company_id, c.currency,
                      c.portfolio_id, c.tournament_ids)
           ) AS uncontested_attribution,
           NOT EXISTS (
               SELECT 1 FROM public.accounting_import_runs later
               WHERE later.source_type = 'banco' AND later.mode = 'apply'
                 AND later.status = 'completed' AND later.filename = r.filename
                 AND (later.started_at, later.id) > (r.started_at, r.id)
           ) AS current_run,
           m.id AS movement_id, m.source_row_number, m.fecha, m.hora,
           m.signo, m.importe, m.saldo, m.cuenta_bancaria,
           to_jsonb(m)->>'company_id' AS movement_company_id,
           to_jsonb(m)->>'bank_account_id' AS movement_account_id,
           to_jsonb(m)->>'currency' AS movement_currency
    FROM eligible c JOIN needed n ON n.id = c.id
    JOIN public.accounting_import_runs r ON r.id = c.import_run_id
      AND r.source_type = 'banco' AND r.mode = 'apply' AND r.status = 'completed'
    LEFT JOIN public.bank_movements m ON m.import_run_id = r.id
      AND encode(sha256(convert_to(m.cuenta_bancaria, 'UTF8')), 'hex')
        = c.account_fingerprint
    ORDER BY c.coverage_start, c.id, m.source_row_number
    """)


def _cents(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
        rounded = parsed.quantize(CENT)
        return rounded if parsed.is_finite() and parsed == rounded else None
    except InvalidOperation:
        return None


def _day(value: date | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def bank_series_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    """Fingerprint the source rows used in certification, in ordinal order."""
    return _hash(
        [
            [
                row[key]
                for key in (
                    "source_row_number",
                    "fecha",
                    "hora",
                    "signo",
                    "importe",
                    "saldo",
                )
            ]
            for row in sorted(rows, key=lambda item: item["source_row_number"])
        ]
    )


@dataclass(frozen=True)
class _Movement:
    day: date
    delta: Decimal
    balance: Decimal


@dataclass(frozen=True)
class _Statement:
    start: date
    end: date
    opening: Decimal | None
    closing: Decimal
    movements: tuple[_Movement, ...]
    receipt: str

    def balance_on(self, day: date) -> Decimal | None:
        return next(
            (item.balance for item in reversed(self.movements) if item.day <= day),
            self.opening,
        )


def _source_movements(
    certificate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> tuple[_Movement, ...]:
    if (
        not certificate["uncontested_attribution"]
        or not certificate["current_run"]
        or certificate["live_file_sha"] != certificate["source_sha256"]
        or bank_series_sha256(rows) != certificate["series_sha256"]
    ):
        raise ValueError(INVALID_SOURCE)
    ordinals = {row["source_row_number"] for row in rows}
    if len(ordinals) != len(rows) or any(n < 2 for n in ordinals):
        raise ValueError(INVALID_SOURCE)
    movements = []
    previous_day = certificate["coverage_start"]
    for row in sorted(
        rows,
        key=lambda item: item["source_row_number"],
        reverse=certificate["descending_rows"],
    ):
        day = _day(row["fecha"])
        importe, saldo = _cents(row["importe"]), _cents(row["saldo"])
        if (
            day is None
            or not previous_day <= day <= certificate["coverage_end"]
            or importe is None
            or importe < ZERO
            or saldo is None
            or row["signo"] not in {"+", "-"}
            or str(row["cuenta_bancaria"])[-4:] != certificate["account_last4"]
            or any(
                row[row_key] is not None and row[row_key] != str(certificate[cert_key])
                for row_key, cert_key in (
                    ("movement_company_id", "company_id"),
                    ("movement_account_id", "account_id"),
                    ("movement_currency", "currency"),
                )
            )
        ):
            raise ValueError(INVALID_SOURCE)
        movements.append(
            _Movement(day, importe if row["signo"] == "+" else -importe, saldo)
        )
        previous_day = day
    return tuple(movements)


def _verified_timeline(
    batches: Sequence[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
) -> list[_Statement]:
    timeline: list[_Statement] = []
    identities = set()
    for cert, rows in batches:
        movements = _source_movements(cert, rows)
        start, end = cert["coverage_start"], cert["coverage_end"]
        opening, closing = _cents(cert["opening_balance"]), _cents(
            cert["closing_balance"]
        )
        identity = (
            start,
            end,
            opening,
            closing,
            cert["series_sha256"],
            cert["descending_rows"],
        )
        if identity in identities:
            continue
        previous = timeline[-1] if timeline else None
        if previous and start <= previous.end:
            raise ValueError(INVALID_SOURCE)
        if previous and start == previous.end + DAY:
            if opening is not None and opening != previous.closing:
                raise ValueError(INVALID_SOURCE)
            opening = previous.closing

        # The first row can check the internal series when only a closing
        # anchor is available. Its implicit opening never becomes a report value.
        running = opening
        if running is None and movements:
            running = movements[0].balance - movements[0].delta
        if running is not None:
            for movement in movements:
                running += movement.delta
                if running != movement.balance:
                    raise ValueError(INVALID_SOURCE)
        if closing is not None and running is not None and closing != running:
            raise ValueError(INVALID_SOURCE)
        if closing is None and movements and opening is not None:
            closing = running
        if closing is None:
            raise ValueError(INVALID_SOURCE)
        timeline.append(
            _Statement(
                start, end, opening, closing, movements, str(cert["evidence_ref"])
            )
        )
        identities.add(identity)
    return timeline


def _account_period(
    timeline: Sequence[_Statement], start: date, end: date
) -> tuple[dict[str, Decimal | None], dict[str, str], set[str]]:
    selected = [item for item in timeline if item.end >= start and item.start <= end]
    prior = next((item for item in timeline if item.end == start - DAY), None)
    opening = prior.closing if prior else None
    receipts = {prior.receipt} if prior else set()
    if selected and selected[0].start <= start:
        first = selected[0]
        receipts.add(first.receipt)
        opening = first.balance_on(start - DAY)
    values = dict.fromkeys(FIELDS)
    values["opening"] = opening
    gaps = {} if opening is not None else {"opening": NO_OPENING}
    cutoff = start - DAY
    for statement in selected:
        if statement.start > cutoff + DAY:
            break
        cutoff = statement.end
        receipts.add(statement.receipt)
        if cutoff >= end:
            break
    if cutoff < end or opening is None:
        gaps.update(
            dict.fromkeys(FIELDS[1:], NO_COVERAGE if cutoff < end else NO_OPENING)
        )
        return values, gaps, receipts

    deltas = [
        m.delta for item in selected for m in item.movements if start <= m.day <= end
    ]
    incoming = sum((n for n in deltas if n > ZERO), ZERO)
    outgoing = -sum((n for n in deltas if n < ZERO), ZERO)
    last = selected[-1]
    closing = last.closing if last.end == end else last.balance_on(end)
    if closing != opening + incoming - outgoing:
        raise ValueError(INVALID_SOURCE)
    values.update(origins_total=incoming, applications_total=outgoing, closing=closing)
    return values, gaps, receipts


def unavailable_bank_period(
    start: date, end: date, reason: str = SOURCE_UNAVAILABLE
) -> dict:
    """Return an explicit selected period, preserving unavailable values."""
    return {
        "periods": [
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                **dict.fromkeys(FIELDS),
                "gaps": dict.fromkeys(FIELDS, reason),
                "source": "Tesorería · estados de cuenta bancarios",
                "scope_label": None,
            }
        ],
        "gap": reason,
        "revision": None,
    }


def _project(rows: Sequence[Mapping[str, Any]], start: date, end: date) -> dict:
    if not rows:
        return unavailable_bank_period(start, end)
    if any(row["currency"] != "MXN" for row in rows):
        return unavailable_bank_period(
            start, end, "La evidencia bancaria del alcance requiere validación en MXN."
        )

    batches: dict[Any, tuple[Mapping[str, Any], list[Mapping[str, Any]]]] = {}
    for row in rows:
        _, movements = batches.setdefault(row["id"], (row, []))
        if row["movement_id"] is not None:
            movements.append(row)
    by_account = defaultdict(list)
    for cert, movements in batches.values():
        by_account[cert["account_id"]].append((cert, movements))

    expected = {frozenset(row["scope_account_ids"] or ()) for row in rows}
    scopes = {(row["portfolio_id"], row["company_id"]) for row in rows}
    fingerprints = {(row["account_id"], row["account_fingerprint"]) for row in rows}
    if (
        len(scopes) != 1
        or len(expected) != 1
        or len(fingerprints) != len(by_account)
        or len({fingerprint for _, fingerprint in fingerprints}) != len(by_account)
    ):
        return unavailable_bank_period(start, end, INVALID_SOURCE)

    expected_accounts = next(iter(expected))
    if (expected_accounts and expected_accounts != by_account.keys()) or (
        not expected_accounts and len(by_account) != 1
    ):
        return unavailable_bank_period(
            start, end, "Falta acreditar el conjunto completo de cuentas del alcance."
        )

    try:
        account_values = [
            _account_period(_verified_timeline(items), start, end)
            for items in by_account.values()
        ]
    except ValueError:
        return unavailable_bank_period(start, end, INVALID_SOURCE)
    values: dict[str, str | None] = {}
    gaps: dict[str, str] = {}
    for field in FIELDS:
        parts = [item[0][field] for item in account_values]
        known = [part for part in parts if part is not None]
        if len(known) == len(parts):
            values[field] = str(sum(known, ZERO))
        else:
            values[field] = None
            gaps[field] = next(
                item[1][field] for item in account_values if field in item[1]
            )
    masked = ", ".join(sorted({f"•••• {row['account_last4']}" for row in rows}))
    receipts = sorted(set().union(*(item[2] for item in account_values)))
    projection = {
        "periods": [
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                **values,
                "gaps": gaps,
                "scope_label": (
                    "Total bancario del alcance acreditado"
                    if expected_accounts
                    else f"Cuenta bancaria {masked}"
                ),
                "source": (
                    f"Tesorería · estados de cuenta {masked} · evidencia {', '.join(receipts)}"
                    if receipts
                    else "Tesorería · estados de cuenta bancarios"
                ),
            }
        ],
        "gap": next(iter(gaps.values()), None),
        "revision": None,
    }
    # The signed context may describe authorized evidence even when the
    # selected period has no complete monetary values.
    identity_fields = (
        "import_run_id",
        "source_sha256",
        "series_sha256",
        "company_id",
        "account_id",
        "account_fingerprint",
        "currency",
        "portfolio_id",
        "tournament_ids",
        "scope_account_ids",
        "coverage_start",
        "coverage_end",
        "opening_balance",
        "closing_balance",
        "descending_rows",
        "evidence_ref",
        "certified_by",
        "certified_at",
    )
    projection["revision"] = _hash(
        [
            projection,
            sorted(
                (str(cert["id"]), [cert.get(key) for key in identity_fields])
                for cert, _ in batches.values()
            ),
        ]
    )
    return projection


async def load_direction_bank_period(
    session: AsyncSession, *, scope: Mapping[str, Any], start: date, end: date
) -> dict:
    """Read only certificates for the exact authorized portfolio and selection."""
    portfolio_ids = scope["portfolio_ids"]
    tournament_ids = sorted({str(item["id"]) for item in scope["selected"]})
    if not portfolio_ids or not tournament_ids:
        return unavailable_bank_period(start, end)
    try:
        async with session.begin_nested():
            installed = await session.scalar(
                text(
                    "SELECT to_regclass('public.direction_bank_statement_certifications') IS NOT NULL"
                )
            )
            if not installed:
                return unavailable_bank_period(start, end)
            result = await session.execute(
                _READ_STATEMENTS,
                {
                    "portfolio_ids": portfolio_ids,
                    "tournament_ids": tournament_ids,
                    "tournament_count": len(tournament_ids),
                    "prior_day": start - DAY,
                    "end_date": end,
                },
            )
            return _project([dict(row) for row in result.mappings()], start, end)
    except SQLAlchemyError:
        return unavailable_bank_period(
            start, end, "La fuente bancaria no está disponible en este momento."
        )
