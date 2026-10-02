# #431 · Portada de Dirección y Sam contextual

Estado: implementación para revisión, sin despliegue ni aceptación de negocio.
Fecha: 2026-09-30. Épica #430; historias posteriores #432 y #433.
Base revisada: `78d77cb72279291bc896967cc113d4c7fd38f852`.

## Resultado y recorrido

La entrada de Dirección del panel y navegación conduce a `/direccion/inicio`.
Los expedientes anteriores permanecen en `/direccion/tableros`.
La portada comparte edición, cartera, torneo e intervalo de registros con Sam.
Ofrece indicadores con significado/cobertura, ejecución frente al presupuesto anual,
comparación por torneo, evolución mensual conciliada, operación respaldada y hasta
tres asuntos prioritarios. Obligaciones programadas dentro de siete días preceden
otros asuntos; después se ordena por importe, sin compensar la desviación de un torneo
con el ahorro de otro. Las brechas se presentan después de asuntos cuantificables.
Este orden es una regla explícita de presentación, pendiente de aceptación del dueño.

Seleccionar una cifra abre el contexto de Sam. “¿Qué explica esto?” devuelve hechos,
definición, cobertura y brechas del mismo dato. No atribuye causas a partir de un
agregado. “Fuente y definición” muestra evidencia local del corte; “Comparar torneos”
consulta el mismo indicador en todo el alcance seleccionado. Las conversaciones se
persisten en las tablas existentes del asistente. No se invocan herramientas de escritura
financiera ni el dispatcher general. Causalidad, opiniones, recomendaciones y escenarios
continúan en #432; exportación para consejo continúa en #433.

## Fuentes y definición

Todos los importes publicados son MXN. Cobertura significa torneos con fuente
acreditada / torneos seleccionados; un subtotal parcial nunca representa la cartera
completa. La disponibilidad técnica no equivale a validación de negocio.
El corte es el instante de consulta, con observación por torneo; las lecturas independientes
entre Postgres/Supabase no constituyen un snapshot atómico entre sistemas.

| Indicador | Definición / fórmula | Fuente canónica | Cobertura y periodo | Validación propuesta / dependencia |
|---|---|---|---|---|
| Presupuesto autorizado | Suma de líneas de gasto de versión approved/frozen | `samchat.budgets.service.build_budget_snapshot` | Anual por edición; UUID o puente de alias previamente acreditado | Finanzas/Presupuestos: versión y conciliación del presupuesto |
| Ejercido presupuestal | `summary.actual_total`; base presupuestal de gastos activos | Mismo servicio y helper de base fiscal | `expense_reports.fecha` dentro del intervalo; no es caja | Contabilidad/Finanzas: reparto real de CFDI compartidos, moneda e importes |
| Comprometido documental | `summary.committed_total`; incluye estados pagado/cerrado según servicio existente | Mismo servicio | Fecha de creación de solicitud dentro del intervalo; no se suma al ejercido | Finanzas: confirmar estados y corte documental |
| Pagado documental | `summary.paid_total`; estado o fecha documental de pago | Mismo servicio | Cohorte de solicitudes creadas en el intervalo; no fecha del movimiento bancario | Contabilidad/Tesorería: conciliación bancaria independiente |
| Cierre estimado | `forecast.projected_close_total`, mecánico | Mismo servicio | Solo edición corriente, desde 1 de enero hasta hoy; proyección anual | Finanzas/Dirección: validar curva, cobertura y compromisos |
| Exceso estimado | Cierre estimado − presupuesto autorizado | Mismo snapshot | Mismo alcance y proyección; positivo = sobre presupuesto | Finanzas: no afirmar causa a partir del total |
| Obligaciones próximas | Σ importe pagable canónico de solicitudes aprobadas/en proceso sin pago, fecha en próximos 30 días | `finance_platform.service.build_finance_source_snapshot`, proyección financiera y `resolve_payable_document_amount` | Stock actual por UUID; tope 5000, lectura truncada invalida total; fechas/moneda/importes desconocidos no son cero | Tesorería: calendario y disponibilidad real de caja |
| Cobranza pendiente | Saldo atribuible de CFDI menos cobros aceptados | `samchat.ar.service.build_ar_read_model` | Edición seleccionada, UUID estricto, emisores configurados; brechas de atribución/cobro/moneda/lectura invalidan total | Cobranza/Contabilidad: atribución, moneda y matching |
| Cobranza vencida | Saldo pendiente con vencimiento contractual anterior al corte | AR, pendiente de términos validados | Sin dato; no se usa crédito implícito de cero días | Cobranza: términos y vencimientos aprobados |
| Liquidez disponible | Saldo bancario conciliado atribuible al alcance | Sin fuente acreditada de saldo | Sin dato; flujo neto no sustituye saldo | Tesorería: fuente, conciliación y atribución autorizada |
| Operación | Conteos de equipos/jugadores de fuente operativa; no porcentaje de avance | Dossier operativo canónico de Dirección | Edición completa; el intervalo financiero no filtra estos conteos | Operación: metas/baseline antes de publicar avance porcentual |

La serie mensual reutiliza el helper de base presupuestal, filtros de UUID y fechas
del agregado. Solo aparece cuando su suma concilia con el ejercido a un centavo.
Un mes sin movimientos puede ser cero únicamente si la fuente completa está
acreditada; sin fuente, sin importe o sin conciliación se informa una brecha.
Los meses de inicio/fin pueden ser parciales. No se mezclan las series de resultado
contable/caja con ejercido presupuestal.

CFDI compartidos y moneda desconocida conservan brechas: esta entrega no redefine
el reparto ni valida balanzas. No agrupa tres razones sociales y no trata entidades
deportivas como empresas. Los nombres técnicos mostrados en evidencia identifican
fuentes; no sustituyen conciliación financiera.

## Arquitectura y permisos

- Servidor: renderer y rutas de Dirección en el runtime documentado
  `copa_telmex_dashboard:app`. Asset versionado `static/direction_home.js`, servido por
  el mount existente `/static`; no depende del bundle externo de `/assistant`.
- Lecturas de presupuesto extendidas mediante parámetros opcionales; los consumidores
  existentes mantienen sus valores predeterminados. AR estricto filtra asignaciones
  UUID y matching de los ítems autorizados. Finanzas scoped admite documentos únicamente;
  no retorna gastos/pólizas globales cuando falta un scope.
- Dueños: identidad interna activa, posición elegible y cartera asignada.
  `superadmin` y `super_admin`: supervisión de todas las carteras activas y sus
  torneos activos, sin asignación de posición de Dirección. Acceso a portada,
  expedientes, consulta contextual y reportes de lectura; un deny
  explícito conserva precedencia. Denegaciones explícitas de las fuentes presupuesto/
  finanzas también impiden su lectura. No se crea ni amplía autoridad.
- Token firmado con `SESSION_SECRET_KEY`, actor, snapshot y TTL 15 minutos.
  El navegador no calcula dinero ni puede autorizar cantidades/filtros alternativos.
  El POST comprueba sesión/CSRF, identidad, cartera, torneos y permisos actuales antes
  de contestar o persistir; cambios de alcance exigen actualizar el tablero.
- HTML/JSON `no-store`, escape HTML/JSON, historial con `textContent`, límites de entrada,
  consultas abortadas y rechazo de respuestas tardías de un contexto anterior.
- Persistencia existente `AssistantConversation`, `AssistantMessage`, `AssistantRun`;
  metadata de scope/periodo/corte e indicador. No migraciones ni nuevas dependencias
  productivas. No escrituras financieras, presupuestales ni de permisos.

## Plan técnico y estado

1. Portada, filtros y snapshot con definiciones: implementado.
2. Lecturas canónicas acotadas, cero/ausencia, moneda y reparto: implementado con brechas.
3. Serie mensual conciliada y prioridades explícitas: implementado.
4. Consulta factual, firma, reautorización y continuidad: implementado.
5. Pruebas unitarias/HTTP/navegador y evidencia: véase [evidencia local](../evidence/direction-home-431/README.md).
6. Verificar despliegue activo, datos reales, cuentas y aceptación: pendiente de entorno/UAT.
7. Conciliación con exportaciones finales: dependencia #433; no cerrar la épica.

## Aceptación y salida a revisión

Antes de desplegar, comprobar el commit efectivamente servido por el runtime web,
archivo estático, esquema existente del asistente y secreto de sesión. La revisión
local del repositorio no verifica producción. No usar `artifacts` como frontend vivo.

En UAT, cada titular autorizado debe entrar con su propia cuenta, seleccionar cartera
y torneo, encontrar el asunto principal en un minuto sin guía, consultar una cifra,
cambiar periodo, comprobar nuevo contexto y contrastar importes/fechas/referencias con
el sistema fuente. Probar otro usuario sin posición, otra cartera y un deny explícito.
Finanzas, Tesorería, Cobranza y Operación deben firmar sus definiciones, cobertura,
cortes y discrepancias. La prueba sintética no acredita estas condiciones.

Para la aceptación final de #430: dueño → desviación → Sam → alternativa de #432 →
PDF/Excel de #433; conciliar tablero/conversación/exportación al mismo scope/corte y
registrar aceptación de negocio, cuentas empleadas y discrepancias resueltas.

Rollback: revertir el commit del PR; no requiere reversa financiera ni migraciones.
No mezclar, desplegar ni marcar #431 completada hasta terminar revisión y UAT.

## Canons

`Canon unchanged`: esta entrega implementa la lectura ejecutiva y el contexto de
conversación ya descritos; reutiliza servicios y persistencia canónicos, conserva
posición/cartera/torneo y separación documental/caja, y no introduce autoridad de
escritura ni redefine contabilidad. No cambia la política de entidades o balanzas.
La disponibilidad/aceptación real se mantiene pendiente, sin elevar evidencia local
a afirmación productiva. No se editó ninguno de los tres canons.

Hashes verificados contra `docs/roadmap/samchat-convergence-register.md`:

| Canon, en orden de lectura | SHA-256 |
|---|---|
| Product | `99b06fb10c09fc079bad21bd6cb5a141d018845e2fd6cd0680d2a16ff684a479` |
| Engineering | `9599c5872ad5308f6dc5546f94ba0596028fd0051f5f63492dd54d4a5fa1ce1c` |
| Sweep | `f930556b0c6002d8d6242e5591fdce72362d922a39a0746d4fac04538580e433` |
