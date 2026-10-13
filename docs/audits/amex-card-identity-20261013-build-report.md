# AMEX: identificación explícita de tarjeta — informe de implementación

Fecha: 2026-10-13. Base: main 84ec01bf0bc96fd931a034a97d08263b03c44f4e. Historia y especificación aprobadas explícitamente antes de implementar. Estado: diff y commit/push/PR/merge aprobados; publicado en PR #478 y pendiente de CI sobre el ajuste final antes de merge. Sin despliegue ni reparación productiva.

## Problema y comportamiento resultante

Los gastos de informe podían marcarse AMEX sin identificar su tarjeta. Conciliación filtraba candidatos por últimos cuatro dígitos iguales al cargo importado, presentando selectores vacíos. El servicio de vínculo tampoco permitía asociar una partida sin identidad de tarjeta.

La sección Partidas AMEX sin tarjeta presenta referencias de partida e informe, estado, fecha, importe y bloqueos. Finanzas selecciona explícitamente la tarjeta activa, las partidas, un motivo y confirmación. El servicio canónico identifica únicamente los últimos cuatro dígitos, guarda auditoría y no genera una póliza. Luego el usuario confirma el vínculo y completa la revisión/corte por los servicios existentes.

Marcado masivo, captura rápida y edición requieren identidad explícita donde corresponde. Los suplementos mantienen la clasificación AMEX canónica por cuenta y reciben únicamente la tarjeta confirmada; no se infiere tarjeta por titular. Edición conserva contexto de informe al declarar AMEX, valida fuentes protegidas y remite las identidades ya faltantes a recuperación auditada. La pantalla explica por qué no hay opciones y muestra acceso a revisión contable.

## Alcance y archivos

- src/devnous/gastos/routes/user_routes.py: sección y POST de identificación, mensajes, navegación y controles de captura/marcado/edición.
- src/devnous/gastos/services/amex_recognition_service.py: validación de tarjeta/cuenta, identificación auditada e idempotente, validación de transición de edición, locks y elegibilidad completa.
- src/devnous/gastos/services/amex_expense_service.py: reutilización de identificación validada en marcado; protección de marcado/desmarcado.
- tests/unit/gastos/test_amex_card_identity_routes.py: pruebas nuevas de render y HTTP real, autoridad, transacciones, período, contexto, captura y suplementos.
- tests/unit/gastos/test_amex_card_identity_service.py: pruebas nuevas de guardas, identidad y transición de edición.
- tests/unit/test_employee_debtor_accounting_contract.py: adaptación mínima de tres fixtures al contrato explícito de tarjeta y guardas nuevas.
- tests/integration/test_amex_shared_recognition_postgres.py: 27 casos nuevos, sin cambiar las fixtures ni seeds existentes.
- docs/private-plugin/route-inventory.json y route-matrix.csv: regenerados con el único POST nuevo y las fuentes definitivas.
- docs/roadmap/amex-card-identity-recovery.md: historia, especificación aprobada, evidencia y límites.
- Este informe.

Sin nueva dependencia de producción, tabla, migración, job, backfill ni reparación productiva. Checkout principal con cambios ajenos preservado; trabajo en /root/samchat/.worktrees/amex-card-identity-20261013.

## Verificación y resultados finales

Entorno general: /root/samchat/.venv/bin/python, Python 3.12.3. Dependencias declaradas faltantes para pruebas se instalaron únicamente en /tmp/samchat-amex-testdeps: pypdf>=6,<7, reportlab>=4.4,<6 y aiosqlite>=0.20. No se modificaron requirements ni el entorno productivo.

PostgreSQL 15 efímero: contenedor samchat-amex-review-tests-20261013, bind exclusivo127.0.0.1:55471 y datos en tmpfs. La fixture protege host/puerto y crea/elimina schemas propios; no conecta a producción.

Comandos principales:

```bash
PYTHONPATH=/tmp/samchat-amex-testdeps:src /root/samchat/.venv/bin/python -m pytest tests/unit/gastos tests/unit/test_employee_debtor_accounting_contract.py -q --disable-warnings --no-cov
```

Resultado tras revisión:1,996 passed,8 subtests,67.63 s. Log:/tmp/samchat-amex-review-full-unit.log. Advertencias SQLAlchemy/utcnow heredadas.

```bash
TEST_AMEX_DATABASE_URL=<isolated localhost55471> COVERAGE_FILE=/tmp/samchat-amex-review.coverage PYTHONPATH=/tmp/samchat-amex-testdeps:src /root/samchat/.venv/bin/python -m pytest tests/unit/gastos/test_amex_card_identity_routes.py tests/unit/gastos/test_amex_card_identity_service.py tests/unit/gastos/test_amex_card_account_ui.py tests/unit/gastos/test_amex_expense_service.py tests/unit/gastos/test_quick_shared_invoice_amounts.py tests/unit/gastos/test_block_messages.py tests/unit/gastos/test_edit_expense_cuenta_gastos_loading.py tests/unit/test_employee_debtor_accounting_contract.py tests/integration/test_amex_shared_recognition_postgres.py -q --disable-warnings --cov=devnous.gastos.routes.user_routes --cov=devnous.gastos.services.amex_recognition_service --cov=devnous.gastos.services.amex_expense_service --cov-report=xml:/tmp/samchat-amex-review-coverage.xml --cov-fail-under=0
```

Resultado tras revisión:301 passed,8 subtests,158.75 s; incluye87 casos PostgreSQL y214 unitarios relevantes. Los214 unitarios se solapan con la suite amplia, no se suman como pruebas únicas. Log:/tmp/samchat-amex-review-scoped-final-suite.log. El archivo PostgreSQL completo también pasó87/87 en la corrida independiente de aceptación.

```bash
/tmp/samchat-pr462-plugin-venv/bin/python -m diff_cover.diff_cover_tool /tmp/samchat-amex-review-coverage.xml --compare-branch=origin/main --fail-under=85 --json-report=/tmp/samchat-amex-diff-coverage.json
PYTHONPATH=src /root/samchat/.venv/bin/python scripts/private_plugin/inventory.py
/tmp/samchat-pr462-plugin-venv/bin/python scripts/private_plugin/run_offline_tests.py
```

Cobertura del diff tras revisión:222 de 259 líneas = 85.71%; gate de 85% aprobado. Desglose: rutas 76.9%,expense_service 95.8%,recognition_service 100%. El porcentaje es del código cambiado, no de los archivos completos.

Inventario:582 rutas, 573 live_static, 44 acciones, 0 parse gaps. Suite aislada135/135 OK, incluida integridad del canon y reproducibilidad. Log:/tmp/samchat-amex-review-plugin.log.

Black/isort y Flake8 a 88 columnas de los tres tests nuevos/ampliados principales pasan. Flake8 filtrado a líneas fuente añadidas: cero nuevos diagnósticos. Compilación de los tres archivos fuente y git diff --check pasan. No se reformateó código ajeno.

Mypy focalizado con follow-imports=skip e ignore-missing-imports:134 errores en candidato y134en baseline, firmas normalizadas idénticas; cero añadidos/retirados. Sigue fallando por deuda heredada (133 en rutas y 1 en backend), por lo que no se declara validación global de tipos ni lint completamente limpia.

## Mapa de aceptación

- Rojo contra vista original: la partida sin identidad no aparece en selector y falta sección de recuperación. Verde: pendiente visible, contexto y ayuda, sin ofrecer otra tarjeta ni preseleccionar tarjeta inferida.
- Vista real con SQLPostgreSQL: identidad ausente antes; disponible tras identificar la tarjeta correcta; otra tarjeta vuelve a excluirla.
- Recorrido canónico identify→bind→review→cut: una sola póliza e idempotencia del corte.
- Auditoría del actor, partida, tarjeta, antes/después y motivo; reintento idéntico sin duplicados.
- Lote mixto/errores trasflush: rollback real, sin cambios ni auditorías parciales.
- Sin permiso/actor inactivo, tarjeta/pasivo inactivo, identidad conflictiva, partida importada/cancelada/no AMEX y fuente COI, corte o vínculo protegida: rechazo.
- Fecha/moneda/importe incompatibles siguen bloqueando el vínculo.
- Cambiar identidad invalida revisión fuente por fingerprint y exige revisión nueva, sin borrar historia.
- Fuentes anteriores a activación permanecen sujetas a revisión histórica obligatoria.
- Identificación concurrente y vínculo que espera elcommit: orden canónico de locks y una sola auditoría.
- HTTP POST real: movimiento/desvinculación simultánea del informe al declararAMEX rechazados antes de helper/escrituras; mismo informe permitido.
- Captura, suplementos, marcado y edición verificados, incluidas identidades faltantes e invariantes de clasificación existentes.

Sin navegador autenticado ni UAT productiva ni carrera específica identidad-contra-corte añadida; sí carrera contra binding mediante el mismo lock global y corte posterior real. Estas limitaciones se conservan explícitas.

## Fallos anteriores y atribución

- pypdf/reportlab ausentes causaron fallas de colección: infraestructura del entorno de pruebas, resueltas por overlay temporal.
- aiosqlite ausente causó139 errores de fixtures en suite amplia: infraestructura, resuelta; la corrida final completa pasa1962/1962.
- La corrida combinada amplia con cobertura falló durante colección con KeyError:test_accounting_cleanup_period_and_origin. Atribución pendiente e intermitente: baseline con la misma instrumentación logró coleccionar1,947 tests y candidato sin instrumentación coleccionó 2,046 antes de las últimas 3 pruebas. No se afirma que ese comando haya pasado ni que el cambio lo haya causado. La suite funcional completa y la cobertura del código cambiado se verificaron satisfactoriamente en ejecuciones separadas.
- Un test PostgreSQL inicial tenía expectativa incorrecta de zona horaria: corregida en el propio test; archivo final con 87 passed.
- Un rerun focalizado instrumentado falló al recolectar con KeyError:test_amex_card_identity_routes; atribución pendiente. Se preservó el log fallido /tmp/samchat-amex-review-scoped-suite.log. El reintento sobre la misma fuente final pasó301 pruebas y8 subtests con cobertura85.71%.
- Mypy y avisos globales de estilo existentes siguen como deuda; no se amplió alcance para repararlos.

## Revisión independiente y correcciones

El validador de solo lectura encontró dos riesgos: invertir locks al validar tarjeta en bulk antes del lock global, y validar informe fuente antes de mover la partida en el mismo POST. Ambos se devolvieron a build y se corrigieron con pruebas antes de revalidar. La revisión manual final no mantiene defectos de código abiertos; su cierre final es CLEAN_WITH_NOTES, con los resultados finales de suite y cobertura verificados directamente. No se toma validación como aprobación humana.

La revisión automatizada de PR #478 señaló tres regresiones: reselección de tarjeta en edición ajena a identidad, selector ausente en suplementos AMEX por proveedor y exposición del catálogo/asignación a propietarios. Se corrigieron las tres con reproducciones HTTP y pruebas negativas. El primer head tuvo CI aprobado, pero ese resultado no sustituye CI del ajuste. CodeRabbit omitió su revisión automática; no se presenta como revisión de código realizada. El ajuste de narrowing de tipos se verificó con68 pruebas focalizadas y134 errores baseline idénticos; log /tmp/samchat-amex-review-final-mypy.txt.

## Desviaciones, riesgos y canon

Aclaración del punto 6: Finanzas activa consulta catálogo y asigna identidad; propietarios conservan captura factual preexistente con identidad pendiente. Las ediciones ajenas a identidad preservan last4 conocido o ausente, aun con tarjeta inactiva. No se concede acceso al catálogo ni asignación corporativa a propietarios. Guardas de edición y orden de locks completan las garantías aprobadas. No se modificó el mecanismo de reconocimiento ni la aprobación histórica.

Evidencia actual de producción en READ ONLY:60 partidas de informe sin últimos cuatro dígitos y 56 cargos importados; no se asume que correspondan entre sí ni a una tarjeta determinada. 14 partidas preceden a activación. Ninguna se corrigió: Finanzas debe identificar la tarjeta real y los históricos necesitan revisión separada.

Canon unchanged: propietarios canónicos, autoridad, identidad explícita, validaciones, fingerprints y corte inmutable se conservan. Los tres canon mantienen los hashes del registro; no se editó ningún canon.

Esta entrega no está desplegada ni aceptada por negocio. Diff humano, publicación,CI,merge,deploy y aplicación de datos se distinguen conforme autorización vigente. PR #462, trabajo separado de esta entrega, fue mergeado por autorización explícita como 84ec01bf0; no hubo deploy.
