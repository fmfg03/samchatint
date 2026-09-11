# SamChat Codex Workspace Metadata

This directory exists for sandbox- and agent-local metadata. It is not an
application runtime, deployment, database, credential, or production
configuration directory.

## Governing instructions

Agent behavior is defined by, in precedence order:

1. Higher-priority platform and user instructions.
2. `../AGENTS.md`.
3. `../.agents`.
4. The versioned SamChat canons and their integrity register named in those
   files.

Before work begins, follow the bootstrap and integrity checks in `../.agents`.

## Local-only constraints

- Do not store secrets, `.env` values, private keys, database dumps, production
  data, or deployment state here.
- Do not treat this directory as evidence of a deployed runtime or a release.
- Do not add executable configuration, plugins, skills, hooks, or automation
  without a repository-supported format and explicit approval.
