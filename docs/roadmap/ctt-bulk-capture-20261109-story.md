# CTT-BULK-CAPTURE-001 — Captura por lotes con revisión de excepciones

Estado: historia propuesta, pendiente de aprobación. Sólo investigación y preparación privada; ninguna captura, implementación o autorización productiva nueva.

## Historia

Como responsable de Copa Telmex2026 quiero capturar los equipos pendientes de mis PDF consolidados aprobando un conjunto exacto, revisando sólo las excepciones y una muestra de control, para evitar abrir y confirmar individualmente cada equipo sin perder fuente, validación ni auditoría.

## Alcance propuesto

Los16PDF originales de inventory.json (771páginas). Descubrir el número real de equipos sin inferirlo de los nombres de archivos. Equipo, jugadores, DT y auxiliar. Misma edición y perfil nacional aprobado cuando corresponda al ámbito explícito del lote; no extender las excepciones nacionales a otra fase.

## Criterios de aceptación

1. Integridad de originales por hash, identificación por equipo y continuidad de páginas incluso entre partes de un consolidado. No asumir pares consecutivos: las agrupaciones dudosas quedan excluidas.
2. Cada valor y fotografía incorporados conserva PDF/página/campo/recorte exactos. No inventar datos ni acreditar exactitud por la confianza que declara un modelo. Contrastar lecturas y validaciones, registrando discrepancias.
3. Calificar la versión vigente antes de ejecutar con referencia contrastada y muestra estratificada por consolidado/formato/categoría. La especificación debe fijar métricas, criterio de admisión y parada; concordancia entre dos lecturas no garantiza ausencia de error.
4. Separar aptos, excepciones, duplicados y previamente capturados. Revisión humana sólo de excepciones y muestra, sin300confirmaciones. No corregir automáticamente nombres/fechas contradictorios para habilitar captura.
5. Aplicar política vigente exacta por edición/fase/rol. CURP opcional en el nacional aprobado; fotografía de jugadoras opcional allí. Ausencia permanece ausente; no ampliar esa excepción fotográfica al staff ni otra fase. Captura operativa no acredita elegibilidad externa.
6. Vista previa del conjunto exacto con totales, exclusiones, hashes y versiones. Una aprobación humana con passkey por lote o bloque delimitado, cuyo texto declare validación automática y aprobación del conjunto sin fingir revisión humana individual.
7. Nuevo contrato de autorización agregada: ámbito, caducidad, revocación y autorizaciones hijas de un solo uso ligadas al conjunto firmado. No reutilizar una firma individual para300intentos ni ampliar arbitrariamente suTTL. Mantener separados app público, operador, emisor y escritor.
8. Usar owners protegidos actuales y una transacción por equipo. Revalidar fuente/versiones/facultades/incidencias/duplicados antes del efecto; un fallo no deja plantilla parcial ni exige repetir los completados.
9. Proceso reanudable, acotado, con pausa y recibo por equipo. Recuperar el mismo intento ante resultado incierto. No recapturar River Plate ni otro ya confirmado; no fusionar personas/equipos por similitud de nombre.
10. Conciliar identificados, ya capturados, nuevos confirmados, excluidos, fallidos y pendientes, sin huecos/dobles conteos. Detener ante deriva de fuente/ámbito, duplicidad no resuelta o fallo de calidad.
11. Ensayo sin escritura y primer bloque controlado antes de escala completa. Aprobar esta historia no firma fuentes nuevas ni autoriza capturas productivas de un manifiesto todavía no preparado.
12. Verificar concurrencia, replay, alteración de miembros, revocación/expiración, fallos parciales y recuperación; validación independiente antes de publicar. Cambios de canon y contrato externo requieren revisión humana explícita.

## Fuera de alcance

Prometer cero errores; conceder elegibilidad externa; simular revisión humana; desactivar controles; aprobar excepciones automáticamente; modificar capturas existentes; inferir identidad/género; conceder autoridad indefinida; reutilizar la autorización técnica del despliegue anterior para este lote.

## Decisiones posteriores de especificación

Contrato agregado SamChat/Zaubern; tamaños de bloque y duración; criterio de calificación/muestreo; medición de extracción vigente; conciliación completa de capturas previas; alcance por fase; costes y límites de proveedores antes de llamadas masivas. No fijados por conjetura.
