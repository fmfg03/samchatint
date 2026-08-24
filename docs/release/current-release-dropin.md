# SamChat production release drop-in

`samchat-gastos.service` must be governed by exactly one active systemd drop-in:

```text
/etc/systemd/system/samchat-gastos.service.d/50-current-release.conf
```

Do not create new `zzzz...conf` files for deploys. That pattern caused old
partial releases to override newer consolidated releases by alphabetical order.

## Deploy rule

1. Build or create a release under `/srv/samchat/releases/gastos-prod-*`.
2. Validate the release with both release gates:
   - `scripts/ci/check-registration-operational-surface.py`
   - `scripts/ci/check-accepted-regressions.py`
3. Run:

```bash
scripts/deploy_current_release_dropin.sh /srv/samchat/releases/gastos-prod-<commit>-<label>
```

The script archives all previous active `*.conf` drop-ins under:

```text
/srv/samchat/release-cleanup-audit/dropins-<timestamp>
```

Then it writes `50-current-release.conf`, updates `/srv/samchat/current`,
restarts `samchat-gastos.service`, and checks `/healthz` and `/readyz`.

The generated drop-in also runs both gates as `ExecStartPre` checks. If a future
release drops an accepted feature marker or an operational surface guard, systemd
will refuse to start that release instead of silently serving a regressed build.

## Verification after every deploy

For a machine-readable release manifest, run:

```bash
scripts/release_manifest.py --run-gates
```

Manual verification remains:

```bash
find /etc/systemd/system/samchat-gastos.service.d -maxdepth 1 -type f -name '*.conf' -printf '%f\n'
systemctl show samchat-gastos.service -p WorkingDirectory -p NRestarts
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
```

Expected:

- only `50-current-release.conf` is active;
- `WorkingDirectory` points to the intended release;
- `NRestarts=0`;
- health and readiness are OK.

## Current correction

On 2026-08-24, the active drop-in stack was collapsed from many `zzzz...conf`
files into the single canonical drop-in above. The archived files were preserved
for forensic rollback, but they are no longer active.
