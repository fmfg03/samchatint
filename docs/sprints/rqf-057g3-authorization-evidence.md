# RQF-057G3 - Authorization strategy evidence on send

Status: CLOSED_LOCAL_PENDING_COMMIT
Scope: advisory evidence before enforcement.

## Implemented

When a document workflow action is `send`, SamChat now builds advisory authorization strategy evidence and persists it in `customer_success_audit_events.metadata_json` under `authorization_strategy`.

The evidence includes:

- inferred resolver inputs: area, erogation type, amount, invoice/budget/urgent flags;
- matched strategy rule, when any;
- required authorization role keys;
- matching active authorization profiles and whether each profile can act as first or second authorization;
- fallback reason when no strategy rule matches.

## Boundary

This does not block sending, reroute Telegram, or change approval permissions. It is designed to observe real traffic first and give us traceable proof of what the future enforcement would do.

## Next enforcement slice

After reviewing enough send-time evidence, the next slice can render the required approval path on the document detail page and then progressively enforce first/second authorization.