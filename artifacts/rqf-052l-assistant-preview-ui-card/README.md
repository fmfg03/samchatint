# RQF-052L ? Assistant preview UI card

This artifact records the frontend source patch applied to the archived `goal-fest-page` project that builds `/assistant`.

The backend repository does not currently vendor the full `goal-fest-page` React source tree. The live `/assistant` SPA is built from `/srv/samchat/archive/projects/goal-fest-page` and served by `copa_telmex_dashboard.py` from `goal-fest-page/dist` in each release.

Included patch:

- `goal-fest-page.Assistant.tsx.patch`: adds a structured specialist preview card renderer for `preview_render`, including proposed changes, evidence, steps, checks, and authority boundary.

Validation performed:

- `npm run build` in `/srv/samchat/archive/projects/goal-fest-page`.
