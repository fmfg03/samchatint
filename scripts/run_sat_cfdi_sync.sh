#!/usr/bin/env bash
set -euo pipefail

# SamChat SAT massive CFDI sync.
# Intended crontab (America/Mexico_City):
#   0 9,23 * * * /root/samchat/scripts/run_sat_cfdi_sync.sh
#
# Required environment:
#   SAT_SYNC_SECRET       shared secret sent as X-SAT-Sync-Secret
#   SAT_SYNC_BASE_URL     defaults to https://sam.chat
#   SAT_SYNC_MODE         defaults to auto
#   SAT_USE_PRODUCTION    must be true on the app server for live SAT endpoints

BASE_URL="${SAT_SYNC_BASE_URL:-https://sam.chat}"
MODE="${SAT_SYNC_MODE:-auto}"
SECRET="${SAT_SYNC_SECRET:-}"

if [[ -z "${SECRET}" ]]; then
  echo "SAT_SYNC_SECRET is required" >&2
  exit 64
fi

curl -fsS -X POST \
  -H "X-SAT-Sync-Secret: ${SECRET}" \
  "${BASE_URL%/}/ingress/sat-cfdi-sync?mode=${MODE}"
