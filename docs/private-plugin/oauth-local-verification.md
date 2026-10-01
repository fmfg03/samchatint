# OAuth y recibos locales: evidencia del 2026-10-01

**Evidencia histórica del commit `0488664`, anterior al flujo transaccional.**
Las cifras y funciones ausentes que aparecen aquí corresponden a ese corte.
Consultar [verificación transaccional actual](oauth-transactional-verification.md)
y [preflight de activación](activation-preflight.md) para el estado vigente.

Alcance autorizado: únicamente la instalación actual de Plataforma Sports y sus cuentas existentes. No se crea arquitectura multiempresa ni se altera la separación de tres razones sociales. Base de negocio PR445 `6cd122f`; avance anterior `87f0827`. Ninguna nueva ruta, listener, grant real, credencial, despliegue o conexión a ChatGPT.

## Implementado y probado

- `oauth_identity.py`: verificador de access tokens sintéticos firmados ES256/RS256, con claves públicas previamente configuradas. Verifica `typ=at+jwt`, kid/alg, firma, issuer, audience, cliente, tiempos estrictos, jti y scopes. No descarga claves indicadas por el token. Resuelve grant, vínculo y empleado activo de nuevo en cada acción; la intersección token/grant/configuración nunca amplía facultades. Perfil opaco estable obtenido del vínculo, no del correo o actor propuesto por el modelo.
- `identity_records.py`: adaptador SQL de lectura parametrizada para el contrato **local** de registros existentes. No abre conexiones, crea tablas ni concede grants; pruebas con SQLite de archivo y conexión `mode=ro` comprueban revocación inmediata, ausencia de escrituras, ambigüedad y corrupción. Las tablas `local_identity_*` y `local_existing_employees` son fixtures, no una migración productiva ni el modelo canónico de empleados.
- `durable_audit.py`: almacenamiento SQLite local dedicado y migración explícita. Cada recibo se confirma antes de retornar su ID; recuperación por actor, hash de integridad, rechazo de cambios/borrados normales y errores saneados. Se prueban 24 inserts concurrentes, fallo de commit/rollback, reapertura y aislamiento. El hash no protege contra un administrador que reescriba archivo y hashes. No equivale a almacenamiento productivo ni exactly-once de negocio.
- `test_authenticated_receipts.py`: sesión real del SDK MCP en memoria → token con firma sintética → identidad existente → adaptador Direction con guards canónicos → recibo en archivo → reapertura y hash del resultado. Revocar el grant bloquea la siguiente lectura antes de construir datos. La fuente de negocio y el mapeo de organización siguen siendo fixtures; no se presenta como integración de datos reales.

Se conserva `get_profile` y `direction_read_summary` en la composición de pruebas, sin HTTP. El perímetro original continúa inerte y todas las mutaciones deshabilitadas. El inventario completo sigue siendo el objetivo de cobertura, no una lista de funcionalidades ya implementadas. No se usa el fallback deportivo que inventa nacimiento/teléfono/categoría.

## Verificación reproducible

```sh
python scripts/private_plugin/run_offline_tests.py
python scripts/private_plugin/run_pr445_regressions.py
```

95 pruebas privadas y 40 regresiones puras de PR445 aprobadas (135 total). El segundo comando requiere las dependencias opcionales de pruebas. No es la suite completa de PR445. Cobertura de statements del paquete: 96% (708/736). Black/isort/flake8 y `git diff --check` aprobados. El runner bloquea red, subprocess no permitido y lectura de archivos de secretos; no se llamaron modelos ni servicios pagados.

## Decisiones y bloqueos antes de habilitar

1. Seleccionar issuer/IdP, resource audience, client IDs autorizados, scopes mínimos y fuente de claves públicas/rotación. El prototipo exige JWT RFC9068 `at+jwt`; si el IdP usa otro perfil u opaque tokens, debe implementarse y probarse su adaptador, sin relajar validación incidentalmente. No se ha construido un authorization server, flujo de consentimiento/PKCE, discovery público ni refresh/revoke endpoint.
2. Aprobar cómo se vincula una cuenta OAuth a un empleado **ya existente** y persistir/revocar ese vínculo en el almacenamiento canónico. No reutilizar el puente Supabase que aprovisiona/reactiva/promueve. La consulta local prueba el contrato, no este vínculo productivo. No hay API de creación de vínculos o grants.
3. Demostrar por servicio la relación empleado→razón social→objeto, además de las guardas actuales de cartera/torneo. La instalación única no resuelve este gap. `organization_for_scope` sigue siendo dependencia explícita que bloquea si no prueba igualdad; no se incorporó una tabla ficticia de permisos.
4. Elegir almacenamiento productivo de auditoría, retención y recuperación; enlazar actor/resultado/versión de política sin PII innecesaria. Para escrituras, implementar confirmación exacta e idempotencia durable junto al commit canónico; el contrato existente por sí solo no prueba ninguna captura o aprobación real.
5. Completar adaptadores y pruebas por cada familia del inventario. La UI activa no está versionada aquí: permanece sin paridad UI demostrable. No se accedió al servidor. Events permanece fuera del scaffold por diferencia entre protocolo requerido y SDK disponible.

Documentación oficial consultada: [autenticación](https://developers.openai.com/plugins/build/auth), [plugins](https://developers.openai.com/plugins/build/plugins), [conexión a ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt). La skill `plugin-creator` no estuvo disponible; no se inventó su scaffold o validador. Artefacto local no equivale a plugin conectado, distribución privada aprobada ni elegibilidad universal de planes de paga.
