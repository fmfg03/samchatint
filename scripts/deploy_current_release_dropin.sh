#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/deploy_current_release_dropin.sh <release-dir>

Example:
  scripts/deploy_current_release_dropin.sh /srv/samchat/releases/gastos-prod-bd8a71e9f-ownerpack-readiness

This script makes samchat-gastos.service use exactly one active systemd drop-in:
  /etc/systemd/system/samchat-gastos.service.d/50-current-release.conf

All previous *.conf drop-ins are archived before the canonical file is written.
USAGE
}

if [[ $# -ne 1 ]]; then
  usage
  exit 64
fi

release="$1"
venv="${SAMCHAT_RUNTIME_VENV:-/srv/samchat/venvs/baseline-db08f745e8da7a82}"
unit_dir="${SAMCHAT_SYSTEMD_DROPIN_DIR:-/etc/systemd/system/samchat-gastos.service.d}"
archive_root="${SAMCHAT_DROPIN_ARCHIVE_ROOT:-/srv/samchat/release-cleanup-audit}"
canonical="$unit_dir/50-current-release.conf"

case "$release" in
  /srv/samchat/releases/gastos-prod-*) ;;
  *)
    echo "Refusing unsafe release path: $release" >&2
    exit 65
    ;;
esac

if [[ ! -f "$release/copa_telmex_dashboard.py" ]]; then
  echo "Release does not look like SamChat gastos runtime: $release" >&2
  exit 66
fi

if [[ ! -x "$venv/bin/python" ]]; then
  echo "Runtime Python not found: $venv/bin/python" >&2
  exit 67
fi

"$venv/bin/python" "$release/scripts/ci/check-registration-operational-surface.py" --root "$release"

mkdir -p "$unit_dir" "$archive_root"
archive="$archive_root/dropins-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$archive"

find "$unit_dir" -maxdepth 1 -type f -name '*.conf' -print0 | while IFS= read -r -d '' file; do
  mv "$file" "$archive/$(basename "$file")"
done

cat > "$canonical" <<EOF
[Service]
WorkingDirectory=$release
EnvironmentFile=
EnvironmentFile=-/etc/samchat/samchat.env
EnvironmentFile=-/etc/samchat/zaubern-registration.env
Environment=
Environment=PATH=$venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=PYTHONPATH=$release/src:$release
Environment=SAMCHAT_ENV_FILE=/etc/samchat/samchat.env
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=CTT_CANONICAL_PROMOTION=off
Environment=ASSISTANT_AGENT_RUNTIME_ENABLED=true
Environment=ASSISTANT_AGENT_RUNTIME_READONLY_ONLY=true
Environment=ASSISTANT_AGENT_RUNTIME_EMPLOYEE_IDS=73847177-aca1-4348-8f1b-709a8cd8b432,b8816679-ad77-4590-83d5-50ffce335854
Environment=ASSISTANT_AGENT_WRITES_ENABLED=false
Environment=ASSISTANT_AGENT_SHADOW_ENABLED=false
Environment=ASSISTANT_AGENT_PROVIDER_TIMEOUT_SECONDS=15
Environment=ASSISTANT_AGENT_RUNTIME_TOTAL_BUDGET_SECONDS=25
Environment=ASSISTANT_AGENT_PROVIDER_MAX_CONCURRENCY=2
Environment=ASSISTANT_ANALYST_LIVE_EVIDENCE_ENABLED=true
Environment=ASSISTANT_ANALYST_LIVE_EVIDENCE_EMPLOYEE_IDS=73847177-aca1-4348-8f1b-709a8cd8b432,b8816679-ad77-4590-83d5-50ffce335854
Environment=ASSISTANT_ANALYST_CASE_PERSISTENCE_ENABLED=true
Environment=ASSISTANT_ANALYST_CASE_PERSISTENCE_EMPLOYEE_IDS=73847177-aca1-4348-8f1b-709a8cd8b432,b8816679-ad77-4590-83d5-50ffce335854
Environment=ASSISTANT_CAPABILITY_NEGOTIATION_ENABLED=true
Environment=ASSISTANT_CAPABILITY_NEGOTIATION_EMPLOYEE_IDS=73847177-aca1-4348-8f1b-709a8cd8b432,b8816679-ad77-4590-83d5-50ffce335854
Environment=ASSISTANT_RECEIPT_WORKFLOW_WRITES_ENABLED=false
Environment=ASSISTANT_RECEIPT_WORKFLOW_EMPLOYEE_IDS=73847177-aca1-4348-8f1b-709a8cd8b432,b8816679-ad77-4590-83d5-50ffce335854
Environment=ASSISTANT_READONLY_WORKSPACE_ENABLED=true
Environment=ASSISTANT_READONLY_WORKSPACE_EMPLOYEE_IDS=73847177-aca1-4348-8f1b-709a8cd8b432,b8816679-ad77-4590-83d5-50ffce335854
Environment=ASSISTANT_READONLY_WORKSPACE_ROOT=/srv/samchat/workspaces/assistant-readonly-rqf048
Environment=ASSISTANT_TASK_WORKSPACE_MUTATIONS_ENABLED=true
Environment=ASSISTANT_TASK_WORKSPACE_EMPLOYEE_IDS=73847177-aca1-4348-8f1b-709a8cd8b432,b8816679-ad77-4590-83d5-50ffce335854
Environment=ASSISTANT_TASK_WORKSPACE_ROOT=/srv/samchat/workspaces/assistant-task-rqf049
Environment=ASSISTANT_TASK_WORKSPACE_TTL_SECONDS=86400
ExecStartPre=
ExecStartPre=$venv/bin/python $release/scripts/ci/check-registration-operational-surface.py --root $release
ExecStart=
ExecStart=$venv/bin/python -m uvicorn copa_telmex_dashboard:app --host 127.0.0.1 --port 8000
EOF

ln -sfn "$release" /srv/samchat/current
systemctl daemon-reload
systemctl restart samchat-gastos.service
sleep 6

systemctl is-active samchat-gastos.service >/dev/null
curl -fsS http://127.0.0.1:8000/healthz >/dev/null
curl -fsS http://127.0.0.1:8000/readyz >/dev/null

echo "deployed_release=$release"
echo "active_dropin=$canonical"
echo "archived_dropins=$archive"
systemctl show samchat-gastos.service -p WorkingDirectory -p NRestarts --no-pager
