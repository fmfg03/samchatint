# Corrección auditada del criterio de signos AMEX

Fecha: 2026-10-22. Fase: historia aprobada explícitamente en conversación. Implementación autorizada tras aprobación posterior de la especificación; sin cambios productivos en esta fase.

## Historia

Como Finanzas, quiero corregir desde el backend el criterio de signos declarado por error al cargar el estado `43119aac-2cbe-4875-ad88-9133c187221c`, conservando el archivo y los hechos originales, para continuar su conciliación con cargos y abonos correctos.

El usuario ya autorizó corregir este estado. Se solicita aprobar únicamente la ampliación necesaria: crear una operación auditada porque el backend desplegado no admite esta corrección y protege la fuente física como inmutable. No se desactivarán protecciones para editarla.

## Evidencia reproducida, sólo lectura

- Release observado: `/srv/samchat/releases/gastos-prod-14c17c538-amex-no-cfdi`.
- CSV original obtenido de su almacenamiento canónico; SHA-256 validado contra cabecera.
- Parser desplegado reproduce 16 filas con `debit_negative`.
- Reinterpretación pura con `debit_positive`: mismas filas, celdas, importes y signos originales.
- Antes: 2 cargos por MXN 17,357.40; 14 abonos por MXN 8,747.10.
- Después esperado: 14 cargos por MXN 8,747.10; 2 abonos por MXN 17,357.40.
- Fila 6 Google MXN +939.00: abono a cargo; fila 10 Amazon MXN -46.53: cargo a abono.
- Ningún CFDI vinculado, ninguna clasificación, todos pendientes contables; cero cortes en la consulta previa. Revalidar todo al ejecutar.
- Dos registros de gasto asociados a los cargos interpretados originalmente. Deben dejar de ser gastos activos de este estado mediante una transición auditada y coherente; no borrar ni alterar historia silenciosamente.

## Criterios de aceptación

1. Alcance aplicado exclusivamente a este estado y estas 16 filas; preservar el CSV y la carga original con su trazabilidad.
2. Nueva interpretación registrada con motivo, actor autorizado, fecha, antes/después y prueba del archivo original; elegir en especificación una representación compatible con los propietarios existentes.
3. Google queda como cargo elegible para selección y Amazon como abono, sin convertir el importe original ni fabricar facturas o decisiones fiscales.
4. Sólo los 14 cargos corregidos tienen los registros de gasto operativos correspondientes; los dos anteriores no conservan una apariencia de gasto activo que duplique o contradiga la interpretación corregida. Preservar rastro histórico.
5. Conciliación, previsualización y posterior contabilización consumen una misma interpretación coherente, sin duplicar movimientos, gastos ni pólizas.
6. Previsualización determinista, confirmación explícita, autoridad financiera, comprobación de versiones, ejecución idempotente y transacción atómica.
7. Bloquear si aparece contabilización, pago, reconocimiento, CFDI vinculado, clasificación, asignación presupuestal u otra dependencia incompatible desde esta inspección. No liberar ni migrar decisiones ajenas automáticamente.
8. Respaldo validado y preimagen antes de producción; verificación posterior de Google/Amazon, las 16 filas y sus dependencias. Rollback definido sin borrar evidencia ni degradar datos.
9. No desactivar triggers, falsificar hashes originales ni hacer una carga paralela que deje ambos estados operativos.
10. Probar corrección, repetición, concurrencia y rechazos por dependencias en base desechable; validar de forma independiente antes de proponer aplicación.

## Fuera de alcance

- Rediseño de prestaciones al socio/deudores/proyectos; PASE; criterios fiscales; decisiones de CFDI; cambio del parser para adivinar signos por comercio.
- Cambios UI generales, otros estados y backfill automático.

## Riesgos y decisiones para especificación

La protección de fuente física y el control del perfil existen tanto en aplicación como en PostgreSQL. La recuperación de período existente sólo admite omisiones por fecha y no debe reutilizarse para falsear una corrección de signos. Definir la revisión de interpretación, los registros de gasto que cambian de función, su visibilidad en tableros adyacentes, los hashes/versiones efectivos y la preservación de cortes; cualquier DDL exige migración owner-run y rollback revisados.

## Canon

No se editaron canons. La especificación deberá evaluar la afectación de datos/arquitectura y presentar el texto exacto de cualquier enmienda para revisión humana; si no cambia, justificar `Canon unchanged`.

## Comprobaciones de esta fase

Se leyeron canons en orden y sus hashes coinciden con el registro del checkout. Se inspeccionó el propietario desplegado y se ejecutó el parser sobre el CSV original en lectura, reproduciendo el fallo y comprobando la previsualización corregida. No se aplicaron mutaciones, no hay código implementado ni validación de implementación.
