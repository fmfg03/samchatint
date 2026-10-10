# Documentación posterior de solicitudes de transferencia

Fecha: 2026-10-09. Implementación y revisión independiente completadas.
Publicación y fusión autorizadas por Francisco, condicionadas a CI aprobado.
No se autoriza despliegue manual ni cambios de información o permisos productivos.

## Base y propietarios

- Checkout independiente: `samchat-support`, rama `fix/post-transfer-support`.
- Base remota consultada por HTTPS: `7d857ad326a48e0c2a8315caf81f792f3279ce51`.
  Incluye #472, anticipos a proveedores y comprobación vinculada. Ese flujo
  se reutiliza sin copiarlo ni convertir solicitudes históricas en anticipos.
- Runtime: `copa_telmex_dashboard.py`; dominio: gastos/documentos.
- Lecturas y escrituras: rutas existentes y `documento_service`, con
  `cfdi_ingestion_service` como propietario de identidad/reserva fiscal.
  Persistencia SQLAlchemy/PostgreSQL; sin camino Supabase, DDL o migración nuevos.
- `AGENTS.md` y `.agents` inspeccionados. `.agents` es un archivo versionado;
  no existe un directorio `.agents/skills` en esta base.
- Los hashes SHA-256 de los tres canons, sobre blobs Git, coinciden con
  `docs/roadmap/samchat-convergence-register.md`.

## Causa

El permiso `_can_add_solicitud_adjuntos` omitía `control_presupuestal`,
`enviado` para el solicitante y `en_proceso_pago`. El formulario existente de
archivos desaparecía en esas etapas. El permiso de borrar soporte reutilizaba
el permiso de añadirlo, por lo que ampliar solamente la lista habría ampliado
también el borrado. El guardado de materialidad podía reingerir facturas
anteriores aun sin una carga fiscal nueva.

## Comportamiento

Se conserva el formulario y endpoint actuales. El solicitante del documento y
los perfiles financieros ya permitidos pueden añadir documentación; un usuario
ajeno o un coordinador ajeno no obtiene autoridad por poder ver el documento.

| Estado | Añadir documentación posterior |
| --- | --- |
| borrador | Permitido según identidad existente |
| control_presupuestal, enviado, aprobado, en_proceso_pago | Permitido |
| pagado, cerrado, reembolsado, aplicado, liquidado | Permitido: contrato previo de materialidad |
| rechazado, cancelado o cancelación registrada | Bloqueado |
| desconocido u otro tipo de documento | Bloqueado por el guard de ruta |

La anexión no concede edición de importe, beneficiario, cuenta bancaria, estado,
autorizaciones o clasificación. El borrado queda limitado al solicitante en la
ventana de edición previa; la evidencia fiscal posterior nunca se borra mediante
este control. La sustitución de comprobantes bancarios sigue su ruta y autoridad
específicas. Subir materialidad o una factura nunca ejecuta el registro de pago.

Antes de clasificación/autorización se conserva la ingesta fiscal existente.
Después, XML/PDF se almacenan como `cfdi_xml_evidence`/`cfdi_pdf_evidence`, con
etiqueta **revisión contable pendiente**, sin crear un CFDIReport, modificar el
vínculo fiscal, generar un gasto ni crear o sustituir una póliza. El formulario,
la lista y la respuesta explican esa diferencia. Materialidad sigue siendo
`supporting`, incluso si contiene archivos XML o PDF; no acredita por sí sola
deducibilidad ni pago.

Se valida UUID, tipo de factura, RFC emisor del proveedor, receptor configurado,
moneda e importe. Un XML es la fuente de identidad fiscal; un PDF de texto exige
su XML previo o adjunto en el mismo lote y coincidencia de los campos que puede establecer. Se reutilizan
los campos canónicos para rechazar evidencia XML contradictoria. Un archivo
ilegible, un PDF ambiguo o una factura de otro UUID requieren revisión; no se
sustituye la evidencia previa. Se mantienen los tipos permitidos y 15 MiB por
archivo. La carga fiscal es de una factura por lote; materialidad admite varios.

El guardado es transaccional y conserva los archivos anteriores. Una repetición
exacta de categoría/contenido no crea otro adjunto. La ruta bloquea el documento
y las cargas fiscales serializan el UUID en PostgreSQL. La reserva posterior
queda auditada en `Aprobacion` con acción `adjuntar_factura` y UUID canónico;
`adjuntar_soporte` registra actor y anexión sin simular una aprobación. La ingesta
canónica consulta también esa reserva y los saldos compartidos incluyen ambas
fuentes sin contar dos veces la misma solicitud. Los archivos de una factura
compartida conservan la confirmación preexistente y no habilitan una nueva
confirmación implícita.

En anticipos originales y comprobaciones vinculadas se permite soporte, pero la
factura sigue entrando exclusivamente por **Comprobar anticipo**. No se amplía
el pago ni la contabilidad del flujo incorporado en #472.

## Decisión contable explícita

Un pago sin CFDI puede haber generado ya un gasto no deducible. Adjuntar la
factura posteriormente no permite cambiarlo a deducible, asignar impuestos,
reescribir su póliza o reexportar silenciosamente otra versión. Contabilidad
debe determinar la regularización o reversión aplicable. Esta corrección no
implementa ni presupone esa decisión. Si el importe/RFC de una factura real no
coincide, no se cambia la solicitud para hacerlo coincidir. Un folio concreto
permitiría identificar el gasto/póliza afectado sin bloquear esta investigación.

## Verificación y límites

El recibo de pruebas y revisión final se entrega junto al diff local. La primera
ronda ejecutable pasó 178 pruebas; una revisión independiente pasó 91 pruebas
del flujo nuevo sin hallazgos pendientes. La regresión final de trece suites pasó
307 pruebas; cobertura del diff 159/180 instrucciones (88,33 %). Incluye permisos legítimos y
ajenos, estados, repetición, rollback, tipos/tamaños, XML/PDF, identidad previa,
reservas duplicadas/compartidas y preservación del flujo de anticipos.

Las regresiones locales de persistencia usan SQLite aislado. La prueba
`tests/integration/test_late_solicitud_evidence_postgres.py` utiliza el PostgreSQL
aislado de CI, en un esquema efímero: verifica repetición concurrente, reserva
entre documentos y competencia con ingesta fiscal canónica. No acepta un DSN
productivo; su resultado debe confirmarse en CI antes de cerrar esa evidencia.
Estas pruebas no acreditan despliegue, SAT en vivo o UAT autenticado. PostgreSQL puede abortar
transacciones concurrentes por deadlock; la ruta hace rollback y permite reintento.
El orden fiscal UUID antes de saldo compartido es común a los caminos modificados.
La idempotencia concurrente de materialidad se garantiza por el lock del
endpoint; un consumidor interno directo debe conservar su propia transacción.

Rollback de código: retirar este diff antes de publicación. Si alguna futura
publicación genera categorías/reservas nuevas, conservar esos adjuntos y su
auditoría; no degradarlos a CFDI canónico ni borrarlos para facilitar un rollback.

**Canon unchanged**: se corrige acceso a evidencia y se preservan autoridad,
identidad, idempotencia y separación entre documentos/pagos/contabilidad. No se
modifica ninguna regla de reconocimiento contable ni se declara cierre de UAT.

## Hallazgos de revisión y coordinación de release

El guard de auditoría y la migración owner-run
`database/migrations/20261010_late_support_audit_actions.sql` amplían únicamente
el CHECK existente con `adjuntar_soporte` y `adjuntar_factura`. La migración debe
aplicarla el dueño antes de liberar el código; este trabajo no ejecuta DDL en
producción. Las pruebas PostgreSQL reproducen el CHECK legado y aplican el guard
idempotentemente, en lugar de asumir que la metadata ORM representa el esquema.
Un rollback posterior debe conservar estas acciones y registros ya escritos.

Una reserva compartida también bloquea un nuevo consumidor no confirmado. Los
linkers masivos de SAT consultan la reserva dentro del lock del UUID, mantienen
la evidencia del dueño y sus gastos relacionados sin promoción automática y
permiten otros consumidores sólo con confirmación preexistente y saldo suficiente.
Un savepoint evita vínculos parciales si falla un candidato y el caller captura
el error. Esto no regulariza contabilidad ni cambia confirmaciones o importes.

La ingesta SAT directa y los linkers individuales aplican la misma protección
antes de crear un reporte o asignar un vínculo. `allow_shared=True` no reemplaza
la confirmación persistida: se consulta bajo lock y sin autoflush, para no aceptar
un flag pendiente como aprobación. Un vínculo canónico previamente persistido
al mismo reporte conserva su enriquecimiento habitual sin una nueva aprobación;
la importación fiscal sin consumidor sigue disponible y no promueve evidencia.
Crear o editar una solicitud con UUID manual consulta también la reserva antes
de asignar un reporte fiscal, aun sin archivos nuevos. La edición bloquea y
actualiza el estado documental antes de validar y conserva el orden de locks
documento antes de UUID. La confirmación explícita del formulario conserva su
contrato de saldo; los dueños históricos no se promueven mediante una edición.

En PDF de texto se distingue dato extraído de default/inferencia. El XML del
mismo UUID aporta los campos obligatorios ausentes; todo dato explícito del PDF
se compara. Repetir XML/PDF sin moneda o total impreso no sustituye archivos ni
crea falsa contradicción. Las regresiones cubren ausencias, contradicciones,
reserva compartida/no compartida, CHECK, saldo, repetición, fallos y locks reales.

**Canon unchanged**: estos ajustes cierran omisiones de implementación dentro
de las mismas reglas de identidad, auditoría, reserva y reconocimiento contable.
