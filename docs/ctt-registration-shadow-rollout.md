# CTT registration shadow rollout

The dedicated registration bot remains the source of user-facing responses and
web-review persistence. The canonical CTT extractor is attached only as a
background observer and cannot replace the existing result in this rollout.

## Runtime contract

- `CTT_RESPONSES_ROLLOUT=off` disables the observer and is the default.
- `CTT_RESPONSES_ROLLOUT=shadow` buffers two or three validated pages in memory
  and runs the canonical canary in the background.
- `CTT_RESPONSES_ROLLOUT=active` is rejected by this bridge and disables it.
- `CTT_SHADOW_REVIEW_HANDOFF=on` stores an accepted canonical draft and its
  normalized photo previews beside the existing web-review draft.
- `CTT_CANONICAL_PROMOTION=off` keeps field adoption disabled in the web
  workspace and is the default. Set it to `on` only for an authorized cohort.
- `OPENAI_API_KEY` is required only when shadow mode is enabled.
- `CTT_LAYOUT_PATH` may override `config/layout_ctt_2026.json`.
- `CTT_SHADOW_MINIMUM_PLAYERS` defaults to 16 and is bounded to 1 through 25.

The observer never receives a database session. Its deterministic replay cache
is created with private permissions in a temporary directory and deleted after
each observation. In-memory buffers expire after ten minutes and are bounded by
page, chat, and global byte limits. Logs contain only the sanitized canary
report; player names, dates, raw images, and provider response IDs are excluded.

When review handoff is explicitly enabled, the intake runtime owns a separate
sink that writes only inside the already existing temporary review session:

- the legacy `extraction`, `review_edits`, and operator-facing layout remain
  unchanged and authoritative;
- the canonical comparison bundle is stored under
  `ocr_raw.canonical_shadow`;
- its audit summary is stored under `validation.audit.canonical_shadow`;
- photo previews are cropped from normalized pages with fixed template
  coordinates and stored with private permissions below the session directory;
- no `Team` or `Player` row is created or changed by the handoff.

## Deployment

1. Deploy the merged revision to `/root/samchat` without replacing `.env`,
   uploaded photos, or other runtime state.
2. Install `deployment/systemd/samchat-registration-bot.service` in systemd and
   run `systemctl daemon-reload`.
3. Start with `CTT_RESPONSES_ROLLOUT=off`, restart the registration service, and
   verify Telegram intake and web-review creation.
4. To enable the cohort without editing the secret environment file, install
   `deployment/systemd/samchat-registration-bot.service.d/ctt-shadow.conf.example`
   as `/etc/systemd/system/samchat-registration-bot.service.d/ctt-shadow.conf`,
   reload systemd, and restart only the registration service.
5. Verify sanitized `CTT shadow report` and
   `CTT canonical review handoff: persisted=true` log entries for a bounded
   cohort.
6. Compare the quarantined canonical bundle and previews with the legacy draft.
7. Do not enable active mode in this release.
8. Leave `CTT_CANONICAL_PROMOTION=off` until PR10 has been deployed and the
   authorized operator cohort has been confirmed. Enabling it requires a
   restart of the web dashboard service, not the registration bot.

## Controlled canonical promotion

PR10 adds a field-level adoption step without making the sidecar authoritative:

- only the existing internal review roles can use the endpoint;
- the browser submits allowlisted field paths and the canonical hash, never the
  canonical value itself;
- the server reloads the accepted, non-authoritative sidecar under a row lock
  and rejects stale hashes, non-allowlisted paths, or fields without preserved
  evidence;
- team, manager, and existing legacy-player fields can be selected; canonical
  roster rows without a legacy slot require manual review and are not created
  implicitly;
- every applied field stores its previous value, canonical value, actor,
  timestamp, document/canonical hashes, and available extraction evidence in
  the existing draft audit JSON;
- adoption only updates the editable draft and reopens a rejected session as
  `ready`; it never writes `Team`, `Player`, or `OCRRegistration` rows;
- the operator must explicitly confirm the field selection, then separately use
  **Aprobar y capturar** before any main-table write can occur.

Canonical sidecar refreshes and review mutations acquire the same review-session
row lock so a refresh, edit, rejection, reprocess, or capture cannot silently
overwrite a promotion event.

## Home operations surface

PR11 makes the existing internal dashboard a continuation point for the review
queue. It is read-only and uses the same authorized roles as the review inbox:

- Home counts every non-committed review and separates sessions that are ready
  to capture, blocked, processing, or rejected;
- `RegistrationReviewSession.status = "ready"` is not treated as capture-ready
  by itself; the ready count requires `validation.ready_to_commit = true` and
  zero blocking issues;
- the recent queue is ordered by the latest session or draft update and shows a
  bounded set of direct links back to the existing review workspace;
- the Home query projects only identifiers, status, counts, timestamps,
  tournament slug, and the optional intake folio. It does not load or render
  player values, field corrections, canonical sidecars, or photo paths;
- the surface introduces no new write endpoint, feature flag, database
  migration, or authority to capture a team.

## Rollback

Set `CTT_SHADOW_REVIEW_HANDOFF=off` to stop creating comparison bundles, or set
`CTT_RESPONSES_ROLLOUT=off` to disable the observer entirely, then restart
`samchat-registration-bot.service`. Existing review drafts and all user-facing
behavior remain unchanged because shadow results are never promoted.

Set `CTT_CANONICAL_PROMOTION=off` and restart the web dashboard service to remove
all adoption controls and make the endpoint inert. Previously adopted values
remain explicit editable-draft changes with their audit events; rollback never
deletes evidence or rewrites a captured team.

## Review UI roadmap

The operator experience is split into bounded releases so the canonical OCR can
be inspected before it is ever allowed to replace the legacy result:

1. **PR8 - observable comparison:** put the production review surface under
   source control, expose the registration-review inbox from Home, and render
   the accepted canonical sidecar as a read-only comparison with private photo
   previews. Legacy fields remain the only editable and committable values.
2. **PR9 - interface revamp:** replace the current long form with a responsive,
   difference-first workspace; add clear approve, reject, and modify actions;
   improve page/player navigation, accessibility, empty states, and mobile
   behavior. Rejection is stored in the existing draft audit JSON and blocks
   commit until the operator modifies or reprocesses the draft. It does not
   delete evidence or promote canonical values.
3. **PR10 - controlled canonical promotion:** an authorized operator can choose
   allowlisted canonical values field by field. The server preserves the legacy
   value and extraction evidence in the audit trail, rejects stale sidecars,
   and requires a separate explicit approval before capture. Structural roster
   changes remain manual.
4. **PR11 - Home operations surface:** add review counts, blocked/ready status,
   recency, and direct continuation links to the main dashboard so operators do
   not need a Telegram URL or a remembered route.

Each release keeps `/photos/review_sessions/...` behind the existing review
access check and must not expose canonical PII in logs or public static paths.

PR9 keeps the same authority boundary as PR8: canonical fields are rendered as
read-only comparison values and are never submitted by either decision form.
Only the legacy editable draft reaches the existing commit endpoint. Field-level
canonical adoption remains exclusive to PR10.
