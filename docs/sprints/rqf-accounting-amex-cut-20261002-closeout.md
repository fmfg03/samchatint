# Cierre de implementación AMEX — 2026-10-02

Estado: implementación, verificación automatizada y revisión independiente completas; pendiente aprobación humana del diff final. Rama aislada `fix/amex-shared-recognition`, base `2465a4cbf`. La aprobación vigente cubre implementación; no se ha realizado commit, push, PR nuevo, merge, despliegue ni migración o cambios de datos en producción.

## Resultado funcional

La conciliación automática prepara la clasificación. Contabilidad revisa y marca cada partida de un informe aprobado, elige gasto o deudores socios activos 1170-002-XXX y confirma un corte completo. Cada corte pertenece a un solo informe y genera una sola póliza. La modalidad socio carga el importe íntegro aun con CFDI, conservando la evidencia sin duplicar IVA/gasto. La reclasificación posterior conserva la póliza original y agrupa sus reversas fiscales exactas y cargos a socios en una sola póliza de ajuste; el saldo AMEX no cambia.

El reconocimiento usa identidad explícita del cargo importado, bloqueos PostgreSQL, restricciones únicas y revisión versionada con huella financiera. Si cambia el importe, cuenta o CFDI después del check, el corte exige una nueva revisión. Las exportaciones COI usan la evidencia congelada del corte y mantienen una cabecera. Pago AMEX conserva pasivo por tarjeta y Santander.

## Archivos del alcance

- `database/migrations/20261001_amex_shared_recognition.sql`
- `docs/sprints/rqf-accounting-amex-cut-20261001-spec.md`
- `src/devnous/gastos/amex_schema_policy.py`
- `src/devnous/gastos/routes/admin_amex_accounting_routes.py`
- `src/devnous/gastos/services/amex_accounting_cut_service.py`
- `src/devnous/gastos/services/amex_cut_export_service.py`
- `src/devnous/gastos/services/amex_recognition_service.py`
- `tests/integration/test_amex_shared_recognition_postgres.py`
- `tests/unit/gastos/test_amex_accounting_cut_routes.py`
- `tests/unit/gastos/test_amex_cut_exports.py`
- `tests/unit/gastos/test_amex_schema_policy.py`
- `tests/unit/gastos/test_amex_shared_recognition.py`
- `.github/workflows/test.yml`
- `copa_telmex_dashboard.py`
- `docs/sprints/rqf-accounting-laminar-001-spec.md`
- `docs/sprints/rqf-accounting-laminar-001-story.md`
- `src/devnous/gastos/models.py`
- `src/devnous/gastos/routes/user_routes.py`
- `src/devnous/gastos/services/amex_accounting_posting_service.py`
- `src/devnous/gastos/services/amex_card_payment_service.py`
- `src/devnous/gastos/services/coi_poliza_exporter.py`
- `tests/unit/gastos/test_accounting_laminar_executable_spec.py`
- `tests/unit/gastos/test_amex_accounting_posting_contract.py`
- `tests/unit/gastos/test_amex_card_payment_service.py`

## Pruebas y comandos

- Regresión de `test_amex*`, `test_accounting_laminar*`, `test_coi*`, deudores, CFDI compartido, contrapartida de informe y semántica de reembolsos; PostgreSQL real aislado: **318 passed**, 1399 warnings, 124.04 s. Comando usa `pytest --import-mode=importlib --asyncio-mode=auto -o addopts='' --cov=devnous.gastos` y `TEST_AMEX_DATABASE_URL` local puerto 55471.
- JUnit: `/tmp/samchat-amex-cut-tests.xml`; log completo: `/tmp/samchat-amex-cut-test-results.txt`; cobertura XML: `/tmp/samchat-amex-cut-coverage.xml`.
- `diff-cover --compare-branch HEAD --include-untracked --fail-under 85`: **87.55%**, 739 líneas ejecutables cambiadas, 92 sin cubrir. Incluye módulos nuevos. HTML `/tmp/samchat-amex-diff-coverage.html`; JSON `/tmp/samchat-amex-diff-coverage.json`. Esta medición cubre código Python de `devnous.gastos`; startup y YAML se verifican mediante contrato y test de política, no están en el denominador de cobertura.
- PostgreSQL: cortes y ajustes concurrentes, atomicidad y reintentos; source_key y versiones obsoletas; reversa fiscal congelada; cuentas de las siete tarjetas; importe socio total; vínculos ambiguos; actores/períodos inválidos. SQL real ejecutado dos veces: conserva activación, FK y unicidad. [Mapa de aceptación](/tmp/samchat-amex-recovery-acceptance-map.md).
- Pruebas de permisos reales HTTP, validación de check/versión y exportaciones XLSX/CSV/ZIP; no equivalen a UAT manual autenticada en producción.
- Lint scoped módulos y pruebas nuevos: `flake8 --max-line-length=88 --extend-ignore=E203,W503,E501` PASS. Lint de líneas modificadas en archivos legacy: 0 hallazgos. Black/isort scoped PASS.
- Mypy de seis módulos nuevos/cambiados: PASS, sin errores. Configuración temporal copia la del repo cambiando únicamente python_version de 3.8 a 3.12 para el runtime actual; se usa `--follow-imports=silent --ignore-missing-imports`.
- `python scripts/ci/check-pr-quality-gate.py`: PASS, contrato estricto y completo.
- `python scripts/check_runtime_packaging_consistency.py`: PASS.
- `git diff --check`: PASS.

## Fallos conocidos y correcciones

La primera corrida integrada falló por source_key no poblado; corregido. Las nuevas pruebas de importes cero/negativo descubrieron un rechazo tardío en CHECK SQL; corregido con validación previa y nueva corrida verde. El validador detectó un hueco adicional al cambiar el código de una cuenta manteniendo su UUID; corregido incluyendo código/activo de cuentas relevantes en la huella. Tres nuevas pruebas PostgreSQL y la regresión completa confirman el bloqueo hasta nueva revisión. Una nota documental histórica contradictoria fue aclarada. No quedan fallos atribuibles al cambio en los checks ejecutados. Persisten advertencias preexistentes de modelos datetime.utcnow/declarative_base y setuptools license; no se ampliaron cambios a esa deuda. No se ejecutaron test/lint/mypy globales de todo el repositorio.

## Validación independiente

Sin hallazgos críticos, importantes o menores abiertos. Revisión de historia/spec, alcance, servicios, permisos, snapshots/exportadores, startup/migración y CI. [Informe independiente](/tmp/samchat-amex-final-validation.md). El bloqueo transaccional actual abarca todo el catálogo contable: contención bajo carga no medida. El fixture PostgreSQL omite FK legacy ajenas al módulo; conserva todas las FK AMEX y prueba la migración SQL real con FK/índices.

## Desviaciones, canon y límites

Sin desviaciones funcionales del alcance aprobado. `Canon unchanged`: el corte inicial completo de un único informe conserva la atomicidad y una póliza por informe; el ajuste compensatorio auditable no constituye otro reconocimiento inicial. Canon y hashes verificados contra el registro; ningún canon fue editado.

Límites previstos: MXN, informe AMEX exclusivo y completo de un mismo mes, identidad explícita, cuentas activas y período abierto. Sin backfill automático. La migración owner-run debe aplicarse antes de activar estas pantallas; el startup excluye las cinco tablas nuevas. Informe mixto o histórico sin identidad verificable requiere revisión separada y queda bloqueado.

Riesgos y pendientes: aprobación humana del diff final; migración y despliegue separados; UAT manual autenticada de Finanzas con un informe real y posterior reclasificación; comprobar catálogo real de cuentas en el entorno destino. Las pruebas automatizadas y el servicio local no prueban disponibilidad en producción.
