# Copa Telmex: primera entrega de inscripción sobre PostgreSQL

Fecha: 2026-10-05. Candidato aislado sobre `d548d148f7521988587d6498b5461901c21a2ee3`.
La dirección arquitectónica y la especificación de esta primera entrega fueron aprobadas en la conversación. No se ha hecho commit, push, PR, despliegue ni aplicación de datos o migraciones.

## Resultado

Se implementó un catálogo explícito de torneo/edición y una proyección de inscripción de solo lectura sobre el PostgreSQL operativo. Para las ediciones configuradas, el asistente y Dirección usan esa misma proyección: equipos, jugadores activos y provisionales, revisiones pendientes y agregados territoriales. Los errores y los ámbitos ambiguos se muestran explícitamente, sin sustituirlos silenciosamente por el roster de otra base. Las ediciones sin configurar conservan el comportamiento anterior.

La consulta de SOUL puede excluir inscripción antes de leer equipos, jugadores, responsables o registros de Supabase. Conserva los otros dominios operativos. Se mantienen los controles existentes de cartera y posición del usuario, la separación entre estado operativo y elegibilidad, y la ausencia de datos personales individuales en la proyección ejecutiva.

El reconciliador local compara las versiones automáticas y revisadas de los 11 expedientes, preservando fuentes, hashes y ambos cargos técnicos. No aprueba, importa ni declara elegibilidad. El inventario fuente contiene 16 PDF, 771 páginas y 378 grupos candidatos, con 13 páginas sin asignación; esos grupos no equivalen a equipos verificados. Los 11 registros originales siguen marcados como pendientes de revisión humana y admisión canónica.

## Archivos del alcance

- `database/migrations/20261005_copa_telmex_tournament_editions.sql` y su rollback: catálogo de edición y restricciones; sin aplicación automática.
- `src/devnous/copa_telmex/edition_models.py` y `registration_read_model.py`: modelo y proyección compartida, ámbito exacto y aislamiento de fallos SQL sin autoflush.
- `src/samchat/assistant/registration_postgres_adapter.py`, `adapters.py`, `tools.py`, `router.py`: contratos de consulta y selección de la fuente antes de las rutas heredadas.
- `src/samchat/client_executive/service.py` y `ui.py`: lectura compartida, exclusión del roster alterno y presentación de disponibilidad y agregados.
- `src/samchat/tournaments_v2/adapters/queries.py` y `services/soul_service.py`: exclusión opcional de inscripción, con comportamiento anterior por defecto.
- `scripts/reconcile_ctt_reviewed_pilot.py`: reconciliación local sin efectos de escritura en bases.
- Pruebas nuevas: `test_registration_read_model.py`, `test_registration_read_model_postgres.py`, `test_ctt_postgres_consumers.py`, `test_ctt_pilot_reconciliation.py`, `test_soul_registration_source_exclusion.py`. Se ajustó la fixture de `test_client_executive_service.py` para verificar la ruta sin configuración.
- Este informe.
- Los tres canons `SAMCHAT_*_2026-09-10.md` y `docs/roadmap/samchat-convergence-register.md`: enmienda expresamente aprobada y recibo de hashes actualizado.

## Verificación y comandos

La suite consolidada terminó con **209 tests aprobados**, sin skips y con 37 advertencias de deprecación de dependencias. Incluye migración y rollback en PostgreSQL efímero, fallos de fuente dentro de una sesión compartida, objetos ORM pendientes sin autoflush, nombres exactos, ámbito ambiguo, ausencia de fallback, exclusión efectiva de consultas al roster alterno, autorización, consistencia asistente/Dirección y reconciliación del piloto.

Comando: `PYTHONPATH=src /root/samchat/.venv/bin/python -m pytest -q`, con socket y puerto exclusivos del PostgreSQL efímero, los cinco archivos nuevos de pruebas y los archivos de regresión de informes ejecutivos, Dirección, gobernanza, superficie operativa, contratos/router del asistente, plataforma deportiva e inbox. Se generaron `acceptance.xml` y `coverage.json` en el directorio de evidencia. La cobertura global no es una medida de esta entrega: se calculó la intersección de líneas ejecutables con las líneas añadidas del diff y los módulos nuevos. Resultado: **364/366 instrucciones modificadas cubiertas (99,45 %)**, sobre el umbral de 85 %.

La revisión independiente, de solo lectura, cerró sin defectos pendientes tras comprobar ámbito exacto y exclusión de inscripción; ejecutó adicionalmente 81 tests. `git diff --check` y Black sobre los 12 archivos seleccionados pasaron. Los módulos nuevos pasaron los controles específicos de lint y tipos.

La validación general de calidad es **parcial**: `flake8 --max-line-length=88` sobre los Python del alcance falla con 509 hallazgos fuera de las líneas modificadas, sin hallazgos introducidos. `mypy --python-version 3.12 --follow-imports silent --ignore-missing-imports --show-error-codes` falla con 216 errores en cinco archivos heredados; una comparación con el checkout limpio del mismo SHA encuentra los mismos 216 mensajes y ninguno nuevo. No se reparó esa deuda fuera del alcance ni se presenta como validación general aprobada.

Evidencia local: `/tmp/samchat-ctt-postgres-evidence-20261005/`. `pilot-reconciliation.json` es privado (modo 0600) y contiene datos personales: no se incluye en el diff ni en publicación alguna. Los comprobantes agregados son `changed-code-coverage.json`, `quality-attribution.json`, `acceptance.xml` y los logs de calidad.

## Canon y pasos pendientes

El usuario aprobó explícitamente el diff y `canon-amendment-proposal.md`. Se incorporó el texto aprobado, con fecha, razón y evidencia, a los tres canons protegidos y se actualizaron sus SHA-256 en `docs/roadmap/samchat-convergence-register.md`. La verificación posterior confirmó la correspondencia de los tres hashes. Las anotaciones describen un candidato local, sin atribuirle despliegue, importación o aceptación de negocio.

No hay desviaciones de la especificación aprobada. La importación de los 11 expedientes, su aprobación humana, la captura completa del lote, la persistencia completa del cuerpo técnico y el retiro total de Supabase pertenecen a entregas posteriores. También quedan pendientes la aplicación autorizada de la migración/catalogación, el despliegue y la aceptación autenticada en operaciones. Los tests prueban el candidato, no el runtime productivo ni la aceptación de negocio.

## Integración para PR: 2026-10-06

El commit de la entrega aprobada es `912261730`. Se integró `origin/main` en `966384ee6d31b3fe1d10525fa1a3d785d4db193b` conservando las enmiendas posteriores del canon y combinando el cliente SOUL por lotes con la exclusión de inscripción. Los hashes se recalcularon sobre ambos textos; las secciones añadidas se renumeraron para evitar colisiones. Un caso adicional comprueba el paso del cliente junto con `include_registration=False`.

La suite ampliada terminó con **370 tests aprobados** y 45 advertencias. La revisión independiente de las resoluciones pasó otros **96 tests**, sin defectos pendientes. La primera ejecución ampliada falló por ausencia de `reportlab` en el entorno de tests; otra ejecución con el pytest del entorno de release tuvo tres errores de fixture. La ejecución final conservó el pytest original y reutilizó las dependencias faltantes ya instaladas mediante un runner local, sin instalar paquetes ni modificar el runtime. Los valores de lint, tipos y cobertura anteriores corresponden a la entrega sobre su base original.

Riesgo detectado en código posterior de `main`: el nuevo inicio de Dirección, `home.py`, usa una ruta separada `_build_operational_summaries` que todavía consulta los conteos de inscripción de SOUL. Esta entrega integra la proyección aprobada del tablero y sus consultas del asistente; no acredita consolidación en ese nuevo inicio. El PR se abre como borrador y este pendiente debe resolverse antes de activación. No se amplió silenciosamente el diff aprobado para reescribir esa ruta.

## Cierre de revisión previo a merge: 2026-10-06

Tras la autorización explícita de merge, se corrigieron cuatro observaciones sustantivas sobre compatibilidad del alcance aprobado. El riesgo del nuevo inicio descrito arriba queda resuelto: `home.py` consulta la proyección PostgreSQL para cada identidad autorizada antes de cualquier lectura alternativa. Solo los torneos sin configuración conservan SOUL, tanto en la ruta serial como por lotes. La fuente configurada fallida sigue visible y no activa fallback; activos, provisionales y revisiones pendientes permanecen separados.

Los filtros de estado, municipio, categoría, rama y equipo conservan coincidencia parcial sin distinguir mayúsculas, con comodines literales escapados y edición/identidad exactas. El límite de fecha de calendario incluye el día completo mediante un límite exclusivo al día siguiente. El adaptador restaura las claves españolas existentes de agregados y equipos (`municipio`, `equipos`, `jugadores`, `team_id`, `equipo`), conserva campos provisionales adicionales y no expone personas individuales.

Archivos de las correcciones: `src/devnous/copa_telmex/registration_read_model.py`, `src/samchat/assistant/registration_postgres_adapter.py`, `src/samchat/client_executive/home.py`, `tests/unit/test_registration_read_model.py`, `tests/integration/test_registration_read_model_postgres.py`, el nuevo `tests/unit/test_ctt_postgres_home.py` y una fixture explícita de torneo sin configurar en `tests/unit/test_direction_home.py`. Se regeneraron `docs/private-plugin/route-inventory.json` y `route-matrix.csv` con el generador canónico, sin activación ni ampliación de autoridad.

La aceptación consolidada pasó **380 tests**, con 51 advertencias y ningún fallo o skip; incluye SQL real de PostgreSQL para límites de fechas, filtros y ámbito, además de permisos y conteos del inicio en ambas rutas. Comando: el runner local con el pytest original, `PYTHONPATH=src`, socket/puerto PostgreSQL efímeros, las suites de primera entrega y regresión de Dirección, `--cov=.` y salida JSON/JUnit. La cobertura de las instrucciones corregidas es **32/32 (100 %)**; evidencia local en `review-fix-acceptance.xml`, `review-fix-coverage.json` y `review-fix-changed-coverage.json`. La revisión independiente pasó otros **117 tests**, sin defectos pendientes. Los módulos nuevos/corregidos pasan Black, isort, flake8 y mypy específicos; persiste la deuda general heredada ya documentada.

Canon unchanged respecto de la enmienda aprobada: estas correcciones restablecen la fuente compartida y los contratos previstos, sin cambiar arquitectura, autoridad, reglas de elegibilidad ni estado productivo descritos en esa enmienda. Se verificaron sus hashes. La admisión/importación, migración/catalogación productiva, despliegue y UAT siguen pendientes de sus propios recibos y autorizaciones.
