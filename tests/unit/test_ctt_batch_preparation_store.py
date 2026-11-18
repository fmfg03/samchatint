"""Preparation transport keeps exact bytes in private transaction-owned calls."""

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from devnous.copa_telmex.staff_batch_store import BatchDenied, PostgresBatchReviewStore


class Connection:
    def __init__(self, *, result=None, error=None, nested=False):
        self.result = result or {"backend_pin": "isolated"}
        self.error, self.nested, self.calls = error, nested, []
        self.transactions = []

    def is_in_transaction(self):
        return self.nested

    @asynccontextmanager
    async def transaction(self):
        self.transactions.append("begin")
        try:
            yield
        except Exception:
            self.transactions.append("rollback")
            raise
        else:
            self.transactions.append("commit")

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        if self.error:
            raise self.error
        return self.result


class Pool:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


@pytest.mark.asyncio
async def test_exact_admission_bytes_never_become_json_authority_or_dynamic_sql():
    connection = Connection()
    store = PostgresBatchReviewStore(Pool(connection), backend_pin="isolated")
    prep = uuid4()
    raw = [b'{"one":1}', b'{"two":2}', b'{"three":3}', b'{"four":4}']
    await store.source_preparation_admit(prep, *raw, "sha256:" + "a" * 64)
    sql, args = connection.calls[0]
    assert sql == "SELECT ctt_staff_private.source_preparation_admit($1,$2,$3,$4,$5,$6)"
    assert args == (prep, *raw, "sha256:" + "a" * 64)
    assert connection.transactions == ["begin", "commit"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "context",
        "budget_report",
        "reserve",
        "finish",
        "bind_run",
        "revoke",
        "budget_revoke",
    ],
)
async def test_preparation_operations_stay_in_typed_private_functions(operation):
    prep, other = uuid4(), uuid4()
    connection = Connection()
    store = PostgresBatchReviewStore(Pool(connection), backend_pin="isolated")
    if operation == "context":
        await store.source_preparation_context(prep)
    elif operation == "budget_report":
        await store.source_preparation_budget_report(prep)
    elif operation == "reserve":
        await store.source_preparation_reserve(prep, other, 100)
    elif operation == "finish":
        await store.source_preparation_finish(prep, b'{"pages":[]}', b'{"report":1}')
    elif operation == "bind_run":
        await store.source_preparation_bind_run(
            prep, other, b'{"provider":{"pricing":{}}}'
        )
    elif operation == "revoke":
        await store.source_preparation_revoke(prep)
    else:
        await store.source_budget_revoke(other)
    sql, args = connection.calls[0]
    assert sql.startswith("SELECT ctt_staff_private.source_")
    assert args[0] == (other if operation == "budget_revoke" else prep)
    if operation == "bind_run":
        assert sql == "SELECT ctt_staff_private.source_preparation_bind_run($1,$2,$3)"
        assert args == (prep, other, b'{"provider":{"pricing":{}}}')
    assert connection.transactions == ["begin", "commit"]


@pytest.mark.asyncio
async def test_backend_mismatch_rolls_back_even_admitted_output():
    connection = Connection(result={"backend_pin": "foreign"})
    store = PostgresBatchReviewStore(Pool(connection), backend_pin="isolated")
    with pytest.raises(BatchDenied):
        await store.source_preparation_context(uuid4())
    assert connection.transactions == ["begin", "rollback"]


@pytest.mark.asyncio
async def test_nested_caller_transaction_cannot_own_preparation_side_effects():
    connection = Connection(nested=True)
    store = PostgresBatchReviewStore(Pool(connection), backend_pin="isolated")
    with pytest.raises(BatchDenied):
        await store.source_preparation_reserve(uuid4(), uuid4(), 1)
    assert connection.calls == [] and connection.transactions == []


@pytest.mark.asyncio
async def test_database_error_is_public_safe_and_rolls_back():
    connection = Connection(error=RuntimeError("private source or database details"))
    store = PostgresBatchReviewStore(Pool(connection), backend_pin="isolated")
    with pytest.raises(BatchDenied) as error:
        await store.source_preparation_finish(uuid4(), b"{}", b"{}")
    assert "private source" not in str(error.value)
    assert connection.transactions == ["begin", "rollback"]
