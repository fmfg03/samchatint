# RQF-053H — Assistant UI revamp, primera pasada

## Status

IMPLEMENTED_STATIC_DEPLOYED

## Closure contract

- Conversación + tool traces colapsadas: raw traces remain behind a `<details>` disclosure.
- Cards de fuentes: existing source panel remains surfaced as `Fuentes usadas`.
- Cards de artefactos: new `Artefactos` panel surfaces artifacts, Owner Pack sections, and artifact-review buckets.
- `Faltantes` visible: new panel extracts missing fields/evidence/items and next questions.
- `Acciones propuestas` separadas: proposed actions are separated from evidence and marked as requiring authority.
- Read-only badge: assistant answers backed by tools/previews show `Read-only · consultas, previews y propuestas sin efectos reales`.

## Non-claims

- No write path was enabled.
- No approval, export, notification, folder publication, or mutation behavior changed.
- This is not the full assistant redesign; it is the first UI pass that makes the existing surfaces legible.

## Verification

- Frontend build: `npm run build` in `/srv/samchat/archive/projects/goal-fest-page`.
- Deployed markers verified in `/srv/samchat/current/goal-fest-page/dist/assets/Assistant-*.js`.
- Runtime health: `/healthz` and `/readyz` OK.
