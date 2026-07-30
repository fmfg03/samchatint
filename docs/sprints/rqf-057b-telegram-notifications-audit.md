# RQF-057B - Telegram notification recipients audit

Status: CLOSED_COMMITTED_LOCAL_PENDING
Scope: customer Excel delta - Telegram notifications for Odilon and Benjamin.

## Production configuration evidence

Audited production service configuration on 2026-07-30:

- Service: `samchat-gastos.service`
- Working directory: `/srv/samchat/releases/gastos-prod-fac607250-sam-inbox`
- Environment files:
  - `/etc/samchat/samchat.env`
  - `/etc/samchat/zaubern-registration.env`
- Required variables present, values redacted during audit:
  - `TELEGRAM_BOT_TOKEN`
  - `DATABASE_URL`
  - `POSTGRESQL_URL`

## Employee / recipient evidence

Sanitized production DB query confirmed:

| Person | Email | Role | Department | Active | Telegram linked | Current approver |
| --- | --- | --- | --- | --- | --- | --- |
| Benjamin Jimenez | `bjimenez@plataformasports.com` | `finanzas` | Finanzas | yes | yes | Luis Angel Orozco Colin |
| Jose Odilon Trujillo Macedo | `otrujillo@plataformasports.com` | `admin` | Operaciones | yes | yes | Federico Gonzalez y Vega |

Current active employees assigned to Odilon as approver:

- Alicia Edith Zuniga Salazar
- Bibiana Raquel Roman Arguelles
- Carlos Felipe Lozano Pardinas
- Roberto Miguel Martinez Rogers

Current active employees assigned to Benjamin as approver:

- Daniel Dominguez
- Jacqueline Ocegueda
- Sebastian Espinosa

Active Finance recipients with Telegram linked include Benjamin, Daniel, Jacqueline, and Sebastian. A test finance user exists without Telegram and therefore does not receive live Telegram delivery.

## Notification timing contract

### Odilon

Odilon receives approval requests when he is the `aprobador_id` of the authenticated requester/owner of the document.

- Trigger: document workflow action `send`.
- Notification type: `workflow_send_approver`.
- Applies to: `SOLICITUD` and `INFORME` because both use the document workflow.
- UI behavior: includes approve/reject inline keyboard when Odilon has `telegram_user_id`.
- Authorization preserved: notification target follows requester approver, not beneficiary.

Historical outbox evidence exists for Odilon:

- `workflow_send_approver` for standalone `SOLICITUD`.
- `workflow_send_approver` for linked `SOLICITUD` under Cuenta de Gastos.
- `workflow_send_approver` for linked `INFORME` under Cuenta de Gastos.

### Benjamin

Benjamin currently receives two classes of notifications:

1. As direct approver, when he is `aprobador_id` of the requester.
   - Trigger: document workflow action `send`.
   - Notification type: `workflow_send_approver`.
   - Historical outbox evidence exists for `SOLICITUD`.
