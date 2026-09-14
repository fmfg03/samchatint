# RQF-UI-001 — Controles de acción y navegación de tablas extensas

Status: STORY_READY_FOR_IMPLEMENTATION
Date: 2026-09-14
Target runtime: `copa_telmex_dashboard.py` / `samchat-gastos.service`
Domains: vistas web de Operaciones, Gastos, Finanzas, Presupuestos, Dirección y Soporte
Evidence level: authenticated UAT observation; implementation and production acceptance pending

## Problema observado

Durante la verificación end-to-end autenticada, usuarios de Plataforma Sports
reportaron dos problemas repetibles en tablas extensas:

1. Los botones de acción se comprimen dentro de columnas angostas y parten sus
   etiquetas, por ejemplo `Apro/bar` y `Rech/azar`. La cercanía entre acciones
   opuestas incrementa el riesgo de seleccionar la acción equivocada.
2. En tablas anchas o largas, el encabezado deja de estar visible durante el
   desplazamiento vertical y la barra horizontal sólo queda accesible al final
   del contenido. El usuario pierde el contexto de cada columna o debe recorrer
   toda la tabla para cambiar la posición horizontal.

La bandeja `/documentos/pendientes` es el caso testigo. La historia no se limita
a esa ruta: debe identificar y corregir las superficies equivalentes del
runtime web activo.

## Historia de usuario

Como usuario de SamChat que revisa, autoriza o consulta registros en tablas,
quiero que cada acción conserve una etiqueta completa y una separación clara,
y que el encabezado y el control de desplazamiento permanezcan disponibles,
para decidir sin ambigüedad y navegar tablas extensas sin perder el contexto.

## Resultado esperado

SamChat contará con un contrato visual compartido para tablas operativas:

- las acciones mantienen etiqueta, área clicable y separación suficientes;
- las acciones opuestas se distinguen por texto, color, foco y espacio;
- el encabezado permanece visible dentro del viewport desplazable de la tabla;
- el desplazamiento horizontal permanece accesible mientras se recorren filas;
- escritorio, viewport angosto, teclado y zoom conservan la funcionalidad;
- las excepciones heredadas quedan inventariadas en vez de declararse cubiertas.

## Alcance incluido

### 1. Inventario de superficies

- Enumerar las vistas conectadas al runtime que renderizan tablas HTML.
- Clasificarlas como:
  - `SHARED_TABLE_SHELL`: usan el contenedor compartido `.table-shell`;
  - `DOMAIN_TABLE_SHELL`: usan un componente equivalente del dominio;
  - `LEGACY_INLINE_TABLE`: dependen de estilos locales o inline;
  - `SPECIALIZED_GRID`: presupuesto u otra cuadrícula con columnas ya fijadas.
- Registrar ruta, propietario de código, tipo de tabla, acciones disponibles y
  evidencia de prueba por superficie.
- No afirmar cobertura global a partir de una sola clase CSS.

Exploración inicial del `main` inspeccionado:

- se detectaron tablas en 12 archivos fuente;
- `.table-shell` aparece únicamente en 4 archivos fuente;
- existen contenedores heredados con `overflow-x:auto` y cuadrículas de
  presupuesto con columnas horizontales fijadas.

Estas cifras son punto de partida, no inventario final ni aceptación.

### 2. Contrato de botones de acción

- La etiqueta de una acción no se divide entre líneas.
- Cada botón tiene ancho mínimo derivado de su contenido, no de una anchura fija
  menor a la etiqueta.
- El área clicable incluye fondo, relleno y etiqueta completos.
- Acciones contiguas conservan al menos 8 px de separación en ambos ejes.
- En columnas de tabla, las acciones pueden apilarse de forma deliberada, pero
  cada botón permanece completo y la separación continúa visible.
- Acciones destructivas o de rechazo mantienen apariencia distinta de las
  acciones primarias.
- El foco de teclado es visible y el orden de tabulación coincide con el orden
  visual.
- No se cambia la ruta POST, autoridad, confirmación, idempotencia ni auditoría
  de ninguna acción.

### 3. Contrato de tablas desplazables

- El encabezado usa comportamiento `sticky` dentro del contenedor desplazable,
  con fondo opaco y `z-index` suficiente para no mezclarse con las filas.
- La tabla dispone de un viewport vertical acotado en vistas largas; su altura
  se adapta a la ventana y no oculta navegación o mensajes críticos.
- El mismo contenedor gestiona desplazamiento vertical y horizontal, de modo que
  su barra horizontal permanezca en el borde inferior visible del viewport.
- Se reserva el espacio de la barra cuando el navegador lo soporte para evitar
  saltos de contenido.
- No se implementan dos barras horizontales independientes salvo que una prueba
  demuestre que el viewport acotado no satisface una superficie especializada.
- Las tablas cortas no adquieren un área vacía ni una altura artificial.
- Encabezados agrupados, columnas fijadas y tablas anidadas deben probarse como
  excepciones explícitas.

### 4. Adopción progresiva

1. Introducir el contrato compartido y probarlo primero en
   `/documentos/pendientes`.
2. Aplicarlo a vistas `SHARED_TABLE_SHELL` sin alterar semántica de dominio.
3. Migrar superficies `DOMAIN_TABLE_SHELL` con pruebas focales.
4. Inventariar `LEGACY_INLINE_TABLE`; corregirlas por lotes acotados o registrar
   una excepción con propietario y fecha objetivo.
5. Validar `SPECIALIZED_GRID` sin romper columnas ya fijadas.

## Fuera de alcance

- Rediseñar navegación, filtros, ordenamiento o paginación.
- Cambiar permisos, roles, rutas de autorización o estados financieros.
- Sustituir tablas por tarjetas, grids JavaScript o un framework frontend.
- Hacer responsive todo el producto mediante una regla CSS indiscriminada.
- Corregir contenido, datos, cálculos o deuda documental no relacionada.
- Mezclar esta mejora con el hotfix funcional de aprobación del PR #328.

## Criterios de aceptación

### AC-UI-001 — Etiquetas completas

En anchos de viewport de 1440, 1280, 1024 y 768 px, las etiquetas `Aprobar`,
`Rechazar` y las demás acciones inventariadas no se parten ni se recortan.

### AC-UI-002 — Separación y prevención de error

Los controles de acciones opuestas conservan al menos 8 px de separación y no
comparten un área clicable. Cada acción conserva su estilo primario, secundario
o destructivo.

### AC-UI-003 — Encabezado persistente

En una tabla con al menos 30 filas, todos los encabezados visibles permanecen
fijos al desplazar verticalmente el contenido de la tabla.

### AC-UI-004 — Desplazamiento horizontal accesible

En una tabla más ancha que su contenedor, el usuario puede desplazarla
horizontalmente sin recorrer primero todas las filas. La posición horizontal se
conserva al continuar el desplazamiento vertical.

### AC-UI-005 — Sin solapamientos

Encabezados, menús, botones masivos, avisos y primera fila no quedan ocultos ni
solapados por elementos `sticky`.

### AC-UI-006 — Teclado y zoom

Con teclado y zoom de navegador al 200 %, las acciones siguen alcanzables, el
foco es visible y el desplazamiento no atrapa al usuario.

### AC-UI-007 — Móvil y entrada táctil

En viewport de 390 px, la tabla conserva desplazamiento horizontal táctil y las
acciones mantienen un objetivo de interacción de al menos 44 px de alto cuando
se muestran como botones.

### AC-UI-008 — Cobertura verificable

El PR incluye el inventario completo y distingue superficies corregidas,
excepciones y deuda pendiente. “Todas las vistas” sólo se declara cuando no
quedan superficies aplicables sin evidencia.

### AC-UI-009 — Regresión funcional

Las acciones siguen enviando exactamente las rutas, métodos y campos previos.
Las pruebas existentes de autorización y transición permanecen verdes.

### AC-UI-010 — Evidencia visual

El PR conserva capturas antes/después de la bandeja de aprobación y al menos una
vista de Finanzas, Presupuestos, Dirección y Soporte que contenga una tabla
aplicable.

## Estrategia técnica propuesta

- Extender primero los estilos compartidos de workspace en vez de copiar reglas
  por ruta.
- Definir clases semánticas para contenedor desplazable y grupo de acciones;
  evitar selectores globales sobre todo `table` o todo `button`.
- Usar CSS nativo para `position: sticky`, `overflow: auto`, `white-space:
  nowrap`, `min-inline-size`, `gap` y `scrollbar-gutter`.
- Mantener JavaScript fuera del camino principal. Si una cuadrícula especializada
  exige barras sincronizadas, aislar el adaptador, probar sincronización en ambos
  sentidos y conservar operación sin JavaScript donde sea posible.
- No añadir una nueva ruta de escritura ni duplicar lógica de acciones.

## Plan de pruebas

### Automatizadas

- Pruebas del contrato de clases y marcado compartido.
- Pruebas de que acciones críticas conservan método, destino y campos.
- Regresiones de autorización y transiciones de documentos.
- Pruebas de navegador para encabezado sticky, desplazamiento horizontal,
  teclado y viewports definidos.
- `git diff --check` y compilación de los módulos modificados.

### UAT autenticado

Perfiles mínimos:

- Operaciones/aprobador;
- Finanzas;
- Dirección en modo lectura;
- administrador de Presupuestos;
- Soporte, si la vista inventariada requiere esa autoridad.

Para cada perfil se conservará ruta, resolución, navegador, resultado esperado,
resultado observado, captura y referencia de evidencia.

## Despliegue y rollback

- PR separado desde `main`, sin incluir el hotfix #328 ni archivos de datos.
- Despliegue sólo con Test Suite y checks requeridos verdes.
- Smoke anónimo/read-only primero; después smoke autenticado por perfil.
- Rollback mediante retorno al release anterior; no requiere migración ni
  reparación de datos.
- Si una tabla especializada falla, revertir su adopción sin retirar el contrato
  de las superficies ya verificadas.

## Canon

`Canon unchanged`: la historia mejora ergonomía y accesibilidad de superficies
web existentes. No crea una autoridad, transición, modelo financiero, frontera
de datos, capacidad contractual ni reclamo de aceptación. Si la implementación
descubre un cambio de comportamiento, éste debe separarse y reevaluarse antes de
modificar el canon.

## Definition of Done

- Inventario revisado y adjunto al PR.
- Criterios AC-UI-001 a AC-UI-010 con evidencia.
- Pruebas automatizadas y checks requeridos verdes.
- Revisión humana del diff visual y de accesibilidad.
- Despliegue verificado con release exacto, `/healthz`, `/readyz`, servicio sin
  reinicios y smokes focales.
- UAT autenticado aceptado por los perfiles aplicables.
- Excepciones restantes asignadas a un propietario humano, sin atribución
  automática de responsabilidad.
