# Flujo provisional de aprobaciones — informe de construcción

Fecha: 2026-10-23. Base: d591cc4a13987d33d2336bcf872401f3a6c7acb6. Autorización de historia y continuidad hasta despliegue registradas en la conversación.

## Resultado de implementación

- Ruta por solicitante **o** beneficiario protegido hacia GN, matriz de 15 proyectos y supervisión explícita de GV para solicitudes e informes.
- Folios separados de autoridad: Patrocinios/RRSS no invocan al allocator en creadores web, servicios y recibos/asistente; folios históricos se conservan.
- Coherencia de bandeja, etiqueta de aprobador pendiente, detalle, acciones, notificaciones, Telegram y backend. Decisiones históricas conservan su actor real.
- Snapshots con huella de contexto en source existente, validación de elegibles actuales y auditoría con actor real; lecturas sin actor no reescriben snapshots previos.
- Motivo opcional de rechazo en Control Presupuestal, individual/lote, con historial escapado, estados y permisos conservados.
- Operador acotado de preview/hash/aplicación: 4 posiciones exclusivas de aprobaciones, 8 membresías, 15 reglas y reparación únicamente de S-26000450; no cambia importe, estado ni folio 363.

## Archivos y pruebas

Servicios project_authorization, documento, documento_workflow, documento_telegram, authorization_profile y receipt_workflow; ruta web user_routes. Nuevos tests puros/de interfaz y tres suites PostgreSQL nativas; adaptación mínima de fixtures legacy afectadas por las nuevas consultas/parámetros. scripts/apply_provisional_approval_policy.py incluye la operación auditable. Inventarios JSON/CSV regenerados por su herramienta oficial. No cambios de esquema ni nuevas dependencias de producción.

## Comprobaciones locales del candidato final

- **6,252 passed, 8 skipped, 8 subtests passed**, sin fallos, en la suite unitaria completa y las tres suites nuevas PostgreSQL, con Python 3.12. Las ocho omisiones son pruebas preexistentes de Payment History que requieren su propio PostgreSQL sintético; ninguna de las pruebas nativas nuevas se omitió.
- PostgreSQL real: 44 pruebas de política, 5 del operador y 17 del workflow. La concurrencia observa espera real del segundo backend por lock y una única aprobación persistida para solicitudes e informes, con cualquiera de los aprobadores alternativos primero. Los resultados internos de contabilización y adaptadores de salida se aíslan; no se afirma UAT de esos propietarios.
- Cobertura de instrucciones modificadas: **89% (169/189)**, superior al 85% exigido, mediante diff-cover contra la base exacta.
- Cobertura del operador: **91.39% (244/267)** y sus 43 pruebas puras/nativas.
- Inventario: 606 declaraciones, 574 rutas estáticas live, 44 acciones canónicas, sin gaps de parseo.
- Guardas de superficie de inscripción y regresiones aceptadas: PASS. Coherencia runtime/empaquetado/documentación: PASS. Compilación y git diff --check: PASS.
- Revisión independiente de sólo lectura: sin defectos críticos, importantes o menores pendientes.

## Comandos

PYTHONPATH de tests: src:.:tests/unit/gastos:/tmp/samchat-amex-source-testdeps. Entorno explícito de prueba con claves dummy.

- pytest tests/unit y las tres suites de integración PostgreSQL nuevas, ignorando únicamente los paquetes aislados private_plugin y readonly_gateway, con cobertura source y timeout120. Rutas exactas de los nuevos tests: test_provisional_authorization_postgres.py, test_provisional_approval_policy_tool_postgres.py y test_provisional_approval_workflow_postgres.py.
- pytest de tests/unit/gastos/test_provisional_approval_policy_tool.py y su suite PostgreSQL, con --cov=scripts.
- diff-cover del XML final, --compare-branch=d591cc4a13987d33d2336bcf872401f3a6c7acb6 --fail-under=85.
- python scripts/private_plugin/inventory.py; scripts/ci/check-registration-operational-surface.py; scripts/ci/check-accepted-regressions.py; scripts/check_runtime_packaging_consistency.py.

## Fallos, desviaciones y límites

Los fallos de fixtures introducidos por consultas/parámetros nuevos quedaron corregidos sin omitir pruebas ni cambiar baselines. La medición inicial de cobertura global de dos módulos (49.23%) no medía cobertura de instrucciones modificadas; el candidato final supera el gate real. Flake8 de archivos completos conserva hallazgos preexistentes, comprobados contra la base: F821 _render_resumen_gastos_empleado en user_routes, F401 APPROVER_ROLES en authorization_profile, F841 expense/F401 datetime en fixtures legacy. No se corrigieron fuera de alcance ni se afirma lint global limpio.

No hay desviaciones de reglas aprobadas. Canon unchanged: justificación concreta en §10 de la especificación; archivos protegidos sin cambios y hashes verificados.

## Publicación y operación

Este informe registra la validación local antes de publicación. La CI del head final, merge, release exacto, activación y reparación del caso conservarán recibos separados. El operador debe ejecutarse desde el release publicado con commit y hash frescos y realizar una lectura independiente tras commit. No se envían mensajes a usuarios ni se aprueba o paga ningún documento. UAT autenticada y aceptación financiera son evidencia separada.

La actualización del catálogo presupuestal (3) se aplicó por separado con su propio plan/auditoría: 2248 partidas, tres altas, 53 retiros indicados por el archivo, cero identidades activas duplicadas, referencias/importes/calendarios preservados. Sus datos privados no se incorporan a la PR.

## Compatibilidad detectada por CI

La primera CI detectó dos dependencias de prueba/consumo adicionales dentro del mismo alcance de coherencia de autoridad: la copia pura del predicado en readonly_gateway y la fixture PostgreSQL de aprobación final AMEX. Las pruebas generales dieron 650 integraciones correctas, seis omisiones preexistentes y cuatro fallos por ausencia de tablas de configuración en esa fixture; no se omiten ni debilitan esos escenarios.

El candidato mantiene equivalencia AST exacta del predicado y sus cuatro helpers puros. El adaptador canónico reconoce también una denegación de política activa sin folio. El adaptador limitado de informes no cuenta con los datos para resolver esa autoridad y devuelve explícitamente autoridad no resuelta, conservando sus vistas, ámbito admitido y permisos. Las pruebas ejercitan permisos insuficientes y mantienen la denegación segura. No se modifica, reactiva ni reinicia el gateway instalado separadamente en /opt/agentius-samchat.

La suite aislada exacta de CI del gateway pasó **80 pruebas**, cobertura **88%**, en PostgreSQL desechable sin acceso a datos de negocio. La fixture AMEX recibe únicamente tablas vacías sin activar la política; conserva las comprobaciones de aprobación/evidencia, rollback y carrera de edición. Esta corrección no introduce esquema productivo, dependencias ni autoridad nuevas. Canon unchanged por la misma razón de la especificación: reutilización de los propietarios de autoridad y auditoría existentes.

Tras incluir esa compatibilidad, la cobertura combinada local de instrucciones modificadas es **90% (197/218)** contra la misma base, con los 80 casos aislados adicionales. Inventarios regenerados nuevamente: sin cambios de superficie ni gaps de parseo.

La suite afectada de aprobación final AMEX pasó sus **5 casos en PostgreSQL 16 real, cero omisiones**, incluidos los cuatro resultados que fallaron en CI y el escenario de enlace/exportación. Se ejecutó en un namespace de red desechable para conservar intacto un cluster ajeno que ocupaba el puerto requerido. Black, Flake8 F/E9 y diff check de la fixture: PASS.

Una segunda CI pasó las 6,186 pruebas unitarias generales, pero una observación de la fixture aislada excedió 3000 ms. La investigación en PostgreSQL desechable encontró estadísticas ausentes: EXPLAIN estimaba coste 160413.46 y generaba JIT de 212 funciones para sólo dos documentos. Tras ANALYZE de los datos sintéticos: coste 12.11, sin JIT y ejecución local 0.118 ms frente a 95.628 ms. Esto respalda la atribución a compilación innecesaria en el runner; no se midió su duración absoluta en CI debido a la cancelación. Se corrige únicamente la preparación de la fixture y se añade una regresión de cardinalidad/plan. Se conserva el timeout de 3000 ms, el predicado, las protecciones y la configuración productiva. Suite aislada exacta final: **81 pruebas correctas, cobertura 88%**.

## Cierre de observaciones de la PR

La CI de 81986fe pasó (6,186 unitarias generales, 81 del gateway, 654 integraciones, cobertura modificada 90% y gate obligatorio), pero seis hilos automáticos pendientes impidieron fusionar. El predicado readonly ya estaba sincronizado. Se corrigieron los otros cinco casos dentro de las invariantes aprobadas:

- Cada decisión provisional refresca la ruta con auditoría del actor real y revalida su autoridad antes de cambiar estado o producir efectos financieros. Las pruebas nativas cubren cambios de regla y membresía, aprobaciones/rechazos, actor anterior denegado sin escrituras confirmadas, y preservación de 363.
- Telegram prioriza autoridad canónica con política activa; 31 documentos no autorizables sin folio no desplazan el pendiente asignado al aplicar el límite de 30.
- Un borrador que cambia de RRSS/Patrocinios a un proyecto normal reevalúa la política emisora del creador sin depender del flag de la regla. Dos envíos nativos reales prueban cero consumo mientras está excluido y un folio al enviar al proyecto normal; los documentos pendientes históricos conservan su referencia.
- El operador bloquea los proyectos seleccionados antes de reconstruir el snapshot y el hash. Dos pruebas PostgreSQL concurrentes intentan renombrar/desactivar durante la operación.
- El operador reutiliza el guard financiero canónico y rechaza gasto generado, anticipos/reembolsos activos e historial de aprobación/pago, incluso con estado/timestamps inconsistentes.

Verificación focal: 377 pruebas backend, 24 nativas del workflow y 53 del operador (42 puras y 11 nativas) correctas. Cobertura del archivo operador: 91.67% (253/276). Una primera medición aplicó incorrectamente el umbral al directorio scripts completo (5.41%, pese a 53 pruebas correctas); se repitió con medición y gate explícitos del archivo aprobado, sin omitir pruebas ni cambiar el umbral de 85%. Revisión independiente sin hallazgos pendientes. La ejecución integrada y CI del nuevo SHA se registran por separado; no se atribuye el verde anterior al nuevo diff.

El diff tras las seis observaciones pasó la ejecución integrada: **6,278 passed, 8 skipped preexistentes, 8 subtests**, cero fallos, con las tres suites nativas completas (44 política + 11 operador + 24 workflow). Log y JUnit independientes registrados. Cobertura combinada modificada final: **88% (231/262)**, gate ≥85% correcto. Black y Flake8 F/E9 de los ocho archivos de la última corrección: PASS con el intérprete runtime que ya contiene esas herramientas; el venv de tests no las incluye. Inventario regenerado 606/574/44 sin gaps. Canon intacto.

## Compatibilidad de auditoría verificada en producción

La PR #499 se fusionó como 114be344e y el release pasó salud/disponibilidad. La activación se revirtió íntegramente porque el CHECK real `aprobaciones_accion_check` no admite `actualizar_ruta_autorizacion` como acción SQL. Una lectura independiente confirmó igualdad completa de configuración, rutas, documento e historial respecto al preview: la política no quedó activa y S-26000450 no cambió. El servicio volvió temporalmente al release d591cc4a1, saludable; otra verificación confirmó el catálogo de 2248 partidas y sus referencias/importes/calendarios intactos.

La corrección acotada reutiliza `editar`, acción no financiera que ya admite el esquema, y conserva `action=actualizar_ruta_autorizacion` dentro del JSON con actor/contexto/rutas anterior y nueva. La investigación de consumidores confirma que decisiones y preparación financiera filtran acciones financieras explícitas; el evento técnico no aprueba, paga ni notifica. No se modifica el esquema ni se agrega un propietario de auditoría.

El operador valida las definiciones reales de CHECK desde PostgreSQL antes de escribir y las vincula al hash de preview/aplicación/readback. Acepta únicamente la representación positiva conocida del enum, con identificador o cast seguro a TEXT; restricciones ausentes, adicionales sobre acción o desconocidas bloquean antes de mutar. El esquema servidor comprobado tiene columnas TEXT; las pruebas también cubren el contrato VARCHAR(50) del modelo. Ambos reproducen los 25 valores permitidos y los cuatro tipos de entidad observados.

Archivos: propietario `project_authorization_service.py`, operador y sus cuatro suites puras/nativas, más esta evidencia y la aclaración de especificación. Pruebas focales: 63 backend, 208 regresiones y 77 del operador (51 puras + 26 nativas), correctas. Cobertura del operador: 92.12% (269/292). Revisión independiente sin hallazgos pendientes; preflight productivo de sólo lectura PASS. Ejecución integrada y CI del nuevo SHA pendientes de su propio registro. La activación fallida nunca se acredita como completada. Canon unchanged: mismo propietario y esquema existentes, mismas reglas y estados financieros, con transportador de auditoría compatible.

Cierre integrado de compatibilidad: **6,305 passed, 8 skipped preexistentes, 8 subtests**, cero fallos. Incluye 97 casos PostgreSQL: 44 de política, 26 del operador y 27 de workflow. Cobertura de instrucciones modificadas, combinando XML de src y del operador contra 114be344e: **100% (17/17)**; los dos valores literales cambiados del owner se verifican mediante persistencia nativa bajo CHECK real. Black, Flake8 F/E9 y diff check: PASS. Validador independiente confirmó también el preflight productivo y no dejó hallazgos; sólo resta la CI del nuevo commit, promoción y activación con hash fresco.

Refresco de base: la PR #500 se fusionó y desplegó concurrentemente como 8bc0a6ab. Se integró esa base sin conflictos; el diff propio conserva sus ocho archivos. Los tres cánones actualizados se releyeron en orden y sus hashes coinciden con el registro. Sobre el árbol combinado pasaron 184 pruebas específicas y revisión independiente; el catálogo productivo volvió a verificarse completo. CI de 3ad86446 pasó 6,278 unitarias generales, 81 readonly y 727 integraciones, antes del ajuste de fecha descrito a continuación.

El nuevo evento técnico omitía fecha explícita y heredaba `datetime.utcnow` naive del modelo; un probe productivo de sólo lectura y un RED PostgreSQL demostraron dos horas de desfase bajo Europe/Berlin. Se fija exclusivamente su fecha con `datetime.now(timezone.utc)`, conservando modelo e históricos. La regresión compara el dato persistido con `clock_timestamp()` y restaura TZ en finally, manteniendo actor, estado, folio y ausencia de aprobación/notificación. GREEN: 63 pruebas específicas; verificación raíz: 184 aprobadas y revisión independiente sin hallazgos. La cobertura por módulo produjo un bloqueo de instrumentación antes de colectar tests y se canceló; la repetición por directorio src pasó y generó el XML utilizado. Black, Flake8 F/E9 y diff check correctos. CI del SHA con fecha explícita requiere evidencia nueva.
