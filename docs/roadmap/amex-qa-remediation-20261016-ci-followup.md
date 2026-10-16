# AMEX QA — corrección del descubrimiento y medición de CI

Fecha: 2026-10-16. PR #491, candidato original `3170b73a2`. El usuario aprobó el resultado y autorizó commit, push, PR y merge. Esta corrección mantiene el comportamiento y las 40 huellas revisadas del candidato; no agrega reglas de negocio, autoridad, dependencias o efectos productivos.

## Problemas demostrados

La primera corrida de GitHub pasó las unitarias, integración, seguridad y contrato; falló cobertura con 63% porque `tests/integration` no descubría 39 casos nuevos de `tests/acceptance`. Los imports anteriores exponían sólo el fixture, no los tests. Además, el caso de navegador para asignar una partida usaba un cargo ya asignado: esperaba un selector que el cambio aprobado correctamente retira.

La medición tampoco declaraba `greenlet`, utilizado por SQLAlchemy async. En los mismos tests HTTP aprobados, coverage atribuyó a un archivo de 180 líneas números superiores a 1900 y omitió filtros demostrablemente ejecutados. Repetir con concurrencia configurada produjo 121 líneas correctamente atribuidas, incluidos los guards y filtros, sin líneas imposibles. Referencia primaria: [configuración oficial de coverage](https://coverage.readthedocs.io/en/latest/config.html#run-concurrency). La cifra local anterior de85.51% se conserva como evidencia histórica y queda sustituida por la medición corregida.

## Cambio acotado

- Un bridge de integración carga los módulos exactos y reexporta los 39 tests originales conservando parametrizaciones y assertions, sin importar Playwright ni el CSV privado.
- Tres casos HTTP sobre PostgreSQL y servicios reales validan búsqueda por RFC/UUID/fecha/importe (incluidos resultados excluidos, filtros y cursor inválidos), consulta SAT con autoridad/CSRF/consentimiento, recuperación auditada, journal canónico antes/después del corte y replay sin duplicación. Sólo identidad de prueba y transporte SAT son sustituidos.
- La prueba de navegador usa estado `sent` para asignar y un caso separado verifica que `assigned` sólo permita devolver.
- `.github/coverage-repository.ini` declara `thread` y `greenlet`; dos opciones `--cov-config` se aplican únicamente a los pytest del repositorio. Los runners aislados de plugins conservan su configuración. No cambian jobs, disparadores, permisos, baselines, exclusiones, fuente medida ni umbral del85%.

## Comprobaciones

- **839 pruebas aprobadas**,1271warnings heredados,132.98s: 797 unitarias AMEX +39 assertions originales ahora descubiertas por integración +3 HTTP. PostgreSQL15, la versión del job de CI.
- Archivo de navegador afectado: **5 aprobadas**,7.04s.
- Cobertura ejecutable modificada con datos frescos y concurrencia correcta: **594/642,92.52%**. No se combinó con la medición anterior ni se afirma cobertura de ramas/SQL/repositorio completo.
- Contrato estricto del quality gate: PASS. Formato de los tres tests: PASS; lint de integración: PASS. El navegador conserva una línea E501 preexistente del fixture CSV (línea61), sin nuevas líneas largas; el resto del lint pasa.
- El primer intento HTTP esperaba redirect303 para hash obsoleto; se corrigió la expectativa a conflicto409 y se repitió satisfactoriamente. Ningún handler se modificó para acomodar pruebas.
- Los 40 hashes del candidato aprobado permanecen iguales. La migración y el CSV completo ya validados mantienen su evidencia; no fue necesario repetir la simulación de145cargos porque no cambió su código ni sus assertions.

La validación independiente terminó **CLEAN_WITH_NOTES**, sin hallazgos: véase `amex-qa-remediation-20261016-ci-validation.md`. La nueva ejecución de GitHub queda pendiente al publicar este ajuste. La migración productiva, recuperación de datos, despliegue y UAT no están autorizados por este paso de publicación.

## Canon

Canon unchanged para este ajuste de CI: se conserva el contrato del85%, la separación de evidencia y toda autoridad financiera. Los apéndices de producto/ingeniería previamente aprobados y sus hashes siguen formando parte del PR.
