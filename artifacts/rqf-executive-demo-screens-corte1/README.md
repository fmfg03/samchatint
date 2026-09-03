# RQF Executive Demo Screens - Corte 1

## Scope

- Update active assistant frontend source to present the Assistant module as `Asistente Ejecutivo`.
- Add a first-screen executive demo band with guided prompts and direct links to:
  - Owner Pack
  - Presupuestos
  - Flujo de efectivo
  - Cuentas por cobrar
- Reframe financial widgets with executive copy and MXN formatting.
- Replace customer-visible technical copy such as internal report names and `Read-only`.

## Runtime Source

Updated active file:

`/srv/samchat/archive/projects/goal-fest-page/src/pages/Assistant.tsx`

Snapshot stored here:

`artifacts/rqf-executive-demo-screens-corte1/Assistant.tsx`

## Verification

Command:

`npm run build`

Working directory:

`/srv/samchat/archive/projects/goal-fest-page`

Result:

Passed. Vite built the production bundle and emitted `dist/assets/Assistant-DSKL6vTc.js`.

## Notes

- No backend assistant engine, permission, or confirmation behavior was changed in this cut.
- The untracked WhatsApp images in the repository root are unrelated and must remain unstaged.
