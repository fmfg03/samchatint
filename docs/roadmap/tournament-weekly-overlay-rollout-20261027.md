# Distribución semanal por torneo: promoción y aplicación

Alcance aprobado: Liga Telmex Telcel de Béisbol, 93 partidas de gasto y $3,686,827.60 en semana 40; Homeless World Cup México, 77 partidas de gasto y $30,384,939.78 en semana 52. El usuario ya aprobó el cambio y su despliegue. Actor de auditoría previamente autorizado: Juan Pablo, `e3d13040-2360-420f-98a1-516440ef63c3`; el comando valida que siga activo y posea las capacidades presupuestales `read`, `create`, `line_update` y `approve`.

La aplicación crea planificación versionada por torneo; conserva presupuestos económicos, entradas y planes originales, el historial y toda referencia financiera. Copa no se toca: su presupuesto y su semana 52 continúan pendientes de la equivalencia final y carga económica. Los demás proyectos sin plan siguen sin plan. Las fechas, importes y cantidades de líneas son selectores estrictos; ante cualquier diferencia se detiene y se revisa el nuevo estado antes de operar.

## Puertas previas

1. Comprobar antes de promover el commit probado en CI, el árbol exacto y que `samchat-gastos.service` usa una sola configuración `50-current-release.conf`. Verificar PID, cwd, `NRestarts`, `/healthz`, `/readyz` y rutas protegidas; preservar el release y drop-in actuales. Comparar la implementación con el release que sirve realmente, no asumir que el checkout coincide.
2. Ejecutar los checks focales y de aceptación, incluida integración sobre PostgreSQL aislado real, prueba de permisos, idempotencia, carreras, falta vs cero, conflicto por cambios económicos, otros proyectos intactos y futura carga de Copa. El lector y las pruebas deben pasar antes de cualquier migración o aplicación real. Registrar también la revisión independiente y el estado de las pruebas que falten.
3. Identificar base y rol efectivos a partir de la configuración autorizada, sin imprimir credenciales. Verificar tablas, columnas, restricciones, propietario y grants, nombres y hashes de la migración y que se ejecuta exactamente la migración revisada. No permitir que el arranque de la aplicación haga DDL.
4. Realizar backup privado de PostgreSQL en formato custom, calcular SHA-256 y verificar el archivo con `pg_restore --list`. Restaurar en una base desechable aislada y comprobar entidades y referencias necesarias; verificar la migración y las pruebas de ida y retorno en esa copia. Nunca restaurar sobre la base activa para resolver una prueba.
5. Preparar artefactos de evidencia en una carpeta privada (`0700`, archivos `0600`) y una copia del drop-in y el estado antes de actuar. Conservar la captura de rutas, versiones, líneas y planes originales que produce el CLI, así como la evidencia de auditoría y permisos.

## Migración y despliegue

Aplicar la migración owner-run sólo desde el archivo versionado del commit probado, tras el respaldo y la restauración de prueba. Si se ejecuta como `postgres`, asignar explícitamente propietario/grants mínimos del runtime para las nuevas tablas, verificar ACL y comprobar que no cambia el ownership de tablas existentes. Confirmar que la migración es atómica, que ambas tablas existen y que el usuario de la aplicación puede leer y escribir sólo lo previsto. El release anterior debe seguir funcionando mientras las tablas existen pero no tienen distribuciones activas.

Preparar el artefacto selectivo sobre el release realmente servido `c34427aa4` mediante cherry-pick del cambio revisado, sin incorporar los cambios de #503 que no están desplegados. El commit resultante del artefacto puede diferir del commit fusionado de la PR: registrar ambos y verificar que el diff funcional propio es equivalente al revisado, sin cambios ajenos. Ejecutar de nuevo los checks y pruebas de aceptación sobre esta base de serving, incluidos los puntos de integración que puedan diferir. Crear el release inmutable a partir del artefacto selectivo probado; conservar el resto de capacidades y artefactos del release que sirve en ese momento. Promover usando el mecanismo canónico de SamChat y verificar el único drop-in, commit, cwd real, salud, readiness, guardas de release, ausencia de bucle de reinicios y autorización de las rutas protegidas. Si el release o `main` cambian durante la preparación, reconciliar y volver a probar el candidato; no desplegar un parche antiguo sobre un release distinto.

No ejecutar la aplicación de distribuciones hasta que el nuevo release esté seleccionado y sano. El CLI exige que `WorkingDirectory` y `/proc/<MainPID>/cwd` coincidan con su raíz y que el commit y archivos de código coincidan con lo revisado. La verificación de acceso puede fallar cerrada si el sistema no permite leer cwd; no intentar extraer cmdline, variables o secretos del proceso para evitarla.

## Vista previa y ejecución

Usar el ejecutable Python del venv realmente configurado en el nuevo servicio, el script **del release desplegado** y la ruta existente del archivo de entorno, sin mostrar su contenido. En la carpeta privada de evidencia, ejecutar primero:

```bash
<python-del-runtime> <release>/scripts/apply_tournament_weekly_overlay.py \
  --env-file /etc/samchat/samchat.env --output-dir <carpeta-privada>
```

La salida `PREVIEW` contiene `plan_sha256` y `source_commit`, y `preview.json` contiene fuente/versión, ambos hashes canónicos, línea por línea, sus planes anteriores y totales. Revisar que el esquema está listo, las 93/77 partidas y montos concilian, los únicos torneos y semanas son los autorizados y el actor sigue autorizado. El digest también liga el commit y los archivos de la implementación. Esta vista previa se rehace después del despliegue y la migración; no reutilizar una previsualización de un worktree o de otra versión de código.

Tras revisar, usando **ese mismo release inmutable** y su `plan_sha256` íntegro:

```bash
<python-del-runtime> <release>/scripts/apply_tournament_weekly_overlay.py \
  --env-file /etc/samchat/samchat.env --output-dir <carpeta-privada> \
  --apply --expected-plan-sha256 <hash-revisado> --expected-commit <commit-completo>
```

El CLI vuelve a calcular ambos planes bajo `SERIALIZABLE`, valida fuente, estado, hash y permisos, aplica ambos torneos dentro de una sola transacción, verifica cada partida y sus ingresos, y confirma que la versión, líneas y calendarios originales siguen idénticos antes del commit. El filtro de SQL prohíbe DDL y escrituras fuera de las dos tablas de distribución y el log presupuestal. La lectura posterior usa una transacción independiente y comprueba ambas revisiones, conciliación, demás proyectos y auditoría. Se requiere `receipt.json` con `COMMITTED_VERIFIED` para afirmar finalización.

Si aparece `committed-unverified.json` sin `receipt.json`, **no reejecutar ni restaurar a ciegas**: la transacción puede haber sido confirmada y el marcador significa verificación pendiente. Inspeccionar lectura, auditoría, estado económico y actividad concurrente; clasificar y resolver el fallo de verificación. Si la aplicación falla antes del commit, el resultado completo revierte; conservar `fresh-preview.json` y `precommit.json` como evidencia según corresponda.

## Verificación posterior

Releer sobre la base activa y el release real: 93 líneas Liga, importe $3,686,827.60 en semana 40; 77 líneas Homeless, importe $30,384,939.78 en semana 52; cada línea conciliada; sin importes añadidos desde planes históricos. Verificar que los demás proyectos conservan sus planes o ausencia, la versión y referencias financieras son idénticas, y Copa sigue lista para su carga futura sin cambiar guardas de su historia. Revisar que Presupuestos y Dirección usan las mismas cifras y muestran conflictos como pendientes cuando hay cambios en el origen. Confirmar alcance por puesto y denegación de acceso no autorizado.

Registrar health, readiness, PID/cwd, reinicios, resultados, hash/commit, auditoría y evidencia de rollback. La verificación técnica no equivale a una prueba de navegador autenticada ni a aceptación de Dirección o Finanzas; dejar esas actividades como pendientes hasta que sean realizadas por sus usuarios.

## Recuperación

No ejecutar `DROP` ni restaurar un backup global sobre datos posteriores. Una migración sin datos puede revertirse sólo mediante su rollback revisado y comprobando que las nuevas tablas están vacías. Con datos reales, conservar toda la auditoría. La herramienta permite previsualizar la desactivación exacta de las revisiones creadas por esta aplicación, ligada al `receipt.json` verificado y a su SHA-256. Omite una revisión que ya existía cuando la aplicación fue idempotente. Conserva las filas y restaura la revisión anterior, si existía; si no, retorna al plan original. Rechaza una revisión distinta o ya reemplazada y no permite restaurar un plan anterior conflictivo.

```bash
<python-del-runtime> <release>/scripts/apply_tournament_weekly_overlay.py \
  --env-file /etc/samchat/samchat.env --output-dir <carpeta-privada-reversion> \
  --deactivate --receipt-file <carpeta-privada>/receipt.json \
  --expected-receipt-sha256 <sha256-archivo-recibo>
```

Revisar `deactivation-preview.json`, incluidos los UUID y la revisión que se restituirá. Para ejecutar, volver a llamar con los mismos argumentos más `--apply --expected-plan-sha256 <hash-preview-desactivacion> --expected-commit <commit-completo>`. La acción válida usa una sola transacción para ambos torneos, comprueba permisos y hashes actuales, preserva la fuente económica y emite `deactivation-receipt.json` con `DEACTIVATED_VERIFIED`. Si aparece `deactivation-committed-unverified.json` sin recibo final, leer y verificar el estado antes de cualquier reintento.

Un rollback de código debe ser compatible con las tablas y su estado, y verificar que no hace invisibles distribuciones comprometidas; si no hay mecanismo seguro probado, dejar la operación detenida y conservar la evidencia. Reaplicar o reparar siempre requiere un nuevo preview por torneo y comprobación del estado vigente.
