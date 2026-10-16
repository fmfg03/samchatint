# AMEX: corregir los bloqueos del recorrido hasta COI

Fecha: 2026-10-15. Base evaluada: `a983be79e1ad8a6b5aa56affe27a945160ac8166`.

## Aprobación y fase

El usuario respondió «ok, aprobado» al assessment `amex-accounting-journey-qa-20261015.md`, con sus ocho hallazgos y prioridades. Se conserva esa aprobación para la historia y criterios de cierre siguientes. No se solicita de nuevo aprobación del diagnóstico ni de la intención de corregirlo. La primera fase incompleta es la especificación técnica, especialmente la recuperación de fuentes inmutables y la nueva selección/consulta de CFDI.

## Historia

Como integrante autorizado de Contabilidad, quiero cargar el estado original, resolver sus facturas y clasificar los cargos, enviarlos a Juan Pablo para asignar partida y volver a una propuesta contable completa que pueda confirmar y descargar en COI, sin informes de gastos ficticios, movimientos duplicados ni campos técnicos que impidan operar.

## Criterios de aceptación aprobados por el assessment

1. Preview y carga coinciden: fechas fuera del período se explican antes de persistir, sin producir silenciosamente filas omitidas que después impidan terminar.
2. Un estado directo pendiente que quedó afectado por ese defecto ofrece recuperación explícita, auditada e idempotente. Conserva original, UUID de estado y filas, los 140 cargos existentes y sus evidencias; crea únicamente los cinco cargos que faltaron en el caso de referencia. No convierte estados legacy ni modifica fuentes reconocidas.
3. Contabilidad encuentra CFDI SAT por datos de negocio, revisa vigencia/conflictos y confirma correspondencia sin conocer el UUID interno. La selección permite solicitar vigencia con consentimiento explícito; no inventa identidad de comercio ni elimina controles fiscales.
4. Proyecto/torneo y categorías dependientes son claros: Dirección General muestra sus categorías autorizadas, sin categoría deportiva adicional.
5. Guardar clasificación y Enviar a Control Presupuestal son acciones y confirmaciones distintas. Juan Pablo sigue asignando sólo partida, y la cuenta/contrapartida siguen sus owners existentes.
6. La pantalla muestra todos los bloqueos y la propuesta Debe/Haber antes de confirmar. Después de reconocer, CP no ofrece acciones imposibles sobre cargos congelados; su historia permanece disponible.
7. Una póliza multicargo íntegra se descarga en XLSX, CSV y ZIP desde snapshot, incluyendo movimientos e impuestos completos. Se rechazan movimientos faltantes, extra, duplicados indebidamente o con cuentas/importes distintos. El orden de representaciones equivalentes no impide la exportación.
8. La aceptación recorre carga → CFDI → clasificación → actor CP distinto → preview → confirmación → descarga y revisión del archivo. Incluye el CSV real intacto con dependencias sintéticas declaradas; ese escenario no acredita una póliza real ni sustituye UAT de Contabilidad.

## Fallos y límites

Mantener pendientes explícitos por XML, receptor, vigencia, uso global, alcance, mapeo, versión o período incompatible. Concurrencia, reintentos y cambios de contexto no permiten doble reconocimiento. No cambiar originales para evadir hashes, borrar recibos, deshabilitar triggers, generar exportaciones parciales ni regularizar los 14 históricos.

No añade dependencias productivas, descargas SAT nuevas, roles, permisos globales, conversión de moneda, clasificación contable inferida, decisiones reales de Juan Pablo ni cambios al owner de pago de tarjeta. No autoriza todavía commit/push/PR/merge, migración, recuperación productiva o despliegue; esos efectos conservarán sus checkpoints.
