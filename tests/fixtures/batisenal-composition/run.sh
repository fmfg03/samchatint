#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
: "${PILOT_WORK:?prepared new fixture root required}"
: "${PILOT_PY:?isolated Python required}"
PG_BIN=/usr/lib/postgresql/16/bin
CHECKOUT="$PILOT_WORK/c"
cd "$CHECKOUT"
test "$(id -u)" != 0
test ! -e /tmp/samchat-pilot-env-absent
DATA="$PWD/artifacts/pilot/integration/pgdata"
SOCKET="$PWD/artifacts/pilot/integration/socket"
test ! -e "$DATA"; test ! -e "$SOCKET"
mkdir -p "$SOCKET"; chmod 700 "$SOCKET"
cleanup() {
  if [[ -f "$DATA/postmaster.pid" ]]; then
    "$PG_BIN/pg_ctl" -D "$DATA" -m fast -w stop
  fi
}
trap cleanup EXIT
"$PG_BIN/postgres" --version
"$PG_BIN/initdb" -D "$DATA" -U agent --auth-local=trust --auth-host=reject --encoding=UTF8 --locale=C
"$PG_BIN/pg_ctl" -D "$DATA" -l "$PILOT_WORK/postgres.log" \
 -o "-c listen_addresses='' -c unix_socket_directories='$SOCKET' -c unix_socket_permissions=0700" -w start
[[ "$("$PG_BIN/psql" -h "$SOCKET" -U agent -d postgres -Atc 'SHOW listen_addresses')" == '' ]]
mkdir -p artifacts/pilot/ci-results
env -i PATH=/usr/local/bin:/usr/bin:/bin \
 PYTHONPATH=src:.:tests/batisenal:tests/batisenal_pilot \
 SAMCHAT_ENV=test SAMCHAT_ENV_FILE=/tmp/samchat-pilot-env-absent \
 EXPENSE_REPORT_RECEIPT_BLOB_COLUMN=archivo_data \
 BATISENAL_NATIVE_PILOT_TEST=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
 COVERAGE_FILE=artifacts/pilot/ci-results/.coverage \
 "$PILOT_PY" -m coverage run --concurrency=greenlet --source=src \
 artifacts/pilot/application/review/offline_native.py \
 -p pytest_asyncio.plugin -o asyncio_mode=auto -o addopts='' \
 tests/batisenal_pilot/test_case_http.py \
 tests/batisenal_pilot/test_case_authority.py \
 tests/batisenal_pilot/test_case_operations.py \
 tests/batisenal_pilot/test_application.py \
 tests/batisenal_pilot/test_identity_binding_composition.py \
 -q --tb=short --junitxml=artifacts/pilot/ci-results/results.xml
COVERAGE_FILE=artifacts/pilot/ci-results/.coverage "$PILOT_PY" -m coverage json -o artifacts/pilot/ci-results/coverage.json
"$PILOT_PY" "$ROOT/verify_results.py" artifacts/pilot/ci-results
