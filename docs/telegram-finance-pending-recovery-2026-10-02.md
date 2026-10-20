# Recuperación de avisos pendientes de Finanzas

Historia y especificación aprobadas por el usuario el 2026-10-02. Base de
implementación: `2da3da1f17304a4395e8ffea5a708e5fb49bc867` (`origin/main`).

## Problema y comportamiento esperado

Un aviso `finance_pending_payment` de una solicitud aprobada puede quedar en
`pending` si se interrumpe el emisor. El monitor anterior revisa documentos en
`enviado` para avisos de aprobación y no recupera estos avisos de Finanzas.
La recuperación periódica debe atender filas pendientes cuya última actividad
supere cinco minutos, con lotes acotados y prioridad por antigüedad.
El mismo ciclo debe ejecutar el único reintento de Finanzas cuando esté vencido
en `next_retry_at`, evitando perderlo cuando termina el proceso de monitor.

El propietario de escritura sigue siendo `telegram_outbox_service.py`; la
fuente es PostgreSQL directo del runtime `copa_telmex_dashboard.py`. Recuperar
un aviso reutiliza la fila y revalida que la solicitud siga aprobada y sin
evidencia de pago, y que su destinatario siga activo, con rol Finanzas y
Telegram vinculado. Los enviados se conservan. El bloqueo de la fila debe
abarcar la comprobación, la llamada HTTP y la persistencia del resultado, tanto
en el envío inicial como en consola, monitor y reintento diferido.

## Alcance y límites

No se agregan dependencias, esquema, permisos, estados financieros ni UI.
El reintento de fallos mantiene el límite existente; no se abre un bucle
ilimitado. Un bloqueo ocupado posterga el reintento sin consumir un envío;
los ciclos posteriores consultan su fecha persistida. El mensaje se reconstruye
con los datos vigentes, refrescando las identidades ORM, y se registra el
resultado sin exponer tokens, chat IDs ni cuerpos en logs.

Telegram no ofrece idempotencia para `sendMessage`: una caída tras aceptar el
mensaje y antes de guardar `sent` puede ocasionar duplicación en la recuperación
posterior. `sent` no acredita lectura. Los cinco minutos determinan elegibilidad
para recuperación, no un plazo garantizado de entrega.

## Canon y publicación

**Canon unchanged**: se restaura la recuperación de notificaciones dentro del
contrato vigente, sin cambiar reglas financieras, alcance de destinatarios,
arquitectura de producto ni autoridad. Los hashes del canon coinciden con el
registro de convergencia.

El despliegue requiere verificar que el monitor cargue la release corregida:
usa `/srv/samchat/current`, mientras el dashboard selecciona un directorio
explícito mediante `50-current-release.conf`. El cambio de código no autoriza
commit, push, PR, merge, despliegue ni envíos externos de pruebas.

Rollback: promover la release anterior por el procedimiento gobernado. No
borrar ni revertir recibos enviados; un rollback de código no deshace mensajes.

## Verificación

- Regresión inicial: la prueba de recuperación falló antes de implementar el
  servicio. Las pruebas de PostgreSQL también detectaron `pagado_en` ignorado
  y un desfase de dos horas en escrituras sin zona; ambos casos pasan después
  de corregir las guardas y fechas de Finanzas.
- Aceptación combinada final: **72 pruebas pasaron**, sin skips, en 45.32 segundos:
  47 unitarias/existentes y 25 sobre PostgreSQL 16 real, temporal, con TCP
  deshabilitado. Se simula únicamente el transporte; una prueba conserva la
  reconstrucción real del mensaje.
- Concurrencia: monitor contra monitor, envío inicial, consola y reintento;
  inserción simultánea con índice único canónico; sesión ORM obsoleta;
  destinatarios con chat compartido; un solo intento diferido consumido.
- Elegibilidad: documento pagado/rechazado/en proceso de pago, `pagado_en`,
  fecha efectiva y comprobante de pago; destinatario inactivo/otro rol/sin
  Telegram y chat actualizado. Fechas de creación/actividad/envío y reintento
  comprobadas en sesiones PostgreSQL con zona `Europe/Berlin`.
- Cobertura de líneas ejecutables modificadas: **149/160 (93.12%)**, umbral
  85%, por intersección de coverage.py JSON y diff contra HEAD.
  No se usa la cobertura global del módulo como sustituto del
  criterio de código cambiado. Script 5/5; outbox 144/155.
- Flake8 a 88 caracteres, isort y `git diff --check` pasaron. Black se aplicó
  a los rangos cambiados del archivo existente y a los archivos nuevos/script.
- Avisos preexistentes: deprecaciones de `declarative_base` y defaults ORM de
  `datetime.utcnow` fuera de las escrituras de Finanzas corregidas; no hay
  fallos funcionales atribuidos a la corrección.

Comando de aceptación, ejecutado desde el worktree:

```bash
COVERAGE_FILE=/tmp/samchat-telegram-final.coverage PYTHONPATH=src:. \
  /root/samchat/.venv/bin/python -m pytest \
  tests/unit/gastos/test_telegram_finance_recovery.py \
  tests/unit/gastos/test_telegram_document_approvals.py \
  tests/unit/gastos/test_telegram_pending_payment_backfill.py \
  tests/integration/test_telegram_finance_recovery_postgres.py \
  -q -o addopts= --cov=src/devnous/gastos/services --cov=scripts \
  --cov-report=json:/tmp/samchat-telegram-final-coverage.json \
  --cov-report= --cov-fail-under=0
```

El umbral se aplica por separado al código cambiado, mediante
`/root/samchat/.venv/bin/python /tmp/samchat_changed_coverage.py
/tmp/samchat-telegram-final-coverage.json`.

La primera revisión independiente detectó pérdida de reintento por bloqueo y
reconstrucción con identidades ORM obsoletas. Se corrigieron y se añadieron
pruebas reales de ambos casos. También se comprobó que el monitor de una sola
ejecución conserva el reintento vencido en la base sin depender de tareas en
memoria que desaparecen al salir del proceso. La revalidación final confirmó
que no quedan hallazgos críticos ni importantes. Se conservan
los límites de entrega y despliegue indicados arriba. No se ha
ejecutado la suite global, CI remoto ni UAT autenticado; no se han realizado
commit, publicación ni despliegue. No se ejecutaron envíos externos durante
estas pruebas. Los cambios locales del checkout principal siguen intactos.

## Archivos y controles

- `src/devnous/gastos/services/telegram_outbox_service.py`: recuperación,
  serialización de envíos, elegibilidad, refresh ORM y fechas UTC de Finanzas.
- `scripts/monitor_telegram_workflow_notifications.py`: recuperación dentro
  del ciclo existente y conteos `finance_*`.
- `tests/unit/gastos/test_telegram_finance_recovery.py`: nuevas regresiones.
- `tests/integration/test_telegram_finance_recovery_postgres.py`: PostgreSQL
  aislado, concurrencia, fechas, selección y recuperación tras reinicio.
- Este documento: alcance, límites y evidencia.

Comandos de estilo (Python de herramientas ya instalado):

```bash
/srv/samchat/venvs/release-598e7a286/bin/python -m flake8 \
  src/devnous/gastos/services/telegram_outbox_service.py \
  scripts/monitor_telegram_workflow_notifications.py \
  tests/unit/gastos/test_telegram_finance_recovery.py \
  tests/integration/test_telegram_finance_recovery_postgres.py \
  --max-line-length=88 --extend-ignore=E203,W503
# isort --check-only sobre los mismos cuatro archivos.
# black --check sobre script y nuevas pruebas; rangos cambiados en outbox.
git diff --check
```

Resultado: controles anteriores aprobados. No hay desviaciones materiales de
la historia/especificación. La lectura de reintentos vencidos en el monitor es
la implementación del límite ya aprobado de un reintento a las dos horas en
un proceso de corta duración, con los campos persistidos existentes. No hay
fallos funcionales conocidos causados por la corrección.

## Corrección de compatibilidad detectada en CI

El primer CI del PR #451 pasó integración (95 passed, 2 skips ajenos a estas
pruebas) pero falló una prueba unitaria existente de reenvío forzado de Control
Presupuestal: su objeto simulado no contiene `notification_type`. Fue una
regresión causada por el nuevo acceso al campo para clasificar las fechas;
se reprodujo antes de corregirla, sin modificar ni debilitar la prueba existente.

Los caminos de marcado usan un valor por defecto cuando el campo está ausente,
conservando el comportamiento previo de los objetos antiguos. Los registros
reales de Finanzas mantienen sus fechas UTC. Se agregaron dos regresiones de
marcado para `sent` y `failed`.

Verificación ampliada: **124 pruebas pasaron**, sin skips, en 27.43 segundos,
incluyendo la suite de Control Presupuestal, los avisos de aprobación, la
recuperación de Finanzas y las 25 pruebas de PostgreSQL. Comando:

```bash
PYTHONPATH=src:. /root/samchat/.venv/bin/pytest -q \
  tests/unit/gastos/test_budget_control_gate.py \
  tests/unit/gastos/test_telegram_finance_recovery.py \
  tests/unit/gastos/test_telegram_document_approvals.py \
  tests/unit/gastos/test_telegram_pending_payment_backfill.py \
  tests/integration/test_telegram_finance_recovery_postgres.py
```

Flake8, isort y `git diff --check` pasaron; la revisión independiente de lectura
no encontró hallazgos críticos, importantes ni menores en este ajuste. La
autorización de merge queda sujeta al nuevo CI del commit corregido; no se
omite ninguna protección. Este ajuste conserva el alcance y comportamiento
aprobados y no requiere cambios de esquema, dependencias ni autoridad.

## Correcciones de revisión y aceptación final

La consola distingue `busy`, `skipped` y `already_sent` de un fallo real;
estos resultados no aumentan el contador de fallos ni de intentos de envío.
El reenvío manual explícito conserva `force_resend`, sujeto a la elegibilidad
vigente. La recuperación automática sigue protegiendo los avisos enviados.
Si ocurre un rollback durante el reenvío, se conserva cualquier acuse más
reciente que haya persistido otro emisor; no se sobrescribe con un fallo.

Las regresiones cubren estos resultados, el reenvío manual y la carrera de
rollback con PostgreSQL real. Aceptación conjunta final: **140 passed**, sin
skips, en **55.07 segundos**: 108 pruebas unitarias/existentes y 32 de PostgreSQL.
Se ejecutó el comando de cobertura anterior incluyendo también
`tests/unit/gastos/test_budget_control_gate.py`. Cobertura final de líneas
ejecutables cambiadas contra `origin/main`: **178/191 (93.19%)**, superior al
85% requerido; script 5/5 y outbox 173/186. El analizador de cobertura recibió
`origin/main` como segundo argumento para incluir todos los commits del PR.

Flake8, isort y `git diff --check` pasaron. La validación independiente no
encontró hallazgos pendientes. Los ajustes restauran comportamiento existente
dentro del alcance aprobado, sin desviaciones materiales. El CI del nuevo
commit debe pasar antes del merge; no se omiten protecciones. No hay despliegue
ni envíos externos de prueba. Los resultados anteriores se conservan como
evidencia histórica y esta sección establece el corte final de aceptación.
