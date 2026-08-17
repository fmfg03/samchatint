# RQF-SAMCHAT-ASSISTANT-052Y ? Resume from workspace snapshot

## Story

Como usuario del asistente operativo, quiero poder retomar un preview/workspace especialista previo dentro de la misma conversacion para no perder el hilo despues de una pausa, sin que SamChat invente contexto ni ejecute acciones.

## Boundary

- La fuente durable v0 es `AssistantMessage.tool_payload.operator_workspace_snapshot`.
- No se promueve todavia a `AssistantArtifact` porque falta pasar consistentemente `created_by_empleado_id` por el path conversacional.
- Reanudar significa reconstruir lectura, estado, readiness, calidad de evidencia y siguiente paso sugerido.
- No significa ejecutar writes, llamar provider, activar botones de accion ni asumir autoridad.

## Acceptance

- Detecta solicitudes explicitas de reanudar workspace/preview.
- No secuestra mensajes genericos como `sigamos` o `continua`.
- Carga el snapshot valido mas reciente de la conversacion.
- Rechaza snapshots inseguros o incompatibles.
- Si no hay snapshot, falla cerrado con una instruccion clara.
- Persiste el mensaje de resume con payload `operator_workspace_resume`.
