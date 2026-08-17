# RQF-SAMCHAT-ASSISTANT-052Z ? Operator continuity surface

## Story

Como usuario del asistente operativo, quiero que al retomar un workspace SamChat me muestre que sabe, que falta, que fuentes tiene y cual es el siguiente paso seguro, para poder continuar el trabajo sin reconstruir manualmente el contexto.

## Acceptance

- El resume muestra contexto conocido desde el snapshot persistido.
- Expone hallazgos, faltantes, fuentes disponibles y siguiente paso recomendado.
- Conserva el `task_id` como preview recomendado.
- No cambia la fuente durable: sigue siendo `AssistantMessage.tool_payload.operator_workspace_snapshot`.
- No llama provider ni ejecuta writes.
- No secuestra frases genericas como `sigamos`.
