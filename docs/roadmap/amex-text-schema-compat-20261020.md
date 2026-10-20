# Compatibilidad del esquema TEXT para despliegue AMEX

Fecha: 2026-10-20. Base: main `0d1af142514ae33187b247f32556a1d991b890d1` (PR493 integrado, incluye PR492). Corrección dentro de la historia/especificación AMEX aprobadas y autorización explícita vigente hasta deployment. El validador independiente confirmó continuidad: no modifica reglas de negocio, arquitectura ni autoridad financiera.

## Problema y recuperación

El PR493 aprobó todos sus controles GitHub: 5869 unitarias generales, 510 integración, browser, asistente, seguridad, contrato y cobertura90%. El primer intento owner-run produjo un contrato incompatible antes del commit. La transacción se revirtió; se comprobó en producción que las cuatro tablas nuevas seguían ausentes y que el contrato anterior estaba listo. Se restableció el release671ea167d y sus timers; salud/disponibilidad200. Ningún cambio de esquema ni operación financiera se confirmó.

La diferencia comprobada reside en las tres columnas históricas documentos.tipo, documentos.estado y expense_reports.estado_gasto: producción usa TEXT y los fixtures ORM VARCHAR. PostgreSQL omite conversiones ::text redundantes al representar las dos vistas nuevas sobre columnas TEXT. Sus joins y todos sus predicados financieros permanecen iguales. Una copia aislada sólo del esquema, sin registros de negocio, reprodujo el caso. El restore de pg_dump también reescribió dos CHECK antiguos a otra representación equivalente: sólo en esa copia vacía se reemitieron desde la migración histórica versionada para recuperar la representación exacta de producción. No se modifica ni amplía su admisión en código o producción.

## Cambio

El verificador conserva los hashes VARCHAR existentes y admite además exactamente un hash TEXT completo por cada vista nueva. La definición de evidencia TEXT deriva sólo de eliminar las conversiones conocidas de esas tres columnas calificadas. No se normaliza SQL recibido de forma genérica; cambios de autorización, identidad, importe u otros casts siguen rechazados. Todos los contratos anteriores, CHECKs, triggers, funciones, claves y tipos permanecen idénticos. No cambia la migración493 ni añade DDL.

Archivos:

- src/devnous/gastos/services/amex_direct_schema_contract.py
- src/devnous/gastos/services/amex_direct_schema_service.py
- tests/unit/gastos/test_amex_direct_schema_admission.py
- tests/integration/test_amex_text_schema_compat_postgres.py
- docs/roadmap/amex-text-schema-compat-20261020.md

## Verificación

Pruebas: formas exactas TEXT/VARCHAR aceptadas; cambios de aprobación/importe/identidad rechazados en ambas vistas/formas; conversión ajena no aceptada. PostgreSQL convierte realmente las tres columnas a TEXT y recrea vistas desde el DDL original. R2 finaliza sin otra póliza; R3 usa la cuenta original y dos movimientos, sin nuevos impuestos/presupuesto, conservando exports. Cambiar el predicado de aprobación bloquea el esquema incluso sobre TEXT.

**50 PASS**,120 advertencias heredadas,15.99s. Log `/tmp/amex-text-compat-final-20261020.log`. Cobertura de sentencias modificadas: 4/4 (100.0%), umbral85%. Recibo `/tmp/amex-text-compat-diff-coverage-20261020.json`. Ejecución sobre la copia vacía del esquema productivo: ready=true, ninguna columna/trigger faltante, conservando los CHECK canónicos. Datos restaurados: cero filas de negocio.

Comandos:

```sh
PYTHONPATH=src:.:tests/acceptance:tests/unit/gastos:/tmp/samchat-amex-source-testdeps TEST_AMEX_DATABASE_URL=postgresql+asyncpg://test_user@127.0.0.1:55471/devnous_test COVERAGE_FILE=/tmp/amex-text-compat-final-20261020.coverage /root/samchat/.venv/bin/python -m coverage run --rcfile=/tmp/amex-suggestions-units-20261020.coveragerc -m pytest -q --no-cov tests/unit/gastos/test_amex_direct_schema_admission.py tests/unit/gastos/test_amex_direct_schema_cli.py tests/integration/test_amex_text_schema_compat_postgres.py
```

Mypy focal del servicio, Black/isort y flake8 focal de servicio/tests aprobados; sin dependencias nuevas. El contrato conserva literales largos heredados. No se amplía alcance para limpieza general.

Canon unchanged: corrige únicamente la compatibilidad del verificador con dos representaciones completas y equivalentes del mismo DDL aprobado; no cambia producto, reglas fiscales, flujos contables, autoridad ni el texto canónico previamente revisado. El despliegue sigue pendiente de publicar esta corrección, controles del PR y nueva aplicación owner-run con respaldo actualizado y verificación transaccional antes de confirmar.

Validación final independiente: CLEAN_WITH_NOTES; sin hallazgos Critical/Important/Minor. Contratos anteriores idénticos, fuentes congeladas coinciden y canons íntegros. No cambia negocio/arquitectura/autoridad; las aprobaciones vigentes cubren publicación y despliegue de esta corrección. Respaldo actualizado, controles de PR y comprobación transaccional siguen siendo requisitos del nuevo intento.
