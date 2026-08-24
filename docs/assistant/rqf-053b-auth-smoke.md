# RQF-ASSISTANT-053B ? Authenticated assistant runtime smoke

## Objetivo

Dejar un smoke repetible para comprobar que el asistente vivo funciona con una sesi?n autenticada real, sin activar escrituras de negocio.

## Comando

```bash
python scripts/assistant_auth_smoke.py \
  --base-url http://127.0.0.1:8000 \
  --cookie 'session=...' \
  --timeout 10
```

Tambi?n acepta `--cookie-file` con un encabezado Cookie crudo o un archivo Netscape exportado por navegador. Si el despliegue usa bearer token, se puede usar `--bearer`.

## Qu? valida

1. La superficie `/assistant` responde.
2. `/api/assistant/me` identifica al empleado autenticado.
3. Se puede crear y volver a listar una conversaci?n marcada con `external_session_id`.
4. Se puede ejecutar un turno read-only por `POST /api/assistant/conversations/{id}/messages`.
5. El historial persiste mensajes de usuario y asistente.
6. El turno no deja `pending_confirmation` ni trazas obvias de herramientas de escritura.

## L?mite de seguridad

El smoke crea una conversaci?n/mensajes del asistente para probar persistencia. No debe ejecutar acciones de negocio ni confirmaciones. Si el turno pide confirmaci?n de escritura o aparece intenci?n mutativa en la traza, el smoke falla.

## Estados esperados

- `pass`: sesi?n v?lida y runtime funcional.
- `authentication_required`: no se proporcion? una sesi?n v?lida; la frontera de autenticaci?n est? viva.
- `failed`: el runtime autenticado no satisfizo el contrato.
