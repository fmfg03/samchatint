"""Explicit operator-run, metadata-only AMEX statement migration readiness check."""

import argparse
import asyncio
import json
import os
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from devnous.gastos.services.amex_statement_schema_service import (
    inspect_amex_statement_schema,
)


async def inspect_schema(database_url: str) -> dict[str, Any]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                return await inspect_amex_statement_schema(connection)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url-env", default="AMEX_STATEMENT_SCHEMA_DATABASE_URL"
    )
    args = parser.parse_args()
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        parser.error(
            "Supply the explicitly authorized database URL environment variable"
        )
    result = asyncio.run(inspect_schema(database_url))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
