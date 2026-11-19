# Expediente Vivo de Dirección

## Origen de cada dato

El expediente utiliza el torneo autorizado y la edición explícita para abrir cada
fuente. Una fecha de calendario, gasto o pago se conserva como dato del registro.
La interfaz identifica la fuente consultada y su estado. La fecha de consulta es
distinta de la fecha de actualización de cada registro.

| Ámbito | Datos | Fuente y captura |
| --- | --- | --- |
| Estatal | Entidad, equipos reales y jugadores por categoría, rama y edad | Padrón operativo para ediciones activas de Copa Telmex y operaciones SOUL para sus ediciones acreditadas. Se distingue el número de inscripciones de las personas con identidad verificable. La rama del equipo se etiqueta como tal. Las edades usan el corte configurado de la edición. Si el padrón carece del desglose por género, Operaciones puede adjuntar un conteo agregado respaldado. |
| Estatal | Responsables de Plataforma Sports y de la entidad, contactos y fechas personales | Captura respaldada de responsables con acceso especial. Una representación de equipo se identifica como tal cuando la fuente la acredita. |
| Estatal | Meta de equipos, avances, rondas, clasificación al nacional y clasificación final | La meta se captura por categoría y rama. El calendario y los resultados provienen del registro deportivo cuando la fase consta explícitamente. Se captura la evidencia de avance o clasificación que falte. |
| Estatal | Descripción, cuotas de arbitraje y transporte, entregas de uniformes y viajes | Capturas estructuradas de Operaciones por edición y entidad. El monto de una cuota reportada conserva su moneda. |
| Estatal | Ayudas y pagos al operador, uniformes, balones y equipamiento | Documentos existentes de Gastos y estado de Payment Run. Finanzas vincula el documento con la entidad y el concepto cuando falta esa asociación. |
| Estatal | Visitas de AZ, CL y otros responsables y gastos por visita | Operaciones registra la visita y la referencia de su informe. Finanzas vincula las solicitudes o informes de gastos correspondientes. |
| Nacional | Categoría, fechas, duración, ciudad, sedes y canchas | Calendario y fases acreditadas por SOUL cuando existan. La captura de evento completa los datos que falten y calcula la duración en días calendario inclusivos. |
| Nacional | Hoteles, camas-noche, raciones por tipo, servicio médico y traslados | Capturas de Operaciones. Los traslados son un agregado y pueden quedar sin dato. |
| Nacional | Viajes del personal, hoteles por anticipo y liquidación, proveedores, servicio médico y seguro | Solicitudes e informes existentes de Gastos. Finanzas mantiene el vínculo a la fase y concepto. Payment Run aporta el estado y la evidencia de pago. |
| Nacional | Proveedores de activación, visitantes, actividades, resultados y fotografías | Mercadotecnia registra las actividades y vínculos con sus patrocinadores. La galería de SOUL suministra evidencia fotográfica cuando el objeto queda asociado a la activación. |

Los importes solicitados, de informe y efectivamente pagados conservan su
significado. Los registros financieros agrupados se separan por moneda. El
expediente sigue el identificador del documento incluso cuando su fecha queda
fuera del año de edición.

Cuando SOUL carezca de una edición explícita, la asociación se acredita con
el identificador del catálogo de la edición de Copa Telmex o con una referencia
de Operaciones al torneo ya vinculado en el catálogo de SamChat.

## Acceso y captura

La consulta exige usuario activo y el alcance existente de Dirección en
cartera y torneo. Los permisos de captura de Operaciones, Finanzas,
Mercadotecnia, contactos y fotografías se administran con el control de accesos
existente. Se requieren permisos explícitos para guardar. El permiso de ver
contactos o fotografías también debe ser explícito.

El usuario revisa los datos antes de confirmar. La confirmación se vincula a la
sesión, alcance y contenido. El sistema registra el autor y la revisión de cada
cambio. Las asociaciones financieras apuntan a documentos existentes del mismo
torneo y respetan la edición y fase declaradas cuando la fuente las posee.

## Puesta en producción sujeta a autorización

1. Confirmar el commit del PR y volver a revisar el release servido y los PR de
   Dirección, reportes CTT y Finanzas. Validar con consultas de solo lectura
   las asociaciones de torneo y edición, los permisos de catálogo, la presencia
   de las tablas requeridas y la fuente de fotografías. La captura queda
   deshabilitada mientras falte su autorización explícita.
2. Guardar el identificador del release anterior y la configuración activa de
   systemd. Obtener una copia verificable y con acceso restringido de la base
   antes de migrar. Registrar fecha, SHA-256, ubicación privada, responsable y
   resultado de una prueba de restauración en una base desechable.
3. El propietario de la base aplica
   `database/migrations/20261118_direction_living_dossier.sql` en una transacción.
   Comprobar tablas, índices y permisos del rol web antes de cambiar el release.
   La migración crea tablas de captura y auditoría, sin actualizaciones al
   padrón ni a documentos financieros.
4. Desplegar el release autorizado. Comprobar el selector activo, `/healthz`,
   `/readyz`, reinicios y journal. Verificar con una identidad autorizada una
   lectura por edición y con una identidad sin acceso la denegación del
   expediente y de sus fotografías. Registrar por separado la aceptación de
   Operaciones, Finanzas y Mercadotecnia.
5. Para revertir, seleccionar el release anterior y volver a comprobar el
   servicio. Si hubo captura, conservar las tablas y la auditoría. Si siguen
   vacías, el propietario de la base puede ejecutar
   `database/migrations/20261118_direction_living_dossier.rollback.sql`.

Los riesgos de liberación son atribuir un registro a una edición incorrecta,
mostrar información personal por un permiso mal configurado y ocultar una
fuente incompleta detrás de un conteo aparente. Las comprobaciones de alcance,
atribución, permisos y conciliación del primer expediente se hacen antes de
autorizar capturas.

Canon unchanged. La proyección respeta el alcance y las denegaciones de
Dirección. La captura explícita y auditada incorpora datos faltantes y
asociaciones sin modificar los flujos propietarios del padrón o Finanzas.
