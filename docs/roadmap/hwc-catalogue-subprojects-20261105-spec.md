# HWC: restaurar subproyectos del catálogo y conservarlos al editar el torneo

Fecha:2026-11-05. Estado: historia y especificación propuestas; diagnóstico READ ONLY completado. Sin cambios de código, catálogo o presupuesto productivos.

## Historia y alcance

Como responsable de Presupuestos necesito que las partidas de HWC respeten el subproyecto del último catálogo aprobado y conserven esa clasificación cuando se edite la configuración del torneo, para seleccionar y consultar partidas sin duplicarlas ni perder referencias operativas.

La importación anterior del catálogo y sus decisiones permanecen aprobadas. Este documento solicita sólo la ampliación correctiva: reparar el alcance actual, corregir una fase de importe cero mediante su owner y retirar el reset implícito al guardar configuración. El último punto cambia el comportamiento compartido de edición de torneos; no estaba en el PR515.

## Evidencia actual

Runtime: samchat-gastos.service, /srv/samchat/releases/gastos-prod-710021a2f-ctt-source-525. Backend: devnous_db verificado, consultas REPEATABLE READ READ ONLY. Canons leídos en orden, hashes contra registro válidos.

Fuente: catalogo_presupuestal (3).xlsx, SHA75da03a5853199c3fab3db96699ed1950bb6b637b2702630b8bbcfb24e3d4eae. El catálogo contiene75 partidas activas HWC;74 tienen subproyecto explícito y una celda vacía. Producción contiene76 activas: las75 del archivo y Viajes de supervisión/Scouting añadida posteriormente. Las76 están globales con listas de alcance vacías. Se restauran sólo74 UUIDexactos; se preservan la partida explícitamente global, Scouting y todas las inactivas. No se infiere ni crea alcance para ellas.

Las77 líneas aprobadas suman30,384,939.78;76 fases coinciden ya con el archivo por UUID. La restante, línea0728a2f8-a64d-49fe-b53b-c2e93762b9c2, concepto5f31a896-e460-439d-b8ba-2c9eaf4919b7, importe0.00, conserva Hospedaje y alimentos y debe quedar Gastos Preoperativos. La revisión semanal HWC vigente es02748c2e-28d7-4e01-901e-710026bea568, semana52.

74 partidas a restaurar tienen11 documentos y20 gastos relacionados. Hay3 documentos con fase vacía y13 gastos con fase distinta/vacía respecto al alcance del catálogo. No se autoriza reescribir esos registros ni convertirlos en conciliados; se preservan íntegros y quedan en la lista de revisión.

Mecanismo hallado: POST/admin/torneos/update/{id}, admin_routes.py13236-13256, detecta cambios de etapas/categorías y llama clear_budget_concept_scope_for_tournament. Éste borra todos los alcances activos e inactivos. tournament_scope_config_changed compara listas aunque declara ignorar orden: reordenar también dispara el borrado. Las283 partidas HWC comparten updated_at2026-10-26T18:21:41.515727Z. El patrón coincide con ese mecanismo; no se atribuye actor ni evento concreto sin evidencia adicional.

## Criterios de aceptación

1. Los74 UUID de plan.json exponen y filtran el subproyecto exacto del archivo, sin nuevas partidas, duplicados o modificación de active/cuentas/identidad.
2. Las otras2 partidas activas y todas las inactivas permanecen intactas.
3. Las77 líneas y sus importes/referencias se conservan; sólo cambia la fase de la línea de0.00 identificada. Semanas e ingresos esperados permanecen idénticos; HWC sigue en52, Liga40 y Copa52.
4. La renovación del snapshot semanal HWC utiliza el owner existente y conserva el anterior, evitando invalidar la distribución. Borrador y versión aprobada se conservan; aprobación normal sin bypass.
5. Guardar nombre/descripción/orden, reordenar etiquetas o añadir una etapa no borra clasificaciones. Campos de etapas/categorías omitidos conservan el valor previo.
6. Si se intenta retirar una etiqueta usada por una partida con alcance explícito, se rechaza atómicamente con un mensaje que pide reasignar primero su alcance en Presupuestos. No se cambia a global automáticamente. Se preservan roles, CSRF y mutabilidad del torneo.
7. Documentos, gastos, pagos, pólizas, asignaciones e importes históricos quedan idénticos. Los16 registros con fase faltante/diferente se reportan sin mutación ni aprobación ficticia.
8. Ensayo real restaurado prueba rechazo de hash obsoleto, rollback entre owners, aplicación completa e idempotencia; comparación independiente READ ONLY prueba el diff exacto y los presupuestos/semanas.

9. La edición del torneo y las altas/cambios de alcance de sus partidas se coordinan transaccionalmente. Una retirada de etiqueta concurrente con una asignación no puede dejar una referencia huérfana. Al recibir scope_labels explícitos, el owner valida contra la configuración vigente; una edición ajena al scope no reescribe metadata heredada.

## Especificación técnica

Reutilizar update_budget_concept y su auditoría para restaurar los scopes con actor de presupuesto vigente, usando sólo el lote74 fijado por UUID y SHA del Excel. No reaplicar globalmente el importador ni usar metadata SSOT antigua como autoridad. Restaurar sólo las claves de alcance del owner; preservar el resto de metadata.

Actualizar la línea de0.00 mediante el propietario de presupuestos y el flujo normal de versión/aprobación. Retirar/renovar exclusivamente la revisión semanal HWC mediante tournament_weekly_overlay, con snapshot previo conservado y prueba de igualdad de todos los importes/ingresos efectivos. Una UPDATE directa de phase aislada está prohibida porque forma parte del fingerprint semanal. Si los owners requieren un paso adicional que amplíe los campos afectados, detener y documentar antes de aplicarlo.

En tournament_phase_service, comparar conjuntos para detectar cambios semánticos sin considerar orden. En la ruta de actualización preservar campos omitidos, eliminar el reset implícito y validar removals contra los alcances explícitos almacenados de las partidas del torneo antes de persistir. Bloquear cualquier etiqueta retirada que todavía sea referida; su reasignación se hace mediante el owner de catálogo. Añadir etiquetas preserva scopes. No añadir un nuevo modo global por defecto ni un mecanismo alterno de escritura.

Concurrencia: reutilizar el patrón existente de coordinación transaccional por torneo (advisory transaction lock u otro lock owner equivalente), con el mismo orden de adquisición antes de leer configuración/alcances en ruta y owners de creación/edición. Releer el contexto tras adquirirlo y validar nuevas etiquetas explícitas contra etapas/categorías vigentes. No basta con un chequeo sólo en la ruta: el alta de una partida puede competir con retirar una etapa. Añadir prueba PostgreSQL concurrente de ambas operaciones y demostrar rollback o rechazo, nunca referencia huérfana.

Operador: patrón existente de preview, SHA/commit exactos, actor autorizado, transacción exterior que contiene commits internos, before/after íntegros y allowlist de diff; respaldo fresco, SQL guards, ejecución desde release servido y recibos posteriores independientes. No DDL ni dependencias nuevas.

Archivos previstos: src/devnous/gastos/services/tournament_phase_service.py; src/devnous/gastos/routes/admin_routes.py; helper acotado en src/samchat/budgets/service.py para validación y coordinación entre owners; scripts/repair_hwc_catalogue_subprojects.py; config/hwc_catalogue_subprojects_20261105.json; tests focales unit y PostgreSQL; docs operativos; inventario privado JSON/CSV regenerado por el cambio de ruta. La implementación debe partir de checkout aislado, preservar cambios ajenos y reconciliar con el release servido antes de promoción.

Pruebas: catálogos por UUID,75fuente/76activos/74cambios; casos global/inactivo intactos; edición sólo nombre, reordenar, añadir, omitir, retirar etiqueta usada y rollback; selección fase correcta y rechazo fase ajena; lectura histórica de los31 registros preservada y sus16 gaps explícitos; presupuesto matriz/exportaciones con Gastos Preoperativos; snapshot semanal vigente; suite de guards de autoridad; inventario; ensayo PostgreSQL y validación independiente. Pruebas de implementación todavía no ejecutadas porque no se ha aprobado este alcance.

## Rollback y límites

Respaldar metadata anterior, línea0 y revisiones/calendarios. Fallos antes del commit revierten todo. Corrección posterior por owners y evidencia auditada, sin borrar snapshots. No restaurar a ciegas una base completa ni alterar pagos/contabilidad para ajustar clasificación. Sin normalización de históricos ni alcance fiscal nuevo. UAT autenticada permanece separada.

Canon unchanged: restaura clasificación ya aprobada y conserva identidad, historia, owners, autoridad y economía; no altera reglas fiscales ni crea una fuente financiera. El control de edición evita perder alcance sin una decisión explícita. Si la revisión concluye que requiere enmienda del canon, presentar el texto para revisión humana antes de implementarlo.
