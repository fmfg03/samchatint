# Distribución semanal por torneo sin bloquear presupuestos de otros proyectos

Estado: historia y especificación aprobadas por el usuario; autorización vigente hasta despliegue. Las fechas y el resultado de negocio ya están indicados por el usuario.

## Historia y aceptación

Dirección necesita consultar la distribución de Liga y Homeless en el tablero, sin que registrar esas semanas exija inventar las de proyectos que aún no las han definido ni impida incorporar posteriormente el presupuesto de Copa.

1. Liga: distribuir las 93 partidas de gasto de la versión financiera vigente en semana 40, por un total de $3,686,827.60. Homeless: distribuir sus 77 partidas de gasto en semana 52, por $30,384,939.78. Cada partida concilia exactamente a centavos. Ambas incluyen partidas autorizadas en cero; no se interpreta el presupuesto cero de otros proyectos como una programación confirmada.
2. Los importes, conceptos, fases, cuentas, referencias, pagos, datos históricos y calendarios previos se conservan. La asignación anterior de Liga por $584,640.00 en semana 40 se incorpora al nuevo resultado sin duplicarla; su historial se conserva.
3. El usuario ve la distribución efectiva en Presupuestos y Dirección y las exportaciones que consumen las mismas fuentes. Otros proyectos sin plan siguen mostrándose como pendientes; sus ingresos esperados y sus planes existentes no cambian.
4. Copa puede cargar sus importes cuando se resuelva el destino pendiente; su semana 52 se aplicará al presupuesto efectivamente cargado. No se carga el Excel ni se inventa la partida faltante en esta tarea.
5. Toda escritura exige autoridad, actor, auditoría, respaldo y previsualización. La prueba de usuario autenticada y la aceptación financiera se reportan separadamente.

## Evidencia y bloqueo en producción

Release activo c34427aa4 y versión 2026 aprobada 64d963a7-61b1-402a-b58a-7efcb96cd71a. El usuario autorizó semanas 40 y 52. Existe un mecanismo de revisión que clona toda la edición y exige reconciliar todas las partidas de gasto, incluso las que no tienen plan. Hay 306 partidas de 12 proyectos ajenos a Liga/Homeless sin plan, entre ellas 54 de Copa. Su ausencia no debe transformarse en cero programado. Crear una revisión en borrador bloquea la transición de la versión financiera a reforecast/closed y la revisión prohíbe importar partidas, obstaculizando el presupuesto de Copa. No se creó la revisión.

## Diseño propuesto

Agregar planificación efectiva por torneo y partida, independiente del estado de la versión financiera y compatible con las lecturas existentes. La representación persistente versionada debe mantener UUID de torneo y partida, versión financiera y raíz/linaje, monto y cuenta de referencia, semanas, estado, actor, fecha, huellas de origen, auditores y vínculo a la revisión anterior. La migración se hará con el procedimiento existente para migraciones, respaldo y reversión; no añade dependencias.

El servicio propietario permite una previsualización transaccional y una aplicación idempotente por torneo. Valida permisos, edición, versión vigente, todos los UUID, importes y fases, suma exacta, exclusión de duplicados, concurrencia y hash de plan; registra el antes y después y aprueba las dos distribuciones autorizadas. Si cambian las líneas o importes del propio torneo, su planificación se marca conflictiva y deja de presentarse como completa hasta reconcilación explícita. Una actualización económica de un torneo distinto no invalida automáticamente los planes que siguen coincidiendo, ni cambia ni desbloquea los guardas de historia financiera existentes.

La lectura canónica combina la versión financiera y el plan vigente acreditado por torneo: si existe un plan válido lo usa para las cifras semanales, mensuales y acumuladas; para el resto conserva sus planes originales o la ausencia como pendiente. Se debe aplicar esta misma regla a las vistas, API, comparativos y exportaciones relacionadas. Evitar duplicar el importe de planes antiguos; preservar correctamente ingresos y raíz financiera en movimiento real y pagos.

## Pruebas y promoción

Probar: validación de cada semana y centavo; 93/77 partidas y totales; cero explícito sólo dentro del alcance solicitado; otros proyectos sin plan permanecen desconocidos; preservación de planes antiguos, ingresos, pagos y referencias; concurrencia e idempotencia; permisos y auditoría; conflictos de huella y cambios de importe; ausencia de fugas por torneo; tablero/API/exportaciones consistentes; carga económica posterior de Copa sin romper linaje ni inventar distribución; migración y restauración de respaldo en base aislada. Ejecutar aceptación y validación independiente antes de PR, y la promoción autorizada por el usuario sólo tras checks, respaldo y verificación del release activo.

## Entrega prevista y límites

Archivos probables: servicio de distribución y lectura de presupuestos bajo src/samchat/budgets/, rutas admin existentes, migración con los patrones del proyecto, pruebas unitarias e integración y documentación de despliegue. No tocar cuentas, pagos ni lógica de aprobación de documentos. El canon se evalúa al terminar; si el cambio afecta arquitectura o datos, debe modificarse con evidencia y revisión humana en el mismo PR, o explicar concretamente por qué no cambia.

Rollback: conservar planes precedentes y versionar el registro nuevo; desactivar únicamente la planificación añadida con auditoría y verificar que los lectores vuelvan al estado anterior. No restaurar una copia antigua sobre actividad financiera posterior.
