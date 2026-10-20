# Gastos: Pagado y pendiente

Fecha: 2026-10-20. Base inicial: `d548d148f`; integración vigente: `origin/main` `0df07ace2`.
Implementación aislada: `/root/samchat/.worktrees/gastos-payment-browser`.
Estado: código, aceptación técnica y revisión independiente completados. Commit inicial `25d35f5f5`; integración local con main y publicación autorizadas en curso. Sin despliegue ni UAT de negocio.

## Alcance aprobado y resultado

El usuario aprobó la historia y después la especificación técnica en esta conversación. El botón **Pagado y pendiente** aparece en el panel de Gastos, su navegación y el resumen administrativo para lectores ya autorizados. Reutiliza `/admin/finanzas/payment-history`.

La consulta combina estado, mes/año, empleado (solicitante o beneficiario), solicitante, empleado beneficiario, proveedor, fechas desde/hasta y monto mínimo/máximo. Permite seleccionar fecha de solicitud, programación, pago efectivo o captura de confirmación. Pago efectivo lee `fecha_pago_efectiva`; captura lee `pagado_en`, preservando el contrato vigente de main. El mes y las fechas se intersectan. La fecha final incluye todo el día. Cada encabezado alterna ascendente/descendente sobre todos los resultados antes de paginar, con desempate estable y valores ausentes al final. Monto y estado aparecen al inicio del detalle.

Hay 50 solicitudes por página, sin el recorte anterior de 500 solicitudes del historial. Los totales abarcan todos los resultados filtrados y separan moneda y cantidades pagadas/pendientes. Los montos faltantes se mantienen ausentes y se cuentan expresamente; los totales con datos incompletos suman solo importes conocidos. Los importes conservan precisión decimal.

Los cortes en proceso continúan pendientes hasta confirmar el pago. Borradores y rechazados sin pago confirmado no se presentan como obligaciones elegibles, aunque exista un corte histórico. El servicio reutiliza la fuente canónica de Payment Run y su clasificación. La consulta no ejecuta DDL ni escrituras. Payment Run conserva su comportamiento previo y sus permisos de operación. La consulta conserva `get_current_empleado`, el permiso de herramienta heredado y `require_payment_run_access`; no concede permisos nuevos a auxiliares sin acceso vigente.

## Archivos cambiados

- `src/devnous/gastos/routes/admin_routes.py`: botón administrativo, navegación de lectores y conexión del historial.
- `src/devnous/gastos/routes/user_routes.py`: botón en panel Gastos y subnavegación.
- `src/devnous/gastos/services/payment_run_service.py`: extracción de la fuente SQL compartida; compatibilidad del flujo previo verificada.
- `src/devnous/gastos/services/payment_history_service.py` (nuevo): validación, consulta de lectura, filtros, orden, paginación y totales.
- `src/devnous/gastos/routes/payment_history_ui.py` (nuevo): formulario, resúmenes, tabla accesible y enlaces de orden/página.
- `tests/unit/gastos/test_payment_run_routes.py`: adapta la prueba del historial al ordenamiento de servidor.
- `tests/unit/gastos/test_payment_history.py` (nuevo): aceptación unitaria, permisos, errores, escape y cobertura.
- `tests/unit/gastos/test_payment_history_postgres.py` (nuevo): siete pruebas reales con tablas temporales y datos sintéticos.
- `tests/browser/test_payment_history.py` (nuevo): formulario, etiquetas, orden, paginación y tamaños desktop/móvil con respuestas sintéticas.
- `docs/private-plugin/route-inventory.json` y `route-matrix.csv`: inventario estático reproducible actualizado tras integrar main.
- Este reporte de cierre.

Los cambios locales previos del checkout principal se preservaron. No se cambiaron dependencias, esquemas, configuración, credenciales ni registros de negocio.

## Comandos y resultados

Ejecutados desde el worktree, con `PYTHONPATH=src` y `/root/samchat/.venv/bin/python`:

```text
-m pytest -o addopts='' tests/unit/gastos/test_payment_history.py tests/unit/gastos/test_payment_run_service.py tests/unit/gastos/test_payment_run_routes.py tests/unit/gastos/test_payment_run_exporter.py tests/unit/gastos/test_payment_confirmation_dates.py --cov=devnous.gastos.services.payment_history_service --cov=devnous.gastos.routes.payment_history_ui --cov-report=term-missing --cov-fail-under=85 -q
```

Resultado final: **146 passed**, cobertura **100%** en los dos módulos nuevos (143 sentencias).

```text
PAYMENT_HISTORY_SYNTHETIC_URL=<URL del cluster temporal> ... -m pytest -o addopts='' tests/unit/gastos/test_payment_history_postgres.py -q
```

Resultado final: **7 passed** sobre PostgreSQL 16 temporal aislado, con 603 solicitudes sintéticas por caso. Se verificaron límites superiores a 500, sumas decimales exactas por moneda, fechas inclusivas, filtros combinados, faltantes, corte pendiente, exclusión de rechazados, orden por etiquetas y compatibilidad del servicio previo. El cluster se creó bajo `/tmp/samchat-history-*`, con socket local sin TCP, y se detuvo/eliminó al terminar. El runner usado está en `/tmp/run_synthetic_payment_history.sh`. Las pruebas rechazan URLs ajenas a ese patrón temporal y se omiten sin URL explícita. No se consultó la base de la aplicación.

```text
SAMCHAT_BROWSER_ARTIFACT_DIR=/tmp/samchat-payment-history-browser ... -m pytest -o addopts='' tests/browser/test_payment_history.py -q
```

Resultado final: **2 passed**, Chromium 1440x900 y 390x844. Se verificó conservación de filtros, navegación de orden y páginas, etiquetas accesibles y ausencia de desbordamiento horizontal del documento. Estas pruebas usan respuestas sintéticas; no prueban usuarios o datos productivos. Capturas revisadas: `/tmp/samchat-payment-history-browser/history-1440.png` y `history-390.png`.

También pasaron:

- `black --check` e `isort --check-only` de los cinco archivos nuevos Python, usando módulos ya instalados en `/tmp/samchat-amex-direct-tooldeps` mediante `PYTHONPATH` (sin instalar paquetes).
- `flake8 --ignore=E501,W503,E203` de módulos y pruebas nuevos y de `payment_run_service.py`.
- `mypy --config-file=/dev/null --python-version=3.12 --follow-imports=skip --ignore-missing-imports` de los dos módulos nuevos. Es una comprobación acotada, no el typecheck de todo el repositorio.
- `compileall -q` de los cinco archivos fuente afectados.
- `scripts/ci/check-accepted-regressions.py`: válido, sin fallos.
- `scripts/ci/check-registration-operational-surface.py`: válido, sin violaciones.
- `git diff --check`.
- Comparación AST de decoradores de rutas contra HEAD: mismos métodos/rutas en la revisión inicial; se preservan los métodos/rutas de main durante integración. Cambian los filtros de la consulta existente; no se agregaron endpoints.

## Fallos y atribución

Sin fallos pendientes causados por el cambio en las comprobaciones acotadas finales. Durante la construcción se corrigieron una prueba antigua que esperaba ordenamiento local, el aislamiento del bucle de eventos de la prueba Chromium y etiquetas accesibles de selectores.

El lint de archivos heredados **no está limpio**. En la revisión inicial contra la base original: `admin_routes.py` pasó de 123 a 106 hallazgos; `user_routes.py` conserva 254; `test_payment_run_routes.py` conserva 1. Se compararon código, mensaje y línea de origen contra HEAD; no quedaron hallazgos introducidos. No se amplió el alcance para reparar esa deuda. Los avisos SQLAlchemy `MovedIn20Warning` proceden del modelo existente de Copa Telmex.

Los ejecutables de formato en `/tmp` inicialmente no encontraban sus módulos; se resolvió usando los módulos ya disponibles por `PYTHONPATH`. Todas las comprobaciones de formato finales pasaron. No se ejecutó la suite completa del repositorio ni un lint/typecheck global limpio.

## Canon y límites

**Canon unchanged**: el cambio amplía una consulta existente, conserva autoridades y estados financieros, y no incorpora nuevas capacidades de pago ni cambios de datos. Los tres canones se leyeron en el orden requerido al investigar y sus SHA-256 coincidieron con el registro; tras integrar main se leyeron nuevamente y se comprobaron contra su registro actualizado (sin editar canon):

- Producto: `3f92bc667e2b152334a500f2cf9427cebdd4ed9c0b9b10538e075596842f435a`.
- Ingeniería: `b98ba82dade2cc148b06b787c9b0415479a4c68e8109bf6c5f2402dcbe5228e1`.
- Sweep: `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985`.

Desviaciones de la especificación: ninguna de alcance o comportamiento. La extracción SQL compartida y el módulo UI concretan la reutilización prevista.

El usuario aprobó historia, especificación, diff final y después commit, push, PR y merge sin omitir checks. Pendientes al preparar publicación: checks GitHub, merge bajo protecciones, pruebas con cuentas reales de auxiliares y volumen real. Despliegue no autorizado. No se cambiaron perfiles existentes para abrir acceso adicional. La validación técnica sintética no es aceptación de Finanzas.

Reversión: retirar este diff del release antes de desplegar o restaurar el código previo en una promoción autorizada; no requiere reversión de datos ni esquema.

## Validación independiente final

El agente especializado `implementation-validator` realizó una revisión independiente en modo solo lectura contra historia, especificación, diff y pruebas. Resultado: **Critical: ninguno; Important: ninguno; Minor: ninguno**. Confirmó autorización conservada, parámetros SQL y columnas permitidas, escape HTML, ordenamiento antes de paginar, totales por moneda, importes faltantes explícitos y ausencia de dependencias, migraciones o efectos productivos. No repitió los checks verdes ejecutados por el coordinador. Permanecen pendientes datos/sesiones reales, rendimiento productivo, despliegue y aceptación de negocio.

Diff completo para revisión: `/tmp/gastos-payment-browser-final.diff`. La aprobación inicial del diff no autorizaba efectos Git; el usuario los autorizó después explícitamente: commit, push, PR y merge bajo checks. El despliegue continúa excluido.

## Integración con main y publicación

Se integró el main vigente `0df07ace2` preservando sus 286 commits posteriores a la base inicial. Se resolvieron conflictos en imports, renderer del historial y fuente SQL, conservando fecha efectiva, permisos y flujos actuales. No se trasladaron cambios locales ajenos del checkout principal. Se añadió una prueba SQL donde pago efectivo es octubre y captura noviembre, y ambos filtros devuelven resultados diferentes conforme al dato real. Se regeneraron los dos inventarios oficiales y se ejecutó su prueba reproducible. Las autorizaciones y checks GitHub se registran en el PR.
