"""Synthetic transport tests: real SQL, no external database or credentials."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text

from samchat.client_executive.read_batches import ReadBatch
from samchat.tournaments_v2.services.summary_batches import SummaryBatchClient


class SQLSession:
    def __init__(self, connection):
        self.connection = connection
        self.calls = 0
        self.rows = 0

    @asynccontextmanager
    async def begin_nested(self):
        with self.connection.begin_nested():
            yield

    async def execute(self, query, params):
        self.calls += 1
        result = self.connection.execute(query, params)
        rows = result.mappings().all()
        self.rows += len(rows)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 25, 100, 1001])
async def test_sql_batches_bound_selects_rows_and_preserve_values(count):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE facts(id integer, amount numeric)"))
        connection.execute(
            text("INSERT INTO facts VALUES (:id,:amount)"),
            [{"id": n, "amount": n * 10} for n in range(count + 1)],
        )
        session = SQLSession(connection)

        async def read(batch, tid):
            version = await batch.execute(text("SELECT :year AS year"), {"year": 2026})
            assert version.mappings().first()["year"] == 2026
            result = await batch.execute(
                text(
                    "SELECT id, amount FROM facts WHERE id = :id ORDER BY amount DESC LIMIT 1"
                ),
                {"id": tid},
            )
            return result.mappings().first()

        values = []
        for offset in range(0, count, 25):
            batch = ReadBatch(session)
            values.extend(
                await asyncio.gather(
                    *(read(batch, n) for n in range(offset, min(offset + 25, count)))
                )
            )
        assert values == [{"id": n, "amount": n * 10} for n in range(count)]
        chunks = (count + 24) // 25
        assert session.calls == 2 * chunks
        assert session.rows == count + chunks
        assert all(row["id"] != count for row in values)
    engine.dispose()


@pytest.mark.asyncio
async def test_sql_batch_failure_rolls_back_and_preserves_neighbor_source():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.connection.create_function("fixture", 1, lambda n: 1 // n)
        session = SQLSession(connection)
        batch = ReadBatch(session)

        async def read(n):
            return (
                (await batch.execute(text("SELECT fixture(:n) AS value"), {"n": n}))
                .mappings()
                .first()
            )

        values = await asyncio.gather(
            *(read(n) for n in [1, 0, 2]), return_exceptions=True
        )
        assert values[0] == {"value": 1}
        assert isinstance(values[1], Exception)
        assert values[2] == {"value": 0}
        assert session.calls <= 5
        assert (await read(1)) == {"value": 1}
    engine.dispose()


class RestFixture:
    def __init__(self, rows, *, row_limit=100, page_size=1000):
        self.rows = rows
        self.config = SimpleNamespace(max_rows=row_limit, page_size=page_size)
        self.calls = self.returned = 0

    async def select_rows(self, *, filters, offset, limit, **kwargs):
        self.calls += 1
        field = next(iter(filters))
        keys = set(filters[field][4:-1].split(","))
        rows = [r for r in self.rows if r[field] in keys][offset : offset + limit]
        self.returned += len(rows)
        return rows


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 25, 100, 1001])
async def test_rest_batches_bound_requests_and_rows(count):
    ids = [str(UUID(int=n + 1)) for n in range(count + 1)]
    rows = [{"id": f"row-{n}", "tournament_id": tid} for n, tid in enumerate(ids)]
    source = RestFixture(rows)
    client = SummaryBatchClient(source)
    values = []
    for offset in range(0, count, 25):
        values.extend(
            await asyncio.gather(
                *(
                    client.fetch_all_rows(
                        table="teams", filters={"tournament_id": f"in.({tid})"}
                    )
                    for tid in ids[offset : min(offset + 25, count)]
                )
            )
        )
    assert values == [[row] for row in rows[:count]]
    assert source.calls == (count + 24) // 25
    assert source.returned == count


@pytest.mark.asyncio
async def test_rest_batches_preserve_each_partition_cap_with_skew():
    rows = [
        {"id": str(n), "tournament_id": tid}
        for n, tid in enumerate(["a"] * 80 + ["b"] * 3 + ["c"] * 8)
    ]
    source = RestFixture(rows, row_limit=5, page_size=4)
    client = SummaryBatchClient(source)
    values = await asyncio.gather(
        *(
            client.fetch_all_rows(
                table="teams", filters={"tournament_id": f"in.({tid})"}
            )
            for tid in ["a", "b", "c"]
        )
    )
    assert values == [
        [r for r in rows if r["tournament_id"] == tid][:5] for tid in ["a", "b", "c"]
    ]
    assert source.returned < 25


@pytest.mark.asyncio
async def test_canonical_soul_summary_parity_and_independent_caps():
    from samchat.client_executive import service

    ids = [str(UUID(int=n + 1)) for n in range(3)]
    tournaments = [
        {
            "id": tid,
            "name": f"Synthetic tournament {n}",
            "start_date": "2026-01-01",
            "is_active": True,
        }
        for n, tid in enumerate(ids)
    ]
    data = {
        "tournaments": tournaments,
        "categories": [],
        "teams": [],
        "registrations": [],
        "players": [],
    }
    for n, tid in enumerate(ids):
        category = f"category-{n}"
        data["categories"].append(
            {
                "id": category,
                "tournament_id": tid,
                "name": "Synthetic",
                "branch": "varonil",
            }
        )
        for k in range(8 if n == 0 else 2):
            team, registration = f"team-{n}-{k}", f"reg-{n}-{k}"
            data["teams"].append(
                {
                    "id": team,
                    "tournament_id": tid,
                    "team_name": team,
                    "state": "Synthetic",
                }
            )
            data["registrations"].append(
                {"id": registration, "team_id": team, "category_id": category}
            )
            for j in range(2):
                data["players"].append(
                    {"id": f"p-{n}-{k}-{j}", "registration_id": registration}
                )

    class Source:
        config = SimpleNamespace(max_rows=3, page_size=4)

        def __init__(self):
            self.calls = self.rows = 0

        async def select_rows(
            self, *, table, filters=None, offset=None, limit=None, **kwargs
        ):
            self.calls += 1
            values = data.get(table, [])
            if filters:
                field = next(iter(filters))
                keys = set(filters[field][4:-1].split(","))
                values = [r for r in values if str(r.get(field)) in keys]
            result = values[(offset or 0) : (offset or 0) + (limit or 1000)]
            self.rows += len(result)
            return result

        async def fetch_all_rows(self, *, max_rows=None, **kwargs):
            return await self.select_rows(
                **kwargs, limit=max_rows or self.config.max_rows
            )

    serial_source, batch_source = Source(), Source()
    expected = {}
    for t in tournaments:
        dossier = await service._build_operational_dossier(
            t, edition_year=2026, client=serial_source
        )
        assert dossier["source_status"] == "available"
        expected[t["id"]] = {
            k: dossier["summary"][k] for k in ("teams_count", "players_count")
        }
    result = await service._build_operational_summaries(
        tournaments, edition_year=2026, client=batch_source
    )
    assert {
        tid: {k: value[k] for k in ("teams_count", "players_count")}
        for tid, value in result.items()
    } == expected
    assert batch_source.calls < serial_source.calls
    assert expected[ids[0]]["players_count"] == 3
    assert expected[ids[1]]["players_count"] == 3


@pytest.mark.asyncio
async def test_sql_batch_distinct_shapes_validation_and_cancellation():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        batch = ReadBatch(SQLSession(connection))
        values = await asyncio.gather(
            batch.execute(text("SELECT :v AS first"), {"v": 1}),
            batch.execute(text("SELECT :v AS second"), {"v": 2}),
        )
        assert [r.mappings().first() for r in values] == [{"first": 1}, {"second": 2}]
        for statement in (
            "SELECT 1",
            text("DELETE FROM facts"),
            text("SELECT 1; SELECT 2"),
        ):
            with pytest.raises((TypeError, ValueError)):
                await batch.execute(statement)
        await batch.close()
    engine.dispose()
    entered, hold = asyncio.Event(), asyncio.Event()

    class Slow(SQLSession):
        async def execute(self, *args):
            entered.set()
            await hold.wait()

    batch = ReadBatch(Slow(None))

    # This test only needs an async savepoint, not the fixture connection.
    @asynccontextmanager
    async def transaction():
        yield

    batch.session.begin_nested = transaction
    pending = asyncio.create_task(batch.execute(text("SELECT 1")))
    await entered.wait()
    await batch.close()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert batch.task is None


@pytest.mark.asyncio
async def test_rest_batch_failure_scope_validation_and_cancellation():
    class Broken(RestFixture):
        async def select_rows(self, **kwargs):
            if "a" in kwargs["filters"]["tournament_id"][4:-1].split(","):
                raise RuntimeError("synthetic source failure")
            return await super().select_rows(**kwargs)

    client = SummaryBatchClient(Broken([{"id": "ok", "tournament_id": "b"}]))
    values = await asyncio.gather(
        *(
            client.fetch_all_rows(
                table="teams", filters={"tournament_id": f"in.({tid})"}
            )
            for tid in ["a", "b"]
        ),
        return_exceptions=True,
    )
    assert isinstance(values[0], RuntimeError)
    assert values[1] == [{"id": "ok", "tournament_id": "b"}]
    with pytest.raises(ValueError):
        await client.fetch_all_rows(table="teams", filters={"tournament_id": "eq.a"})
    with pytest.raises(ValueError):
        await client.select_rows(table="teams")
    await client.close()
    entered, hold = asyncio.Event(), asyncio.Event()

    class Slow(RestFixture):
        async def select_rows(self, **kwargs):
            entered.set()
            await hold.wait()

    client = SummaryBatchClient(Slow([]))
    pending = asyncio.create_task(
        client.fetch_all_rows(table="teams", filters={"tournament_id": "in.(a)"})
    )
    await entered.wait()
    await client.close()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert client.task is None
