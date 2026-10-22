# Corrección de signos AMEX: informe de construcción

Fecha: 2026-10-22. Base: `14c17c5388ca9b668dcf4c0b6339f9db48f23e11` (`origin/main` al crear el worktree). Historia, especificación y texto exacto de la enmienda al canon aprobados en conversación. Rama de trabajo: `fix/amex-sign-correction-20261022`.

## Resultado implementado

Operación de backend preview/confirm y comando de mantenimiento restringido al estado autorizado. Conserva archivo y fuente física; una revisión append-only interpreta las 16 filas con el perfil corregido. Los dos gastos originales se cancelan con actor/motivo y aprobación auditable; catorce cargos obtienen anclas nuevas mediante el propietario de gastos. Fuente efectiva común para pantalla, conciliación, presupuesto, hash, corte y exportación. No genera decisiones fiscales ni póliza durante la reparación.

Se preserva la misma carga/URL, el CSV y los signos bancarios originales. Google +939.00 pasa a cargo; Amazon -46.53 pasa a abono. Totales esperados: 14 cargos por 8,747.10 y 2 abonos por 17,357.40 MXN. Replays con perfil original o corregido resuelven la misma importación. Si existen decisiones o efectos incompatibles, se rechaza la corrección.

La migración owner-run instala dos tablas, la proyección efectiva y guardas de integridad; no hace backfill. El rollback se limita a tablas vacías, con exclusión de escrituras concurrentes antes de comprobarlas. Con revisión aplicada se requiere una recuperación hacia adelante compatible, conservando evidencia.

## Archivos y responsabilidades

- `database/migrations/20261022_amex_direct_sign_correction.sql` y `_rollback.sql`: esquema, vistas y guardas.
- `src/devnous/gastos/amex_direct_models.py`, `models.py`, `amex_schema_policy.py`: registro mediante el patrón existente y exclusión del DDL de startup.
- `services/amex_direct_sign_correction_service.py` y `scripts/correct_amex_direct_sign_profile.py`: propietario de reparación y mantenimiento.
- `services/amex_direct_effective_source_service.py`, `amex_direct_common.py`, `amex_direct_context_service.py`, `amex_direct_accounting_service.py`, `amex_direct_matching_service.py`, `amex_direct_intake_service.py`, `amex_direct_no_cfdi_service.py`, `amex_direct_recovery_service.py`: consumidores coherentes, pruebas/hashes y rechazo de combinación con recuperación de período.
- `services/amex_direct_schema_contract.py`, `amex_direct_schema_service.py`: admisión exacta de contrato propietario.
- `routes/admin_amex_direct_routes.py`: perfil efectivo, aviso auditado y mensajes; ninguna ruta o acción nueva.
- Tres archivos unitarios nuevos de servicio/CLI/consumidores; dos archivos PostgreSQL nuevos de esquema/aceptación. Se actualiza el bootstrap compartido, la prueba de rollback Sin CFDI por capas y la admisión de contrato unitario.
- `docs/private-plugin/route-inventory.json`, `route-matrix.csv`: regeneración requerida por cambios de hash/líneas del handler, sin cambiar rutas ni autoridad.
- Historia, especificación, este reporte, aceptación y validación en `docs/roadmap/amex-sign-correction-20261022-*`.
- Canon de ingeniería: sólo el texto expresamente aprobado; registro con hash actualizado. Canons de Producto/Sweep sin cambios porque no se introdujo tratamiento contable ni se revisó el inventario histórico.

## Pruebas y comandos

Entorno pytest existente: `/root/samchat/.venv/bin/python`. Herramientas de estilo/tipos existentes: `/srv/samchat/venvs/release-598e7a286/bin/python`. Sin instalación de dependencias. `PYTHONPATH=src:.:tests/acceptance:tests/unit/gastos:/tmp/samchat-amex-source-testdeps`.

Comandos focales ejecutados:

```sh
python -m pytest -q --no-cov tests/unit/gastos/test_amex*.py tests/unit/test_amex*.py tests/unit/private_plugin/test_inventory.py
python -m pytest -q tests/integration/test_amex_direct_sign_correction_postgres.py
python -m pytest -q tests/integration/test_amex_direct_sign_schema_postgres.py
python -m pytest -q tests/integration/test_amex_direct_no_cfdi_schema_postgres.py
python scripts/private_plugin/inventory.py
python -m black --check <archivos modificados del alcance>
python -m isort --check-only <archivos modificados del alcance>
python -m flake8 --select=E9,F63,F7,F82 <archivos modificados del alcance>
python -m mypy --follow-imports=silent --ignore-missing-imports <propietarios modificados>
git diff --check
```

Los comandos PostgreSQL exigen `TEST_AMEX_DATABASE_URL` de la base desechable local `127.0.0.1:55471`, usuario `test_user`, base `devnous_test`; el fixture rechaza cualquier otro destino y usa esquemas aleatorios. Las pruebas de cobertura usan ficheros separados bajo `/tmp` para no interferirse.

Resultados de construcción observados antes del cierre independiente:

- 187 casos focales de consumidores/rutas/Sin CFDI: PASS.
- 107 casos de búsqueda/sugerencias/presupuesto/consumidores: PASS; se solapan con la regresión completa, no se suman como casos únicos.
- Regresión completa inicial: 1,072 unitarios PASS; la ejecución posterior añadió comprobaciones de inventario y cobertura final.
- Servicio/CLI: 56 unitarios PASS, cobertura conjunta 97.93%; servicio 98.09%, CLI 97.62%.
- Esquema PostgreSQL: 8 casos nuevos PASS antes de añadir el cierre de carrera del rollback. Regresión completa Sin CFDI: 25 PASS. Se verificó reversión vacía y reinstalación por capas.
- Aceptación independiente: 14 casos únicos PASS en ejecuciones 11+3; ver reporte de aceptación para evidencia, tiempos y límites. Incluye póliza real balanceada en la base desechable por 8,747.10, 14 consumos, 16 miembros y exportaciones CSV/XLSX/ZIP.
- Mypy de ocho consumidores y dos archivos de servicio/CLI: PASS. Compilación, diff y comprobaciones focales de estilo/errores: PASS.

Resultados finales sobre código congelado (las cifras anteriores conservan su evidencia intermedia y solapamiento):

| Comprobación | Resultado |
| --- | --- |
| Unitarios AMEX completos e inventario reproducible | 1,082 PASS, 104.92 s; `/tmp/amex-sign-unit-frozen-coverage-20261022.txt` |
| Servicio/CLI (incluidos en unitarios completos) | 56 PASS, cobertura conjunta 97.93% |
| Esquema nuevo, incluida carrera de rollback | 9 PASS, 15.09 s |
| Regresión PostgreSQL Sin CFDI | 25 PASS, 43.06 s |
| Aceptación independiente PostgreSQL/HTTP/póliza/export | 14 casos únicos PASS: 11 en 56.55 s y 3 adicionales en 32.88 s |
| Cobertura combinada de instrucciones modificadas | 313/325, 96.31%; `/tmp/amex-sign-changed-coverage-20261022.json` |
| Gate de superficie operacional de inscripción | PASS, 52 archivos revisados, cero violaciones |
| Gate de regresiones aceptadas | PASS, 13 marcadores, cero fallos |
| Compilación de archivos Python modificados | 24 PASS |
| Canon/hashes e higiene de diff | PASS |

La cobertura combina JSON de la ejecución unitaria final congelada, servicio/CLI y aceptación 11+3; considera las instrucciones ejecutables añadidas de todos los archivos Python cambiados, incluidos archivos nuevos no staged. El cálculo anterior de desarrollo quedó sustituido para no mezclar posiciones de líneas previas a la congelación. Las líneas no cubiertas se conservan en el JSON. SQL se verifica mediante PostgreSQL real, no mediante porcentajes Python.

## Fallos detectados y atribución

Se corrigieron durante construcción los fallos introducidos: lectura incorrecta de FK compuestas en detección de dependencias, comparación SQL del importe nullable del abono y uso de una clave inexistente en el snapshot de corte. El inventario reproducible falló tras modificar el handler; se regeneraron sus dos artefactos con el generador existente y sus nueve pruebas pasaron. La revisión cruzada encontró una carrera en el rollback vacío; se añadió bloqueo exclusivo antes de la comprobación y se verifica en PostgreSQL.

El test histórico de reversión Sin CFDI debía revertir primero la nueva capa y reinstalarla al final: se actualizó el harness, sin debilitar las guardas. Otras aserciones de aceptación se corrigieron para comparar sumas numeric y esperar la excepción de autoridad real. El entorno pytest local no tiene herramientas de estilo; se usó el entorno de herramientas ya instalado. El entorno release usado inicialmente para pytest resolvía otro paquete `tests`; se usó el entorno fuente existente. Ninguno de estos problemas se ocultó ni requirió instalar paquetes.

Persisten deudas anteriores de formato del archivo de contrato/modelos; no se reescribieron zonas ajenas. Las verificaciones focales no representan lint o mypy global limpios. Warnings heredados de SQLAlchemy/fechas y un código dinámico de fixture sin archivo para cobertura se clasifican como infraestructura/harness preexistente; no prueban UAT productiva.

## Desviaciones y riesgos pendientes

Sin cambio de reglas respecto a historia/spec. El registro de modelos se hace en el archivo dividido `amex_direct_models.py` y se reexporta por `models.py`; la política de exclusión DDL y los artefactos de rutas son ajustes obligatorios del patrón existente, no una arquitectura nueva.

Sin commit, push, PR, merge, migración, despliegue ni corrección de datos productivos en esta fase. Antes de aplicar: revisión humana del diff final; actor financiero efectivo validado; backup/restaurabilidad y preimagen exacta; promoción coordinada del contrato SQL/release; reparación confirmada y verificación posterior. Si hubo actividad en el estado desde el diagnóstico, la preimagen caduca y la aplicación se rechaza. UAT real y aceptación de Contabilidad permanecen separadas.

## Seguimiento autorizado de publicación

Tras aprobar el usuario el cambio final y despliegue, se publicó el commit `c1034c81c` en el PR #497. CI detectó un defecto del artefacto de inventario: el generador se ejecutó antes de que el nuevo servicio estuviera registrado en Git y conservó `source_file_count=476`; el checkout limpio del commit cuenta 477. Se regeneró el artefacto sobre el commit publicado: cambia únicamente ese conteo, sin cambios de rutas, acciones, autoridad ni código financiero. Verificación focal posterior: nueve pruebas de inventario PASS en 22.65 s. Revisión independiente de la corrección registrada en el reporte de validación. Se repiten los gates remotos sobre el commit correctivo antes de merge/promoción; el fallo remoto inicial se conserva como evidencia y no se presenta como aprobación.

La release candidata pasó además 110 pruebas focales de servicio/CLI/intake con el venv productivo, sin conectarse a producción. Se preparó respaldo privado con catálogo y decodificación completa verificados y preimagen de las 16 filas. Daniel fue expresamente designado por el usuario para la atribución de la reparación; su autoridad activa se verificó en lectura. Esto no acredita todavía aplicación o UAT.

### Conservación autorizada del hotfix productivo HWC

La comprobación exacta de ascendencia detectó que la release activa había cambiado a `612bd50bffadd21deb33586fbe3ac8fa30c0e903`, hijo de la base AMEX `14c17c538`, pero aún no integrado a `main`. El usuario aprobó expresamente conservar ese hotfix sin cambios. Se integra su commit original mediante merge: `admin_routes.py`, `expense_accounting_cleanup_service.py`, `expense_coi_export_service.py` y `test_hwc_non_deductible_account.py` permanecen idénticos byte a byte al commit ya productivo. Se conserva la excepción existente de la cuenta `5300-016-003` sin introducir otra regla.

Los artefactos de inventario se regeneran por cambios de procedencia/líneas del handler existente; siguen siendo 606 rutas y 44 acciones, sin nuevos permisos ni endpoints. Revisión independiente de los cuatro archivos e inventario: sin hallazgos. HWC focal: 14 PASS en 2.66 s sobre la release activa. Regresión conjunta AMEX + HWC + inventario: **1,096 PASS en 34.98 s**, con 40 warnings heredados, recibo `/tmp/amex-sign-hwc-preservation-tests-20261022.txt`. La implementación de signos/DDL no cambia respecto al candidato aprobado. La aprobación inicial cubre signos; esta ampliación de preservación fue revisada y autorizada por separado antes del merge local.

### Cierre del historial real de conciliaciones pendientes

Antes de aplicar, la preimagen exacta mostró 16 eventos `context_revision` y dos `match_pending`, con versión fuente 4 y sin decisiones/vínculos efectivos. La primera guarda del servicio rechazaba cualquier evento distinto de contexto, produciendo un falso rechazo del caso autorizado. Se reprodujo independientemente ese rechazo mediante el propietario real de conciliación en PostgreSQL antes de cambiarlo.

La corrección permite únicamente `context_revision` y `match_pending` como historial no vinculante, conservándolos íntegros. No cambia DDL ni autoridad, y se mantienen todas las validaciones de partidas pendientes, ausencia de CFDI/proof, clasificación, presupuesto, reconocimiento, pagos, auditoría de decisiones reales y dependencias FK/polimórficas. Se añaden ocho casos unitarios de la guarda: intentos pendientes permitidos y confirmaciones, selecciones, liberaciones y eventos desconocidos rechazados.

Validación final posterior: **1,104 unitarios AMEX + HWC + inventario PASS en 34.71 s**; 64 de servicio/CLI dentro de ese conjunto, cobertura focal 97.93%. Aceptación independiente completa: **14 PostgreSQL PASS en 64.31 s**, incluyendo el estado con 18 eventos y versión 4, preservación del historial y flujo hasta póliza/exportación. Reportes de aceptación y validación incluyen el cierre; sin hallazgos pendientes. Recibo: `/tmp/amex-sign-final-pending-history-regression-20261022.txt`. La migración forward conserva SHA-256 `dc7d6de8aa1aca20b579bd51eb98a4b0c3bbd467458e3d33d5a2360e5ac32596`. Debe pasar CI del commit corregido antes de publicación productiva.

### Corrección del harness de integración detectada por CI

El CI del head `2371c9028` pasó 6,045 pruebas unitarias (8 omitidas), pero integración terminó con **4 fallidas, 584 aprobadas y 6 omitidas**. Son fallos atribuibles a la incorporación de la nueva capa y a su prueba concurrente, no un resultado productivo: dos pruebas históricas del esquema directo y una de settlement retiraban/reinstalaban migraciones sin considerar la nueva capa de signos; la segunda conexión de la prueba de rollback omitía la contraseña exigida en CI.

Se ajustan únicamente tres archivos de pruebas PostgreSQL: retiro de signos antes de capas anteriores y reinstalación al final; la conexión concurrente reutiliza los atributos de URL ya validados por el fixture, incluida la contraseña sin imprimirla. Permanecen todas las aserciones, rechazos, guardas y el test de bloqueo concurrente. No cambia SQL productivo, implementación, autoridad ni comportamiento. Canon unchanged: el ajuste corrige el harness que verifica el contrato aprobado, sin alterar el contrato ni su texto.

La prueba de autenticación reprodujo primero el fallo con SCRAM y pasó tras la corrección. Suite de signos: **9 PASS en 15.80 s**, 243 warnings heredados. Las tres pruebas históricas afectadas pasan en 6.66 s. Formato/lint focal y `git diff --check` pasan. Los dos módulos históricos completos pasan **67 pruebas y conservan 1 omisión existente en 109.80 s**, sin fallos; evidencia y revisión independiente constan en el reporte de validación. Se exige repetir CI en el commit correctivo; estos resultados locales no sustituyen el gate remoto ni acreditan despliegue o UAT.
