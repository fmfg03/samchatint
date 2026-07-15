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

## Rollback

Set `CTT_SHADOW_REVIEW_HANDOFF=off` to stop creating comparison bundles, or set
`CTT_RESPONSES_ROLLOUT=off` to disable the observer entirely, then restart
`samchat-registration-bot.service`. Existing review drafts and all user-facing
behavior remain unchanged because shadow results are never promoted.
