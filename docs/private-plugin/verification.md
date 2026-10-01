# Evidencia local y entrega al padre

Fecha: 2026-10-01. Rama `feat/private-plugin-review-20261001`, worktree
`/workspace/samchat-plugin-review`, base `d83cd104ae6106978e40d61de33c98139de145bb`.
No push, PR, publicación, instalación, listener, túnel, despliegue, migración,
credencial ni grant real. Ninguna operación de negocio habilitada.

## Procedencia y preservación

El worktree previo `/workspace/samchat-private-plugin` contenía archivos sin
commit antes de iniciar esta entrega. Se copiaron sus contratos, perímetro,
catálogo, pruebas y documentación como punto de partida; no se editaron allí.
Se agregaron en el worktree nuevo montaje estático verificable, evidencia de
perfiles por defecto, fábrica MCP inerte, adaptador de preview canónico y runner
aislado. El checkout `samchatint` y la rama/archivos de PR445 permanecen intactos.

AGENTS.md y los tres canons fueron leídos en orden. SHA-256 verificados:

| Canon | SHA-256 |
| --- | --- |
| Producto | `99b06fb10c09fc079bad21bd6cb5a141d018845e2fd6cd0680d2a16ff684a479` |
| Ingeniería | `9599c5872ad5308f6dc5546f94ba0596028fd0051f5f63492dd54d4a5fa1ce1c` |
| Sweep | `f930556b0c6002d8d6242e5591fdce72362d922a39a0746d4fac04538580e433` |

**Canon unchanged**: se documentan brechas y componentes locales deshabilitados;
no cambian reglas, autoridad ni arquitectura productiva.

## Comprobaciones reproducibles

Con Python 3.12 y `mcp==1.29.0` disponible (dependencia de pruebas opcional en
`requirements-private-plugin-test.txt`):

```sh
python scripts/private_plugin/inventory.py
python scripts/private_plugin/run_offline_tests.py
```

El runner impide conectar/bindear sockets, lanzar subprocess salvo el inventario
Git de sólo lectura, abrir `.env` o directorios de credenciales. Sustituye el
bootstrap `samchat/__init__.py` por un namespace para no cargar el runtime.
Esto es aislamiento de pruebas, no validación de composición productiva.

Resultado: **38 pruebas aprobadas**. Cobertura de sentencias del paquete nuevo:
**99% (224/226)** con coverage; no equivale a cobertura de negocio completo.
Formato Black/isort, lint flake8 y `git diff --check` forman parte del cierre.

| Evidencia | Qué demuestra | Qué queda sin demostrar |
| --- | --- | --- |
| Inventario regenerado y hashes | 561 declaraciones, 627 paths con alias, 44 acciones, 36 permisos por defecto; deriva reproducible | UI activa, permisos efectivos, rutas dinámicas y efectos transitivos |
| SDK MCP por streams en memoria | initialize, discovery vacío, rechazo de calls, contexto por solicitud, errores sanitizados | Streamable HTTP/TLS, OAuth, conexión privada en ChatGPT |
| Identidad/policy sintéticas | expiración/revocación, denegaciones de objeto/empresa, ausencia de ampliación por scopes y respuesta de policy malformada | integración con empleados, roles, posiciones, organizaciones y permisos reales |
| Confirmación/replay puros | vínculo exacto actor/grant/draft/versión/importe/moneda/destino/payload, conflicto y efecto incierto | reserva/consumo/negocio/audit en misma transacción productiva |
| SQLite exclusivo del test | recibo mínimo sobrevive reapertura y no contiene payload/PII | auditoría productiva, retención, outbox, recuperación de crash |
| Promotor CTT real sobre copias | reglas canónicas de hash/evidencia/allowlist/slot; no modifica entrada ni fabrica datos | autorización de revisión, extracción OCR real, adopción persistida, commit |
| Reintentos de preview/captura deshabilitada | no se crea captura ni se duplica efecto en esta base inerte | captura real exactamente una vez bajo concurrencia |
| Envoltura de archivo | rechaza vacío/tamaño/tipo incompatible | antivirus, decodificación, propiedad del asset, OCR y almacenamiento |

## Siguiente decisión concreta

El padre debe resolver instalación personal vs workspace restringido, una
organización por conexión vs selección validada de organizaciones, y el IdP que
vincule OAuth con empleado local vigente. Evidencia y alternativas en README.
No basta tener un token ni un plan de paga para inferir facultades o elegibilidad
universal de instalación. No se requieren estas decisiones para revisar este
código, pero sí para implementar composición autenticada y habilitar funciones.

La UI activa sigue sin fuente confirmada y PR445 es dependencia del contexto
backend. Cuando exista su merge, actualizar deliberadamente el baseline del
inventario, regenerarlo y volver a revisar adapters. El código no envuelve el
fallback legacy de roster (`tools.py:2979/3154`, `router.py:10602`). La adopción
canónica no sustituye `/api/registration-review/{session_id}/commit` ni su
revalidación bajo lock (`copa_telmex_dashboard.py:5996`).

La paridad funcional completa permanece como criterio final; esta entrega no la
declara terminada. Cerrar cada fila requiere autoridad canónica efectiva,
schema específico, pruebas por perfil/objeto, transacción y postcondición antes
de que la tool aparezca en discovery.
