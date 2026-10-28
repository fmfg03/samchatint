# Native PostgreSQL JSON binding test compatibility

CI on a43ececee passed6692unit cases but16new JSONB tests incorrectly required a non-null bind_processor. All strict JSON assertions passed before that assumption. Classification: task-caused test assumption; no runtime failure. SQLAlchemy2.1.6 changes the default PostgreSQL dialect to psycopg3 with native JSON serialization; returningNone is its valid binding contract. The local2.0.52 default psycopg2 returns a callable. This is not metadata corruption; the SQLite fixture builds a separate Table.

The tests now explicitly exercise psycopg2 and psycopg, require the actual AssistantRun.tool_trace column to be JSONB, and require the native-JSON flag when no processor is returned. Both paths still serialize/parse strictly and reject NaN/Infinity; no skip, relaxed data comparison or source change. Additional tests cover both binding paths locally. Existing legacy tests remain AST-identical.49adaptercases and89consumercases pass locally;49cases also pass in a disposable process simulating the exact official native condition (not an installation of2.1.6). Full CI must confirm real2.1.6 after publication.

266acceptance cases passed,91.68%measuredcoverage; all eight runtime hashes equal review-corrections.json/a43ececee. This commit changes tests/evidence only.

Primary implementation evidence: [default dialect](https://raw.githubusercontent.com/sqlalchemy/sqlalchemy/rel_2_1_6/lib/sqlalchemy/dialects/postgresql/__init__.py), [native JSON flag](https://raw.githubusercontent.com/sqlalchemy/sqlalchemy/rel_2_1_6/lib/sqlalchemy/dialects/postgresql/psycopg.py), [JSON bind processor](https://raw.githubusercontent.com/sqlalchemy/sqlalchemy/rel_2_1_6/lib/sqlalchemy/sql/sqltypes.py).
