# AMEX directo — revisión QA independiente del recorrido de Contabilidad

Fecha: 2026-10-15. Fuente: `/srv/samchat/releases/gastos-prod-a983be79e-pr489-amex-odilon-nav` (PR #489). Revisión de código y pruebas, sin mutaciones productivas, SAT externo, generación real de pólizas ni aceptación autenticada. El assessment de datos reales y la simulación aislada corresponden al agente principal; no se atribuyen sus resultados a esta revisión.

## Relevant files

- `src/devnous/gastos/routes/admin_amex_direct_routes.py`: carga, clasificación, vigencia, excepciones, corte/exportación y bandeja CP.
- `src/devnous/gastos/services/amex_direct_context_service.py`: opciones de proyecto/categoría y lectura de estados.
- `src/devnous/gastos/services/amex_direct_matching_service.py`: identidad de comercio, candidatos estrictos y vínculo manual.
- `src/devnous/gastos/services/amex_direct_status_service.py`: universo autorizado de consultas SAT.
- `src/devnous/gastos/services/amex_direct_budget_service.py`: asignación, devolución y bandeja CP.
- `src/devnous/gastos/services/amex_direct_accounting_service.py`: preparación, confirmación y exportación desde snapshot.
- `tests/browser/test_amex_direct_workflow.py` y `tests/unit/gastos/test_amex_direct_routes.py`.
- `docs/roadmap/amex-sat-budget-direct-20261015-spec.md`: alcance aprobado, categoría=BudgetConcept, Contabilidad define proyecto/categoría y CP sólo partida.

Bootstrap: los tres canons se solicitaron en orden producto→ingeniería→sweep; SHA256 verificados contra register: 7062756077f455741f03027b335f0be6627476c23f9f0b4ab8744f0a9608c99e, bd011d2d33deaea655e4bc3f863e61610454520544d477e1cba1560f6fabd47c, 2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985. Sin propuesta de editar canons.

## Confirmed patterns — hallazgos priorizados

### QA-01 · P1 operativo · Resolver una factura pendiente exige identificador interno sin herramienta de selección

**Esperado:** un contador pueda localizar y elegir un CFDI SAT descargado desde un cargo pendiente, viendo emisor, UUID fiscal, fecha, total, vigencia y conflictos. Las restricciones fiscales siguen intactas.

**Actual confirmado por fuente:** `_exception_forms`, rutas líneas 887–895, pide texto libre «UUID de registro CFDI» (`cfdi_id`) y no ofrece buscador, lista de candidatos ni enlace contextual a facturas. El owner manual, matching líneas 638–643, ejecuta `session.get(CFDIReport, cfdi_id)`; requiere el UUID interno de registro, no el UUID fiscal del XML. La tabla `_row`, líneas 657–660, presenta preferentemente `cfdi_uuid`, con lo que copiar el identificador fiscal visible no satisface este contrato. Si no existe PK igual, devuelve `cfdi_missing`.

**Reproducción:** cargo sin asociación → formulario «Declarar vínculo fiscal manual» → introducir el UUID fiscal de su factura en el único campo UUID → owner no encuentra el registro. Confirmar con fixture donde `CFDIReport.id != CFDIReport.cfdi_uuid`; no usar datos productivos para mutar vínculos.

**Clasificación:** defecto del recorrido de usuario/descubrimiento; no justifica quitar el control de vigencia ni permitir facturas ajenas. Puede existir consulta de IDs en otro módulo, pero esta pantalla no proporciona un camino conectado para obtenerlos.

### QA-02 · P2 operativo · Proyecto administrativo etiquetado «Torneo», categorías mezcladas y sin dependencia visible

**Esperado:** al seleccionar Gastos Administrativos – Dirección General, la pantalla muestre sus categorías autorizadas, sin aparentar que requiere un torneo deportivo adicional.

**Actual:** rutas 748–762 llaman «Torneo» al catálogo `Tournament`, que también alberga proyectos administrativos. Ofrece además otro selector «Proyecto no torneo». Imprime `concepts` de todos los proyectos autorizados sólo con ID/nombre, sin atributos de pertenencia ni filtro al cambiar proyecto. El servicio context 345–434 sí devuelve tournament_id/project_code por categoría; el HTML descarta esa relación. El owner budget 105–140 exige exactamente uno de los dos contextos y categoría del mismo contexto, y devuelve `budget_category_context_mismatch` cuando se mezcla.

**Reproducción:** elegir Dirección General en el primer selector y una categoría homónima de otro proyecto en la lista global; guardar clasificación → pending/conflicto. El usuario no puede distinguir opciones homónimas desde esta UI.

**Límite:** exigir una categoría BudgetConcept es comportamiento aprobado. No es categoría deportiva ni debe eliminarse automáticamente. El assessment principal debe confirmar los catálogos efectivos y las opciones de la pantalla real.

### QA-03 · P2 funcional · Bandeja CP conserva cargos que ya terminaron en póliza

**Esperado:** después de confirmar el estado, los cargos reconocidos dejen de aparecer como pendientes accionables de CP; su historia siga disponible.

**Actual:** budget `list_direct_budget_queue`, líneas 599–635, incluye cualquier item con `budget_status in {sent,assigned}` sin excluir accounting_status recognized ni imports con corte. accounting 603–605 sólo cambia accounting_status a recognized/excluded; conserva assigned. La UI CP 1198–1208 sigue dibujando «Asignar partida existente» y «Devolver a Contabilidad». Ambas mutaciones llegan a `require_unfrozen` y rechazan un estado ya cortado.

**Reproducción:** clasificar → enviar → asignar → confirmar póliza → abrir bandeja CP: el mismo cargo assigned/recognized sigue presente con acciones incompatibles. Prueba propuesta: fixture real o stub del reader con budget_status=assigned, accounting_status=recognized, cut existente; esperar exclusión de la bandeja pendiente y conservación del histórico.

**Clasificación:** defecto funcional de cierre/cola; no genera duplicación contable porque el owner bloquea. También los assigned sin corte aparecen como accionables aunque una nueva asignación no idempotente devuelve `budget_submission_required` (budget 325–333).

### QA-04 · P2 UX · Guardar clasificación exige marcar confirmación que dice que se envía a CP

**Actual:** el mismo formulario contiene Guardar clasificación y Enviar a CP. `_confirm` líneas 319–320 genera checkbox HTML required, cuyo texto aquí es «Confirmo enviar los cargos seleccionados a Control Presupuestal» (774–776). El navegador exige marcarlo incluso para Guardar clasificación. Sin embargo, el owner de clasificación no envía cargos a CP: sólo cambia a classified; la acción enviar es otra transición explícita.

**Reproducción browser:** seleccionar cargo/proyecto/categoría/edición y pulsar Guardar con checkbox vacío → bloqueo HTML requerido; marcar «Confirmo enviar…» y pulsar Guardar → sólo clasifica. Dos intenciones confundidas.

**Corrección candidata:** separar la confirmación de envío de la acción guardar, sin fusionar transiciones ni quitar confirmación del envío real.

### QA-05 · P2 UX · La confirmación contable no presenta la propuesta de póliza calculada

**Actual:** accounting `_prepare` devuelve `progress.exceptions`, `new_count`, `prior_count` y `lines` (líneas 459–466). La ruta 809–831 sólo muestra la primera razón si bloqueado o fecha/motivo/confirmación si ready. No renderiza desglose Debe/Haber ni totales de la propuesta antes de que Finanzas firme el estado completo. La tabla sí muestra cuentas por cargo; no equivale a revisar movimientos/impuestos/pasivo del journal completo.

**Esperado operativo:** antes de confirmar, poder revisar lo que se va a generar y la totalidad de bloqueos actuales, con responsables; nunca inventar cero ni permitir exportación parcial.

**Clasificación:** hueco de transparencia y resolución, no prueba de journal incorrecto. Conviene resolver junto con el recorrido, sin cambiar owners financieros.

## Confirmed patterns — controles correctos que no deben reportarse como bugs

1. Sin UUID declarado ni identidad de comercio/alias inequívoco, `eligible_candidates` matching 157–174 y `_status_candidates` status 38–55 retornan cero candidatos. Esto responde a política aprobada: monto/fecha solos no confirman identidad. La fricción es que la UI ofrece un campo de descriptor/RFC manual y poca guía; no corresponde habilitar heurística insegura.
2. SAT exige consentimiento; descargar factura no prueba vigencia; receipt TTL 24h y owner verificado. GET no debe consultar SAT.
3. Clasificación no es partida; Contabilidad elige proyecto+BudgetConcept, Juan Pablo asigna BudgetLine. El contador solo no tiene que asumir autoridad CP.
4. El estado completo bloquea póliza si falta un cargo, evidencia, mapping o exclusión explícita de crédito. No fabricar CFDIs/IVA, no ocultar cargos ni producir póliza parcial para que el escenario aparente terminar.
5. Programar pago abre owner existente `/admin/gastos/amex/conciliacion`; no paga ni debe esperar CFDI. La ruta antigua de pago es intencional según spec, distinta de regresar a conciliación por INFORME.
6. Un estado legacy no puede abrirse como directo cambiando la URL; es una incorporación de fuente diferente, separada de bugs de UI. El estado activity ya directo no necesita esa migración.

## Likely commands / verificación realizada

Lectura con `cat`, `sed -n`, `rg -n` y `rg --files` de archivos anteriores; `sha256sum SAMCHAT*2026-09-10.md` y cotejo textual del register. Sin pruebas de mutación realizadas por este agente, sin SAT. No se declara browser UAT ni una póliza productiva generada.

Para cierre del assessment principal: ejecutar en PostgreSQL aislado el recorrido real del owner y exportadores usando bytes del CSV, actores sintéticos con los mismos límites de rol, facturas/evidencia fiscal explícitamente sintéticas, BudgetConcept→BudgetLine→mapping, período abierto, y assert una póliza balanceada/inmutable y replay sin duplicados. El estado productivo real debe evaluarse sólo en lectura separada: las facturas faltantes o decisiones no proporcionadas no se sustituyen por fixture como evidencia de aceptación.

## Test impact

La suite browser existente `tests/browser/test_amex_direct_workflow.py` verifica intake/preview/render desktop/móvil, selección de línea CP y corrección de contexto con servicios AsyncMock. Su prueba «…complete_state» termina tras cargar/renderizar; no recorre matching canónico→clasificación→asignación real→confirmación→archivo COI. Por tanto su verde no acredita el resultado que pide el usuario.

Prioridades de aceptación: (a) contador resuelve factura sin conocer PK internos; (b) Dirección General usa categoría de su proyecto con opciones claras; (c) Guardar no afirma envío; (d) JP resuelve partida y la cola se limpia tras reconocimiento; (e) contador ve propuesta y obtiene XLSX/CSV/ZIP válidos; (f) replay/múltiples fuentes no duplican; (g) pago mantiene su owner y trazabilidad.

## Risks

- Actividad real puede contener créditos, divisas, propinas o cargos sin CFDI vigente. Un test sintético completo no autoriza decisiones contables sobre esos movimientos.
- No retirar filtros de scope/receiver/card, checks de SAT/uso global o corte completo para resolver fricción.
- Mantener cambios de implementación acotados y revisables; la QA no requiere reescritura del módulo ni migración automática de fuentes legacy.

## Unknowns

- Esta revisión no ha observado sesión autenticada real ni confirmado datos vivos de activity; lo verifica el assessment principal.
- No se establece cuántos cargos tienen CFDI elegible, cuántos alias deben confirmarse ni qué proyectos/partidas decide negocio. No inventar esos resultados.
- Hallazgos de fuente QA-01 a QA-05 deben convertirse en reproducciones concretas del escenario aislado/browser antes de afirmar que todos impactaron a los 148 movimientos reales.
