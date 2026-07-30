# RQF-057G2 - Authorization profile board

Status: CLOSED_LOCAL_PENDING_COMMIT
Scope: UI/product slice for Francisco's "Perfil Odilon / copiar / mover switches" model.

## Implemented

- Added persistent authorization profile service backed by `authorization_profiles`.
- Seeds default person-like profiles from the canonical strategy resolver:
  - Perfil Odilon
  - Perfil Luis Angel
  - Perfil Olof
  - Perfil Benjamin
  - Perfil DG
- Added admin board at `/admin/estrategias-autorizacion`.
- Registered the board in Configuracion via Control de accesos tool key `configuracion.estrategias_autorizacion`.
- The board supports:
  - profile list with rule counts;
  - profile detail;
  - copy profile;
  - switch editing per rule:
    - active/inactive;
    - first authorization;
    - second authorization.

## Boundary

This is still advisory. It does not yet alter live approval enforcement, Telegram routing, or `empleados.aprobador_id` behavior.

The point of this slice is to make the matrix visible and editable in the product language Francisco requested: profiles named like people, copied from a known profile, then adjusted with switches.

## Next enforcement slice

The next safe slice should record which authorization profile/rule would apply to a document at send time and display that evidence. Only after that should we block or reroute approvals.