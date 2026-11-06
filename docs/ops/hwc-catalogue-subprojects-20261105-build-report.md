# HWC: informe de implementación y revisión final

Fecha: 2026-11-05. Cierre del ensayo: 2026-11-06. Historia y especificación aprobadas mediante «continua»; implementación concluida. Sin commit, push, PR, merge, despliegue ni reparación productiva en esta tarea. Revisión humana final pendiente.

## Resultado

Se restaura el alcance de 74 UUID activos del catálogo aprobado, conservando las otras dos partidas activas y todas las inactivas. La fase de una línea de importe cero se corrige mediante sus propietarios normales y se renueva exclusivamente la revisión semanal HWC, conservando su historial. La edición del torneo deja de borrar alcances; rechaza retirar etiquetas utilizadas y se coordina con altas/cambios del catálogo mediante bloqueos transaccionales. Se conservan roles, CSRF, auditoría, importes, cuentas, pagos y pólizas. Sin DDL ni dependencias nuevas.

HWC conserva $30,384,939.78 y semana 52; Liga $3,686,827.60/semana 40; Copa $17,471,291.92/semana 52. Los 3 informes y 13 gastos con fase vacía/diferente quedan íntegros y pendientes de revisión operativa; no se los marca conciliados.

## Archivos

- src/devnous/gastos/routes/admin_routes.py
- src/devnous/gastos/services/tournament_phase_service.py
- src/samchat/budgets/service.py
- scripts/repair_hwc_catalogue_subprojects.py
- config/hwc_catalogue_subprojects_20261105.json
- tests/unit/test_budget_service.py (ajustes mínimos de fixtures)
- tests/unit/gastos/test_budget_scope_config_preservation.py
- tests/unit/test_hwc_catalogue_subprojects_operator.py
- tests/integration/test_budget_scope_config_concurrency_postgres.py
- tests/integration/test_hwc_catalogue_subprojects_operator_postgres.py
- docs/private-plugin/route-inventory.json y route-matrix.csv (regenerados)
- docs/roadmap/hwc-catalogue-subprojects-20261105-spec.md (copia exacta aprobada)
- este informe

## Verificación

La suite final del candidato basado en el release servido pasó **442 pruebas, sin skips**, con cuatro advertencias de herramientas/deprecación. Incluye catálogo/configuración, autorización, propietarios presupuestales, distribución, procedencia, lectores contables, operador, inventario y PostgreSQL real para concurrencia y rollback. Comando reproducible: PYTHONPATH=src SAMCHAT_WEEKLY_TEST_DATABASE_URL=<PostgreSQL local desechable> /srv/samchat/venvs/release-598e7a286/bin/python, importar devnous.gastos y ejecutar pytest.main sobre los 15 archivos enumerados en el recibo serving-final-tests.log.

Pasaron scripts/ci/check-registration-operational-surface.py, scripts/ci/check-accepted-regressions.py y git diff --check. Inventario regenerado con scripts/private_plugin/inventory.py; sin nuevas rutas ni acciones. Cobertura local de líneas ejecutables modificadas de los tres archivos src: 48/54 (88.89%); las seis líneas no cubiertas se enumeran en local-changed-coverage.json. Black/isort/flake8 focales y comparación con baseline no introducen nuevos diagnósticos; permanecen 28 diagnósticos F/E9 heredados en los propietarios grandes. No se afirma lint/typecheck global limpio. CI remoto pendiente de publicación.

El ensayo hardened-02 del candidato main verificó rechazo de hash obsoleto, rollback exacto en dos puntos posteriores a commits internos, reparación completa, lectura independiente y repetición idempotente. Comparó 19 tablas, las 263 líneas efectivas de tres torneos, sus semanas/ingresos y los 31 registros históricos. Sus 146 pruebas pasaron. La revisión independiente detectó y volvió a validar el cierre de una brecha de vinculación de conexión: el operador ahora exige coincidencia exacta con la conexión del proceso servido y archivo canónico; no hay hallazgos de implementación pendientes en esa revisión.

El ensayo serving-03 verificó el estado corregido, lectores, filtros/matriz e idempotencia. Su limitación de no haber repetido la reparación completa quedó resuelta el 2026-11-06: el usuario autorizó el reset exacto de la copia local; se verificó el respaldo SHA256 antes de restaurar y el ensayo **serving-05 pasó la cadena completa sobre el código selectivo congelado**. Probó rechazo del hash obsoleto sin escrituras, rollback íntegro después del commit interno del propietario y después de renovar el snapshot semanal, aplicación completa, lectura canónica independiente y repetición idempotente con las 19 tablas idénticas. Se cierra la limitación técnica de AC8. Producción intacta; UAT autenticada y aceptación financiera no ejecutadas.

Incidencia de infraestructura resuelta: serving-04 falló antes de la primera consulta por un socket temporal sin proceso activo. Se recuperó el relay acotado a la base de ensayo; serving-05 pasó sin cambios de código. No es un defecto de la reparación ni se oculta un check fallido.

## Evidencia privada y fuentes

Recibos: /root/samchat/tests/artifacts/hwc-subproject-20261105/, especialmente serving-final-tests.log, acceptance/hardened-02/, acceptance/serving-03/, acceptance/serving-05/, local-changed-coverage.json, backup.json y live-preview-hardened/. No publicar estos datos privados en el PR.

Excel: catalogo_presupuestal (3).xlsx, SHA256 75da03a5853199c3fab3db96699ed1950bb6b637b2702630b8bbcfb24e3d4eae. Respaldo READ ONLY previo: SHA256 66fe7d77915fe921eef544577d21e6cece9c96a33767a2b99225b35840333382. El backup no sustituye el respaldo fresco requerido antes de aplicar en producción. Cada ejecución futura exige preview/hash nuevos; no reutilizar hashes del ensayo.

Main candidato: base 01a4401309f85a9eb902e1c46b7daa6378f499b9. Selectivo servido: base 710021a2f7ff7809f4d9460911a5c38d951c0928, conservando el ajuste CxC ya servido. Se preserva el checkout raíz ajeno y su trabajo sin commit.

## Riesgos, desviaciones y siguiente checkpoint

Sin desviación funcional del alcance aprobado. La repetición completa desde estado limpio pasó en serving-05 después de la autorización explícita del reset local. No quedan fallos de aceptación técnica pendientes para este alcance. Se requiere revisión final del diff antes de publicar/fusionar/desplegar y aplicar; los checks remotos deberán estar verdes. El release debe conservar el código ya servido; aplicación exclusivamente desde release limpio con respaldo fresco, propietario autorizado y lectura independiente posterior. Los gaps históricos no se corrigen con esta reparación.

Canon unchanged: se restaura clasificación previamente aprobada y se protege su conservación, sin cambiar reglas fiscales, autoridad, modelo financiero ni propietarios. Los tres canons se leyeron en orden y sus SHA256 coinciden con el registro. Sin enmiendas de canon automáticas.
