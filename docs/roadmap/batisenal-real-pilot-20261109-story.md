# Batiseñal — primer piloto con Chatty real

Fecha: 2026-11-09. Estado: **aprobada por el usuario** (respuesta «Aprobar historia de la primera entrega»).

Esta historia define la primera entrega verificable de la conexión real. El objetivo general de Batiseñal incluye que soporte revise y publique respuestas en el caso; este piloto termina en revisión privada. El caso sintético atestado conserva su bloqueo de publicación, incluso humana. El PR #516 aportó pruebas aisladas y no activó esta capacidad.

## Historia de usuario

Como usuario piloto autorizado de SamChat y propietario de un caso y de su informe de gastos sintéticos, quiero pedir explícitamente a Chatty, desde la conversación existente, que consulte el contexto permitido y guarde una propuesta privada en ese mismo caso, para revisar su contenido, fuentes, faltantes y vigencia sin modificar el informe ni ejecutar decisiones de negocio.

La entrega reutiliza el candidato de Batiseñal, el soporte, la sesión, el consentimiento y los borradores existentes. Su éxito exige un recorrido con Chatty real, PostgreSQL real y navegador real sobre la misma revisión identificada. Una simulación conserva su identificación como tal y no satisface ese recorrido.

## Criterios de aceptación

| ID | Resultado verificable |
| --- | --- |
| A1 · Recorrido real explícito | Con una identidad de usuario piloto realmente autenticada y un caso e informe sintéticos previamente autorizados, una invocación consciente desde la conversación existente permite leer contexto, guardar una propuesta y verla privadamente en el mismo caso. La evidencia correlaciona cliente, servidor, persistencia y panel. Otra revisión requiere otra invocación explícita. |
| A2 · Identidad y procedencia | El usuario, cliente, caso, empresa y proyecto se resuelven por la autorización vigente del servidor y una sesión individual real de SamChat con consentimiento expreso; un login de fixture no acredita esa conexión real. El acceso administrativo al host o un nombre indicado en el mensaje no los sustituye. Una llamada real de Chatty conserva su procedencia técnica verificable; un resultado de `synthetic_stub` nunca se presenta como respuesta real de Chatty. Los intentos de imponer identidad o ámbito desde los argumentos se rechazan. |
| A3 · Aislamiento | Sólo el usuario piloto autorizado y propietario puede consultar el contexto, el resultado de la operación y el borrador privado de este caso. Se deniegan otras identidades, empresas, proyectos, casos, fuentes y borradores, también por acceso directo. Los permisos generales de administrador o soporte no abren una vía alternativa. |
| A4 · Consentimiento y vigencia | El usuario ve y confirma el alcance antes de habilitar la consulta. La ventana inicial del piloto dura como máximo 60 minutos; refrescar tokens, cambiar de perfil, consultar de nuevo o repetir consentimiento no amplía la ventana ya fijada. Caducidad, revocación o pérdida de permisos impiden nuevas autorizaciones de lectura, envío, recuperación y render. |
| A5 · Revocación concurrente | La evidencia prueba ambos órdenes de envío y revocación. Si la revocación comprometida precede a la autorización final, se deniega la operación o la divulgación. Un resultado ya comprometido permanece histórico; revocar no borra lo guardado ni recupera bytes ya entregados. |
| A6 · Contexto y fuentes | La propuesta queda vinculada al contexto y a las versiones de las fuentes que realmente se revelaron. Un cambio relevante anterior al envío produce `STALE_CONTEXT`; uno posterior hace visible que el resultado dejó de estar vigente. Una referencia de otro usuario o concesión no permite reutilizar un contexto ajeno. El contexto insuficiente y los datos omitidos se muestran como faltantes. |
| A7 · Idempotencia | Repetir el mismo envío, incluso concurrentemente, conserva un único borrador y el mismo recibo. Reutilizar la clave con otro contenido produce conflicto. Una propuesta nueva exige una decisión consciente y su propia identidad de envío. |
| A8 · Confirmación y recuperación | Sólo se muestra «borrador guardado» tras persistir de forma conjunta el borrador, la operación, el recibo y la auditoría correspondientes. Tras un timeout puede recuperarse el resultado con su referencia o clave original, sin duplicarlo. Un rollback no produce un recibo de éxito. |
| A9 · Fuentes y contenido hostil | Los hechos citan fuentes y versiones pertenecientes al contexto autorizado. Fuentes ajenas o versiones falsas se rechazan. Las instrucciones incluidas en el contenido del caso no cambian permisos, herramientas, destinatarios o límites. Hipótesis y preguntas se distinguen de hechos respaldados. |
| A10 · Privacidad y divulgación | El borrador permanece privado y no ejecutable; se rechazan publicación, comentarios de respuesta y cambios del informe desde este flujo, incluso por un operador humano. Acceder a objetos inexistentes o no autorizados no revela su existencia. No se divulgan credenciales, cookies, adjuntos, datos fiscales ni información ajena al caso; texto o enlaces no cargan recursos remotos. |
| A11 · Experiencia y accesibilidad | En navegador real de escritorio y móvil se verifican teclado, foco, etiquetas, espera, minimizado, error, resultado incierto, contexto cambiado y acceso terminado. Minimizar conserva texto y referencia; el panel distingue consulta, resultado por confirmar y borrador privado guardado, sin mostrar aprobación, pago o resolución inexistentes. |
| A12 · Apagado y cierre | Deshabilitar el piloto impide nuevas operaciones y revelaciones. La reversión se prueba sin perder integridad ni modificar el informe. Se comparan los registros de negocio antes y después y se conserva evidencia de cero efectos financieros o publicación. Ningún stub, prueba histórica, DOM aislado o simulación se declara aceptación del recorrido real. |

La aceptación incluye una revisión independiente de la misma entrega y la identificación de sus revisiones de código, esquema y configuración. Cada criterio se declara aprobado, fallido o bloqueado con evidencia; los bloqueos no cuentan como pases.

## Caminos de fallo

- Si falta una conexión soportada, el vínculo individual, el consentimiento o una fuente verificable, la consulta queda pendiente de conexión o bloqueada con causa precisa. No se sustituye por acceso administrativo o simulación.
- Si caduca o se revoca el acceso, el panel deja de autorizar contenido y oculta su copia al conocer el cambio. Reabrir no prolonga la autorización anterior.
- Si cambia el contexto o hay conflicto de idempotencia, se conserva la referencia y se pide una nueva consulta consciente; no se actualiza ni reenvía contenido diferente silenciosamente.
- Ante timeout o respuesta perdida, el resultado queda por confirmar hasta recuperarlo. Cancelar la espera no se presenta como cancelación de una transacción ya enviada.
- Si el contexto es insuficiente, Chatty limita la propuesta, expone faltantes o formula preguntas. No inventa información ni amplía lecturas.
- Fallos de política, persistencia o verificación de identidad bloquean la operación y producen errores saneados y trazables.

## Casos límite

- Revocación, vencimiento o cambio de permisos mientras se espera una operación o se reabre el panel.
- Dos envíos simultáneos; mismo envío con respuesta perdida; misma clave con contenido distinto.
- Cambios del informe, partidas, fuentes o reglas antes y después de guardar la propuesta.
- Acceso directo al borrador por otra identidad, por soporte heredado o tras terminar la ventana.
- Contexto truncado o incompleto, fuentes no disponibles y texto que intenta dar instrucciones al agente.
- Reintento o reinicio que conserva un resultado histórico sin atribuirle vigencia nueva.

## Fuera de alcance de esta entrega

- Activación productiva, incorporación de empleados en general o uso de informes y datos financieros reales.
- Publicación de la respuesta, comentarios al solicitante, cierre del caso o retirada de la atestación para habilitarlos. La publicación humana del flujo general se aborda en una etapa posterior explícita.
- Events, disparadores automáticos, notificaciones externas, búsquedas masivas o recorridos sin nueva invocación consciente.
- Aprobar, pagar, cambiar contabilidad, modificar informes, conceder permisos, ejecutar SQL o shell desde Chatty, o ejecutar las recomendaciones del borrador.
- Otro asistente, sistema de autenticación o base de datos de producto en paralelo; migración a InsForge o RustDesk.
- Adjuntos, documentos fiscales completos o nuevas fuentes no autorizadas. Las fuentes acotadas del candidato no se presentan como una revisión integral del expediente.
- Cambios de canon sin texto propuesto y revisión humana expresa; nuevas dependencias productivas o cambios de infraestructura no incluidos en una especificación aprobada.

## Preguntas abiertas y dependencias

Antes de implementar, la especificación debe dejar verificables los siguientes puntos, sin inventar personas, URLs, credenciales ni permisos:

1. **Conexión soportada y vínculo individual:** mecanismo efectivo para exponer a la conversación las tres operaciones reconciliadas con el candidato —lectura de contexto, envío de borrador y recuperación— y vincularlas al cliente y a la sesión autorizados. El catálogo actual de Agentius no las acredita.
2. **Piloto concreto:** identidad canónica, titularidad, empresa/proyecto, caso e informe sintéticos y facultades exactas del usuario. El nombre de una persona no concede esas facultades.
3. **Destino aislado:** revisión exacta del candidato y ajustes necesarios, instalación de prueba, persistencia y permisos mínimos; responsable y valores comprobados de cliente, issuer, recurso y retorno de consentimiento. El candidato actual requiere adaptación explícita para procedencia real de Chatty y para su montaje; encender una bandera no basta.
4. **Dependencias y aceptación:** versiones y dependencias que deban incorporarse, navegador permitido, configuración de pruebas y evidencia del recorrido real. La implementación no inicia hasta aprobar la especificación resultante.
5. **Canon y datos:** evaluar el cambio contractual de una integración real y presentar cualquier texto canónico propuesto para revisión humana. La política de conservación, borrado y backups para datos reales queda pendiente antes de una adopción productiva; esta historia no inventa un plazo ni autoriza datos reales.

Estas dependencias se resuelven con investigación y una propuesta técnica concreta antes de solicitar decisiones adicionales. Si una capacidad soportada no existe, se identifica su bloqueo y alcance de reparación; no se declara el piloto terminado por sus pruebas locales.

## Siguiente checkpoint

Aprobar esta historia permite preparar la especificación técnica del piloto. La implementación se inicia después de aprobar esa especificación. Publicación, despliegue y activación conservan sus autorizaciones y evidencias propias; esta propuesta no concede permisos ni activa herramientas o servicios.

Referencia de requisitos: [especificación piloto v0.1, 25 de octubre de 2026](https://docs.google.com/document/d/15jgGgnRDkVsgpwGDoOsokMm7gkqdfDeN4rBEJD4Ij1g/edit). La investigación de la revisión vigente y del runtime se documenta por separado; esta historia no convierte el estado histórico del candidato en capacidad desplegada.
