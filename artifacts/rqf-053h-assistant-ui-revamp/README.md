# RQF-053H — Assistant UI revamp, primera pasada

Status: IMPLEMENTED_STATIC_DEPLOYED

## Objective

Make the production assistant feel like an operational workspace instead of a raw debug surface, while keeping the runtime read-only.

## Implemented UI changes

- Adds a read-only badge for assistant answers backed by tool payloads, traces, previews, or workspace cards.
- Keeps raw tool traces collapsed at the bottom of the answer.
- Promotes missing data / evidence / next questions into a visible **Faltantes** panel.
- Promotes proposed actions into a separate **Acciones propuestas** panel with authority language.
- Promotes detected artifacts, owner-pack readiness sections, and artifact-review buckets into **Artefactos** cards.
- Preserves existing workspace cards, step trace, source cards, and specialist preview surfaces.
- Adds RQF-053B-FU4 history hydration by sending a deterministic
  `external_session_id` for the Assistant route, then loading
  `GET /api/assistant/conversations/{conversation_id}/messages` and preserving
  persisted `tool_payload` so historical assistant messages render workspace
  cards, step traces, source cards, and preview surfaces after reload/re-entry.
- Adds RQF-053B-FU6 executive dashboard error state so failed dashboard loads
  render an explicit error before the empty-alerts state.
- Adds RQF-053B-FU7 RAG ownership note: `/assistant` should treat RAG as
  navigation to `/RAG`, not as an active RAG administration console. Any
  remaining RAG state or handlers in this snapshot are extraction debt for a
  dedicated frontend slice, not Assistant ownership.
- Adds RQF-053H credential-surface hardening: `/assistant` removes legacy
  locally stored provider keys, rejects `openai_api_key` / `openai_key` URL
  parameters without reading their values, and does not send
  `X-OpenAI-API-Key` from browser state.

## Authority boundary

This slice does not enable writes, approvals, folder creation, notifications, exports, or mutations. It only changes presentation of already returned assistant data.

Provider credentials are not accepted through URL parameters or browser
storage. Any live provider credential must be configured through the server-side
runtime authority path, not the Assistant UI.

## Frontend source and deployment note

The active frontend source still lives outside this backend repo at:

- `/srv/samchat/archive/projects/goal-fest-page/src/pages/Assistant.tsx`

The build was generated with `npm run build` from `/srv/samchat/archive/projects/goal-fest-page` and copied to the active release static directory:

- `/srv/samchat/current/goal-fest-page/dist`

A snapshot of the patched `Assistant.tsx` is stored in this artifact directory to prevent losing the UI work during later branch/release consolidation.

## Rollback Notes

This artifact does not deploy or copy frontend assets. Before applying a future
`goal-fest-page` build to the active static directory, capture the existing
bundle receipt:

```bash
find /srv/samchat/current/goal-fest-page/dist/assets -maxdepth 1 -type f -printf '%f %s %T@\n' | sort
```

Also copy the current dist directory to a dated backup before replacing files:

```bash
cp -a /srv/samchat/current/goal-fest-page/dist /srv/samchat/current/goal-fest-page/dist.rollback-YYYYMMDDHHMMSS
```

Rollback command:

```bash
rsync -a --delete /srv/samchat/current/goal-fest-page/dist.rollback-YYYYMMDDHHMMSS/ /srv/samchat/current/goal-fest-page/dist/
```

After rollback, verify `/healthz`, `/readyz`, and the expected `/assistant`
bundle markers before claiming recovery.

## Verification

- `npm run build` passed for the frontend bundle.
- Deployed bundle contains markers: `Read-only`, `Faltantes`, `Acciones propuestas`, `Artefactos`.
- `/healthz` and `/readyz` returned OK after static deployment.
