"""Synthetic batch/catalog SQL QA on an ephemeral socket-only PostgreSQL.

No external DSN, credentials, schema migration or production access. Run with
requirements-private-plugin-postgres.txt. Output contains fixture counts only.
"""

import ast
import asyncio
import hashlib
import importlib.util
import json
import subprocess
import tempfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID
from weakref import WeakKeyDictionary

from sqlalchemy import URL, create_engine, text

ROOT = Path(__file__).resolve().parents[1]
ns = {
    "text": text,
    "Any": object,
    "ClientExecutiveAccessError": PermissionError,
    "json": json,
    "OrderedDict": OrderedDict,
    "_CATALOG_METADATA": WeakKeyDictionary(),
}
tree = ast.parse((ROOT / "src/samchat/client_executive/service.py").read_text())
selected = [
    n
    for n in tree.body
    if isinstance(n, ast.AsyncFunctionDef)
    and n.name
    in {
        "authorized_direction_catalog_page",
        "authorized_direction_portfolio_ids",
        "_authorized_tournaments",
    }
    or isinstance(n, ast.Assign)
    and any(
        isinstance(t, ast.Name) and t.id == "DIRECTION_POSITION_KEYS" for t in n.targets
    )
]
exec(
    compile(ast.Module(body=selected, type_ignores=[]), "canonical-service", "exec"), ns
)
spec = importlib.util.spec_from_file_location(
    "batch", ROOT / "src/samchat/client_executive/read_batches.py"
)
batch_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch_module)


class Session:
    def __init__(self, connection):
        self.connection = connection
        self.calls = 0
        self.returned = 0
        self.metadata_aggregates = 0

    def get_bind(self):
        return self.connection.engine

    async def execute(self, statement, params=None):
        self.calls += 1
        if "WITH revision AS MATERIALIZED" in str(statement):
            plan = self.connection.execute(
                text("EXPLAIN (ANALYZE, FORMAT JSON) " + str(statement)), params
            ).scalar_one()[0]["Plan"]

            def walk(node, fresh=False):
                fresh = fresh or node.get("Subplan Name") == "CTE fresh"
                if (
                    fresh
                    and node["Node Type"] == "Aggregate"
                    and node.get("Strategy") == "Plain"
                ):
                    self.metadata_aggregates += node["Actual Loops"]
                for child in node.get("Plans", []):
                    walk(child, fresh)

            walk(plan)
        result = self.connection.execute(statement, params or {})
        self.returned += max(result.rowcount, 0)
        return result

    @asynccontextmanager
    async def begin_nested(self):
        with self.connection.begin_nested():
            yield


async def verify(connection):
    session = Session(connection)
    for ddl in (
        "CREATE TABLE tournaments(id uuid PRIMARY KEY, name text, active boolean)",
        "CREATE TABLE client_executive_portfolios(id uuid PRIMARY KEY, label text, active boolean)",
        "CREATE TABLE authorization_position_assignments(empleado_id uuid, position_key text, active boolean)",
        "CREATE TABLE client_executive_portfolio_positions(portfolio_id uuid, position_key text, active boolean)",
        "CREATE TABLE client_executive_portfolio_tournaments(portfolio_id uuid, tournament_id uuid, active boolean)",
    ):
        connection.execute(text(ddl))
    evidence = []
    actor = str(UUID(int=999999))
    for count in [1, 25, 100, 1001]:
        connection.execute(text("TRUNCATE tournaments, client_executive_portfolios"))
        ids = [str(UUID(int=n + 1)) for n in range(count)]
        values = [{"id": tid, "name": "Synthetic"} for tid in ids]
        connection.execute(
            text("INSERT INTO tournaments VALUES (:id,:name,TRUE)"), values
        )
        connection.execute(
            text("INSERT INTO client_executive_portfolios VALUES (:id,:name,TRUE)"),
            values,
        )
        # Not active, so never part of counts, digest, page or canonical baseline.
        connection.execute(
            text("INSERT INTO tournaments VALUES (:id,'Inactive',FALSE)"),
            {"id": str(UUID(int=999998))},
        )
        connection.commit()  # Fixture setup is separate from read-only measurements.
        connection.execute(text("SET TRANSACTION READ ONLY"))
        start_calls = session.calls
        start_aggregates = session.metadata_aggregates
        fetched = returned = 0
        after_p = after_t = None
        for offset in range(0, count, 25):
            page = await ns["authorized_direction_catalog_page"](
                session,
                actor,
                is_superadmin=True,
                offset=offset,
                after_portfolio=after_p,
                after_tournament=after_t,
            )
            rechecked = await ns["authorized_direction_catalog_page"](
                session,
                actor,
                is_superadmin=True,
                offset=offset,
                after_portfolio=after_p,
                after_tournament=after_t,
            )
            assert page == rechecked
            assert page["portfolio_count"] == page["tournament_count"] == count
            assert [t["id"] for t in page["tournaments"]] == ids[offset : offset + 25]
            assert [p["id"] for p in page["portfolios"]] == ids[offset : offset + 25]
            assert len(page["tournaments"]) <= 25 and len(page["portfolios"]) <= 25
            assert page["portfolio_ids"] == (ids if count <= 200 else [])
            expected = {
                "portfolio_id": None,
                "portfolio_ids": ids,
                "tournament_id": None,
                "tournament_ids": ids,
            }
            assert (
                page["scope_digest"]
                == hashlib.sha256(
                    json.dumps(expected, sort_keys=True).encode()
                ).hexdigest()
            )
            assert len(json.dumps(page)) < 16000
            after_p = page["portfolios"][-1]["id"] if page["portfolios"] else after_p
            after_t = page["tournaments"][-1]["id"] if page["tournaments"] else after_t
            fetched += len(page["tournaments"])
            returned += 2 * (len(page["tournaments"]) + len(page["portfolios"]))
        assert fetched == count
        queries = session.calls - start_calls
        assert queries == 2 * ((count + 24) // 25)
        assert returned == 4 * count
        metadata_aggregates = session.metadata_aggregates - start_aggregates
        assert metadata_aggregates == 2, metadata_aggregates
        before_calls, before_rows = session.calls, session.returned

        async def read_tournament(batch, tid):
            await batch.execute(text("SELECT :edition AS edition"), {"edition": 2026})
            return (
                (
                    await batch.execute(
                        text(
                            "SELECT id::text AS id, name FROM tournaments WHERE id = CAST(:id AS uuid) ORDER BY name LIMIT 1"
                        ),
                        {"id": tid},
                    )
                )
                .mappings()
                .first()
            )

        projected = []
        for offset in range(0, count, 25):
            batch = batch_module.ReadBatch(session)
            projected.extend(
                await asyncio.gather(
                    *(read_tournament(batch, tid) for tid in ids[offset : offset + 25])
                )
            )
            await batch.close()
        assert [row["id"] for row in projected] == ids
        assert session.calls - before_calls == 2 * ((count + 24) // 25)
        assert session.returned - before_rows == count + ((count + 24) // 25)
        evidence.append(
            {
                "tournaments": count,
                "catalog_queries_with_recheck": queries,
                "sql_batch_queries": session.calls - before_calls,
                "sql_batch_rows_returned": session.returned - before_rows,
                "returned_catalog_items_with_recheck": returned,
                "metadata_aggregates_executed": metadata_aggregates,
                "metadata_note": "EXPLAIN ANALYZE proves two full-scope ID aggregates once per unchanged MVCC revision, zero on subsequent pages/rechecks; keyset pages skip no prior rows.",
            }
        )
        connection.commit()
    # Compare actual role-scoped canonical readers, including duplicate position
    # assignments, inactive memberships and a foreign active tournament.
    connection.execute(
        text(
            "INSERT INTO authorization_position_assignments VALUES (:actor,'direccion_general',TRUE),(:actor,'direccion_general',TRUE)"
        ),
        {"actor": actor},
    )
    connection.execute(
        text(
            "INSERT INTO client_executive_portfolio_positions VALUES (:p,'direccion_general',TRUE)"
        ),
        {"p": ids[0]},
    )
    connection.execute(
        text(
            "INSERT INTO client_executive_portfolio_tournaments VALUES (:p,:t,TRUE),(:p,:foreign,FALSE)"
        ),
        {"p": ids[0], "t": ids[1], "foreign": ids[2]},
    )
    page = await ns["authorized_direction_catalog_page"](
        session, actor, is_superadmin=False, offset=0
    )
    canonical_p = await ns["authorized_direction_portfolio_ids"](
        session, actor, is_superadmin=False
    )
    canonical_t = await ns["_authorized_tournaments"](
        session, actor, is_superadmin=False
    )
    assert page["portfolio_ids"] == sorted(canonical_p) == [ids[0]]
    assert (
        [r["id"] for r in page["tournaments"]]
        == [r["id"] for r in canonical_t]
        == [ids[1]]
    )
    assert await ns["authorized_direction_portfolio_ids"](
        session, actor, is_superadmin=True, limit=1
    )
    old_digest = page["scope_digest"]
    connection.execute(
        text(
            "UPDATE client_executive_portfolio_tournaments SET active=TRUE WHERE tournament_id=:id"
        ),
        {"id": ids[2]},
    )
    assert (
        await ns["authorized_direction_catalog_page"](
            session, actor, is_superadmin=False, offset=0
        )
    )["scope_digest"] != old_digest
    try:
        await ns["authorized_direction_catalog_page"](
            session, str(UUID(int=999997)), is_superadmin=False, offset=0
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("Foreign identity admitted")
    # Actual PostgreSQL UNION transport: bound UUID/date/numeric results, casts,
    # ordering, limit, source isolation after division-by-zero and live connection.
    batch = batch_module.ReadBatch(session)

    async def query(n):
        return (
            (
                await batch.execute(
                    text(
                        "SELECT CAST(:id AS uuid) AS id, CAST(:amount AS numeric) AS amount, 100 / CAST(:denominator AS integer) AS value"
                    ),
                    {"id": str(UUID(int=n + 1)), "amount": "1.25", "denominator": n},
                )
            )
            .mappings()
            .first()
        )

    values = await asyncio.gather(
        *(query(n) for n in [1, 0, 2]), return_exceptions=True
    )
    assert (
        str(values[0]["id"]) == str(UUID(int=2)) and str(values[0]["amount"]) == "1.25"
    )
    assert isinstance(values[1], Exception) and values[2]["value"] == 50
    assert (await query(3))["value"] == 33
    return {
        "synthetic_only": True,
        "catalog_scaling": evidence,
        "role_scope_parity": True,
        "revocation_digest": True,
        "postgres_batch_types_and_failure_isolation": True,
    }


async def verify_revision_cache(engine):
    actor = str(UUID(int=999999))
    reader = engine.connect()
    writer = engine.connect()
    try:
        reader.execute(text("SET TRANSACTION READ ONLY"))
        session = Session(reader)
        first = await ns["authorized_direction_catalog_page"](
            session, actor, is_superadmin=False, offset=0
        )
        assert first["portfolio_count"] == 1
        first["portfolio_ids"].clear()
        reloaded = await ns["authorized_direction_catalog_page"](
            session, actor, is_superadmin=False, offset=0
        )
        assert len(reloaded["portfolio_ids"]) == 1
        assert session.metadata_aggregates == 2, session.metadata_aggregates
        writer.execute(
            text(
                "UPDATE client_executive_portfolio_tournaments SET active=FALSE WHERE tournament_id=:id"
            ),
            {"id": str(UUID(int=2))},
        )
        before_commit = await ns["authorized_direction_catalog_page"](
            session, actor, is_superadmin=False, offset=0
        )
        assert before_commit["scope_digest"] == reloaded["scope_digest"]
        writer.commit()
        changed = await ns["authorized_direction_catalog_page"](
            session, actor, is_superadmin=False, offset=0
        )
        assert changed["tournament_count"] == reloaded["tournament_count"] - 1
        assert changed["scope_digest"] != reloaded["scope_digest"]
        aggregates = session.metadata_aggregates
        assert (
            await ns["authorized_direction_catalog_page"](
                session, actor, is_superadmin=False, offset=0
            )
            == changed
        )
        assert session.metadata_aggregates == aggregates
        admin = await ns["authorized_direction_catalog_page"](
            session, actor, is_superadmin=True, offset=0
        )
        assert admin["tournament_count"] == 1001
        assert (
            await ns["authorized_direction_catalog_page"](
                session, actor, is_superadmin=False, offset=0
            )
            == changed
        )
        writer.execute(
            text(
                "UPDATE authorization_position_assignments SET active=FALSE WHERE empleado_id=:id"
            ),
            {"id": actor},
        )
        writer.commit()
        try:
            await ns["authorized_direction_catalog_page"](
                session, actor, is_superadmin=False, offset=0
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("Cached scope survived committed role revocation")
        try:
            await ns["authorized_direction_catalog_page"](
                session, str(UUID(int=888888)), is_superadmin=False, offset=0
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("Cached scope escaped actor isolation")
    finally:
        reader.close()
        writer.close()
    return True


bins = Path(importlib.util.find_spec("pgserver").origin).parent / "pginstall/bin"
with tempfile.TemporaryDirectory(prefix="direction-batch-") as tmp:
    root = Path(tmp)
    socket = root / "socket"
    socket.mkdir()

    def command(name, *args):
        subprocess.run(
            [str(bins / name), *map(str, args)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    command(
        "initdb",
        "-D",
        root / "data",
        "-U",
        "fixture",
        "--auth-local=trust",
        "--auth-host=reject",
        "--no-locale",
        "--encoding=UTF8",
    )
    command(
        "pg_ctl",
        "-D",
        root / "data",
        "-l",
        root / "log",
        "-o",
        f"-h '' -k {socket}",
        "-w",
        "start",
    )
    engine = None
    try:
        engine = create_engine(
            URL.create(
                "postgresql+psycopg",
                username="fixture",
                database="postgres",
                query={"host": str(socket)},
            )
        )
        with engine.connect() as connection:
            evidence = asyncio.run(verify(connection))
            connection.commit()
        evidence["cache_commit_revocation_actor_role_and_mutation_isolation"] = (
            asyncio.run(verify_revision_cache(engine))
        )
        with engine.begin() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            before = connection.execute(
                text("SELECT COUNT(*) FROM tournaments")
            ).scalar_one()

            async def read_only_check():
                batch = batch_module.ReadBatch(Session(connection))
                try:
                    await batch.execute(
                        text(
                            "WITH forbidden AS (DELETE FROM tournaments RETURNING id) SELECT id FROM forbidden"
                        )
                    )
                except Exception:
                    pass
                else:
                    raise AssertionError("Read-only transaction allowed a write")
                await batch.close()

            asyncio.run(read_only_check())
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM tournaments")
                ).scalar_one()
                == before
            )
            evidence["database_read_only_enforced"] = True
        print(json.dumps(evidence, indent=2))
    finally:
        if engine is not None:
            engine.dispose()
        command("pg_ctl", "-D", root / "data", "-m", "immediate", "-w", "stop")
