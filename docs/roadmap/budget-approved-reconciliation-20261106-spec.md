# Conciliación de movimientos en presupuestos aprobados — especificación técnica

Fecha: 2026-11-06. **Propuesta para aprobación técnica; no autoriza implementación ni aplicar el lote.**
Historia aprobada: `historia.md`, SHA-256 `24d770dd4086f2fd7ed62426daca83944b83fa1fdb21527404ed8117198a7e4b`; respuesta «Aprobar historia de reparación» registrada en `aprobacion-historia.json`. Sus bytes permanecen intactos aunque el encabezado original diga propuesta.
Base investigada: release servido `72e884752602e1ff27c2dfff8e810c17c7cec1e8`; main fusionado `223bda7377072a3851cf819200320fbf8527a5f6`. Preservar la diferencia CxC ya servida y el checkout raíz ajeno.

## Implementation approach

1. Añadir `samchat.budgets.reconciliation`, propietario acotado de una conciliación nueva hacia una línea expense **existente** de la versión financiera raíz `approved`. Mantener intacta la política editable-only del writer genérico; no llamar a `upsert_budget_line_for_concept`, crear líneas/conceptos, reforecast ni aprobación/freeze.
2. Separar funciones core `preview_approved_movement_reconciliation(...)` y `apply_approved_movement_reconciliation(...)`, con sesión transaccional entregada por el adaptador. El core no crea engines, no hace DDL, no confirma ni revierte transacciones del caller. Reutiliza `build_budget_actuals_snapshot`, `budget_movement_key`, validación de scopes/gobernanza y `_audit_budget_event`.
3. Parámetros de intención: versión raíz, torneo/ejercicio, clave canónica exacta, línea destino, actor y motivo no vacío acotado; aplicación añade `expected_plan_sha256` y confirmación explícita. La clave debe existir exactamente una vez en el snapshot; no aceptar el alias documental amplio `document:UUID` si no es la clave canónica específica. No ampliar una decisión `accounting:UUID` o `expense:UUID` al informe/documento entero.
4. Elegibilidad nueva: fuente actual sin línea ni asignación explícita, mismo concepto UUID fuente/destino, torneo/ejercicio/raíz iguales, concepto activo de gasto, fase fuente explícita compatible con el destino según los normalizadores canónicos, una única línea expense elegible. Rechazar versiones de distribución, frozen/draft/reforecast en esta acción, destino ajeno/ambiguo/inactivo, fuentes desaparecidas o incompatibles. El flujo editable anterior continúa por su owner actual.
5. Detectar AMEX directo **desde el snapshot acreditado**, incluidos `source_kind`/`origen`; rechazarlo y conservar su enlace a Control Presupuestal AMEX. No admitir un parámetro cliente para desactivar su prueba de autoridad. Reembolsos, estados de pago, clasificación fiscal y emisión de pólizas mantienen sus owners.
6. La vista previa devuelve elegibilidad/motivos, referencia y clave de fuente, estado/kind, importe observado sin reinterpretarlo, concepto/fase y línea, actor/motivo y fingerprint. Un `pending_accounting` sigue siéndolo después de asignar. No convertir el presupuesto bruto o gasto fiscal mediante1.16.
7. Hash canónico versionado sobre intención, actor/motivo, fuente exacta y evidencias/IDs/estados/importe/kind, raíz/estado, configuración/scopes relevantes, target y conjunto de candidatos elegibles y asignación anterior. Usar JSON estable/normalización decimal canónica; no incluir timestamps aleatorios ni campos de UI. Fuente y target se recalculan al confirmar; el navegador no proporciona importes ni verdad de clasificación.
   **Impacto íntegro antes de confirmar:** cargar el snapshot **sin filtro de fase** de todo el torneo/ejercicio/raíz. Simular las asignaciones virtuales con la misma proyección canónica de BMA, incluyendo su activación de conceptos y supresión deliberada del fallback. Extraer un helper puro de la proyección existente si es necesario para reutilizar exactamente sus reglas, sin cambiar su comportamiento normal ni duplicarlo en otro algoritmo. Comparar el mapa completo por clave: sólo las claves seleccionadas pueden pasar de sin línea al target; todas las demás líneas/estados de conciliación, kinds, importes y períodos siguen idénticos y los totales globales se conservan. Incluir ese impacto/fingerprint en el preview. Si un primer BMA desasignaría un movimiento antes asignado por fallback, **bloquear** el intento y explicar el efecto: no formalizar otros movimientos ni ampliar el lote automáticamente. El lote16 y cada operación individual necesitan esta prueba; el número16 es candidato, no una promesa de que todos son aplicables. Tras los INSERT, la proyección real debe coincidir con la virtual antes del commit.
8. Insertar sólo el BMA nuevo y un evento `approved_movement_reconciled` del audit existente con hash, actor, motivo, fuente, before=null y after/ID generado. `ON CONFLICT DO NOTHING` seguido de verificación exacta; **no UPSERT ni UPDATE** para esta acción. Una asignación diferente preexistente bloquea; no sobrescribirla ni reutilizar autoridad de PR515.
9. Idempotencia: bajo los bloqueos, buscar antes una auditoría de esta acción con el hash de intención confirmado, actor/clave/target y BMA actual exactos. Devolver el recibo original sin escrituras si concuerdan. No recalcular ingenuamente el hash inicial de «sin BMA» después de haberlo insertado. Si el BMA cambió o no hay auditoría acreditada, rechazar como conflicto. El replay sólo acredita la operación anterior y no autoriza una conciliación nueva sobre una versión ahora no elegible.
10. No se demostró un bug vivo en los50 BMA previos: conservar exactamente el fallback probado, la precedencia de `_apply_explicit_movement_assignments`, medidas y lectores; la extracción pura para simular impacto no cambia esa política. Verificar la proyección existente tras la nueva asignación; no reescribir agrupaciones fiscales usando claves de póliza ni cambiar totales globales por una reclasificación de línea.

### Autoridad, transacción y concurrencia

- Web: autenticación vigente y `_require_budget_access(..., "line_update")`; el core relee empleado activo/capacidad con el patrón de `_require_budget_actor` del owner semanal. El mapa actual concede estas capacidades globalmente a los mutadores existentes; **no se inventa una ACL por proyecto**. Aplicar además `require_ungoverned_gastos_project` y validar el torneo exacto del recurso. No añadir roles ni excepciones de gobernanza.
- La sesión de autenticación puede estar autobegun: el adaptador web abre una **sesión de unidad de trabajo nueva desde el mismo AsyncEngine canónico de gastos**, sin crear otro engine, secreto o almacén. `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE` —y `READ ONLY` para preview— es su primera sentencia antes de consultar actor/fuente. No usar SET después de auth ni rollback/commit sobre la sesión de autenticación. El operador entrega al core su propia transacción exterior SERIALIZABLE.
- Apply adquiere/refresca filas concretas en orden estable: Tournament `FOR SHARE`; versión raíz `FOR UPDATE`; líneas destino/candidatas ordenadas; conceptos/configuración y empleado `FOR SHARE`; filas fuente autoritativas derivadas del snapshot, nunca IDs confiados al cliente. Para documentos/gastos/pólizas usar padres antes de hijos y UUID ordenados, respetando los órdenes existentes de sus owners. No `LOCK TABLE` ni locks sobre todas las tablas.
- Las pruebas deben incluir cambio real de fuente mientras la operación espera: en SERIALIZABLE un cambio posterior al snapshot al adquirir el row-lock debe abortar; un UPDATE posterior a nuestro bloqueo espera hasta el commit. Recalcular el hash bajo esos locks. Deadlock/40001/lock-timeout se devuelve como conflicto con rollback íntegro y nueva vista previa; no reintentar una escritura automáticamente con el hash anterior.
- Cerrar la carrera con el writer **genérico no AMEX**: bloquear/refrescar la versión y después el target antes del chequeo editable y la escritura. Sigue aceptando exclusivamente draft/reforecast; no se llama desde la nueva acción. El branch AMEX conserva su orden fuente/import/global locks primero. Es un fortalecimiento del mismo precondition, no una vía approved genérica.
- Un conflicto de unique-key entre confirmaciones concurrentes no cambia asignaciones existentes: una gana, la otra devuelve la misma auditoría si es el mismo intento, o conflicto si son distintos. La revocación del actor, cambio de fase/actividad/target o nueva línea competidora debe provocar rechazo o rollback, nunca asignación con evidencia obsoleta.
- Confirmar la transacción de BMA+auditoría conjuntamente. Verificar el destino canónico y que sólo cambió la clasificación específica; fuente, kind/importe y las otras asignaciones permanecen iguales. No cambiar `budget_versions.updated_at`: también se conservan fingerprint y revisiones semanales.

## Data model changes

Sin migración ni dependencias nuevas. Tablas existentes de escritura permitida:

| Tabla | Operación nueva | Evidencia |
|---|---|---|
| `budget_movement_assignments` | INSERT de una clave ausente; conflicto verificado, no UPDATE/DELETE | raíz, concepto, línea, clave exacta, actor y timestamps existentes |
| `budget_version_audit_log` | INSERT append-only mediante `_audit_budget_event` | intención/hash, referencia, motivo, actor, before/after, ID BMA y contexto observado |

Proteger íntegros: versiones —estado/nota/aprobación/timestamps—, líneas y calendarios/revisiones/ingresos, conceptos/cuentas/scopes, documentos/gastos/CFDI, pólizas, pagos y las50 asignaciones previas. No escribir `historical_accounting_binding(s)`, aprobaciones contables ni crosswalk26. No ejecutar `ensure_budget_schema` desde las rutas/core nuevos; si el esquema existente no está disponible, bloquear.

## API changes

Dos POST HTML en `admin_budget_routes.py`, registrados dentro de la factoría existente:

- `/admin/presupuestos/versiones/{version_id}/conciliacion/preview`: payload mínimo clave+línea+torneo/ejercicio+motivo; autenticación/capacidad/CSRF; transacción READ ONLY; devuelve la revisión y ningún BMA.
- `/admin/presupuestos/versiones/{version_id}/conciliacion/confirm`: misma intención, hash esperado y confirmación explícita; dueño transaccional nuevo; errores de validación/conflicto sin cambios y redirect seguro al contexto de origen.

Usar el patrón existente de token sincronizador en `request.session` firmada, comparación constante y `_csrf_token` oculto; un helper de ámbito presupuesto no debe exigir rol de administrador de torneos. Verificar ambos POST y no aceptar token por URL. El preview guarda sólo un fingerprint/nonce/actor/contexto y tiempo de emisión en la sesión firmada, no el plan financiero completo; una sola vista previa vigente por sesión. Confirm liga también el motivo y rechaza intención/hash/nonce alterados, otra sesión/actor o expiración —15min de constante documentada—. Conservar el identificador de la confirmación exitosa para replay/recuperación del mismo intento; no renovar silenciosamente autoridad. La caducidad de15min bloquea una confirmación nueva o no aplicada. Recuperar después de ese plazo un recibo exacto ya confirmado es una lectura autenticada: exige el mismo actor autorizado, intención/auditoría y BMA actuales concordantes y devuelve el recibo histórico sin INSERT/UPDATE ni nueva facultad; si no existe tal recibo o cambió el BMA, rechazar. No regenerar nonce/hash para convertir una recuperación en confirmación nueva. No loguear tokens ni filas financieras.

## UI changes

- Mantener `can_edit_lines` según editable-only. Añadir `can_reconcile` separado: mutador vigente + versión financiera approved + contexto/gobernanza admitidos; el backend vuelve a validar, el flag no es autoridad.
- En pendientes, usar el estado canónico `unassigned`, no «línea fuera del filtro visible». Ofrecer sólo destinos ya existentes y elegibles y botón **Revisar conciliación**, sin campo de presupuesto ni edición de fase. Cambiar el diagnóstico a «pendiente de vincular» cuando exista target y a la causa concreta cuando no exista; una asignación ya existente fuera del filtro conserva su identidad y no habilita escritura. No reaprovechar el formulario `assign-existing`.
- Pantalla previa: referencia/clave/estado, concepto-fase/target, motivo, advertencias y confirmar/cancelar. Mostrar motivos comprensibles para13 bloqueados e invitación al owner AMEX donde corresponda. Lectores sin permiso conservan vista informativa sin control de escritura.
- Introducir `Request` y el CSRF sólo en el detalle/formularios pertinentes. El fallo conserva el contexto y exige nueva revisión; el éxito distingue «asignado a partida» de «contabilizado» o «pagado».
- Mantener consumo canónico de actuals y exportes. Pruebas HTML/XLSX/PDF verifican el mismo destino y medidas de cada fuente, sin imponer igualdad entre gasto fiscal y resultado contable.

## Background jobs

Ninguno, sin conciliación automática ni backfill de startup. El expediente privado de85pares/879identidades/26filas y las propuestas16+13 sigue siendo de revisión. No crea una decisión financiera por descargarlo.

### Operador acotado y autoridad del lote

Añadir un operador siguiendo preview/hash/commit/backend/auditoría de los cierres previos; reutilizar el **mismo core** dentro de una transacción exterior para todo el lote. Manifest fijo `config/budget_approved_reconciliation_20261106.json` con los16 pares de `16-propuestas-nuevas.csv`, sus UUID, raíz, evidencia/hash y actor autorizado. No selecciona nuevos candidatos por nombre, importe o fila de Excel. Guardar los13 bloqueados como evidencia, no como targets.

Preview es READ ONLY. Aplicación requiere diff aprobado, aprobación explícita del lote16 —distinta de la historia y de los16 antiguos de PR515—, receipt privado de esa autorización ligado a manifest/CSV/code SHA, actor vigente, release limpio realmente servido y conexión de archivo canónico idéntica a la del proceso. No se concede aprobación escribiendo un booleano en el manifest. Rollback del lote completo ante un target/source cambiado, permiso revocado o fallo entre items; guard SQL limita escrituras a los BMA nuevos y audit append-only. Recibos/fingerprints anteriores y posteriores independientes; rerun sin nuevos BMA/auditorías. No aplicar el lote por desplegar la UI. La simulación de investigación ya verificada sobre el contexto actual produjo370movimientos:16 cambios esperados y354sin cambio; kinds/importes,11pending_accounting y conjunto de conceptos activados intactos;0fuentes AMEX directo. Es evidencia del corte investigado, no aprobación del lote ni garantía futura: el owner repetirá la simulación y el hash con fuentes frescas bajo sus controles.

## Test plan

| Historia | Prueba de aceptación obligatoria |
|---|---|
| AC1/2 | approved+línea exacta única; sin upsert/reforecast; misma raíz/torneo/ejercicio/concepto/fase; rechazo frozen/distribución/ajeno/ambiguo/inactivo/sin fase |
| AC3 | usuario anónimo/lector/inactivo/revocado; capacidades reales y gobernanza; CSRF ausente/erróneo, otra sesión/actor, alteración de intención/motivo/hash y expiración |
| AC4 | PostgreSQL real: dos confirmaciones iguales/diferentes, generic draft-writer frente a aprobación/reconciliación, mutación de fuente mientras espera, cambio de target/actividad y fallo tras BMA antes de audit/commit; rollback exacto |
| AC3/4 | nonce vencido con plan aún no aplicado: rechazo sin escrituras; recibo exitoso exacto recuperado tras15min: sólo lectura, sin renovar autoridad; replay con actor distinto o BMA cambiado: rechazo y ninguna reasignación |
| AC4/5 | impacto virtual y real idénticos: primer BMA que eliminaría fallback de otra clave se bloquea; batch y selección individual no amplían efectos. replay prueba absent→inserted sin falso drift; conflicto con BMA cambiado; claves contable/gasto/documento específicas, dos gastos del mismo informe y AMEX directo rechazado sin bypass |
| AC5/6 | kind/importe/moneda/estados/pago/póliza intactos; dos propuestas pending_accounting no producen ledger; no cambia el fallback probado; matriz y exportes conservan grano/base y totales |
| AC6/7 | manifest16 exacto (6Liga/10Copa),13 bloqueados (8sinlínea/5retirados),50 BMA viejos y16PR515 intactos; HWC3+13 y N/D/cobertura retenida sin cambios; pack85/26 no genera autoridad |
| AC8 | ensayo de lote restaurado con SQL guards, hash obsoleto, drift, rollback intermedio, ejecución/idempotencia y comparación independiente de tablas protegidas/263líneas/40-52-52 |

Cobertura mínima85% del **diff completo src+scripts**, no sólo src. Ejecutar focales nuevos, `test_budget_monthly_plan.py`, distribución/weekly overlay, datos documentales y autorización; tests de rutas/render y reproducción en navegador con identidad sintética; PostgreSQL sin omitir cohortes obligatorias. Black/isort/flake8 focal y diferencial contra deuda heredada, inventario privado regenerado, guards y `git diff --check`. Reportar checks fallidos/no ejecutados con atribución; no afirmar lint/typecheck global limpio. Validador independiente read-only antes de presentar PR/diff final.

## Files expected to change

- Nuevo `src/samchat/budgets/reconciliation.py`; `src/samchat/budgets/service.py` sólo para lock/refresco del writer genérico no AMEX y extracción pura de su proyección de BMA para el cálculo contrafactual, con semántica/fallback preservados.
- `src/devnous/gastos/routes/admin_budget_routes.py`; `admin_budget_ui.py` para cap/formulario/preview/confirm y CSRF; no tocar UI contable ni estrictos bindings.
- Nuevo `scripts/reconcile_approved_budget_movements.py`; `config/budget_approved_reconciliation_20261106.json`.
- Nuevos tests unitarios de core/operador, rutas/UI y PostgreSQL; fixtures mínimos en `test_budget_monthly_plan.py` sólo si cambian por los locks reales.
- Historia/spec/build-report versionados bajo docs/roadmap/docs/ops; `docs/private-plugin/route-inventory.json` y `route-matrix.csv` regenerados. No crear una superficie nueva del asistente/plugin writer.
- No cambios en `accounting_bridge`, `report_layouts`, fuentes históricas, policies, aprobación/pago ni canon automático.

## Risks

El bloqueo actual es una capacidad ausente, no evidencia de que los lectores hayan perdido asignaciones. No arreglar lo no reproducido. No confundir clasificar presupuesto con contabilizar o corregir bases fiscales. Mantener separadas las16 propuestas nuevas, las16 antiguas y los13 bloqueados. Las cuentas compartidas, falta de balanza septiembre y la aprobación de17hojas del reporte siguen pendientes de Contabilidad.

**Canon unchanged:** añade una acción acotada, explícita, idempotente y auditada del owner presupuestal sobre un vínculo auxiliar; conserva la inmutabilidad económica y estado del presupuesto aprobado y los owners fiscales/pago. AMEX ya tiene su autoridad separada y no se amplía. Si al implementar fuera necesario cambiar reglas/roles/estado/owner o el canon, detener esa porción y presentar texto/alcance para revisión humana; no editarlo automáticamente.

## Rollback considerations

1. Trabajar aislado desde main actual, preservar el release servido y la diferencia CxC. Sin tocar el checkout raíz ajeno.
2. Antes de escribir datos productivos: respaldo completo fresco READ ONLY con propietario local —sin ampliar privilegios de la aplicación—, SHA/tamaño/identidad de cluster/base comparados con la conexión del proceso servido; snapshot protegido antes del lote. No usar los backups viejos para omitir este paso.
3. Ensayo sólo en la base local propiedad de esta tarea **`samchat_approved_reconciliation_restore_20261106`**. La aprobación técnica incluye crear/reiniciar/restaurar/eliminar **exclusivamente esa copia desechable**, verificando SHA del backup previo a cada reset, sin `--force` ni targets dinámicos y sin tocar `devnous_db` u otras bases. El operador de ensayo queda fijado a ese nombre/socket, nunca una URL arbitraria de restore. Esta autorización de infraestructura debe quedar explícita en el checkpoint de aprobación de la spec.
4. Todo fallo precommit revierte BMA+audit; ninguna restauración global de producción. Un error posterior al commit requiere inspeccionar recibo/BMA/audit antes de repetir. Una corrección posterior de un vínculo ya confirmado es otro alcance autorizado con historia, no borrar auditorías o sobrescribir fuentes en silencio.
5. Presentar diff/lote y validación antes de solicitar aprobación final. Tras autorización de publicación/merge/deploy y checks remotos vigentes todos verdes (baseline PR526:9), conservar tag del commit selectivo servido, usar el único `50-current-release.conf`, backup del selector y health/readiness/protected401/NRestarts/journal/rollback técnico. Desplegar no aplica automáticamente los16.
6. Aplicar sólo el lote final expresamente aprobado, con preview/hash nuevo y commit servido limpio. Comparación independiente posterior de BMA/audit, fuentes protegidas, importes/ingresos/semanas y visualización. UAT autenticada/aceptación financiera separadas. Eliminar ramas temporales tras merge, conservar PR/tag/recibos privados; nunca publicar backups/snapshots financieros.
