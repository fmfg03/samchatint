# AMEX: cuenta aprobada como fuente — reporte de implementación 2026-10-13

Estado: implementación candidata lista para revisión final humana; validación independiente **CLEAN_WITH_NOTES**, sin hallazgos pendientes. Historia, especificación técnica y texto de canon aprobados. No commit, push, PR, merge, DDL productivo, backfill ni despliegue de este cambio.

Base: `3619ebcab586d96d6740c5dda2a578dd74bd83aa`. Rama: `feat/amex-approved-informe-20261013`. Worktree aislado: `/root/samchat/.worktrees/amex-approved-informe-20261013`. El checkout principal conserva sus cambios ajenos. La propuesta anterior de captura desde cargo permanece separada y no forma parte de este diff.

## Trabajo completado

La aprobación final real del INFORME captura su evidencia factual atómica y su actor/fecha originales, sin póliza ni decisión fiscal inferida. Contabilidad prepara todas las partidas AMEX de la cuenta aprobada; una confirmación explícita de Finanzas declara tratamientos en conjunto y genera una póliza del corte completo sin vínculo bancario previo. No se repite la comprobación individual.

Los reconocimientos nuevos tienen una ancla real de partida de informe. Reviews frescas preservan actor, fecha, versión y FK original; las confirmaciones conservan adopción explícita o proyección anterior observada, sin inventar historia. Replay requiere el payload original completo. Los payloads distintos de ajustes no reutilizan otra confirmación. La pantalla individual no puede sobrescribir decisiones ya adoptadas por Finanzas sin nueva confirmación.

La asociación bancaria posterior selecciona explícitamente un cargo y recibos contables existentes. El header bridge no postea, incluso sin hijos; conserva los cortes, pólizas, CFDI y exports existentes. Lectores fiscales y presupuestales excluyen sólo la identidad explícita del header, sin inferir identidad por importe, fecha o factura.

Migration owner-run con constraints y triggers protege anclas, ownership, evidencia/evento de aprobación, grupos bancarios completos e inmutabilidad. Preflight sólo lee metadata; startup y helpers no provisionan tablas. Rollback compatible preparado con patch ligado a SHA de fuentes; downgrade destructivo se rechaza ante historia report-first.

## Pruebas añadidas o actualizadas

Se añadieron pruebas de evidencia operativa, preflight, HTTP/HTML, JS real mediante Node, servicios contables con DDL real, restricciones SQL y concurrencia observable. Las fixtures anteriores de COI y CFDI incluyen las nuevas consultas de identidad sin alterar sus assertions de negocio.

La aceptación independiente cubre transición real de aprobación y rollback después de flush, aprobación/edición y confirmación/vínculo concurrentes, Finance consolidado sin banco previo, adopción sin reatribución, histórico bloqueado, replay, asociación 1→1/1→muchas, no doble aplicación fiscal/presupuesto y snapshot/CSV byte-idénticos. Los auxiliares legacy de autorización, settlement, auditoría y notificaciones se aíslan en la fixture de transición; no sustituye UAT autenticada.

Cuatro hallazgos independientes corregidos y probados: review legacy adoptada con FK NULL, ajuste con payload distinto, carrera sobre evento real de aprobación y origen histórico NULL con marca AMEX explícita.

## Comandos y resultados

Intérprete de pruebas `/root/samchat/.venv/bin/python`, con `PYTHONPATH=src:/tmp/samchat-amex-source-testdeps`. PostgreSQL exclusivamente temporal en localhost:55471 y schemas UUID; ningún servicio productivo usado. Herramientas y SDK de pruebas instalados únicamente en /tmp cuando faltaban, sin cambios en requirements ni paquetes productivos.

| Verificación | Comando / alcance | Resultado |
| --- | --- | --- |
| Suite unitaria de CI | `pytest tests/unit --ignore=tests/unit/private_plugin --ignore=tests/unit/readonly_gateway --cov=src --cov=copa_telmex_dashboard --timeout=60` | **4986 pasan**, 8 subtests; sin fallos |
| PostgreSQL AMEX | `pytest` sobre los seis archivos integration AMEX shared/approved-service/schema/identity/provenance/final-approval, con cobertura y `--timeout=120` | **192 pasan** con migración real, locks/carreras/rollback |
| Lectores SQL adicionales | `pytest tests/integration/test_amex_report_identity_postgres.py` final | **4 pasan**; incluye un caso adicional nuevo de reutilización CFDI |
| Contratos privados | `python scripts/private_plugin/run_offline_tests.py` en venv temporal declarado | **135 pasan**, sin sockets/credenciales/config productiva |
| Cobertura del diff | `coverage combine --keep` sobre datasets finales unit/PG/reader; `coverage xml`; `diff_cover ... --compare-branch=3619ebcab586d96d6740c5dda2a578dd74bd83aa --fail-under=85` | **91%**: 643/699 líneas ejecutables cambiadas cubiertas, 56 faltantes; archivos nuevos incluidos con intent-to-add |
| Formato | Black check de 12 Python nuevos; Black focal de servicios/corrección; isort y flake8 de pruebas nuevas | Pasa; sin reescritura global ajena |
| Tipos | `mypy --follow-imports=silent --ignore-missing-imports` sobre 17 archivos de fuente tocados, comparado con 14 de la base limpia | **Parcial**: 1254 errores heredados vs 1256; **0 nuevos** normalizados |
| Lint | `flake8 --max-line-length=88 --extend-ignore=E203,W503,E501` sobre fuentes tocadas, misma comparación | **Parcial**: 147 heredados; **0 nuevos** |
| Compilación | `python -m compileall -q copa_telmex_dashboard.py src tests scripts/check_amex_approved_informe_schema.py` | Pasa |
| Contratos CI | `check-pr-quality-gate.py`, `check-registration-operational-surface.py`, `check-accepted-regressions.py`, `check_runtime_packaging_consistency.py` | Pasan |
| Inventario | `python scripts/private_plugin/inventory.py` | 582 rutas, 573 static-live, 44 acciones; sin parse gaps; reproducible |
| Seguridad Python | Bandit high severity/high confidence + `check-bandit-baseline.py` | Seis hallazgos heredados coinciden exactamente con baseline aceptada; ninguno nuevo |
| Dependencias | `pip_audit --requirement requirements.txt` con las exclusiones actuales de CI PYSEC-2026-311/3813/3814/3815 | Exit 0; sin vulnerabilidades fuera de las cinco coincidencias ignoradas por contrato CI |
| Canon/register | SHA-256 contra register | Coinciden product y engineering enmendados; sweep sin cambio |
| Higiene | `git diff --check` | Pasa |
| Rollback compatible | `sha256sum -c ...rollback-source.sha256` y `git apply --unidiff-zero --check ...disable-writes.patch` | 3/3 fuentes exactas; patch aplica en dry-run; fuentes transformadas compilan; no aplicado |

La cobertura sigue el alcance Python de CI (`src` y dashboard), no afirma cobertura de líneas SQL; esas invariantes tienen pruebas PostgreSQL específicas. El CLI tiene pruebas funcionales adicionales, sin DDL.

## Fallos conocidos y límites

No quedan fallos causados por el cambio. Las fixtures faltantes, dependencias de test aisladas y defectos encontrados durante la implementación se corrigieron y sus checks se repitieron. Lint y tipos globales **no están completamente verdes** por la deuda heredada descrita; su comparación no detecta regresiones nuevas. No se reparó deuda ajena.

Permanecen pendientes: aprobación final del diff, efectos Git/external publication autorizados aparte, migración y release productivos, aceptación autenticada de Finanzas y revisión histórica de las 14 partidas bloqueadas. Aprobar este candidato no modifica esas partidas ni inventa snapshots del pasado.

## Desviaciones y riesgos de seguimiento

Sin desviaciones materiales de la especificación aprobada. Los lectores adicionales se limitaron a rutas realmente trazadas y documentadas en el inventario aprobado. Para rollback se eligió un patch compatible de la misma fuente, evitando introducir un patrón nuevo de feature flags.

El código antiguo no admite imported_expense_id NULL tras reconocimiento report-first: rollback debe conservar schema y usar el candidato compatible, no activar una release antigua. El patch cero-contexto sólo se admite tras comprobar su manifest SHA exacto. Procedimiento completo: `docs/operations/amex-approved-informe-20261013-rollout.md`.

## Archivos cambiados

- `SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md`
- `SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md`
- `database/migrations/20261013_amex_approved_informe_recognition.sql`
- `database/migrations/20261013_amex_approved_informe_recognition_rollback.sql`
- `docs/operations/amex-approved-informe-20261013-disable-writes.patch`
- `docs/operations/amex-approved-informe-20261013-rollback-source.sha256`
- `docs/operations/amex-approved-informe-20261013-rollout.md`
- `docs/private-plugin/route-inventory.json`
- `docs/private-plugin/route-matrix.csv`
- `docs/roadmap/amex-approved-informe-20261013-reader-inventory.md`
- `docs/roadmap/amex-approved-informe-20261013-spec.md`
- `docs/roadmap/amex-approved-informe-20261013-story.md`
- `docs/roadmap/samchat-convergence-register.md`
- `scripts/check_amex_approved_informe_schema.py`
- `src/devnous/gastos/amex_schema_policy.py`
- `src/devnous/gastos/models.py`
- `src/devnous/gastos/routes/admin_amex_accounting_routes.py`
- `src/devnous/gastos/routes/user_routes.py`
- `src/devnous/gastos/services/amex_accounting_cut_service.py`
- `src/devnous/gastos/services/amex_accounting_identity_service.py`
- `src/devnous/gastos/services/amex_accounting_posting_service.py`
- `src/devnous/gastos/services/amex_recognition_service.py`
- `src/devnous/gastos/services/amex_report_evidence_service.py`
- `src/devnous/gastos/services/cfdi_expense_link_service.py`
- `src/devnous/gastos/services/cfdi_ingestion_service.py`
- `src/devnous/gastos/services/documento_service.py`
- `src/devnous/gastos/services/documento_workflow_service.py`
- `src/devnous/gastos/services/expense_coi_export_service.py`
- `src/samchat/budgets/executive_facts.py`
- `src/samchat/budgets/service.py`
- `tests/integration/test_amex_approved_informe_schema_postgres.py`
- `tests/integration/test_amex_approved_informe_service_postgres.py`
- `tests/integration/test_amex_final_approval_acceptance_postgres.py`
- `tests/integration/test_amex_finance_confirmation_provenance_postgres.py`
- `tests/integration/test_amex_report_identity_postgres.py`
- `tests/unit/gastos/test_amex_accounting_cut_routes.py`
- `tests/unit/gastos/test_amex_accounting_identity_context.py`
- `tests/unit/gastos/test_amex_approved_informe_routes.py`
- `tests/unit/gastos/test_amex_approved_informe_schema_cli.py`
- `tests/unit/gastos/test_amex_card_identity_routes.py`
- `tests/unit/gastos/test_amex_consolidated_browser_acceptance.py`
- `tests/unit/gastos/test_amex_report_source_evidence.py`
- `tests/unit/gastos/test_block_messages.py`
- `tests/unit/gastos/test_coi_exportable_status_ui.py`
- `tests/unit/gastos/test_informe_coi_beneficiary_counterpart.py`

Este reporte también forma parte del diff final. Evidencia detallada en `/tmp/samchat-amex-approved-*20261013.*`, datasets coverage aislados en /tmp y recepción de aceptación `/tmp/samchat-amex-approved-informe-acceptance-20261013.md`.

## Seguimiento de publicación y corrección de CI — PR #480

El estado inicial de este reporte corresponde al candidato antes de publicación. El usuario aprobó explícitamente commit, push, PR y merge; commit inicial `a476c35cf5f45655d5e3a2760cad767ca7d2d5d9`, PR `https://github.com/fmfg03/samchatint/pull/480`. Migración, release y UAT productivos siguen separados.

CI detectó dos regresiones causadas por la extensión AMEX: la prueba browser todavía esperaba revisión por partida y ausencia total de scripts; la fixture compartida de evidencia tardía no creaba la tabla consultada por los lectores nuevos. Se actualizaron únicamente esas pruebas/fixture y se corrigió `white-space` del contexto personal en la nueva segunda columna, que heredaba nowrap. La prueba ahora ejecuta el lote real en escritorio/móvil, verifica rechazo de socio sin cuenta/motivo, payload completo y XSS con nombres que incluyen tags de imagen/script. No se eliminan controles de seguridad ni assertions de saldo/concurrencia.

Canon unchanged en esta corrección: presentación responsive y fixtures del flujo ya aprobado; sin cambio de fuente contable, autoridad, fiscalidad, modelo, migración o contrato de negocio. Se refrescaron los hashes de inventario y el manifest de rollback porque cambió un byte de fuente de UI.

Verificación local de corrección: `pytest -q tests/browser --timeout=90 --tb=short`: **80 passed**; suites PostgreSQL `test_bulk_cfdi_late_evidence_postgres.py` y `test_late_solicitud_evidence_postgres.py`: **47 passed**. Manifest de rollback, `git apply --unidiff-zero --check`, Black/flake8 del browser y `git diff --check`: correctos. Advertencias de deprecación existentes, sin fallos.
Verificación del inventario actualizado: **135 pruebas offline private-plugin aprobadas**.
