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
