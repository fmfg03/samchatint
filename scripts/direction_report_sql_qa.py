"""Verify report-link SQL with synthetic records on a temporary socket-only PostgreSQL.

Requires requirements-private-plugin-postgres.txt. No external DSN or credentials.
"""

import ast
from collections import Counter
import importlib.util
import json
import pathlib
import subprocess
import tempfile

from sqlalchemy import URL, create_engine, text

root = pathlib.Path(__file__).resolve().parents[1]
tree = ast.parse((root / "src/samchat/budgets/service.py").read_text())
fn = next(
    n
    for n in tree.body
    if isinstance(n, ast.FunctionDef) and n.name == "_budget_expense_base_amount_sql"
)
ns = {"_safe_str": str}
exec(compile(ast.Module(body=[fn], type_ignores=[]), "helper", "exec"), ns)
tree = ast.parse((root / "src/samchat/budgets/executive_facts.py").read_text())
expr = next(
    n
    for n in ast.walk(tree)
    if isinstance(n, ast.JoinedStr)
    and any(
        isinstance(v, ast.Constant) and "SELECT e.id" in str(v.value) for v in n.values
    )
)
query = eval(compile(ast.Expression(expr), "query", "eval"), ns)
document_query = next(
    n.value
    for n in ast.walk(tree)
    if isinstance(n, ast.Constant)
    and isinstance(n.value, str)
    and "SELECT d.id::text AS id" in n.value
)
bins = (
    pathlib.Path(importlib.util.find_spec("pgserver").origin).parent / "pginstall/bin"
)
with tempfile.TemporaryDirectory(prefix="direction-links-") as tmp:
    p = pathlib.Path(tmp)
    sock = p / "socket"
    sock.mkdir()

    def cmd(name, *args):
        subprocess.run(
            [str(bins / name), *map(str, args)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    cmd(
        "initdb",
        "-D",
        p / "data",
        "-U",
        "fixture",
        "--auth-local=trust",
        "--auth-host=reject",
        "--no-locale",
        "--encoding=UTF8",
    )
    cmd(
        "pg_ctl",
        "-D",
        p / "data",
        "-l",
        p / "log",
        "-o",
        f"-h '' -k {sock}",
        "-w",
        "start",
    )
    try:
        eng = create_engine(
            URL.create(
                "postgresql+psycopg",
                username="fixture",
                database="postgres",
                query={"host": str(sock)},
            )
        )
        with eng.begin() as c:
            c.execute(
                text(
                    "CREATE TABLE documentos(id uuid primary key,torneo_id uuid,tipo text,cuenta_gastos_id uuid,creado_en timestamp,currency text,estado text,pagado_en timestamp,monto_total numeric,monto_solicitado numeric,concepto_pago text,gasto_generado_id uuid)"
                )
            )
            c.execute(
                text(
                    "CREATE TABLE expense_reports(id uuid primary key,documento_id uuid,solicitud_documento_id uuid,informe_documento_id uuid,cuenta_gastos_id uuid,fecha date,currency text,gasto_cantidad numeric,iva numeric,hospedaje_impuesto_monto numeric,propina_no_deducible numeric,cfdi_compartido_confirmado boolean,cfdi_report_id uuid,estado_gasto text)"
                )
            )
            c.execute(
                text(
                    "CREATE TABLE cfdi_reports(id uuid,subtotal numeric,descuento numeric,total numeric)"
                )
            )
            c.execute(
                text(
                    "CREATE TABLE adjuntos(gasto_id uuid,categoria text,activo boolean)"
                )
            )
            c.execute(
                text(
                    "CREATE TABLE cuentas_de_gastos(id uuid primary key,torneo_id uuid)"
                )
            )
            uid = lambda n: f"00000000-0000-0000-0000-{n:012d}"
            c.execute(
                text("INSERT INTO cuentas_de_gastos VALUES (:id,:tid)"),
                {"id": uid(20), "tid": uid(100)},
            )
            for n, acct, tid in [
                (1, 10, 100),
                (2, 20, None),
                (3, 30, 100),
                (4, 30, 100),
                (5, 40, 200),
            ]:
                c.execute(
                    text(
                        "INSERT INTO documentos(id,torneo_id,tipo,cuenta_gastos_id,creado_en) VALUES (:id,:tid,'INFORME',:acct,'2026-06-01')"
                    ),
                    dict(id=uid(n), tid=uid(tid) if tid else None, acct=uid(acct)),
                )
            for n, tid in [(6, 100), (7, 200)]:
                c.execute(
                    text(
                        "INSERT INTO documentos(id,torneo_id,tipo,creado_en) VALUES (:id,:tid,'SOLICITUD','2026-06-01')"
                    ),
                    dict(id=uid(n), tid=uid(tid)),
                )
            for n, legacy, report, acct in [
                (11, 1, None, None),
                (12, None, 1, None),
                (13, None, None, 20),
                (14, None, None, 30),
                (15, None, 5, None),
                (16, 7, 1, None),
                (17, 6, None, None),
                (18, 7, None, None),
            ]:
                c.execute(
                    text(
                        "INSERT INTO expense_reports(id,documento_id,informe_documento_id,cuenta_gastos_id,fecha,currency,gasto_cantidad,iva,estado_gasto) VALUES (:id,:legacy,:report,:acct,'2026-06-10','MXN',116,16,'activo')"
                    ),
                    dict(
                        id=uid(n),
                        legacy=uid(legacy) if legacy else None,
                        report=uid(report) if report else None,
                        acct=uid(acct) if acct else None,
                    ),
                )
            for n, tid in [(8, 100), (9, 200), (10, 100)]:
                c.execute(
                    text(
                        "INSERT INTO documentos(id,torneo_id,tipo,creado_en,gasto_generado_id) VALUES (:id,:tid,'SOLICITUD','2026-06-01',:expense)"
                    ),
                    dict(id=uid(n), tid=uid(tid), expense=uid(21) if n == 10 else None),
                )
            for n, request in [(19, 8), (20, 9), (21, None)]:
                c.execute(
                    text(
                        "INSERT INTO expense_reports(id,solicitud_documento_id,fecha,currency,gasto_cantidad,iva,estado_gasto) VALUES (:id,:request,'2026-06-10','MXN',116,16,'activo')"
                    ),
                    dict(id=uid(n), request=uid(request) if request else None),
                )
            for n, legacy, explicit in [(22, 7, 8), (23, 6, 9)]:
                c.execute(
                    text(
                        "INSERT INTO expense_reports(id,documento_id,solicitud_documento_id,fecha,currency,gasto_cantidad,iva,estado_gasto) VALUES (:id,:legacy,:explicit,'2026-06-10','MXN',116,16,'activo')"
                    ),
                    dict(id=uid(n), legacy=uid(legacy), explicit=uid(explicit)),
                )
            rows = (
                c.execute(
                    text(query),
                    dict(
                        ids=[uid(100)],
                        start="2026-06-01",
                        end="2026-06-30",
                        limit=10001,
                    ),
                )
                .mappings()
                .all()
            )
            assert {r["id"] for r in rows} == {
                uid(n) for n in [11, 12, 13, 16, 17, 19, 21, 22]
            }, rows
            assert sum(r["base_amount"] for r in rows) == 800
            for account, tid in [(60, 100), (70, 200)]:
                c.execute(
                    text("INSERT INTO cuentas_de_gastos VALUES (:id,:tid)"),
                    dict(id=uid(account), tid=uid(tid)),
                )
                c.execute(
                    text(
                        "INSERT INTO expense_reports(id,cuenta_gastos_id,fecha,currency,gasto_cantidad,iva,estado_gasto) VALUES (:id,:account,'2026-06-10','MXN',116,16,'activo')"
                    ),
                    dict(id=uid(account + 1), account=uid(account)),
                )
            account_only = (
                c.execute(
                    text(query),
                    dict(
                        ids=[uid(100)],
                        start="2026-06-01",
                        end="2026-06-30",
                        limit=10001,
                    ),
                )
                .mappings()
                .all()
            )
            assert {r["id"] for r in account_only} == {r["id"] for r in rows} | {
                uid(61)
            }
            assert sum(r["base_amount"] for r in account_only) == 900
            for n, state, paid in [
                (51, "rechazado", "2026-06-15"),
                (52, "cancelado", "2026-06-15"),
                (53, "rechazado", None),
                (54, "enviado", None),
            ]:
                c.execute(
                    text(
                        "INSERT INTO documentos(id,torneo_id,tipo,creado_en,currency,estado,pagado_en,monto_total) VALUES (:id,:tid,'SOLICITUD','2026-06-01','MXN',:state,:paid,75)"
                    ),
                    dict(id=uid(n), tid=uid(100), state=state, paid=paid),
                )
            c.execute(
                text("INSERT INTO cuentas_de_gastos VALUES (:id,:tid)"),
                dict(id=uid(50), tid=uid(200)),
            )
            for n, account, direct in [
                (55, 20, None),
                (56, 50, None),
                (57, 50, 100),
                (58, 20, 200),
            ]:
                c.execute(
                    text(
                        "INSERT INTO documentos(id,torneo_id,cuenta_gastos_id,tipo,creado_en,currency,estado,monto_total) VALUES (:id,:direct,:account,'SOLICITUD','2026-06-01','MXN','pagado',75)"
                    ),
                    dict(
                        id=uid(n),
                        direct=uid(direct) if direct else None,
                        account=uid(account),
                    ),
                )
            paid_rows = (
                c.execute(
                    text(document_query),
                    dict(
                        ids=[uid(100)],
                        start="2026-06-01",
                        end="2026-06-30",
                        limit=10001,
                        committed_states=[
                            "enviado",
                            "aprobado",
                            "en_proceso_pago",
                            "pagado",
                            "cerrado",
                            "reembolsado",
                            "aplicado",
                            "liquidado",
                        ],
                    ),
                )
                .mappings()
                .all()
            )
            assert {r["id"] for r in paid_rows} == {
                uid(51),
                uid(52),
                uid(54),
                uid(55),
                uid(57),
            }
            for scoped_query in (query, document_query):
                bounded = (
                    c.execute(
                        text(scoped_query),
                        dict(
                            ids=[uid(100), uid(200)],
                            start="2026-06-01",
                            end="2026-06-30",
                            limit=2,
                            committed_states=["enviado", "pagado"],
                        ),
                    )
                    .mappings()
                    .all()
                )
                assert Counter(r["tournament_id"] for r in bounded) == {
                    uid(100): 2,
                    uid(200): 2,
                }
            print(
                json.dumps(
                    {
                        "legacy_link": True,
                        "direct_request_fallback": True,
                        "explicit_request_link": True,
                        "explicit_request_precedence_and_foreign_exclusion": True,
                        "generated_expense_link": True,
                        "submitted_request_commitment": True,
                        "report_link": True,
                        "unique_account": True,
                        "account_tournament_fallback": True,
                        "request_account_tournament_fallback": True,
                        "request_direct_scope_precedence": True,
                        "documentary_partition_limits": True,
                        "account_only_expenses_and_foreign_exclusion": True,
                        "paid_timestamp_overrides_stale_state": True,
                        "ambiguous_account_excluded": True,
                        "foreign_scope_excluded": True,
                        "explicit_report_precedence": True,
                        "fiscal_base_total": "900.00",
                        "synthetic_only": True,
                    }
                )
            )
        eng.dispose()
    finally:
        cmd("pg_ctl", "-D", p / "data", "-m", "immediate", "-w", "stop")
