# AMEX: corrección de similitud comercial en sugerencias

Fecha: 2026-10-20. Base: `3f8146eecc7b212eb0f172f7a5c669aab22c8161`. Alcance: defecto de UAT de la historia y especificación AMEX aprobadas; autorización explícita vigente hasta despliegue. La revisión independiente de continuidad confirmó que §3 prescribe los niveles del orden y tokens normalizados, pero no exige Jaccard sobre una bolsa combinada. La reparación conserva esa especificación; no requiere otra aprobación de historia o especificación.

## Problema comprobado

La consulta SQL real del release operativo coloca una pastelería y una aerolínea delante de una factura del restaurante cuyo nombre coincide con el cargo. Mezclar emisor, RFC y conceptos en una bolsa diluye el nombre distintivo y premia palabras como «restaurante» o «México». Una consulta en transacción READ ONLY reprodujo el caso del usuario y comprobó que el CFDI conocido sí existe en el catálogo autorizado. No se descargó SAT, creó vínculo, alias, propina, reserva ni póliza.

El mismo defecto quedó reducido a una prueba PostgreSQL con tres proveedores ficticios y el patrón comercial del caso: **1 FAIL antes de corregir**, 1.88s, comando focal registrado en `/tmp/amex-suntory-regression-red-20261020.log`. La consulta completa con el código corregido coloca primero la factura conocida, conservando diferencias de importe y fecha. Es una verificación del orden de lectura; no prueba correspondencia fiscal ni aceptación contable.

## Cambio y archivos

- `src/devnous/gastos/services/amex_direct_suggestion_service.py`: similitud con nombre del emisor como componente principal; RFC/conceptos como desempate secundario. Normalización léxica compartida elimina una lista finita de términos legales, genéricos y de ubicación únicamente del cálculo. Conserva los helpers genéricos anteriores, usa parámetros enlazados y publica `suggestions_v2` para invalidar cursores v1.
- `tests/unit/gastos/test_amex_direct_suggestions.py`: nombres, conceptos, acentos, términos genéricos, ausencia de nombre y rechazo de cursor anterior; conserva la verificación de autorización y ausencia de escrituras.
- `tests/integration/test_amex_direct_suggestions_postgres.py`: reproducción realista del fallo, paridad SQL/Python principal y secundaria, conceptos extensos, nombres ausentes/genéricos y prioridad de identidad e importe exacto.
- Este reporte.

Se mantiene el orden identidad explícita → importe exacto → similitud comercial (nombre, después apoyo fiscal) → diferencia relativa → fecha → UUID. No se añade umbral ni filtro de candidatas. El catálogo completo se ordena antes de LIMIT, la paginación conserva alternativas y los cambios de mes/año no excluyen facturas. Las coincidencias siguen siendo propuestas y no acreditan identidad, diferencia económica o propina. Sin cambios de esquema, API, UI, permisos, dependencias, guards o contabilización.

## Pruebas y comandos

- **150 PASS**, 1 advertencia heredada, 12.56s: sugerencias, rutas, cursores, navegación, búsqueda manual y evidencia fiscal.
- **16 PASS PostgreSQL**, 1 advertencia heredada, 2.02s: SQL real en base desechable, incluidos casos negativos y paridad.
- Aceptación independiente: **54 PASS**, cero omitidas, 3.73s; 16 SQL más unidades y contratos de rutas, sin cambios en los tres archivos de código/pruebas observados.
- Cobertura de sentencias modificadas: **8/8 = 100%**, umbral85%. Recibo `/tmp/amex-suntory-ranking-diff-coverage-20261020.json`.
- Mypy focal, Black88/isort y flake8 focal aprobados. El intento inicial con `.venv` no tenía herramientas de estilo; se repitió con el entorno de herramientas ya existente, sin instalar dependencias. Flake8 usa longitud88 del proyecto y conserva la exclusión E203 por el slice Black heredado, idéntico a la base.

```sh
PYTHONPATH=src:.:tests/unit/gastos:/tmp/samchat-amex-source-testdeps COVERAGE_FILE=/tmp/amex-suntory-ranking-final-20261020.coverage .venv/bin/python -m coverage run --rcfile=/tmp/amex-suggestions-units-20261020.coveragerc -m pytest -q --no-cov tests/unit/gastos/test_amex_direct_suggestions.py tests/unit/gastos/test_amex_suggestions_settlement_routes.py tests/unit/gastos/test_amex_direct_navigation_cursors.py tests/unit/gastos/test_amex_direct_navigation.py tests/unit/gastos/test_amex_direct_cfdi_search.py tests/unit/gastos/test_amex_direct_fiscal_evidence.py
PYTHONPATH=src:.:tests/unit/gastos:/tmp/samchat-amex-source-testdeps TEST_AMEX_DATABASE_URL=postgresql+asyncpg://test_user@127.0.0.1:55471/devnous_test .venv/bin/python -m pytest -q --no-cov tests/integration/test_amex_direct_suggestions_postgres.py
/srv/samchat/venvs/release-598e7a286/bin/python -m black --check --line-length 88 src/devnous/gastos/services/amex_direct_suggestion_service.py tests/unit/gastos/test_amex_direct_suggestions.py tests/integration/test_amex_direct_suggestions_postgres.py
/srv/samchat/venvs/release-598e7a286/bin/python -m isort --check-only src/devnous/gastos/services/amex_direct_suggestion_service.py tests/unit/gastos/test_amex_direct_suggestions.py tests/integration/test_amex_direct_suggestions_postgres.py
/srv/samchat/venvs/release-598e7a286/bin/python -m flake8 --max-line-length 88 --extend-ignore E203 src/devnous/gastos/services/amex_direct_suggestion_service.py tests/unit/gastos/test_amex_direct_suggestions.py tests/integration/test_amex_direct_suggestions_postgres.py
git diff --check
```

## Límites, canon y entrega

Sin desviaciones del alcance aprobado ni correcciones de registros. Continúan disponibles alternativas con similitud baja o nula; esta reparación mejora el orden y no elimina resultados por una regla nueva. Importe exacto e identidad explícita conservan su prioridad, incluso cuando el nombre de otro candidato se parece más. Nombres comerciales sin palabras distintivas dependen del apoyo fiscal y siguen requiriendo revisión humana. El caso real sigue pendiente de selección y confirmación del usuario.

**Canon unchanged:** reparación interna de calidad de la puntuación de lectura, manteniendo producto, reglas, autoridad y arquitectura canónicos. Los tres canons se leyeron en orden y sus SHA-256 coinciden con el registro; no se editan textos protegidos.

Publicación y despliegue siguen autorizados dentro del mismo alcance; se requieren validación final independiente, controles GitHub y comprobación del commit efectivo, salud, configuración y SQL de lectura después del despliegue. Sin migración ni modificación de evidencia ya reconocida. Recuperación: promover un release compatible con el esquema actual; no revertir la migración AMEX anterior.

Validación final independiente: **CLEAN_WITH_NOTES**, sin hallazgos Critical/Important/Minor. Verificó alcance, orden aprobado, ausencia de filtros/escrituras, contratos de permisos, cursores, huellas y canon. Los conteos de pruebas se solapan y no se suman como casos distintos. Riesgos residuales: semejanza no confirma correspondencia; nombres genéricos conservan ambigüedad. Sin prueba de carga productiva. Aprobación continua confirmada para esta reparación. Recibo `/tmp/amex-suntory-ranking-validation-20261020.md`.
