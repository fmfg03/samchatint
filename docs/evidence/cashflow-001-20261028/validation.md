# CASHFLOW-001 — Validación independiente

2026-10-28. Revisión en sólo lectura por `implementation-validator`, seguida de
correcciones acotadas y revalidación. No es aprobación humana del diff o release.

## Resultado de código

- Críticos: ninguno pendiente.
- Importantes: ninguno pendiente.
- Menores: ninguno que requiera otra modificación.

Se cerraron los hallazgos de doble conteo en el consumidor T04, universos por
cuenta/proyecto, moneda no acreditada, mapeo HTTP de CompanyScopeError, vencidos
sin reprogramación, selector/edición presupuestales, distribución parcial,
reconstrucción histórica inexistente, períodos futuros, invariancia por
agrupación, paginación y riesgo CxP omitido.

## Criterios y límites

| Criterio | Evidencia local y límite |
| --- | --- |
| AC01 | Identidad, totales completos importados y detalle compartido; fixtures y PG. |
| AC02 | Ausencia de apertura produce N/D; no se acredita saldo positivo productivo. |
| AC03 | Banco/cobranza una sola entrada; escenario y statement no duplican. |
| AC04 | Presupuesto/factura separados, parciales/reversas y proof inconsistente N/D. |
| AC05 | Año-mes, 24 meses, agrupaciones, vencidos/sin fecha y unión de ventanas. |
| AC06 | Aprobada/en proceso/pagada separadas y enviadas fuera de autorización. |
| AC07 | HTML/XLSX mismo modelo/filtros; T04 respeta monedas, años y stocks. |
| AC08 | Guardas HTTP y PG con owners multiempresa reales en overlay aislado; no activación. Atribuciones sin evidencia N/D. |
| AC09 | Más de 5000 filas, fuentes fallidas, plan vacío/parcial y moneda desconocida. |
| AC10 | Escritorio/móvil, teclado, regreso y paginación; sin UAT autenticada. |

Las comparaciones AST acotan los cambios de rutas a tres handlers y los de
template reports a su builder cashflow. Los dos cambios de respuesta del
asistente están registrados como consumidor efectivo. No hay cambios de owners,
permisos, esquema, dependencias o datos financieros fuera de alcance.

## Verificación final y reservas

Tras la revisión se completaron **172 pruebas aprobadas**, **90.67%** de cobertura
del conjunto medido y **9 pruebas de inventario aprobadas**, sobre la base
`0f47e7b8c`. Los logs y hashes quedan vinculados en el manifiesto.

La verificación general permanece **parcial**: Black e isort ya pasan y mypy
acotado al código propio pasa con dependencias externas excluidas. Sin excluirlas
persisten cinco faltantes de metadatos; los 143 diagnósticos en módulos grandes
no afectan a funciones cambiadas y se atribuyeron contra HEAD. No se declara
limpieza de tipos de todo el repositorio.

El gate privado quedó cerrado tras la autorización explícita para el SDK
temporal: **135 pruebas aprobadas con MCP 1.29.0** y su dependencia httpx-sse 0.4.3,
ambos en `/tmp/cashflow001-mcp129`. La `.venv` compartida conserva MCP 2.3.0 y
`pip check` sin conflictos. Los tres errores anteriores sólo correspondían al
SDK 2.3.0 incompatible; su log se conserva como historial. No se modificó el
plugin ni código durante esta instalación, conforme a hashes verificados. Las
deudas de lint en presupuesto y pruebas no modificadas se atribuyen en el
informe. Sin apertura, moneda, clasificación o atribución canónicas, N/D es el
resultado correcto; no acredita disponibilidad empresarial completa.

Conciliación productiva, UAT autenticada y aceptación de negocio siguen
pendientes. No se efectuaron commit, push, PR, merge o despliegue.


## Revalidación tras habilitar herramientas

Revisión independiente en sólo lectura: sin hallazgos críticos, importantes o
menores que requieran cambios. Formato y correcciones de tipos preservan los
cálculos, autoridad y límites del diff; no se añadieron supresiones de código.
La aceptación se repitió: **172 aprobadas, 90.67%**, incluidos cuatro casos PG
reales aislados y dos de navegador. El PostgreSQL temporal volvió a detenerse.
Los recibos de formato/tipos y el cierre autorizado del SDK se conservan en esta carpeta.


## Gate privado completo

El runner offline oficial terminó con **135 tests, OK**, usando el pin del
repositorio y manteniendo sus prohibiciones de sockets/credenciales/efectos.
`mcp129-receipt.json` acredita procedencia, requisitos y separación del SDK
compartido. No hay cambios nuevos de código ni motivos para repetir pruebas
financieras después de esta instalación aislada. Siguen vigentes sus 172 casos
aprobados, la cobertura medida 90.67% y las reservas de tipos/globales/UAT.
