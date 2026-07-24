# Revisión Ejecutiva de Alcance y Valorización

## Plataforma Sports / Fundación Telmex

Fecha: 23 de junio de 2026

## Propósito del documento

Este documento tiene como objetivo facilitar una revisión ejecutiva con el cliente sobre tres bloques:

- trabajo ya realizado dentro del alcance base contratado
- componentes del alcance original que no forman parte de esta valorización
- trabajo adicional ejecutado fuera del alcance inicial

## Base económica de referencia

Precio final contratado:

- `500,000 MXN`

Horas originalmente consideradas en la propuesta:

- `854 horas`

Tarifa promedio de referencia para esta revisión:

- `500,000 / 854 = 585.48 MXN por hora`

Nota:

- esta tarifa se usa únicamente como referencia homogénea para valorar el avance y el trabajo adicional
- no sustituye una conciliación contractual detallada por perfil o por hito

## Resumen ejecutivo

Con base en la revisión del alcance original, del SOW y de la plataforma actualmente construida, la lectura ejecutiva es la siguiente:

- existe un bloque sustancial de trabajo ya ejecutado dentro del alcance base
- existe también un bloque relevante de trabajo adicional ya ejecutado fuera del alcance inicial
- los componentes pendientes del alcance original corresponden principalmente a cierres puntuales y acelerables, no a una reconstrucción estructural
- esos componentes de cierre no deben descontar el valor del trabajo ya construido ni diluir el reconocimiento económico del sistema ya desarrollado

## 1. Trabajo realizado dentro del alcance base

| Componente | Horas estimadas |
|---|---:|
| Flujo de registro papel/digital y captura operativa | 35 |
| Revisión y validación documental de registro | 30 |
| OCR y estructuración de datos de registro | 25 |
| Compliance documental de jugadores y equipos | 55 |
| Validación CURP integrada al flujo de torneo | 25 |
| Captura web de gastos | 18 |
| Creación y edición de cuenta de gastos | 22 |
| Detalle, cierre y operación de cuenta de gastos | 20 |
| Solicitudes personales ligadas a cuenta | 15 |
| Aprobaciones, historial y estados operativos | 20 |
| Cuentas por pagar AP y operación de pagos | 25 |
| Beneficiarios, comprobación y seguimiento | 20 |
| Exportes y operación administrativa AP | 15 |
| Integración CFDI / OCR factura | 20 |
| Ingestión y procesamiento CFDI | 15 |
| Vinculación CFDI-gasto y manejo UUID | 15 |
| Operación COI vía importación / exportación | 20 |
| Exportes contables y operación asociada | 15 |
| Presupuestos por proyecto | 20 |
| Partidas, estructura presupuestal y soporte operativo | 15 |
| Reportes y lectura ejecutiva por proyecto | 10 |
| Usuarios, roles y permisos | 20 |
| Autenticación web y sesiones | 12 |
| Control de acceso administrativo y filtros por usuario | 8 |
| Onboarding, documentación y capacitación | 15 |
| Comunicaciones multi-canal base (email + WhatsApp) | 18 |
| Campañas de email y envíos programados | 20 |
| Inbox de email y respuesta operativa | 12 |
| Inbox de WhatsApp y operación diaria | 22 |
| Templates de WhatsApp Business | 12 |
| Historial y analítica básica de WhatsApp | 10 |
| Configuración de notificaciones por torneo | 10 |
| Galería y activos multimedia | 8 |
| **Total trabajo realizado dentro de alcance base** | **669** |

### Valor referencial del trabajo realizado dentro de alcance base

- `669 horas x 585.48 MXN/h = 391,686 MXN` aproximadamente

### Lectura comercial de este bloque

Este bloque representa el valor directamente reconocible dentro del alcance originalmente contratado. La recomendación es presentarlo como trabajo ya ejecutado, usable y económicamente valorizable.

## 2. Componentes de cierre rápido del alcance original

Los siguientes componentes se presentan como `cierre rápido`, `integración final` o `fase posterior acotada`.

La lógica comercial aquí es importante:

- no representan ausencia de plataforma
- no representan reconstrucción del sistema
- representan cierres específicos, puntuales y acelerables sobre una base ya existente

Por esa razón, este bloque debe revisarse aparte y no debe reducir el reconocimiento económico del trabajo ya ejecutado.

| Componente | Horas estimadas |
|---|---:|
| Motor de gestión de proyectos / plataforma base multi-agente | 25 |
| Validación FMF / suspensiones por categoría | 25 |
| Deduplicación 85% contra padrón global | 15 |
| Calendarización inteligente de partidos | 35 |
| Logística operativa de sedes | 10 |
| Logística de árbitros | 10 |
| Logística de uniformes | 10 |
| Match 3 vías CFDI | 15 |
| Cuentas por cobrar / emisión CFDI AR | 10 |
| Integración viva COI / SAE | 10 |
| Workflow de aprobación marca / ops / sponsor | 12 |
| Publicación real a redes sociales externas | 15 |
| Analítica de engagement y proof-of-performance | 12 |
| Reportes diarios / finales a patrocinadores | 6 |
| Incidentes y medios como workflow formal | 4 |
| **Total componentes de cierre rápido / fase posterior** | **214** |

### Add-on marketing/sponsor sugerido

Además del cierre rápido anterior, el pack de marketing puede incorporar siete capacidades sponsor/media diferenciadas:

- `Agente de Video Recap & Sponsor Proof`: automatización asistida para primeros cortes de highlights, recaps diarios, reels, clips de patrocinador, compilados de branding y evidencia audiovisual. Base conceptual: `browser-use/video-use`.
- `Content Rendering & Sponsor Evidence Agent`: fábrica de render/captura/exportación para tarjetas de partido, tarjetas de marcador, social cards, PDFs sponsor, capturas de dashboard/evento, paquete diario de evidencia y metadata por asset. Base conceptual: Cloudflare Browser Run.
- `Sponsor Obligation Tracker`: convierte acuerdos de patrocinio en matriz rastreable de menciones, logos, posts, banners, entregables, fechas, evidencia y aprobaciones.
- `Brand Compliance & Logo Evidence Agent`: pre-revisión asistida de logos, menciones y evidencia de marca en piezas, fotos, videos o reportes, sin prometer detección perfecta.
- `Content Approval Queue`: cola de aprobación para posts, videos, PDFs, press releases y piezas sponsor, con estado, timestamps, aprobador, comentarios y trazabilidad.
- `Matchday Content Command Center`: snapshot por jornada con partidos, piezas pendientes, highlights esperados, sponsors activos, evidencia faltante, aprobaciones atoradas, prensa/ceremonias/eventos y recap diario.
- `Sponsor Proof Package Builder`: armado del paquete diario/final por sponsor con evidencia, galería, links, export de dashboard y resumen de cobertura de obligaciones.

Estos componentes deben presentarse como capacidades read-only y aceleradores con templates, apoyo de LLM/editor humano y aprobación de marca o sponsor. No deben venderse como publicación autónoma, reemplazo creativo, reemplazo de Canva, reemplazo del editor de video, renderizado productivo ya integrado ni detección garantizada de evidencia/branding.

Rango comercial inicial recomendado para el add-on de video recap y evidencia sponsor: `35,000-75,000 MXN` por torneo/proyecto, según profundidad de setup, integración con dashboard, Google Drive, convenciones de naming, tags sponsor y flujo de aprobación.

### Valor referencial de componentes de cierre rápido

- `214 horas x 585.48 MXN/h = 125,293 MXN` aproximadamente

### Lectura comercial de este bloque

Este bloque debe presentarse como:

- fase de cierre
- aceleración de integraciones finales
- automatización puntual restante

No debe presentarse como “trabajo faltante del sistema completo”, sino como un paquete acotado de terminación sobre una base ya desarrollada.

## 3. Trabajo adicional ejecutado fuera del alcance inicial

| Componente adicional | Horas estimadas |
|---|---:|
| Modelo laboral y ampliaciones de nómina | 18 |
| Importación, normalización y soporte de nómina | 17 |
| CFDI nómina y reglas asociadas | 20 |
| Contabilidad histórica y estructuras de consulta | 25 |
| Importación de pólizas, auxiliares y balanzas | 20 |
| Conciliación, cierres y operación contable ampliada | 25 |
| Forecast financiero y proyecciones | 20 |
| Presupuestos avanzados y lectura comparativa | 25 |
| Expedientes y carpetas operativas | 18 |
| Compromisos, seguimiento y estructura de evidencias | 24 |
| Capacidades ejecutivas de asistente sobre datos reales | 25 |
| Ampliaciones operativas y administrativas recientes fuera del alcance base | 20 |
| **Total trabajo adicional ejecutado** | **257** |

### Valor referencial del trabajo adicional ejecutado

- `257 horas x 585.48 MXN/h = 150,468 MXN` aproximadamente

### Lectura comercial de este bloque

Este bloque debe presentarse como trabajo adicional ya ejecutado y como incremento real de valor sobre el alcance original. No corresponde absorberlo dentro del precio base sin reconocimiento económico separado.

## 4. Lectura económica consolidada

| Concepto | Horas | Valor referencial |
|---|---:|---:|
| Trabajo realizado dentro del alcance base | 669 | 391,686 MXN |
| Trabajo adicional ejecutado fuera del alcance inicial | 257 | 150,468 MXN |
| **Total trabajo ejecutado** | **926** | **542,154 MXN** |
| Componentes de cierre rápido / fase posterior | 214 | 125,293 MXN |

## 5. Lectura recomendada para revisión con cliente

La conversación de revisión con cliente puede estructurarse así:

### A. Reconocimiento del trabajo ya realizado dentro del alcance base

El sistema ya incorpora un bloque amplio de trabajo directamente vinculado con el alcance original y con valor referencial de:

- `391,686 MXN`

### B. Reconocimiento del trabajo adicional ejecutado

Además del alcance base, se desarrolló trabajo adicional con valor referencial de:

- `150,468 MXN`

Este bloque corresponde a ampliaciones, mejoras y profundización funcional fuera del alcance original.

### C. Componentes de cierre rápido / fase posterior

Los componentes que no forman parte de esta valorización deben revisarse por separado como:

- cierre rápido
- integración final
- fase posterior acotada

## 6. Mensaje ejecutivo sugerido

Texto sugerido para revisar con cliente:

“Con base en la revisión del alcance original y del sistema actualmente construido, la valorización distingue tres bloques: primero, el trabajo ya realizado dentro del alcance base; segundo, el trabajo adicional ejecutado fuera del alcance inicial; y tercero, los componentes de cierre rápido que pueden abordarse en una etapa separada. Bajo esta lectura, el valor del trabajo ya ejecutado dentro del alcance base asciende aproximadamente a 392 mil pesos, y el trabajo adicional ya desarrollado representa aproximadamente 150 mil pesos adicionales en valor construido. Los componentes restantes no cambian la valorización del trabajo ya realizado, porque corresponden a cierres puntuales e integraciones finales sobre una base ya desarrollada.”

## 7. Observación final

Este documento busca facilitar una conversación clara de valorización:

- qué trabajo ya se ejecutó dentro del alcance base
- qué trabajo adicional ya fue construido fuera del alcance original
- qué componentes corresponden a una etapa de cierre rápido o integración final

La intención es que el reconocimiento económico se centre en el valor efectivamente construido y entregado.
