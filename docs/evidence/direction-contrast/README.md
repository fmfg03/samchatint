# Dirección: contraste bajo el tema global

Base: main `60f2c3dfebafd1ed8ee92312c0c243f14e5496d4` (PR453/454).
Autorización de Francisco: `Sentinel_501c4e1233448191a114c22218ecd26d`, en respuesta
a la propuesta `Sentinel_460c110d3b6c81919c1a00e7740ff42f`: corregir contraste y
publicar el ajuste. PR separada en borrador; sin merge ni deploy autorizado.

## Causa y cambio

El runtime `copa_telmex_dashboard.py` aplica `_inject_modern_theme` a respuestas
HTML. Inyecta `MODERN_UI_HEAD_INJECTION` al final del head, después del enlace a
`static/direction_reports.css`: su fondo degradado de body y los fondos/colores
de `th` tienen `!important`. La página de Dirección tenía un fondo claro sin esa
prioridad y tarjetas de KPI transparentes. El resultado reproducido es el reportado
por el smoke productivo: h1 rgb(15,23,42) sobre el degradado oscuro, sin selector ni
diálogo abiertos. No es un overlay ni un cambio de sesión. Dirección usa este CSS
estático y `direction_home.js`; no depende de un bundle compilado para sus estilos.

Los fixtures previos renderizaban `render_home` directamente sin pasar por esa
inyección. La nueva regresión carga por AST la constante y la función reales,
sin arrancar el runtime ni consultar credenciales/DB, y ejecuta también su JS de
envoltura de tablas. Así conserva el orden efectivo de estilos de producción.

Se añade `body.direction-reports` solo en este renderer y se acotan a ese marcador
las superficies claras de canvas, tarjetas y encabezados de tabla. La prioridad
explícita responde a los `!important` globales. No se cambia el middleware, el tema
de otros módulos, JS, cálculos, fuentes, permisos o selección.

## Evidencia sintética

Las capturas y todos sus importes son fixtures sintéticos; no contienen datos
financieros reales. Los `before-*` quitan únicamente el nuevo marcador de body,
reproduciendo la cascada anterior con la misma inyección global. Los `after-*`
muestran Resumen, Presupuesto vs Real, Flujo de efectivo, selector y Sam a 1440 y
390 px. Se inspeccionaron visualmente escritorio, tabla móvil y diálogo.

`contrast-1440.json` y `contrast-390.json` registran colores calculados y ratios
por elemento de texto visible. Se componen fondos alfa de ancestros y se exige
4,5:1 para texto normal / 3:1 para grande; se rechazan fondos degradados no
modelados en el contenido auditado. Mínimo observado: 5,30:1 en paneles y 5,69:1
en Sam. No representa una certificación de accesibilidad completa ni una medición
de todos los estados, pseudoelementos, placeholders o contenido futuro.

Pruebas locales aprobadas:
- 144 unitarias: home, rutas, rediseño, análisis y exportes.
- 6 navegador: las cuatro regresiones existentes más escritorio/móvil con tema
  global. Estas últimas se repitieron tras mejorar la detección de visibilidad:
  2 aprobadas. Incluyen tres tabs, filas expandibles, selector, diálogo/escenario,
  foco por teclado, ausencia de overflow de página y errores JavaScript.
- Black, isort y `git diff --check` en los archivos modificados.

Reproducir (Chromium instalado):

```bash
PYTHONPATH=src python -m pytest tests/browser/test_direction_contrast_browser.py tests/browser/test_direction_home_browser.py -q
PYTHONPATH=src python -m pytest tests/unit/test_direction_home.py tests/unit/test_direction_home_routes.py tests/unit/test_direction_report_redesign.py tests/unit/test_direction_analysis_reports.py -q --no-cov
```

Usar `SAMCHAT_BROWSER_ARTIFACT_DIR` para elegir destino de PNG/JSON. La ejecución
local utilizó `/usr/bin/chromium` con el fixture de navegador del entorno. CI usa
su Chromium instalado y conserva artefactos del workflow Browser UX Pilot.

## Límites y release

Runtime: web de Dirección. Owner visual: `home_ui.py` / `direction_reports.css`.
Owners de lectura/escritura y frontera híbrida Postgres/Supabase intactos; no se
accede a producción desde este entorno. Evidencia: código y QA sintética, no
`deployed_verified` ni aceptación productiva. Tras un eventual deploy autorizado,
queda pendiente repetir smoke autenticado con los activos efectivamente servidos.

**Canon unchanged:** corrección CSS de presentación sin cambio de producto,
autoridad, arquitectura de datos o criterios financieros. Los tres hashes del
canon coinciden con el registro. Rollback: revertir la PR; sin migraciones.
