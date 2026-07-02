# Auditoría de Alcance Marketing

## Proyecto

- Documento base revisado: `Oferta Plataforma Sports SP.pdf`
- Documento complementario: `SOW Plataforma.pdf`
- Fecha de revisión: 23 de junio de 2026
- Criterio: análisis del alcance de marketing, sponsors, branding, medios y comunicaciones contra el código actual del repositorio

## Resumen Ejecutivo

Tomando como referencia la propuesta original y contrastándola con el código actual del repositorio, la conclusión actualizada es la siguiente:

- El frente de marketing ya no debe describirse como una idea o como un faltante casi total.
- Sí existe una base material de comunicaciones y marketing operacional en `copatelmex` y, de forma más importante, también en `goal-fest-page`.
- Hoy ya hay evidencia defendible de campañas por email, programación de envíos, inbox de email, inbox de WhatsApp en tiempo real, plantillas de WhatsApp, historial de mensajes, galerías de medios, configuración de notificaciones por torneo y perfiles de marketing por equipo.
- Eso sube la lectura del frente marketing a `parcial alto`, no a `bajo` ni a `inexistente`.
- Aun así, siguen faltando varios cierres contractuales específicos del SOW: aprobación sponsor/brand proof de punta a punta, publicación real a redes sociales externas, analítica de engagement, proof-of-performance automatizado y reportes finales para patrocinadores como paquete contractual cerrado.

## Alcance Marketing Ofrecido en la Propuesta Original

Del texto extraído de `Oferta Plataforma Sports SP.pdf` y `SOW Plataforma.pdf`, el alcance comercial de marketing prometía al menos lo siguiente:

- Activos multimedia
- Entrega de branding
- Comprobantes para patrocinadores
- Flujos de incidentes y medios
- Comunicaciones multi-modal por WhatsApp, email, SMS y push
- Borradores multi-formato para redes, prensa, stories, recap y sponsor
- Publicación ligada a triggers operativos
- Workflows de aprobación de marca / operaciones / sponsor
- Captura de branding y medios con reportes diarios y finales a patrocinadores
- Dashboards con comprobante de patrocinador
- Presentaciones de proof-of-performance
- Manuales de entrenamiento y SOPs para staff / sponsors

## Actualización de la Lectura Actual

Frente a la lectura más antigua de abril, hoy la defendibilidad del frente marketing es mayor por estas razones:

- El repositorio ya no solo contiene funciones aisladas de correo o WhatsApp.
- Existe una superficie administrativa visible y usable para campañas, bandejas, templates, historial y medios.
- `goal-fest-page` aporta un stack adicional que no estaba reflejado con suficiente peso en la lectura anterior.
- La capa `tournaments_v2` ya empieza a consolidar parte del estado de marketing en snapshots operativos, incluyendo perfiles públicos de equipo y readiness de evidencia de activación.

## Tabla de Evaluación

| Entregable SOW de Marketing | Estado | Evidencia en código | Horas estimadas |
|---|---|---|---:|
| Comunicaciones multi-canal base (email + WhatsApp) | Completo / maduro | Hay funciones y superficies reales para email y WhatsApp en `copatelmex` y `goal-fest-page`. `goal-fest-page/supabase/functions/send-email-sendgrid/index.ts`, `goal-fest-page/supabase/functions/send-whatsapp/index.ts`, `goal-fest-page/supabase/functions/email-webhook/index.ts`, `goal-fest-page/supabase/functions/whatsapp-webhook/index.ts` | 30 |
| Campañas de email y envíos programados | Completo / alto | Campañas, plantillas visuales, scheduling y procesamiento diferido. `goal-fest-page/src/components/admin/AdminEmailCampaigns.tsx`, `goal-fest-page/supabase/functions/process-scheduled-emails/index.ts` | 20 |
| Inbox de email y respuesta operativa | Completo / alto | Recepción inbound, agrupación por conversación, reply desde admin. `goal-fest-page/src/components/admin/AdminEmailInbox.tsx`, `goal-fest-page/supabase/functions/email-webhook/index.ts` | 12 |
| Inbox de WhatsApp y operación diaria | Completo / alto | Bandeja en tiempo real, media, respuestas, exportes. `goal-fest-page/src/components/admin/AdminWhatsAppInbox.tsx`, `goal-fest-page/supabase/functions/whatsapp-webhook/index.ts` | 22 |
| Templates de WhatsApp Business | Completo / alto | CRUD de templates, variables, envío a Twilio, sincronización de estado. `goal-fest-page/src/components/admin/AdminWhatsAppTemplates.tsx` | 12 |
| Historial y analítica básica de WhatsApp | Parcial alto | Historial filtrable y métricas operativas por rango/estado/tipo. `goal-fest-page/src/components/admin/AdminWhatsAppHistory.tsx` | 10 |
| Configuración de notificaciones por torneo | Completo / alto | Toggles para confirmaciones, recordatorios, actualizaciones por email y WhatsApp. `goal-fest-page/src/components/admin/AdminTournaments.tsx` | 10 |
| Galería y activos multimedia | Parcial alto | Galería administrativa y pública, activos gráficos, medios por torneo. `goal-fest-page/src/components/admin/AdminGallery.tsx`, `goal-fest-page/src/pages/MediaGallery.tsx`, `copatelmex/src/pages/MediaGallery.tsx` | 14 |
| Perfiles públicos / marketing de equipo | Parcial alto | Instagram, Facebook, escudo y campos de perfil ya aparecen modelados y consolidados. `src/samchat/tournaments_v2/services/soul_service.py` | 8 |
| Evidencia básica de activación / readiness de medios | Parcial | Ya existe consolidación de fotos/videos/streams para readiness, pero no paquete sponsor cerrado. `src/samchat/tournaments_v2/services/soul_service.py` | 8 |
| Captura de branding y medios para sponsor | Parcial | Hay medios, galerías y perfiles; no aparece flujo contractual completo de evidencia sponsor por activación. `goal-fest-page/src/components/admin/AdminGallery.tsx`, `goal-fest-page/src/pages/MediaGallery.tsx` | 8 |
| Workflow de aprobación marca / ops / sponsor | Incompleto | No se encontró un flujo canonizado de aprobación previa de assets sponsor con estados y evidencias. | 0 |
| Publicación real a redes sociales externas | Incompleto | Hay datos y copy-adjacent surfaces, pero no publicación productiva directa a Meta/X/TikTok como cierre contractual. | 0 |
| Analítica de engagement y proof-of-performance | Incompleto | No se encontró un módulo contractual cerrado para reach, shares, views, sponsor exposure y paquete final automatizado. | 0 |
| Reportes diarios / finales a patrocinadores | Parcial bajo | Hay piezas reutilizables, pero no un flujo cerrado que empaquete evidencia y KPIs sponsor como entregable final. | 6 |
| Incidentes y medios como workflow formal | Parcial bajo | Hay comunicaciones y media, pero no se ve un módulo formal de incident/media workflow contractual. | 5 |
| Dashboards de marketing / sponsor como paquete contractual | Parcial | Hay métricas operativas dispersas y snapshots, no un entregable final cerrado de dashboard sponsor-marketing. `src/samchat/tournaments_v2/services/soul_service.py`, `goal-fest-page/src/components/admin/AdminWhatsAppHistory.tsx` | 8 |
| Manuales, SOPs y training de marketing/sponsors | Parcial bajo | Existen superficies y documentación suelta, pero no se acredita el paquete formal de entrenamiento sponsor/marketing. | 4 |

## Clasificación Consolidada

### Hecho completo o casi completo

- Comunicaciones multi-canal base por email y WhatsApp
- Campañas de email y envíos programados
- Inbox de email con respuesta operativa
- Inbox de WhatsApp con operación diaria
- Templates de WhatsApp Business
- Configuración de notificaciones automáticas por torneo

### Hecho parcial relevante

- Historial y analítica básica de WhatsApp
- Galería y activos multimedia
- Perfiles públicos / marketing de equipo
- Consolidación parcial de evidencia de activación
- Reportes y snapshots operativos de marketing
- Declaración read-only en Plataforma Sports de siete capacidades sponsor/media, incluyendo video recap, render/captura, tracking de obligaciones, pre-revisión de marca, cola de aprobación, command center de jornada y armado de paquetes de evidencia sponsor.

### Hecho incompleto

- Workflow formal de aprobación sponsor / branding
- Publicación real a redes sociales externas
- Analítica de engagement multicanal
- Proof-of-performance sponsor automatizado
- Reportes diarios y finales a patrocinadores como paquete contractual cerrado
- Dashboard sponsor-marketing final como entregable único
- Training / SOPs formales de marketing y sponsors

## Estimación de Horas

Tomando únicamente el frente de marketing comunicado en la propuesta original y comparándolo con lo hoy visible en código, la lectura razonable es la siguiente:

### Estimado principal actualizado

- Horas hechas dentro de scope marketing: 124 horas
- Horas faltantes dentro de scope marketing: 49 horas
- Horas hechas de más o reutilizables fuera del scope marketing estricto: 22 horas

### Rango razonable actualizado

- Hechas dentro de scope marketing: 110 a 140 horas
- Faltantes dentro de scope marketing: 35 a 60 horas
- Hechas de más o reutilizables: 15 a 35 horas

## Conclusión Ejecutiva

La lectura más defendible hoy es que el frente marketing sí tiene una base real, reutilizable y operativa. No corresponde describirlo como “por hacer desde cero”.

Lo ya construido cubre una parte importante del marketing operacional:

- campañas de email
- programación de envíos
- recepción y respuesta de correo
- operación de WhatsApp en tiempo real
- templates de WhatsApp
- historial y métricas básicas
- medios / galería
- configuración de notificaciones por torneo
- perfiles públicos de equipos

La brecha real ya no es construir un módulo genérico de comunicaciones, sino cerrar la capa específicamente sponsor/branding del SOW:

- aprobación formal de assets
- publicación externa
- analítica de engagement
- proof-of-performance
- reportes sponsor diarios/finales

### Capacidades Sponsor/Media Propuestas

Dentro del pack de marketing conviene presentar siete submódulos complementarios, separados por función:

- `Agente de Video Recap & Sponsor Proof`: acelerador conceptual basado en `browser-use/video-use` para primeros cortes de highlights, recaps diarios, reels, clips de patrocinador, compilados de branding y evidencia audiovisual.
- `Content Rendering & Sponsor Evidence Agent`: acelerador conceptual basado en Cloudflare Browser Run para renderizar/capturar/exportar tarjetas de partido, tarjetas de marcador, social cards, PDFs sponsor, screenshots de dashboard/evento, paquete diario de evidencia y metadata por asset.
- `Sponsor Obligation Tracker`: matriz de compromisos contra entregables por sponsor, obligaciones pendientes/cumplidas, alertas de evidencia faltante y score de cumplimiento comercial.
- `Brand Compliance & Logo Evidence Agent`: pre-revisión asistida de presencia de logos, menciones, flags de branding faltante o incorrecto, frames de evidencia y notas para aprobación.
- `Content Approval Queue`: trazabilidad de piezas en draft, revisión automática, revisión de operaciones, aprobación sponsor/marca, timestamps, comentarios y paquetes aprobados.
- `Matchday Content Command Center`: snapshot de jornada con partidos del día, piezas pendientes, highlights esperados, sponsors activos por partido, evidencia faltante, aprobaciones atoradas, prensa/ceremonias/eventos y recap diario pendiente.
- `Sponsor Proof Package Builder`: armado de paquetes diarios/finales por patrocinador con índice de evidencia, galería, links de posts, export de dashboard y resumen de cobertura de obligaciones.

El encuadre comercial correcto es venderlos como capacidades read-only y aceleradores con plantillas, apoyo de LLM/editor humano y aprobación de marca o sponsor. No deben presentarse como publicación autónoma, reemplazo creativo, reemplazo de Canva, reemplazo del editor de video, renderizado productivo ya integrado ni detección perfecta de evidencia/branding.

Como add-on inicial, el rango recomendado para automatización de video recap y evidencia sponsor es `35,000-75,000 MXN` por torneo/proyecto, dependiendo de si el alcance incluye solo setup/workflow o también integración con dashboard, Google Drive, convenciones de naming, tags de sponsor y aprobación.

## Frase Recomendada para Cliente

“Del frente marketing originalmente planteado, ya existe una base operativa relevante y defendible: campañas y programación de email, inbox de correo, inbox de WhatsApp en tiempo real, templates de WhatsApp Business, historial de mensajes, galerías de medios, configuración de notificaciones por torneo y perfiles de marketing por equipo. Por eso, marketing no debe tratarse como un alcance inexistente. Lo que aún falta para cerrar el SOW de forma contractual no es la capa base de comunicaciones, sino la automatización sponsor/branding de punta a punta: aprobaciones de marca, publicación externa, analítica de engagement, proof-of-performance y reportes finales para patrocinadores.” 
