# CASHFLOW-001 — Consistencia y lectura del flujo de efectivo

Fecha: 2026-10-28.
Historia y alcance: **APROBADOS** por el usuario con «aprobado», en respuesta
al diagnóstico de cashflow de esta conversación.
Estado de este documento: **ESPECIFICACIÓN APROBADA** por el usuario con «aprobada».
Implementación local iniciada el 2026-10-28; publicación y producción no autorizadas.

## Continuidad y resultado

Como responsable autorizado de Finanzas quiero entender cuánto dinero entró,
de quién y a qué cuenta, cuánto salió, a quién y por qué, y qué variación dejó,
con el detalle que explica cada cifra y las proyecciones identificadas aparte.

Esta especificación concreta ese alcance dentro de T04 de MULTIEMPRESA-002;
conserva sus aprobaciones y restricciones. No reabre su historia ni su spec.
Referencia: `multiempresa-002-cierre-integral-tableros-story.md`,
`multiempresa-002-cierre-integral-tableros-spec.md` y
`../roadmap/reportes-presupuesto-referencias-excel-20261026.md`.
Se conserva la estructura aprobada de CashFlow.xlsx: apertura, orígenes,
aplicaciones y cierre, períodos y acumulados con unidad explícita. Las cifras
de ejemplo del Excel no son datos productivos. El resumen y el detalle son
complementos de esa tabla, no sustitutos.

## Base y evidencia

- Runtime objetivo: FastAPI `copa_telmex_dashboard.py`, dominio cashflow,
  lectura de los owners financieros sobre Postgres. Sin escrituras financieras.
- Checkout investigado: `d548d148f7521988587d6498b5461901c21a2ee3`, con cambios
  locales previos en rutas y pruebas; se preservan y no se atribuyen a esta tarea.
- Los tres canons se leyeron en orden y sus hashes coinciden con el registro.
- `systemctl show` señaló WorkingDirectory
  `/srv/samchat/releases/gastos-prod-ab29ccc64-ctt-pilot-photos`. Esto identifica
  el directorio configurado, no acredita el comportamiento de cashflow en él.
- Existe trabajo pendiente en `.worktrees/multiempresa-002-20261027`, incluidos
  cashflow, CompanyContext y report_layouts. No copiar su diff completo ni editar
  ese trabajo en curso sin reconciliar su manifiesto y sus cambios.
- Antes del build: aislar candidato, comprobar base vigente y reconciliar por
  archivo con el candidato multiempresa. Mantener su autoridad por empresa,
  moneda y torneo; no reintroducir lectores globales del checkout antiguo.

## Superficies y responsabilidad

- `/admin/contabilidad/cash-flow`: reporte operativo y detalle.
- `/admin/contabilidad/cash-flow/export.xlsx`: mismo resultado exportado.
- `/admin/finanzas/cashflow`: lectura ejecutiva y planeación.
- Proyección ejecutiva existente de T04: reutilizar su contrato de reporte y
  comprobar compatibilidad; no reconstruir Dirección ni ampliar sus facultades.
- Conservar rutas y guardas. Ninguna URL de detalle permite evadir el alcance.

El propietario del cálculo será `src/samchat/cashflow/service.py`: extender el
read model existente y extraer de los handlers el ensamblado duplicado que sea
necesario para que pantalla y Excel consuman la misma definición. Reutilizar
los owners de bancos, presupuestos, CxC y Payment Run. Los handlers resuelven
identidad/contexto, validan filtros y renderizan/exportan; no deciden reglas
financieras alternativas.

## Contrato de lectura y cálculos

1. Contexto común: empresa autorizada, moneda acreditada, cuenta, proyecto,
   versión cuando corresponda, fecha inicial/final, corte y agrupación. Los
   límites temporales son inicio inclusivo y final exclusivo; la clave mensual
   es año-mes, también en horizontes de 24 meses. Agrupar no amplía el universo.
   El horizonte futuro parte del corte y puede cruzar meses; no se recorta
   silenciosamente al mes del histórico. Ambos intervalos aparecen visibles.
2. Efectivo real: movimientos bancarios importados autorizados. Un ID bancario
   se cuenta una vez; CFDI, póliza, solicitud y match son evidencia asociada,
   no nuevas entradas o salidas. Usar importes exactos/Decimal en la proyección;
   no añadir una migración para corregir tipos históricos en esta entrega.
3. Resumen real: entradas, salidas y variación = entradas menos salidas.
   Saldo inicial y final requieren evidencia de cuenta, moneda y corte y
   cobertura compatible. `BankMovement.saldo` aislado no acredita cobertura.
   Sin apertura acreditada: N/D con motivo, sin llamar saldo al neto del período.
   Con cobertura acreditada: comprobar apertura + entradas - salidas = cierre;
   diferencias se muestran y no se corrigen alterando importes.
4. Origen/destino: ordenante para entrada y beneficiario para salida; concepto
   bancario y relaciones canónicas aceptadas para contexto y evidencia.
   Ausencias se muestran como sin identificar/sin clasificar. No inferir proyecto,
   categoría, empresa o contraparte por semejanza de texto o importe.
5. Proyecto: separar el banco global del universo atribuible a un proyecto.
   Si falta vínculo canónico, mostrar cobertura y monto sin atribución; no
   presentar un neto mezclado como caja exclusiva del proyecto. La autorización
   se aplica antes de cualquier desglose, incluso a registros sin atribución.
6. Traspasos: distinguir sólo los acreditados entre cuentas propias de la misma
   empresa. Conservar ambas patas en el detalle por cuenta; excluirlos del
   subtotal operativo consolidado sólo con pertenencia y vínculo acreditados.
   Si falta evidencia, permanecen visibles sin clasificación confirmada. No
   netear operaciones interempresa ni crear un detector o flujo de aprobación.
7. Cobranza y proyección: un cobro incluido en banco no se vuelve a sumar como
   cobranza confirmada. La cobranza explica el real y reduce el saldo de CxC.
   Incluir cobros parciales, reversas y estado vigente de los matches. No sumar
   evidencia de póliza y Tesorería del mismo cobro como dos cobros distintos.
8. Facturación y presupuesto: mostrar ambas medidas por separado. Sólo calcular
   remanente presupuestado no facturado con identidad canónica de línea, período
   y moneda. Sin correspondencia, no sumar ambos como un único ingreso esperado;
   mostrar escenarios separados y su limitación. No distribuir importes entre
   meses por suposición. CxC con saldo/fecha desconocidos permanece identificada
   sin entrar como importe/fecha comprobados en el escenario.
9. Obligaciones: usar estado vigente e importe canónico; separar aprobadas,
   en_proceso_pago y enviadas. Las enviadas no son pagos autorizados. Las pagadas
   no vuelven a restarse como futuras; CxP detectada sin captura es riesgo aparte.
   Incluir vencidos y sin fecha en grupos propios, sin ocultarlos por un filtro
   de fecha ni inventarles vencimiento. El escenario aprobado descuenta sólo
   obligaciones autorizadas únicas y suma cobros pendientes con fecha sustentada
   o estimada explícitamente según la regla de crédito vigente. Sin apertura
   acreditada se presenta variación proyectada, no disponibilidad proyectada.
10. Integridad: totales sobre el universo completo autorizado, sin LIMIT que
    trunque el cálculo. Paginar sólo el detalle. Ausencia de fuente, moneda no
    acreditada o error no se transforma en cero. Mostrar estado de cobertura,
    motivo y corte; impedir un total aparentemente completo si falta una fuente.

## Presentación y exportación

Entrada principal: tabla apertura/orígenes/aplicaciones/cierre por períodos y
acumulado, con empresa, moneda, unidad y corte. Resumen breve de entradas,
salidas y variación, principales contrapartes y grupos canónicos disponibles.
Categorías de la plantilla sin mapping validado quedan N/D con motivo.

Seleccionar una cifra abre su detalle contextual: fecha, entrada/salida,
contraparte, concepto, cuenta identificada, proyecto si está acreditado,
referencia, fuente y conciliación. Incluir total de registros y paginación.
Volver conserva filtros y selección. Respetar permisos de documentos vinculados
y no incluir datos bancarios sensibles innecesarios en URLs o exports generales.

Sección separada «Lo que viene»: cobros pendientes, pagos aprobados/en proceso,
solicitudes enviadas y riesgos. Escenarios explican componentes y supuestos.
Pantalla y Excel comparten resultado, clasificación, filtros y corte. Mantener
compatibilidad con el render/export ejecutivo existente, incluidos PDF si consume
el contrato T04; no crear un exportador PDF paralelo.

## Archivos previstos y límites

- `src/samchat/cashflow/{service.py,admin_ui.py,__init__.py}`.
- Sólo handlers cashflow y export asociados en
  `src/devnous/gastos/routes/{user_routes.py,admin_routes.py}`.
- Adaptación T04 estrictamente necesaria en
  `src/samchat/client_executive/report_layouts.py` y su consumidor efectivo,
  reconciliados desde la base multiempresa; registrar el consumidor antes de editar.
- Pruebas cashflow existentes y pruebas de integración/UX de esos recorridos.
- Inventario/matriz de rutas de private-plugin si cambia su fingerprint.

Sin nuevas dependencias de producción, tablas, migraciones, jobs, importaciones,
clasificaciones automáticas, backfills, cambios de permisos o modificaciones a
los owners de contabilización/pago/cobranza. Si un owner no expone evidencia
necesaria, reportar N/D; una ampliación de ese owner se especifica aparte.

## Aceptación y verificación

- AC01: entradas/salidas y detalle coinciden con fuentes únicas del mismo ámbito.
- AC02: apertura/cierre acreditados concilian; falta de apertura produce N/D.
- AC03: $100 en banco y en cobranza produce $100 real, nunca $200 proyectado.
- AC04: presupuesto y factura vinculados no se suman dos veces; sin vínculo se
  presentan separados. Cobro parcial/reversa modifica sólo el saldo que corresponde.
- AC05: año-mes correcto, cambio de año y 24 meses; vencidos/sin fecha visibles.
- AC06: aprobada → en proceso → pagada no duplica efectivo ni obligación.
- AC07: mismos filtros/corte producen mismas cifras en pantalla, detalle y Excel;
  los consumidores ejecutivos conservan el contrato T04.
- AC08: empresa/moneda/proyecto no mezclan universos; petición manipulada y acceso
  revocado fallan conforme a guardas actuales. Traspasos sólo con evidencia.
- AC09: fuente fallida, NULL, moneda desconocida y más de 500/5000 movimientos
  no generan cero ficticio ni totales truncados.
- AC10: escritorio y móvil permiten abrir reporte → leer → consultar cifra →
  volver conservando contexto; las cifras faltantes explican el motivo.

Verificación: pruebas unitarias de cálculo y render, integración sobre PostgreSQL
aislado para filtros/totales/matches, equivalencia de celdas XLSX y resultado,
pruebas de guardas y recorrido visual. Sin credenciales o navegador habilitado,
marcar la verificación correspondiente pendiente; mocks no prueban producción.
Ejecutar los cuatro archivos `tests/unit/test_cashflow_*.py` existentes, nuevas
pruebas de aceptación y checks acotados de estilo/tipos. Para cambios a user_routes,
regenerar y verificar inventario/matriz con la suite private-plugin (`PYTHONPATH=src`)
según los comandos versionados de la base integrada. Atribuir cualquier fallo.

Después: validación de implementación en sólo lectura contra historia/spec,
pruebas y manifiesto. Informe de archivos, comandos, resultados, fallos,
desviaciones y riesgos antes de revisión humana del diff.

## Riesgos, reversión y autorizaciones

Riesgos principales: mezcla de bases/candidatos, atribución bancaria incompleta,
precisión histórica, monedas no acreditadas y categorías pendientes de Finanzas.
No ocultarlos rellenando la plantilla. Probar compatibilidad de consumidores.
Rollback de código al candidato previo; sin reversión de datos porque este
alcance no realiza escrituras financieras. No editar el release en servicio.

Canon unchanged: aplica la separación de banco real, obligaciones, presupuesto,
cobranza y forecast ya exigida; no cambia autoridad ni reglas de escritura.
Si la integración revela un cambio material del canon, requiere revisión humana
explícita del apartado afectado y actualización de hash en el mismo PR.

La aprobación de esta spec habilita construcción y verificación locales del
alcance descrito. No autoriza commit, push, PR, merge, deploy ni cargas productivas.
UAT autenticada y aceptación de Finanzas siguen siendo evidencia separada.

## Revisión de compatibilidad AC07/AC10 y consumidor efectivo T04

2026-10-28: las observaciones de PR513 se corrigen dentro del alcance ya aprobado. El formulario conserva `edition_year` independientemente de `year`. El selector de proyectos usa identidades explícitas de fuentes autorizadas (incluidas partidas sin plan y CxC sin obligaciones), antes de filtros de visualización; no incorpora un catálogo global ni amplía permisos.

Consumidor efectivo adicional registrado antes de editar: `src/samchat/assistant/finance_read_adapter.py`, `run_finance_read_adapter`, exclusivamente las ramas `cashflow.summary` y `cashflow.statement`. Es la frontera hacia `provider_execution` y `AssistantRun.tool_trace` JSONB. La adaptación T04 estrictamente necesaria conserva Decimal en la proyección y entrega números JSON mediante `fastapi.encoders.jsonable_encoder`, dependencia ya existente. `_run_source`, CxC y presupuesto conservan su comportamiento. No es ampliación de un owner de contabilización/pago/cobranza ni de una facultad de escritura.

Pruebas: reenvío real del formulario 2025/2026, opciones sin solicitudes y con límites de empresa/torneo, serialización JSON estricta y bind_processor real de JSONB para resultados con movimientos y reportes anidados por moneda. El release debe conservar los handlers AR vigentes; aplicar sólo los hunks JSON cashflow al adapter, sin copiar otras ramas desde main. Canon unchanged: preserva el contrato de lectura y presentación existente.

La frontera JSON conserva la codificación numérica finita habitual de FastAPI; valores Decimal/float no finitos se convierten recursivamente a `None` (N/D), incluyendo planes brutos y reportes anidados. No se modifica el DTO fuente ni se rellena con cero. La regresión verifica JSON estricto y el formato serializado por la columna JSONB, además de valores finitos, cero y faltantes.
