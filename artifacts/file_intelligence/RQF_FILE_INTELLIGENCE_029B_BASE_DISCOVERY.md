# RQF File Intelligence 029B Base Discovery

## Current Worktree

- Current branch: `main`
- Current HEAD has no committed `src/samchat/assistant` tree.
- The current worktree/index contains assistant runtime files, but several required files are dirty, staged, or untracked rather than committed on `main`.

## Required Runtime Files

- `src/samchat/assistant/router.py`
- `src/samchat/assistant/upload_service.py`
- `src/samchat/assistant/conversation_service.py`
- `src/samchat/assistant/file_parsing.py`
- `src/samchat/assistant/action_router.py`

## Why `fe405520eb49ea193457a2554c77884b7aae2763` Is Not Valid

The previous clean-apply base `fe405520eb49ea193457a2554c77884b7aae2763` does not contain the live assistant runtime files required by the pack:

- no `src/samchat/assistant/router.py`
- no `src/samchat/assistant/file_parsing.py`
- no usable committed `src/samchat/assistant` runtime surface

Applying file-intelligence onto that base would create code around a missing runtime surface and would not validate the live `/assistant` path.

## Candidate Commits And Branches

The following candidates contain all five required runtime files:

- `6b4b7b49dc6b86c4212bf408b62d4cfa38d8c4ea` / `origin/rqf-samchat-assistant-runtime-clean`
- `a1f7c5ca2487b2ad06bc8f303b07009c4d62ee2b` / `origin/rqf-samchat-assistant-runtime`
- `2f5bb526fb586099e634433ea2dcd3f7499a2af6` / `origin/rqf-samchat-assistant-004`
- `c22c9b2f886473c07f91d8e625d4c40da0d33239` / `origin/p0/canonicalize-live-runtime-core-r2`
- `e1c404802933617cd7962b812466b24f00d10b4e`

Candidate evidence from `git ls-tree` confirmed these files on `6b4b7b49dc6b86c4212bf408b62d4cfa38d8c4ea`:

- `src/samchat/assistant/action_router.py`
- `src/samchat/assistant/conversation_service.py`
- `src/samchat/assistant/file_parsing.py`
- `src/samchat/assistant/router.py`
- `src/samchat/assistant/upload_service.py`

## Best Base Candidate

Best base: `6b4b7b49dc6b86c4212bf408b62d4cfa38d8c4ea` (`origin/rqf-samchat-assistant-runtime-clean`)

Reason:

- It contains all required live assistant runtime files.
- Its `router.py` blob matches the pre-file-intelligence router baseline used by the current dirty diff.
- It is a named remote branch specifically for the clean assistant runtime baseline.

## Dirty/Untracked Runtime Note

On current `main`, required runtime files are not all committed at `HEAD`; they are present through the dirty index/worktree. This is why `main`/`fe405520e` is not a valid clean base for the pack.

## Repack Decision

A valid committed base exists, so V2 pack artifacts are rebuilt against `6b4b7b49dc6b86c4212bf408b62d4cfa38d8c4ea`.

The malformed V1 patch is not reused.
