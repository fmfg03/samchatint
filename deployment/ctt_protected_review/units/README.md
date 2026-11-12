# Four private process templates

These files are reviewed source templates, not installed systemd units. Do not
copy an unresolved template into /etc/systemd/system. A trusted deployment step
must substitute exactly these absolute paths before syntax/ownership verification:

- `@RUNTIME_ROOT@`: immutable approved artifact containing samchat/, zaubern/
  and vendor/ (the pinned approved FIDO2 package, not /tmp).
- `@PYTHON@`: reviewed Python interpreter/venv outside /root and /tmp.
- `@CONFIG_ROOT@`: root-managed ancestor with four separate role-owned0700
  subdirectories, each with runtime.json0400/0600 and its sealed DSN/key files.
- `@DATA_ROOT@`: root-managed ancestor containing workflow/ and writer/ private
  source CAS copies (owner-only0700), bus/0700 with a NEW v2 journal, and photos/.

Substitutions must contain no whitespace, newline, systemd specifier `%`, shell
or systemd control characters. There is no shell evaluation in ExecStart. Check
rendered files with systemd-analyze verify; provisioning the four OS users,
private/shared groups, directories, numeric UID/GID config and SQL capabilities
is a separate operator action. Template existence performs none of those effects.
The ancestor directories must permit necessary traversal without exposing files.
Every referenced config/data directory must exist before a unit is started.

Users: workflow=ctt-review, issuer=ctt-review-issuer,
writer=ctt-review-writer, bus=ctt-review-bus. Distinct service SQL roles and nonce
roles remain configured in sealed runtime files; OS user names never grant SQL
faculties. Expected UID and public UID must match the actual deployment. This
threat boundary excludes host-admin/root RCE; the public SamChat UID is unchanged.

The workflow source copy belongs to ctt-review; the writer source copy belongs to
ctt-review-writer. Both retain identical verified CAS bytes, with manifests/pdfs/
pages0700 and objects0400/0600. The source reader never uses group access.
Photos belong to ctt-review:ctt-review-photos, root0750 and objects0440. Stage uses
that root's group; workflow and writer are members only of the private photos
group as needed. The writer receives read-only photos and its own source copy.
It cannot access workflow/issuer/Bus key directories, and its exact role schema
accepts no Gate/issuer/Bus signing-key field.

The writer's primary group is ctt-review-transport. systemd creates the writer's
/run/ctt-review-writer with ownerwriter, grouptransport and0710. Configure client_gid
and capture_client_gid to that numeric group; the server creates socket0660 and
checks the workflow UID allowlist, the client checks server UID/GID/modes. The
workflow only traverses/connects; it cannot unlink objects in that0710 directory.
The writer-only runtime directory is removed by systemd on stop/restart, so code
never needs to unlink an unowned stale socket. No workflow dependency on writer
startup is declared, avoiding a start-order cycle: Bus+issuer→workflow→writer.
Enable public access only after all four are independently verified healthy.

Ports come from mandatory configuration: issuer8022, workflow8023, private v2
Bus8024, each bound only to loopback. Writer has no HTTP listener. Do not reuse
v1gate8021, v1Bus8020 or /var/lib/zaubern-evidence-bus/events.jsonl. Existing v1
units/env/journal are unchanged. PrivateTmp is safe only because code, FIDO2,
config, source copies, journal and socket are all outside /tmp and /root.

ProtectSystem=strict, NoNewPrivileges, empty capabilities, read-only config/source
and exact write paths are included. Templates are not proof of effective kernel
hardening or OS isolation: verify rendered units under the actual service users,
filesystem modes, key denials, bind addresses, graceful stop and workflow smoke.
No key generation, policy adoption, invitation, source admission or team capture
is authorized by rendering a unit. Keep their independent operational records.

The optional V3 candidate adds `ctt-review-batch-workflow.service.in`. It is an
inactive template under the existing private workflow `ctt-review` UID. Rendering
or importing it does not create an account, admit any SQL service/capability, seed a key or
start a process. The public app and the individual workflow retain their prior
identities. The operator must separately arrange the sealed directories and
transport/photo group access before selecting this template.

Its configuration uses `schema=ctt.protected-runtime.v2` and
`role=batch-workflow`. Exact fields are enforced by `runtime.ROLE_FIELDS`; the
capture-specific fields are required only with `capture_enabled=true`.
`profile_path/profile_hash` and the `policy_paths` raw-hash-to-file mapping pin
actual admitted artifacts. There is no wildcard or pilot-policy fallback.
Workflow, verifier, worker and channel nonce DSNs must name separate roles.
The workflow contains only the independent issuer URL/channel key and public
pins; issuer DSN/seed fields are rejected. The existing issuer UID alone loads
the explicit batch issuer extension (its own SQL role/seed/channel/verifier
pins) and mounts the typed consent/child RPC. All channel/signing domains
remain separate. `retained_analysis_only=true` replays retained, source-bound
analysis. Explicit false additionally requires a pinned layout, sealed cache,
provider snapshot, complete price quote and admitted budget; it never falls
back to an unconfigured provider. Existing credential reuse was approved on
2026-11-10. The shared US$25 consumption ceiling and pre-document observation
adjustment were approved on 2026-11-12; neither approval provisions a runtime
identity or admits source/profile artifacts by itself.

The existing writer remains V2 unless all five `BATCH_WRITER_FIELDS` are
explicitly provided with `batch_enabled=true`. Its extra V3 finality client uses
`/v3/staff-review/batch-finality/` and a separate key/MAC domain. The V2 client,
URL, scope parser and signature rules remain unchanged. The new gate loads
committed staged batch inputs through a lock-free MVCC read on its own private
pool, opens its own source bundle and recounts incidents. It receives no signer
key from the writer and accepts no wire CAS/human-review flag. The writer still
rechecks actual authority, finality and canonical consumption in its own
per-team transaction. A receipt is never evidence that SQL committed.

Run `python -m deployment.ctt_protected_review.runtime --config <sealed-file>
--check` for local file/pin validation. That check opens no database/listener.
Only an explicitly selected process run opens pools and starts its background
loops; production promotion, source/capability admission and human consent are
separate steps and are not performed by these templates.


### Private source observation before document admission

The same private workflow launcher has an explicit one-shot
`--prepare-sources <UUID>` mode. It uses only its existing verifier SQL pool,
opens no listener, starts no capture dispatcher and requires no issuer/writer
keys. Its sealed configuration has exactly:

```json
{
  "schema": "ctt.protected-runtime.v2",
  "role": "batch-workflow",
  "expected_uid": 1234,
  "public_uid": 5678,
  "backend_pin": "<operator-selected-backend>",
  "verifier_dsn_file": "/<sealed-verifier-dsn-file>",
  "capture_enabled": false,
  "preparation_only": true
}
```

The numeric IDs and path above are placeholders, not identities or grants.
This minimal mode configuration is rejected by the regular listener launcher.
Use private directories already created by the operator:

```text
python -m deployment.ctt_protected_review.runtime --config <sealed-file> --prepare-sources <preparation-uuid> --inventory-root <private-inventory> --preparation-cache-root <private-cache> --preparation-output-root <separate-private-export> --check
```

`--check` performs only local validation; it does not prove SQL admission.
Without `--check`, SQL must resolve the exact admitted inventory/profile/quote
and shared budget before the existing API credential can be read. The quote
and all source hashes are checked, requests use Standard (`default`) with zero
automatic retries, unknown costs keep their entire reservation, and completed
observations recover without another SDK construction or request.

The private export contains source-inventory.json, source-observations.json,
structural-profile.json and source-preparation-report.json. The offline
manifest CLI consumes those exact artifacts. Its output remains a proposal;
no source admission, capture capacity, sample activation or team is created.
The shared budget binding for a later extraction run requires the exact bytes
of that run's actual extraction profile, its hash and pricing matching the
same admitted quote. No rollover is created by a new preparation or run.
