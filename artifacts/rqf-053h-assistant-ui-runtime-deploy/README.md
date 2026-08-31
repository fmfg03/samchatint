# RQF-053H Assistant UI Runtime Deploy

Status: DEPLOYED_STATIC_ASSETS
Date: 2026-08-31

## Scope

Applied the reconciled Assistant UI artifact to the external active frontend
source, rebuilt Vite static assets, deployed them into the currently served
static dist, and captured rollback evidence.

No database changes, live canary calls, authenticated business writes, or
backend service restarts were part of this deploy.

## Runtime Target

- Backend service: `samchat-gastos.service`
- Service state before deploy: `ActiveState=active`, `SubState=running`
- Service WorkingDirectory:
  `/srv/samchat/releases/gastos-prod-42bf8d6a-expense-report-controls`
- Frontend source:
  `/srv/samchat/archive/projects/goal-fest-page/src/pages/Assistant.tsx`
- Served static dist: `/srv/samchat/current/goal-fest-page/dist`

## Predeploy Evidence

- Asset listing: `predeploy-assets.txt`
- Assistant asset before deploy: `Assistant-BxnXzIWk.js`
- `/healthz`: healthy at `2026-08-31T00:42:04.448740+00:00`
- `/readyz`: healthy at `2026-08-31T00:42:04.527331+00:00`
- Rollback backup created:
  `/srv/samchat/current/goal-fest-page/dist.rollback-20260831-rqf053h`

## Build And Deploy

Artifact copied from:
`/root/samchat/artifacts/rqf-053h-assistant-ui-revamp/Assistant.tsx`

Artifact copied to:
`/srv/samchat/archive/projects/goal-fest-page/src/pages/Assistant.tsx`

Build command:

```bash
npm run build
```

Build result:

- Success
- Generated assistant asset: `Assistant-Cj-wzq_B.js`
- Reported bundle size: `60.35 kB`, gzip `14.30 kB`

Deploy command:

```bash
rsync -a --delete /srv/samchat/archive/projects/goal-fest-page/dist/ /srv/samchat/current/goal-fest-page/dist/
```

## Postdeploy Evidence

- Asset listing: `postdeploy-assets.txt`
- Assistant asset after deploy: `Assistant-Cj-wzq_B.js`
- Served assistant asset size: `60438`
- Asset count before deploy: `101`
- Asset count after deploy: `101`
- `/healthz`: healthy at `2026-08-31T00:42:52.414525+00:00`
- `/readyz`: healthy at `2026-08-31T00:42:52.495313+00:00`

Verified bundle markers in the served assistant asset:

- `Cargando historial`
- `No se pudo cargar el panel ejecutivo`
- `external_session_id`

Verified source parity:

- `artifacts/rqf-053h-assistant-ui-revamp/Assistant.tsx`
- `/srv/samchat/archive/projects/goal-fest-page/src/pages/Assistant.tsx`

## Rollback

Use this command to restore the predeploy dist:

```bash
rsync -a --delete /srv/samchat/current/goal-fest-page/dist.rollback-20260831-rqf053h/ /srv/samchat/current/goal-fest-page/dist/
```

After rollback, verify `/healthz`, `/readyz`, and that the served assistant
asset returns to the predeploy asset listed in `predeploy-assets.txt`.

## Known Follow-Up

The artifact and active frontend still include pre-existing legacy URL and
localStorage API-key intake code. That was observed before this runtime deploy
and was not expanded into this slice. It should be handled in a separate
credential-surface hardening slice.
