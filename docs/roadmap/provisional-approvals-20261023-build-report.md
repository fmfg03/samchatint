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
