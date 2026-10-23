# Especificación: AMEX en una pantalla

2026-10-23. Historia aprobada en la conversación, incluida ejecución hasta despliegue. Fase inicial: investigación concluida; construir dentro del alcance aprobado. Base f15e9a47650913097cfb63b2859dfe76d17e7f1c. Fuente productiva verificada con tres canons leídos en orden y sus hashes válidos.

## 1. Arquitectura preservada

FastAPI/Starlette, HTML renderizado y JavaScript nativo, `<dialog>` existente en la plataforma y estáticos ya montados en `/static`. Conservar `_page` y tokens de `_workspace_shell_styles`. Usar `AMEX_CONTENT_START/END`/extracción de contenido controlado existentes. No introducir framework, dependencia, SSE/websocket, background job, cambio de esquema o lógica contable. Los mismos handlers y owners siguen validando todas las lecturas/escrituras.

## 2. Contrato de presentación

`admin_amex_direct_routes._html` admite metadatos sólo de presentación y produce un contenedor único `#amex-workspace-view` con `data-amex-view="intake|statement|detail|budget"`, `data-statement-url` y `data-source-version` cuando corresponda. Las páginas raíz intake/statement se marcan `data-amex-root="true"`. Detalles/cola conservan navegación normal cuando se abren directamente. Incluir `/static/amex_workspace.css` y `/static/amex_workspace.js` con versión derivada del contenido para evitar caché antigua.

La estructura raíz tendrá tres etapas, mensajes de estado, filtros y acciones de selección visibles antes de la tabla. Controles operativos secundarios se agrupan en fuentes de diálogo `data-amex-dialog-source="treatment|project|budget|no-cfdi|review|journal"`. Cada disparador usa `data-amex-open` con el mismo nombre y un título accesible. Las formas mantienen sus actions/methods/campos y la asociación de checkbox externo `form="direct-classification-form"`.

Cada fila conserva `data-item-id`, e incorpora `data-amex-bucket`, `data-amex-search` y metadatos legibles derivados del read model para filtrar sin cambiar elegibilidad. El owner contable sólo expone diagnósticos negativos por fila; no convertir ausencia de issues en disponibilidad contable positiva. Derivar Por resolver de IDs de issues, Pendiente JP de débito no reconocido de proyecto/prestación en estado sent. Contabilizado/cubierto/crédito/excluido conservan su disposición. Si el owner completo está ready, los débitos nuevos se etiquetan listos; si está pending, usar «Sin bloqueos individuales» y mantener el bloqueo global. Cut/completion congelados no reciben nuevas etiquetas de disponibilidad. Mostrar reasons/exceptions completas como respaldo, pues global_issues deduplica por código y no garantiza scope.

## 3. Navegación mejorada y forma de respuesta

Conservar enlaces y handlers actuales. Interceptar sólo acciones AMEX conocidas del mismo origen y del mismo import_id; finanzas/CP no cambian. GET CFDI y sus filtros/paginación se cargan en panel lateral. Otros detalles/revisiones se muestran en `<dialog>`. Extraer únicamente el contenedor controlado del HTML recibido; rechazar páginas de login/URLs/contextos inesperados. No insertar navegación, estilos o scripts de una página completa ni evaluar scripts descargados. No añadir campos UI a FormData: `_treatment_input` rechaza campos no autorizados.

Enviar FormData original, incluidos item_ids repetidos; respetar validación nativa y submitter. Resolver GET de formularios/enlaces relativos contra la URL real del panel, no la URL principal. Distinguir retorno al statement de permanencia en picker/preview/retry. Subir un estado adopta su nueva URL canónica con history.replaceState, sin recarga de documento. Las operaciones posteriores mantienen esa URL.

Se extraerá la inicialización inline de archivo/signo, proyecto/categoría, tratamiento/cuenta y selección a funciones reutilizables del asset, con listeners delegados y sin duplicación tras reemplazos. Conservar endpoints HTML y su funcionamiento de servidor para navegación normal; no se promete un recorrido sin JS que el baseline ya no tenía (carga depende de preview JS y selección de copias JS).

## 4. Estado del cliente y errores

Preservar filtros, scroll y selección por IDs que aún existan y sean seleccionables. Mantener drafts visibles al fallo y permitir cancelar lecturas; nunca restaurar hidden de versión/hash/preview/nonce desde capturas viejas. Las respuestas 409 HTML de tratamiento contienen datos frescos y deben mostrarse. HTTP 403/409/422 JSON se presenta como texto escapado; falta de sesión no se interpreta como éxito.

Un 303 seguido por fetch puede terminar 200 pero incluir `error_msg`, msg con bloqueo o datos refrescados; no basar éxito en response.ok. Mostrar el mensaje autoritativo y sólo cerrar tras respuesta válida para el contexto. No reintentar POST automáticamente. Un error de red en escritura requiere comprobar estado actual, conservando la identidad idempotente; no afirmar que no hubo efectos. Evitar doble submit, bloquear cierre/Escape mientras una escritura está pendiente y liberar controles al resolverse.

Usar AbortController/generation para lecturas tardías, incluido cambio rápido de cargo. No abortar una escritura para simular cancelación. Mantener foco de apertura/cierre y anuncios aria-live. Las fuentes de forms pueden moverse al diálogo sin duplicar IDs y con asociación de controles correcta; preservar contenido original para cierre sin efectos.

## 5. Avance presupuestal

Lectura periódica acotada (20 segundos) en primer plano usando el GET existente del estado con `workspace_poll=1`; devuelve sólo `source_version`, tras la misma lectura/autoridad del estado y antes de cargar opciones, sugerencias o preview contable. Si cambia la versión, obtener el HTML normal. Así se evita recalcular sugerencias/contabilidad en cada tick. No hay endpoint adicional, job ni efecto. Pausar si document.hidden, existe solicitud de escritura, panel abierto o datos de captura sin guardar. No permitir respuestas antiguas sobreescribir lectura más nueva. Si cambia la versión, actualizar controles/fuente autoritativa y avisar; no invalidar silenciosamente un borrador. Proporcionar actualización explícita. Juan Pablo conserva íntegros handlers, autoridad, cola y formas existentes; nunca asignar partidas desde el cliente de Contabilidad.

## 6. Póliza

La sección de journal y confirmación existente es fuente de un diálogo de revisión con fecha/motivo/checkbox y prueba de source key actual. Sólo status ready habilita confirmación Finance, como ahora. Después del corte, mostrar descargas xlsx/csv/zip de los endpoints existentes en la misma pantalla; crédito/completion previamente reconocido conserva sus receipts, sin fabricar otra póliza. Programar pago conserva el recorrido existente y no se incorpora a este cierre estado→póliza.

## 7. Archivos y propiedad

Coordinador: `admin_amex_direct_routes.py`, nuevo helper de presentación `routes/amex_workspace_ui.py` si evita sobrecargar el router, metadatos generados con herramienta existente, story/spec/reportes. Frontend: únicamente `static/amex_workspace.js`, `static/amex_workspace.css`. Verificación: tests unitarios de rutas/helper, actualización de browser AMEX afectadas y aceptación browser+PG aislado; montar estáticos reales en las fixtures pertinentes. No modificar servicios financieros/modelos/esquemas ni la cola CP.

## 8. Pruebas y gates

HTTP: wrappers únicos, campos/authority/CSRF/commit/rollback/selection/versions intactos, forma fallback, sin nested forms, congelados/descarga sólo owner; metadata/counters/escaping. Browser escritorio/móvil: una navegación inicial; upload inline/adopción URL; drawer filtros/paginación/consulta consentida/vínculo; tratamiento por lote/retiro; SinCFDI; pérdida de red, 409 y 303 error_msg; doble envío, lecturas tardías, foco/Escape, filtros/selección/scroll, avance JP de otro acceso; journal/descarga. Al menos un recorrido real PostgreSQL con identidades sintéticas hasta póliza cuadrada y Excel usando navegador. Añadir volumen 100 filas para el estado de selección/filtros sin prometer un SLA no medido.

Aceptación independiente y validación read-only contra alcance antes de PR. Preservar tests de no-CFDI, signs, recovery, settlement y export; usar suites afectadas y CI obligatorio, cobertura cambiada ≥85%. Ejecutar comandos mediante entornos existentes, sin instalar dependencias productivas. Clasificar todo fallo no cerrado; no debilitar assertions financieras ni omitir casos por la nueva UX.

## 9. Publicación y recuperación

Autorización del usuario cubre commit/push/PR/merge/despliegue; no repetir checkpoint dentro del alcance. Comparar contra main/serving exactos antes de promover, preservar hotfixes y configuración AMEX activa de administrativas/viáticos/Daniel. Sin migración ni cambios de configuración/grants. Backup de configuración y evidencia financiera; release aislado, bundles conservados, única selección 50-current-release, health/readiness/anon denial/journal/sha y pruebas separadas de UAT. Rollback de código compatible si hay fallo operativo, sin modificar datos financieros ni políticas auditadas.

## 10. Canon

Canon unchanged: la propuesta aplica la presentación unificada del recorrido ya aprobado. Conserva owners, entidades, authority, confirmaciones, fuente, grants, regla de corte completo/inmutable y pago. No requiere nuevo texto normativo ni cambiar hashes de canons. Reportarlo con esta razón en el PR.
