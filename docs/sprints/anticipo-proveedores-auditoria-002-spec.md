# ANTICIPO-PROVEEDORES-AUDITORIA-002

Fecha: 2026-11-03. Ampliación de historia y especificación aprobadas por el usuario.
Implementación y validación autorizadas. Retoma el flujo de ANTICIPO-PROVEEDORES-PRECISION-001 y PR #520.
El nuevo alcance no modifica la corrección de precisión ya desplegada.

## Historia aprobada y evidencia

Como solicitante de un anticipo pagado, necesito que la comprobación y su
registro de auditoría se confirmen juntos, manteniendo identidad, evidencia,
límite monetario, idempotencia y validaciones financieras existentes.

El Error ID 88aed23f94c2 corresponde al POST de S-26000449 del 2026-11-03:
el commit falla al insertar Aprobacion con accion comprobar_anticipo_proveedor,
porque el CHECK productivo aprobaciones_accion_check no admite esa acción.
La corrección de centavos sigue activa. La lectura posterior sólo encontró el
anticipo original pagado, sin comprobaciones hijas persistidas.

Se reprodujo el fallo con la definición exacta del CHECK productivo en un
PostgreSQL 16 desechable: el INSERT de la acción falla y revierte la transacción.
El modelo ORM Aprobacion no declara este CHECK; por eso las pruebas SQLite no
lo instalaron y no detectaron el segundo defecto.

Runtime: copa_telmex_dashboard.py / samchat-gastos.service.
Persistencia: PostgreSQL directo, documentos y aprobaciones.
Owner: submit_supplier_invoice; supplier_invoice_submit confirma/revierte la
transacción. La acción ya existe en ese servicio; no se crea una nueva facultad.

## Corrección propuesta

1. Añadir database/migrations/20261103_supplier_advance_audit_action.sql,
   owner-run, transaccional y repetible. Ejecutar después de las migraciones
   20261010_late_support_audit_actions.sql y de precisión de 20261103.
2. Admitir comprobar_anticipo_proveedor conservando exactamente las 25 acciones
   actuales verificadas. No eliminar el CHECK ni permitir acciones arbitrarias.
   Si falta el CHECK, no está validado o su definición tiene acciones/reglas
   inesperadas, abortar y reportar drift antes de cambiarlo. El estado ya migrado
   con las mismas acciones más la nueva debe ser un no-op verificable.
3. Mantener el INSERT de auditoría dentro de la transacción canónica existente.
   No editar el servicio para omitir auditoría, cambiarla por otra acción o
   confirmar primero el documento. No reescribir aprobaciones históricas.
4. No añadir DDL de startup ni modificar el parche legado de schema_guard.py.
   Probar que ese parche conserva el CHECK actualizado en un reinicio normal:
   su lista de condiciones existentes ya queda satisfecha después de migrar.
   Una definición incompatible se atiende por owner, no por una ampliación
   silenciosa del alcance de este trabajo.
5. Sin cambios de UI/API, roles, montos, pagos, pólizas o reglas fiscales;
   sin dependencias nuevas ni aplicación de comprobaciones reales por el agente.

## Archivos previstos

- Nueva migración SQL indicada arriba.
- tests/integration/test_supplier_advance_submission_postgres.py, nuevo.
- tests/integration/supplier_advance_migration.mjs para el CHECK y migraciones.
- docs/roadmap/supplier-advances.md y esta especificación, copiada al worktree
  aislado de implementación con sus aprobaciones y evidencia.
- Los archivos existentes de pruebas de anticipos se ejecutan como regresión;
  sólo se modifican si es indispensable adaptar un fixture de esta aceptación.

## Pruebas y aceptación

Reutilizar la fixture PostgreSQL desechable existente; excluir DSNs productivos.
Instalar los CHECKs productivos relevantes, guard de anticipo y precisión antes
de ejecutar la prueba del servicio. Usar actor, anticipo pagado y CFDI sintéticos.
Ejecutar submit_supplier_invoice y el commit real, con lectura independiente.
Los dobles necesarios para almacenamiento/transporte o dependencias de fixtures
se declararán; no simular el commit, el CHECK ni la escritura de auditoría.

- Rojo: el servicio llega al commit y falla por aprobaciones_accion_check con
  la lista productiva anterior; rollback deja cero hijos y cero auditorías nuevas.
- Verde: tras migrar, documento y auditoría existen juntos; la acción, actor,
  referencia origen, factura y asignación son correctos. Probar también el
  handler de submit con sus límites transaccionales y dependencias controladas.
- Replay de submission_id devuelve el mismo hijo sin duplicar auditorías.
- Fallo de auditoría inyectado revierte documento, reserva y vínculos fiscales.
- Acciones desconocidas siguen bloqueadas; las 25 acciones actuales permanecen
  admitidas. Migración repetida conserva la definición sin pérdida de historia.
- Definición ausente/inesperada produce aborto, no una reparación permisiva.
- El parche legado de schema_guard conserva la acción después de la migración.
- Repetir las 43 pruebas Python de anticipos y la batería SQL de precisión,
  incluyendo sobreasignación real de 0.01 e invariantes anteriores.

Ejecutar controles pertinentes de Black/isort/flake8, sintaxis, diff y gates de
release. Atribuir deuda previa explícitamente. Validación final de solo lectura
contra historia/especificación y alcance antes de publicación. CI y Finance UAT
siguen siendo evidencias separadas.

## Activación, riesgos y rollback

La alteración de CHECK puede tomar bloqueo de tabla y validar filas existentes;
preflight exacto, respaldo pg_dump validado, snapshot de definición/acciones,
lock timeout y statement timeout acotan la operación. Conservar definición y
conteos antes/después. El cambio no debe mutar documentos ni aprobaciones.

La activación, si queda autorizada para esta extensión, puede ser sólo SQL:
el servicio servido ya emite la acción correcta. No desplegar cambios ajenos
de main ni reiniciar innecesariamente. Confirmar metadatos del CHECK, función
de precisión, release servido, guards, health/readiness y protección de acceso.
La confirmación de factura real pertenece al solicitante autenticado.

Rollback: antes de admitir auditorías nuevas puede restaurarse el CHECK previo
transaccionalmente. Si ya existen eventos comprobar_anticipo_proveedor, no
borrarlos ni restaurar una restricción que los invalide: conservar el esquema
aditivo y detener la reversión destructiva; requiere corrección hacia adelante.

## Canon

Canon unchanged: se habilita la persistencia del evento que el owner canónico
ya emite. No cambia autoridad de aprobación/pago ni semántica fiscal; se conserva
la atomicidad y se añade cobertura contra el esquema productivo real.


## Build report — 2026-11-03

Status: approved implementation completed; final read-only validation follows.
No commit, push, PR, merge or production application for this extension yet.

Base: origin/main `ec5bc960b` after fetch. Worktree:
`/root/samchat/.worktrees/supplier-advance-audit-action-20261103`.
Branch: `fix/supplier-advance-audit-action-20261103`.
Five files changed: the new SQL migration and PostgreSQL test, the existing
SQL test runner, this specification and the supplier-advances roadmap.
Runtime source, models, historical migrations and schema_guard.py are unchanged.

### Commands

```sh
PYTHONPATH=src:.:tests/integration:/tmp/supplier-advance-test-deps-20261103 /root/samchat/.venv/bin/python -m pytest tests/integration/test_supplier_advance_submission_postgres.py tests/unit/gastos/test_supplier_advances.py -q --no-cov --tb=short
node tests/integration/supplier_advance_migration.mjs /tmp/supplier-advance-native-pg-adapter.mjs
/root/samchat/.venv/bin/python -m black --check tests/integration/test_supplier_advance_submission_postgres.py
/root/samchat/.venv/bin/python -m isort --check-only tests/integration/test_supplier_advance_submission_postgres.py
/usr/bin/flake8 --max-line-length=88 tests/integration/test_supplier_advance_submission_postgres.py
node --check tests/integration/supplier_advance_migration.mjs
/root/samchat/.venv/bin/python scripts/check_runtime_packaging_consistency.py
/root/samchat/.venv/bin/python scripts/ci/check-registration-operational-surface.py
/root/samchat/.venv/bin/python scripts/ci/check-accepted-regressions.py
/root/samchat/.venv/bin/python scripts/ci/check-pr-quality-gate.py
git diff --check
```

The aiosqlite test-only path and native adapter reuse existing local packages and
PostgreSQL 16 binaries. They are validation artifacts, not new dependencies.
The native pytest fixture starts a fresh Unix-socket-only cluster; no production
DSN or user data is used. The native test file does not rely on that adapter.

### Results and acceptance coverage

- Red before fix: 2 native tests (text/varchar) failed at the real session.commit
  with aprobaciones_accion_check and the exact action from the incident.
- Final Python run: 63 passed in 33.23s, no skips. Of these, 20 exercise native
  PostgreSQL and 43 retain the existing supplier-advance regression suite.
- Native coverage: actual service and route commit, independent readback of
  document/audit/CFDI, idempotent browser replay, atomic rollback after an injected
  audit integrity failure, successful reuse of fiscal evidence after rollback,
  preservation of 25 existing actions and history, admission of the one supplier
  event, unknown-action rejection, repeatable migration without replacing its
  OID, and survival of the existing schema guard.
- Drift cases: absent CHECK, unknown action, permissive replacement, additional
  clause with the same action list, and nonvalidated CHECK all abort and preserve
  the prior definition/OID. Every case runs for both supported column types.
- SQL suite: passed, including old-check failure/new-check admission, rerun,
  unknown actions, cent precision, total/partial applications, real 0.01 excess
  and all previous advance evidence protections.
- Formatting/lint apply to the new Python file; no unrelated legacy formatting
  is changed. The new file, JavaScript syntax, whitespace and local release gates
  are checked before final review. No Python runtime code changed, so no new
  runtime typecheck/build or Python changed-source coverage claim is made.
- Existing code emitted 1171 SQLAlchemy/datetime deprecation warnings. These
  warnings are not hidden or repaired in this correction. An early development
  run had 8 drift tests fail because the harness expected SQLAlchemy DBAPIError
  while the native driver correctly raised asyncpg RaiseError; the assertions
  were corrected, and all final cases pass. One new line-length finding was
  corrected before the final check.

Declared fixture doubles: authorization-route preparation (policy fixtures are
outside this audit-storage correction), Telegram scheduling, and attachment
column introspection pinned to the ORM fixture. Parser, fiscal reservation,
document/attachment/audit persistence, DB CHECKs, commit and rollback remain real.
This does not replace authentication-middleware or human Finance UAT evidence.

No functional deviations from the approved specification. No production data
writes or schema changes were performed. Publication/activation of this extension
and authenticated Finance UAT remain pending their corresponding checkpoints.


## Final approval and publication — 2026-11-03

The user approved the final diff in response to the explicit request to continue
through deploy. Commit, push, PR, merge and controlled SQL activation are now
authorized for this extension; no invoice submission or Finance UAT is delegated.
The runtime changed independently to the national CTT release. Its supplier
service and audit schema guard match the reviewed versions; activation remains
SQL-only, preserving the actual serving application. The latest canon amendments
and their register hashes were inspected and verified.

Read-only implementation validation found no blocking findings. All 63 Python
cases and the SQL suite pass; new-file Black/isort/flake8, JS syntax, diff hygiene
and local packaging/registration/regression/PR-contract gates pass. Original
models, service, startup patches and historical migrations remain unchanged.
