# OAuth transaccional: verificación local

Fecha: 2026-10-01, Ciudad de México. Rama `feat/private-plugin-review-20261001`;
base anterior `0488664`. Instalación objetivo: SamChat actual de Plataforma Sports,
cuentas existentes y facultades actuales. Código revisable, sin montaje ni conexión.

Canon unchanged: se implementan contratos y adaptadores no conectados, con pruebas
sintéticas. No cambia login, roles, razones sociales, contabilidad ni runtime.
Los hashes de los tres canons siguen coincidiendo con el registro de convergencia.

## Implementado y demostrado

- `oauth_flow.py`: consentimiento de cuenta propia con borrador exacto, CSRF,
  browser binding y state; código de uso único; PKCE S256; cliente, callback y
  recurso exactos; token firmado y revocación del grant. Reconsentir conserva
  vínculo/perfil opaco. No aprovisiona empleados ni implementa refresh.
- `oauth_store.py`: unidad transaccional SQLAlchemy; cuatro tablas OAuth con FK
  al empleado existente. Consumo, grant y recibo canónico se confirman juntos.
  Expiración se revalida después de esperas y antes del commit. Orden de bloqueo
  empleado→vínculo→grant; concurrentes sobre un código tienen un solo ganador.
  Falta de audit, insert ignorado, error de firma/commit o revocación incompleta
  revierte la unidad y no devuelve un resultado exitoso.
- `oauth_adapters.py`: recarga grant/vínculo/empleado y registra recibos mínimos
  con el contrato `agent_action_receipts`; recuperación aislada por actor y
  namespace. El servidor vuelve a comprobar identidad después del audit antes
  de liberar la lectura MCP.
- Migración revisable `20261001_private_oauth_storage_review.sql`: aplicada sólo
  en PostgreSQL efímero. No demuestra que exista el schema en producción.
- Prueba HTTP en memoria: middleware de sesión firmado real y cuerpo canónico
  `get_current_empleado`, sobre empleados sintéticos; metadata, consentimiento,
  callback, PKCE, token, replay y cookie alterada. Helper y campos de sesión son
  exclusivos del fixture y no autentican contraseñas ni están montados.
- Integración local: código OAuth→JWT verificado→SDK MCP en memoria→perfil y dos
  lecturas Direction con guardas canónicas→recibo confirmado y recuperable.
  Revocación, denegación Direction y organización distinta bloquean acceso.
  Proyecciones de negocio y mapeo de organización siguen siendo fixtures.

No se persisten bearer, código sin hash, verifier, state sin hash, CSRF sin hash,
claves privadas, passwords o documentos personales. Los secretos sintéticos viven
en memoria durante las pruebas; no se consultaron credenciales reales.

## Evidencia reproducible

Dependencias opcionales: `requirements-private-plugin-test.txt` y
`requirements-private-plugin-postgres.txt`, fuera de requirements productivos.

```sh
python scripts/private_plugin/run_offline_tests.py
python scripts/private_plugin/run_postgres_tests.py
python scripts/private_plugin/run_pr445_regressions.py
```

- 130 pruebas offline aprobadas. Cobertura de statements: 95% (1345/1419);
  store 90%, flow 94%, adaptadores 97%. No es cobertura de ramas ni de paridad.
- 20 pruebas PostgreSQL 16.2 aprobadas: cluster descartable, socket Unix privado,
  TCP deshabilitado, sin DSN externo ni configuración productiva. Incluyen casos
  reutilizados del flujo offline; no se suman como escenarios únicos.
- 40 regresiones puras de PR445 aprobadas; no equivalen a su suite completa.
- Black/isort, flake8 con línea 88 y `git diff --check` sobre el alcance privado.

Los runners impiden efectos externos y lectura de secretos; el runner PostgreSQL
permite sólo comandos exactos de su cluster descartable. No se llaman modelos,
servicios pagados o datos reales. El cluster se destruye al terminar.

## Integración en checks del PR

El PR #446 ejecuta ambos runners en entornos de dependencias privados dentro de
los jobs obligatorios unit/integration. Pytest mantiene todas las pruebas del
repositorio fuera de esas carpetas; esas carpetas se ejecutan completas con sus
runners aislados, sin importación del runtime ni conexión al servicio de CI.
Se combina su cobertura con la de pytest antes del gate de líneas cambiadas (85%).
El contrato de CI rechaza omitir cualquiera de los runners o su cobertura;
11 pruebas del contrato aprobadas localmente. No se añadieron fallos aceptados,
skips de seguridad ni continue-on-error.

## Revisión y pendientes

Regla de trabajo de Francisco: **Code retrieves and calculates. Sol interprets.
Astra reasons when necessary.** Código obtiene inventario, hashes y resultados;
Sol interpretó transacciones, cobertura y límites de las cifras; Astra revisó
amenazas y detectó la inversión de bloqueos corregida y el gap de privilegios.
La regla se registra aquí sin modificar silenciosamente `AGENTS.md` o canons.

Prueba con rol NOLOGIN descartable demuestra que SELECT de ID/activo no permite
`FOR UPDATE`: rechazo PostgreSQL `42501`. Resolver una frontera de bloqueo y
privilegio mínimo antes de activar; no se otorgó UPDATE real ni se retiró el
bloqueo. Ver [preflight preciso](activation-preflight.md).

También faltan composición async real, browser binding/CSRF productivos, claves y
rotación, cliente/callback aprobados, schema/runtime, transporte HTTP MCP protegido
y mapeo canónico empleado→objeto→razón social. El frontend activo no está versionado
aquí. No hubo publicación, push, PR, instalación, cuenta/grant real o despliegue.

La [matriz completa](route-matrix.csv) conserva 561 declaraciones HTTP, 627 rutas
con aliases, 44 acciones canónicas y omisiones explícitas. Las mutaciones de
negocio y captura deportiva permanecen deshabilitadas: esta unidad no demuestra
paridad completa, autorización de gastos o transferencia de dinero.
