# CxC y presupuestos: diagnóstico y corrección local

Fecha: 2026-10-25. Base: `dd985d0c5489b63c8b5fb5af3e267a29b06f0ec6`.
Runtime objetivo: `copa_telmex_dashboard.py`, dominios gastos/AR/budgets,
persistencia SQLAlchemy/PostgreSQL. Evidencia: repositorio y pruebas sintéticas;
runtime, datos actuales y aceptación de Plataforma Sports **no verificados**.

CxC ya tiene partida de ingreso, vínculo CFDI y póliza; no corresponde copiar
Payment Run ni consumo de gasto. El candidato corrige la lectura de parciales,
periodos e indicadores y cierra las entradas manuales identificadas de cobro
CxC según la decisión de Francisco. La reversa queda preparada contra el
original y bloqueada para contabilizar mientras falta la fecha/política
específica. No altera ni reconstruye históricos.

Defecto reproducido contra la base: abonos de 250 y 350 mostraban 350; ahora
suman 600 conservando recibos y pólizas. El problema semanal es un **bug de
conversión demostrable**, no una configuración por decidir: `month_number`
contiene semanas 1–52 y el canon ordena agrupar por mes de inicio de semana.
No se inventa la conversión inversa de un importe mensual a semanas.

## Base, aislamiento y coordinación

- Checkout original limpio, rama `work`, único worktree local. GitHub confirmó
  el mismo `main`, cero PR abiertas y 13 issues abiertas; ninguna de las issues
  ni ramas devueltas correspondía a esta corrección CxC. Lecturas de GitHub del
  25 de octubre; no equivalen a inventario de trabajos privados.
- `agentius_directo_work_list(project="samchat")` respondió
  `INVALID_ARGUMENT: Worker rejected operation`, sin identificador de trabajo.
  No se repitió ni se consultó otro host para sustituir esa operación. Por ello
  no se certifica ausencia de trabajos remotos privados.
- Copia local independiente: `/workspace/samchatint-cxc-budget-20261025`, rama
  `fix/cxc-partial-budget-read-20261025`. El original queda sin cambios.
- Coordinación recibida del padre: MULTIEMPRESA-001 tiene candidato privado
  `.worktrees/multiempresa-20261014` inaccesible/no publicado. Faltan owner,
  estado, diseño aprobado, base, hash de diff y R10/R13/R14 del mismo árbol.
  No reconstruido. Esta corrección usa únicamente los contratos de `main`.
- Se preservan AMEX settlement, Sin CFDI, signos y tratamientos de la base;
  no hay modificaciones en esos módulos, entidades, roles ni permisos.
- No se consultaron el servicio productivo ni las operaciones host denegadas.
  No se inició SATsync, recuperación de CFDI ni verificación de saldos reales.
  El antecedente de septiembre con cero CFDI no acredita el estado actual.

## Contrato canónico verificado

Se leyeron en orden los tres canons exigidos por `AGENTS.md`. SHA-256 coincide
con `docs/roadmap/samchat-convergence-register.md`:

| Canon | SHA-256 |
| --- | --- |
| Producto | `4f369621a03290d320c9b9efbd5f16683bcfb6e50d99bc5bae1e3d8e7d45345e` |
| Ingeniería | `19f670adb418ac2f359f793cde5f32d351686dd2fa6874ddc7ad601dc0f7305e` |
| Sweep | `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985` |

Ingeniería §8.4 separa ingreso esperado, CFDI, aprobación, match aceptado,
póliza de CxC, póliza de cobro y reversa. `ar_collection_matches` es autoridad
del match aceptado; una candidata bancaria no prueba cobro. Producto §18 agrupa semanas por mes de su fecha de inicio y conserva historia
de revisiones de distribución. **Canon unchanged**: el match como autoridad,
la separación de hechos y las reversas trazables ya están definidos. La
confirmación humana del 25-Oct restringe nuevos cobros CxC a esa autoridad;
no se incorpora una nueva fecha/regla fiscal ni se declara despliegue. El
tratamiento pendiente queda bloqueado. Ningún canon se editó silenciosamente.

## Flujo y gaps respaldados por código de la base

Referencias de línea corresponden a `dd985d0c`, antes de este diff.

| Dimensión | Evidencia actual y diferencia CxP/CxC |
| --- | --- |
| Owner presupuestal | `src/samchat/budgets/service.py:8230` es el snapshot canónico de actuals; `admin_budget_routes.py:1681` obtiene partidas `income` separadas. No usar los handlers de presupuesto legacy. |
| CxP y documentos | `documento_workflow_service.py:129,291` controla partida antes de aprobar informe/solicitud; el reembolso hereda informe aprobado y clasificado, sin segundo control. Snapshot `budgets/service.py:8629` distingue compromiso, resultado contable y reembolso para no duplicar gasto. Cerrar Payment Run no acredita pago (canon Ingeniería §8.3). |
| CxC, partida/proyecto | `cfdi_income_bridge_service.py:117,596,739` exige partida de ingreso, conserva torneo/fase/concepto y resuelve raíz financiera de revisiones. Propuesta `pending_approval`; sólo aprobar genera `cxc_cfdi_income`. La partida requiere subcuenta 1150 y cuenta de ingreso configurada (`:375`). |
| Empresa | `cfdi_income_bridge_service.py:53,74,279` admite emisores de RFC activos. Es allowlist global, **no** autorización del actor por empresa ni relación empresa–proyecto–banco. No hay parámetro de empresa en estos owners. Depende de MULTIEMPRESA-001; no activar Sports/GOAT/1218 con esta evidencia. |
| Permisos | `admin_routes.py:1609,8438,8489`: guard de operador por rol/departamento y nombres; POST acepta importe e identificadores del formulario. `admin_budget_routes.py:2207,2271,2304` aplica acción presupuestal, pero esos handlers no validan la pertenencia del ID al torneo de URL. Falta acreditar autorización de objeto y reconstruir importe/fuente desde servidor; no confundir filtro con permiso. |
| Alcance de lectura | `ar/service.py:259` soporta modo estricto por UUID de torneo; rutas Finance de la base no lo activan siempre. El diff extiende ese modo a los recibos y versión financiera; no concede ni sustituye permisos. |
| Periodo | `budgets/service.py:7015,7038,7716` usa semanas 1–52 en columna legacy `month_number`. `ar/service.py:130,829` las proyecta como meses y el calendario omite >12. Además, fallback de links (`budgets/service.py:9080–9137`) usa `budget_month` 1–12 directamente como bucket semanal; ledger usa fecha de póliza. Se requiere unificación explícita de lectura temporal, conservando fecha de factura, presupuesto y cada cobro. No se remapearon históricos. |
| Importes | `cfdi_income_bridge_service.py:375` registra CxC por total y separa impuesto trasladado de ingreso; snapshot `budgets/service.py:8629` usa haber–debe de 4100. Fallback sin póliza usa importe completo del vínculo (`:9116`). Así `real_income` no equivale a cobrado ni es homogéneo con el total de CFDI. Monedas/impuestos requieren evidencia propia; no inferir IVA ni aplicar porcentajes nuevos. |
| Parciales | `ar/collection_matches.py:470,556` permite abonos hasta residual (con tolerancia), póliza banco/CxC y auditoría. `ar/service.py:376` sobrescribía abonos en un diccionario por item. **Corregido en este candidato**. Reversos dejan de sumar; la cola parcial conserva saldo conocido. |
| Dos owners de cobro | `cfdi_income_bridge_service.py:797` registra cobro completo con fecha y cuenta bancaria fija; `ar/collection_matches.py:556` registra match bancario/parciales. No se observa exclusión mutua entre ambos. La lectura prioriza match sobre cobro directo (`ar/service.py:478`). No sumarlos ni consolidarlos automáticamente: riesgo de doble asiento y ocultamiento de evidencia. |
| Otra entrada manual identificada | `user_routes.py:4293,4354,4373` (`/admin/contabilidad/ingresos`) resuelve CxC y puede crear `ingreso_cobrado_ui`, sin match bancario. El candidato rechaza esa rama CxC antes de escribir y detecta evidencia histórica por ID o UUID de CFDI. El registro de ingresos sin CxC queda fuera de este cambio; no equivale a certificar exclusividad de todos los ingresos de la aplicación. |
| Cancelaciones/reversos | `cfdi_income_bridge_service.py:894` sólo hace soft-unlink; no genera reversa de ingreso. La selección de candidatos (`:279`) no comprueba cancelación SAT. `ar/collection_matches.py:655` conserva reversa y auditoría, pero lee antes del advisory lock y usa clave distinta de aceptación; concurrencia requiere prueba PostgreSQL. No equiparar unlink, rechazo, cancelación fiscal y reversa de cobro. |
| Idempotencia | Link pendiente se actualiza; link aprobado se rechaza. Índice activo CFDI+versión en `budgets/service.py:5217`; comprobación aplicativa más amplia no equivale a constraint global. Match: índice único de movimiento bancario activo, lock por item, rechazo de repetición. Cobro directo retorna `already_collected`. El diff deduplica la misma identidad de recibo en la proyección; no acredita idempotencia concurrente de todas las escrituras. |
| Trazabilidad | Vínculo registra CFDI, partida, actor y aprobación; match conserva banco, evidencia, actor, póliza y reversa. El diff preserva todos los recibos en vez de atribuir el total a uno. UI/export de prepólizas sigue siendo proyección (`ar/coi_exporter.py:45`), no exportador universal de pólizas persistidas. |

Rutas de los servicios citados: `src/devnous/gastos/services/`;
rutas web: `src/devnous/gastos/routes/`; AR y presupuestos: `src/samchat/`.

## Indicadores y decisiones resueltas

| Indicador | Fuente del candidato y límite |
| --- | --- |
| Presupuestado | Partida `income` y distribución autorizada; semanas agrupadas con `week_bounds(año, semana)`. Sin año explícito no se inventa calendario. |
| Facturado bruto | Total del CFDI, incluyendo pendientes de aprobación sin duplicar el documento. No equivale a ingreso reconocido. |
| Ingreso reconocido | Haber menos debe de las líneas 4100 de la póliza exacta vinculada; no se calcula como total menos un IVA supuesto. Sin evidencia: N/D. |
| Cobrado registrado | Suma de recibos aceptados activos, cada uno con fecha/póliza. El directo histórico se identifica por separado. Evidencia externa o conflicto no se suma automáticamente. |
| Saldo | Residual del importe de CxC y recibos conocidos. Parciales conservan saldo; ausencia/conflicto de evidencia no vale cero. Sobrepago sigue señalado, sin compensar otras facturas. |
| Cobertura | Año, moneda homogénea, límites de consulta, importe documental, póliza, saldos desconocidos, unlink y conflicto se exponen. Totales incompletos quedan N/D. |

Confirmación humana recibida del padre: Francisco, 25-Oct 04:18 UTC,
“Conciliación bancaria y acepto la reversa explicita”. Autoriza el diseño y
candidato aislado, no transacciones reales.

- **Nuevos cobros CxC por conciliación bancaria**. El owner manual presupuestal
  rechaza cobros nuevos, pero permite consultar una repetición histórica sin
  generar póliza. UI enlaza a conciliación. La rama CxC del formulario contable
  también rechaza el registro manual. No se eliminan registros existentes.
- **Cancelación mediante reversa explícita, ligada al original y autorizada
  por Finanzas**. `reversal_preview.py` conserva ID de póliza/líneas originales,
  invierte cuentas e importes exactos en una propuesta de sólo lectura y no
  adopta cuentas vigentes ni una fecha implícita. El endpoint conserva
  `require_admin_finanzas()` y el guard operativo preexistente; no se añaden
  roles, permisos ni excepciones por nombre.
- **Decisión específica aún pendiente**: fecha contable de la reversa
  (fecha original, si el periodo está abierto, o fecha de un periodo abierto
  autorizada por Finanzas). Ya existe control de cierre en
  `user_routes.py:2898`; no hay que inventar otro. Si se cancela ingreso/CFDI
  con cobros o impuestos, falta definir secuencia y tratamiento particular.
  `reverse_ar_collection_match` rechaza contabilizar con
  `reversal_accounting_policy_required`; no produce DDL/asientos/updates.
  Reversa de cobro, reversa de ingreso, cancelación SAT y soft-unlink siguen
  siendo hechos distintos. La propuesta no cancela fiscalmente el CFDI.

Bruto/reconocido y semanas→mes ya se pueden separar conforme al canon; no
requieren una decisión contable nueva. No se reclasifican datos ni cuentas.

## Diff y comprobaciones

Copia: `/workspace/samchatint-cxc-budget-20261025`.
Rama: `fix/cxc-partial-budget-read-20261025`; base `dd985d0c`.

Cambios por responsabilidad:

- `ar/service.py`: agregación y deduplicación por identidad de recibo; evidencia
  contradictoria se rechaza; alcance estricto incluye recibos y versión;
  fecha/año explícitos; indicadores/cobertura; no inventar una sola prepóliza
  para varios cobros. UI y XLSX usan bruto/reconocido/cobrado/saldo separados.
- `ar/collection_integrity.py` y owners: lock del CFDI existente, exclusión
  frente a cobros directos incluso desvinculados y a `ingreso_cobrado_ui`
  por ID/UUID. Se conserva evidencia y se rechaza el conflicto; no consolidación.
  Residual de nuevos matches se calcula por CFDI, no por alias del item, y se
  revalida importe contra la póliza fuente para rechazar un formulario obsoleto.
- `ar/reversal_preview.py`: preparación exacta del original, comprobación de
  identidad/cuentas/balance y bloqueo explícito de contabilización.
- `cashflow/service.py`: integración necesaria de la lectura modificada;
  clave año-mes, cada parcial en su propia fecha, ingreso desde póliza. No se
  cambia la fórmula general del forecast ni se presenta como conciliación total.
- Rutas presupuestales/contables: desvincular se etiqueta “sin reversa”; nuevas
  entradas manuales CxC se rechazan. Módulos AMEX/SinCFDI no modificados.

**171 pruebas pasan**, con un warning preexistente de SQLAlchemy. Incluyen:

- Happy path bancario, dos parciales, repetición sin duplicar y exceso de saldo
  por un alias alterno del mismo CFDI; cambio del importe fuente entre UI y POST.
- Reversos históricos en proyección dejan de sumar; una nueva reversa propuesta
  usa las líneas originales y su contabilización/reintento queda bloqueada sin
  modificar pólizas, recibos ni auditoría. No se afirma happy path de una reversa
  contabilizada con la política pendiente.
- Evidencia directa presupuestal, incluso soft-unlink, y evidencia manual contable
  por ID o UUID impiden otro cobro. Lectura conserva IDs de pólizas en conflicto.
- Semanas 1–52, año bisiesto, semana que cruza mes, cambio de año, cobros en
  diciembre/enero, año ausente y ausencia de fecha bancaria sin fallback falso.
- Separación sintética 1,160 bruto / 1,000 ingreso / 400 cobrado / 760 saldo;
  CFDI pendiente, moneda/importe/póliza desconocidos y cobertura incompleta.
- Alcance negativo de torneo/versión; dependencia existente de Finanzas rechaza
  actores sin autoridad. Se ejecutan funciones frontera extraídas por AST de
  su fuente original, sin arrancar la aplicación. Esto **no** es UAT autenticada
  ni prueba de autorización actor×empresa×proyecto×banco.
- SQL de owners ejecutado en **PGlite 0.3.14 en memoria**, con tablas y datos
  sintéticos, sin DSN ni credenciales. Veintiún casos de integración SQL pasan.
  No es una prueba de concurrencia entre varias conexiones PostgreSQL nativas.

Cobertura de líneas ejecutables añadidas/modificadas: **95.15 % (216/227)**,
intersección entre diff y Coverage.py. No cuenta como HTTP probado un argumento
multilínea que no tenga su propia línea ejecutable; algunas rutas se verifican
por contrato fuente. La reversa preparada conserva cobertura completa; el detalle de líneas
no ejecutadas del resto de módulos permanece explícito en el recibo. Detalle: `/workspace/cxc-budget-checks-20261025-v2.json`.

Reproducción inicial contra base: el test
`test_unlinked_invoice_partials_do_not_create_budget_assignment` fallaba
`350.0 != 600`; pasa con candidato. Las suites de presupuesto ajenas al cambio
presentaron un ciclo de imports idéntico en original y candidato
(`budgets.service → gastos.__init__ → expense_service →
budget_concept_account_service → budgets.service`); no se cambió ese frente.
Los tests SQL cargan el paquete propietario primero, como el runtime web.

Comando reproducible, desde la copia (Python 3.12 y dependencias de prueba):

```sh
env -u DATABASE_URL -u POSTGRESQL_URL \
  EXPENSE_REPORT_RECEIPT_BLOB_COLUMN=archivo_data \
  AR_TEST_PGLITE_MODULE=/workspace/cxc-pglite/node_modules/@electric-sql/pglite/dist/index.js \
  COVERAGE_FILE=/tmp/cxc-review-v2.coverage PYTHONPATH=src \
  /workspace/cxc-test-venv/bin/python -m coverage run --source=src -m pytest \
  tests/integration/test_ar_collection_channels_postgres.py \
  tests/unit/test_ar_*.py tests/unit/test_cashflow_planning_read_model.py -q
```

## Revisión final adicional del candidato

La revisión se hizo contra los owners, canon y diff completos en esta misma
copia, sin llamar otros agentes ni presentar esto como revisión humana/UAT.
Se escribieron regresiones antes de corregir los defectos: cuatro fallaron
por IDs inconsistentes/bandeja parcial vacía y otras dos por permitir un cobro
en periodo cerrado o sin fecha bancaria. Todas pasan tras las correcciones.

- `ar/matching.py` conserva CFDI, partida y versión financiera al construir
  el formulario. Un abono parcial conocido deja abierta la CxC para el siguiente
  movimiento bancario; la comparación usa residual y el POST mantiene el importe
  original. No se vuelve a mostrar como abierta una cuenta sin residual probado.
- `ar/collection_integrity.py:load_collection_source_context` comprueba la
  relación exacta entre vínculo, versión, partida, CFDI y póliza aprobada vigente.
  IDs ajenos, vínculo retirado, pendiente o sin póliza se rechazan. Un candidato
  desvinculado no adquiere una partida por inferencia; un alias candidato obsoleto
  no evita el vínculo activo. Esto es **integridad del contexto**, no concesión
  de autorización actor×empresa×proyecto.
- El cobro usa la póliza exacta del vínculo y conserva ese contexto en evidencia.
  Se bloquea el movimiento bancario al leerlo para revalidar su estado dentro
  de la transacción. El endpoint manual, ahora sólo de consulta/rechazo, no
  toma un lock de vínculo previo que invierta el orden de locks del cobro.
- Se consulta `accounting_close_periods` con la misma regla existente de
  `user_routes.py:_is_accounting_period_closed`: `status == closed` impide
  contabilizar. Una fecha bancaria ausente se rechaza; nunca se reemplaza por
  hoy ni se traslada a otro periodo. No se reabre/cambia ningún cierre.
- Fallo sintético después de crear recibo, mapeo y póliza: rollback elimina
  todos los efectos de esa transacción y el reintento produce un solo recibo,
  una póliza y un evento. Es prueba transaccional en memoria, no carrera nativa.
- Un recibo acreditado fuera de la proyección deja el total N/D, aun sin un cobro
  manual adicional. La fecha de aceptación ya no sustituye la fecha bancaria.

**Autorización revisada:** `dependencies.py:require_admin_finanzas`,
`admin_routes.py:_can_operate_ar_cxc`, `_require_budget_access` y canon Ingeniería
§7/§7.1. Las rutas conservan sus gates, sin concesiones nuevas. El contrato de
portafolios de `client_executive/service.py:_authorized_tournaments` es de lectura
de Dirección y expresamente no delega escrituras financieras; no se reutilizó
como autoridad de cobro. Tampoco se importaron scopes de AMEX para CxC. Falta un
contrato acreditado actor×empresa×proyecto×documento×banco para completar esa
prueba; depende de MULTIEMPRESA/owner de Finanzas. No es correcto llamar a las
regresiones de IDs una prueba de autorización multiempresa.

**PostgreSQL nativo pendiente por entorno, no por una denegación nueva:**
no hay ejecutables `postgres`, `initdb`, `pg_ctl` ni `psql` en el PATH ni en las
ubicaciones locales comprobadas. Existe cliente Docker, sin una instancia local
acreditada; no se consultó ningún daemon ni se trasladó la prueba a Ryzen,
producción o servicios externos. Requisito concreto: PostgreSQL 15 local y
vacío (versión usada por `.github/workflows/test.yml:187`), socket aislado de
prueba, esquema sintético, driver async y al menos dos sesiones independientes.
Quedan por ejecutar intercalados de mismo movimiento/dos CFDI, dos parciales
con exceso combinado, exclusión de fuentes y rollback/reintento bajo contención,
así como cierre concurrente. No se afirma que PGlite haya probado esas carreras.

## Límites para la entrega al Ryzen

1. Fecha/tratamiento de reversa pendiente según lo anterior. El bloqueo es de
   contabilización, no del diagnóstico, proyección ni cobro bancario sintético.
2. MULTIEMPRESA-001 sigue separado: faltan su contrato/owner/recibos verificables.
   El main usa allowlist global de emisores y guard operativo heredado; esto no
   acredita autorización de objeto por empresa/proyecto. El POST de cobro verifica ahora la relación exacta de IDs del vínculo y
   el importe fuente, pero aún falta acreditar la autorización del actor sobre
   ese contexto; los otros handlers presupuestales no se ampliaron aquí. No habilitar empresas con este candidato.
3. Fallback inverso mensual→semana de presupuesto legacy no modificado. Tampoco
   se resuelven cancelación fiscal SAT ni históricos inconsistentes. La cola bancaria conserva IDs y parciales; su operación autenticada y el
   forecast todavía requieren UAT del negocio.
4. Probar concurrencia y constraints con varias conexiones PostgreSQL aisladas,
   integrar contexto empresarial acreditado y revisar el diff antes de liberar.
   Conservados helpers de esquema preexistentes; no se añadió migración ni se
   ejecutó una real. El runtime y los saldos reales permanecen desconocidos.

No push, merge, deploy, activación de empresa, SATsync, mensajes externos ni
transacciones productivas. Inventario privado remoto no confirmado: operación
`agentius_directo_work_list` rechazada, sin job ID y sin repetición. Las negativas
previas de servicio/path/gateway permanecen respetadas y no se eludieron.
