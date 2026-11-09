# CTT-CAPTURE-COMPLETION-UI-001 — confirmación de captura

Fecha: 2026-11-09. Historia aprobada mediante «aprobado» y especificación
técnica aprobada mediante «aprobada» en la conversación. El diff final,
publicación, CI, integración, despliegue y UAT conservan sus checkpoints.

## Problema y reproducción

La captura protegida había terminado correctamente, pero el aviso de éxito
quedaba fuera de la pantalla al aprobar una plantilla de16 jugadoras. El cliente
sólo actualizaba su widget; la cabecera y otras acciones conservaban el estado
anterior. Después de recargar, la plantilla mostraba una confirmación genérica
con estilo de rechazo y seguía presentando como «Bloqueo» la limitación del
formulario antiguo que sólo admite jugadores.

La investigación reprodujo dos fallos contra los bytes del release servido:
mensaje de captura fuera del viewport y aviso de contrato anterior visible
con session_committed=true. El harness usó únicamente personas y respuestas
sintéticas; todas las solicitudes del navegador estuvieron interceptadas.
La revisión de código también detectó que un error transitorio de polling podía
seguir visible después de un resultado committed posterior.

## Historia y aceptación aprobadas

La persona revisora recibe una confirmación visible y persistente de que la
captura terminó y puede continuar en la bandeja sin intentar capturar otra vez.

- Sólo committed autoritativo del expediente e intento actuales con comprobante
  válido permite anunciar éxito; HTTP 200 o una firma enviada no bastan.
- La confirmación queda visible y accesible con16 jugadoras y en móvil.
- Toda la página refleja Capturado/consulta y sus decisiones quedan cerradas.
- La recarga conserva el éxito y no muestra bloqueos de precaptura como
  impedimentos actuales. La evidencia de validación no se borra de la base.
- Estados inciertos y errores de consulta nunca se presentan como éxito.
- Actualizar la presentación o consultar el estado no emite otra captura,
  desafío o firma. Las respuestas terminales repetidas no duplican la recarga.

## Diseño aprobado

Una transición terminal común en el JS limpia errores transitorios, detiene el
polling, cierra el panel y presenta la confirmación. Sin emitir nuevos POST,
sincroniza la página con un GET real de la ruta actual y un ancla estable de
éxito. No confunde un cambio de hash con una nueva lectura del servidor.

El servidor mantiene su condición session_committed para decisiones cerradas,
presenta Capturado en la cabecera y coloca «Captura completada» junto al inicio
del expediente, con estilo de éxito, role=status, foco programático y enlace a
la bandeja existente. El inicializador del aviso funciona también cuando ya
no existe un panel editable. Los bloqueos anteriores no se presentan como
operativos en un expediente capturado. Los bloqueos de expedientes aún abiertos
y las comprobaciones humanas siguen vigentes.

La caché del script utiliza el hash del archivo final. No se añaden endpoints,
dependencias ni cambios de esquema; workflow, issuer, writer, claves, facultad,
fuentes, fotografías, personas y elegibilidad conservan sus owners y reglas.

## Archivos del alcance

Producto: static/js/ctt_staff_review.js y
templates/registration_review_detail.html.

Pruebas: tests/browser/test_ctt_staff_review_browser.py,
tests/unit/test_ctt_review_templates.py y, si su extracción del fragmento se ve
afectada, tests/unit/test_ctt_staff_review_routes.py. Este documento conserva
la historia, especificación y resultados. No se incluyen cambios financieros
ajenos al alcance.

## Verificación y liberación

Los casos cubren éxito inmediato, resultado inicialmente incierto que termina
committed, error de consulta seguido de éxito, recarga/entrada directa a
capturado, estados no finales, respuestas inválidas y eventos terminales
repetidos. La página completa se prueba además del widget, comprobando acciones
globales, bloqueo antiguo, foco, viewport y ausencia de nuevos POST.

Comandos previstos: pytest de navegador CTT y módulos unit afectados;
node --check; formato/lint acotados; git diff --check; guardas de superficie y
regresiones. Los resultados concretos y la revisión independiente se registran
antes de presentar el diff. CI requerido sobre el head final precede al merge.

El release público se prepara sobre la base que realmente esté servida al
desplegar y preserva bundles, fotografías y componentes privados. Recuperación
mediante selector público anterior, sin reversión de capturas ni migración.
El smoke productivo consulta un expediente ya capturado, salud y readiness;
no repite su firma ni captura. UAT humana se informa por separado.

Canon unchanged: es una corrección de presentación de un resultado autoritativo
existente. No introduce estados de negocio, contratos ni facultades nuevas.

## Resultado de construcción

Implementación local completa. Los únicos cambios de producto son el JS y el
template indicados. Se actualizaron los tres módulos de pruebas del alcance;
este documento es el sexto archivo. Ningún cambio de backend, datos, autoridad,
política, esquema o dependencias se incluye en el diff.

Aceptación final: **144 aprobadas, cero omitidas, 26,03 segundos**. Son 48 casos
de navegador y 96 unitarios de plantillas/rutas. La batería final usa los bytes:

- JS SHA256: 7a8ca863d0ad848f2e8d2d93ec030650788f3662a13b5a11a6ed5cae62ee7ea3.
- Template SHA256: fba2564a3f97ba62ddebcd4ed2cfaf5468f79ec8e15242246f3f735a575b918e.
- Caché del JS: v=7a8ca863d0ad848f.

El harness inicial de página completa reprodujo dos fallos en escritorio/móvil
antes de modificar el producto. Las pruebas finales verifican captura inmediata,
confirmación posterior, fallo 503 seguido de committed también en móvil,
identificadores inválidos, consulta manual, entrada directa a capturado y
respuestas concurrentes. El contador de sincronización prueba que se efectúa
una sola lectura completa; no depende de que el navegador agrupe dos recargas.

La revisión visual y la validación independiente detectaron textos anteriores
que todavía decían Borrador editable o cuerpo técnico bloqueado y campos
interactivos en expedientes históricos capturados. Se corrigieron dentro del
mismo alcance: se muestra Borrador de origen, se identifica como evidencia de
consulta y el fieldset queda deshabilitado. No se presenta ese borrador como
el roster registrado. Se repitió la batería final sobre el template corregido.

Comandos ejecutados en el checkout aislado:

- pytest --no-cov -q tests/browser/test_ctt_staff_review_browser.py
  tests/unit/test_ctt_review_templates.py tests/unit/test_ctt_staff_review_routes.py,
  con el entorno existente de navegador y FIDO2, artefactos/JUnit fuera del repo.
- node --check static/js/ctt_staff_review.js.
- black --check, isort --check-only --profile black y flake8 --ignore E203,W503,E501
  sobre los tres archivos Python de pruebas afectados.
- git diff --check.
- python3 scripts/ci/check-registration-operational-surface.py --root .:
  63 archivos operativos examinados, cero infracciones.
- python3 scripts/ci/check-accepted-regressions.py --root .:
  13 marcadores requeridos y un patrón prohibido verificados, cero fallos.

Todos esos controles locales pasan. No se atribuye un porcentaje de cobertura
Python a los cambios JS; la aceptación de interfaz se demuestra mediante los
casos de navegador y plantillas. No se ejecutó otra suite global local ajena al
alcance. CI remoto requerido, smoke productivo y UAT en dispositivo humano
siguen pendientes: aún debe aprobarse el diff antes de publicar el candidato.

Fallos de investigación/invocación resueltos: el primer harness externo no
encontraba el fixture page del directorio original; se añadió un fixture local
aislado y se reprodujeron los dos fallos reales. Una invocación del guard de
regresiones usó --write-report, opción inexistente; se corrigió a su CLI real
--root y pasó. Se conserva la distinción entre regresiones rojas esperadas,
errores de invocación y pruebas finales aprobadas. La advertencia deprecada de
SQLAlchemy observada en los tests es previa y no se amplió el alcance para
corregirla.

Sin desviaciones funcionales de la especificación aprobada. Si las APIs del
navegador impiden sustituir el ancla o recargar la página, se conserva el aviso
de éxito confirmado y se cierran los controles protegidos; la sincronización
global queda a una recarga GET ordinaria. No se crea una petición de captura
de recuperación. El camino normal de navegación real está probado; no se
afirma cubrir todos los fallos posibles de navegador o red. Las capturas de
pantalla usan únicamente datos sintéticos.
