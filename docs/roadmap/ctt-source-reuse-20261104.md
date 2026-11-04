# CTT — reutilizar extracción y confirmar por equipo

Fecha: 2026-11-04. Estado: investigación terminada; historia autorizada por la petición del usuario; especificación técnica aprobada por el usuario con «ok, dale». La implementación está en verificación; las pruebas sintéticas no constituyen captura del piloto. No se han preparado ni capturado equipos en producción con este cambio.

## Historia y alcance aprobado

Como revisor de miles de equipos quiero conservar los nombres, fechas y fotografías ya correctos y confirmar un equipo completo, atendiendo únicamente excepciones reales, sin dibujar recortes para cada dato. El usuario confirmó que el ejemplo River Plate es correcto y pidió corregir el flujo. Esta afirmación no se convierte automáticamente en una firma WebAuthn ni una captura.

Se conserva el piloto y la política nacional existentes. Esta corrección no importa automáticamente 2,500 equipos ni extiende el manifiesto autorizado. La aprobación previa hasta despliegue sigue vigente para la corrección del circuito; la captura requiere confirmación real con passkey.

## Evidencia y causa

Runtime público inspeccionado: /srv/samchat/releases/gastos-prod-c1418e7ea-ctt-asset-523. Privado: artefacto 87a4c786c-20261103. Los tres canones leídos tienen hashes correctos frente al registro.

staff_review_plan_builder._initial_member inicializa boxes={} y no aprovecha vínculos geométricos; además fuerza los dos puestos a UNCERTAIN y presence_confirmed=False. El navegador agrupa bloqueos por código y oculta la fuente mientras no haya propuesta preparada. No hay indicación por persona/campo.

El Gate existente admite que varios campos usen la misma ficha física como recorte original, conservando una decisión y vínculo de valor por campo; exige al menos 12,000 píxeles y hashes correctos. No necesita que la persona dibuje el rectángulo. No comprueba por OCR la igualdad del texto con el valor.

El alineador actual está empaquetado en el privado; valida líneas de filas y columnas fotográficas. No valida todavía el borde completo de las fichas. No se usarán sus cuatro raíles de foto como si delimitaran ya toda la tarjeta. El JSON de layout no está incluido en ese artefacto; no se añadirá una dependencia silenciosa de una ruta pública.

En la vista protegida consultada River Plate mantiene 16 nombres/fechas y ocho fotos derivadas. Ocho previews no están disponibles. El mensaje PHOTO_SOURCE_INVALID reúne extracción y almacenamiento: no demuestra que haya ocho recortes erróneos. Se reprodujo la derivación real en memoria con las páginas CAS 5/6 de River Plate (612×792): propuestas photo en slots 1,3,4,6,8,11,14,16; missing en 2,5,7,12,13,15; uncertain en 9,10. Esos estados no son fallos de staging en esa reproducción. missing es la clasificación del detector, no una acreditación humana de ausencia. No se modificaron las fotos correctas ni se produjo una captura. Se conserva la separación de errores de almacenamiento para otros casos.

## Especificación técnica aprobada

1. Derivar del snapshot CAS y del slot físico validado una propuesta de ficha completa en coordenadas de la página ORIGINAL. Ampliar el alineador existente para comprobar bordes completos, columnas, filas y tamaño; no asumir alineación por ser carta. Si hay referencia dudosa, giro no soportado, falta de borde, geometría inválida o ficha demasiado pequeña, dejar esa persona como excepción identificada.
2. Usar la ficha verificada como fuente compartida de nombre completo, fecha y CURP aportada. Mantener un vínculo/hash de cada valor. Conservar literalmente los valores actuales y las correcciones guardadas; no volver a ejecutar OCR ni normalizar nombres/fechas para resolver este problema. CURP ausente en nacional continúa opcional.
3. Respetar las selecciones manuales existentes; las propuestas automáticas sólo completan fuentes vacías. Nunca reemplazar una corrección explícita al preparar o consultar. Al cambiar página/slot/valor o fuente se invalida el estado de confirmación correspondiente y se crea otra propuesta bajo CAS.
4. Conservar el algoritmo y encuadre fotográfico ya aprobados. Probar legacy y protegido con los mismos bytes/slot antes de modificar cualquier ruta de foto; distinguir fallo de almacenamiento frente a celda vacía o dudosa. Un fallo técnico muestra un error recuperable, no pide al usuario recortar otra vez una fotografía correcta. Una foto realmente ausente sigue siendo excepción según política.
5. Proponer presencia de DT/auxiliar sólo desde un estado y valores ya declarados que sustenten esa propuesta, unidos a la referencia física confiable y mostrados a la persona para contraste. La geometría no acredita el contenido semántico ni la presencia. Resolver DT/auxiliar desde la página ORIGINAL y su puesto físico (page:N:director_tecnico / page:N:auxiliar), nunca desde el orden de la lista ni sólo por una página válida. Esa propuesta NO establece presence_confirmed. Si el estado no sustenta presencia o la referencia es ambigua, conservar UNCERTAIN; no convertir automáticamente puestos ambiguos o ausentes en presentes.
6. Sustituir la repetición de confirmaciones por puesto por una confirmación explícita de equipo que incluya datos y puestos respaldados, antes de preparar la versión final. Esa acción humana confirma sólo los puestos efectivamente propuestos PRESENT y mostrados en esa misma solicitud canónica; nunca confirma ABSENT ni UNCERTAIN, ni actúa por un GET o un flag autoactivado. Mantener resolución humana individual de incertidumbres y ausencias. Corregir cualquier dato o fuente desmarca la confirmación. Se mantiene el plan congelado y la ceremonia WebAuthn vinculada exactamente a su hash.
7. Mostrar persona, puesto/campo y motivo para cada excepción, con navegación a su fuente. Mantener la corrección manual como recurso de excepción. Al preparar explícitamente el plan se habilitan las páginas protegidas existentes: no agregar un nuevo endpoint público ni escribir durante una consulta.
8. Conservar Gate, política, SQL, permisos, credenciales, vigencias, claves, separación de procesos y finalidad. Sin DDL, dependencias nuevas, OCR externo ni captura automática.

## Criterios de aceptación

- Un equipo con fuentes completas y alineación comprobada se prepara sin dibujar cajas ni reescribir valores. La persona contrasta el equipo y los puestos y confirma mediante passkey.
- Para cada campo automático se verifica página/slot correctos, coordenadas originales, tamaño, hash y vínculo al valor. Nunca invadir la ficha vecina ni convertir posición lógica no validada en evidencia geométrica.
- Los nombres, fechas y recortes correctos de River Plate quedan iguales. Una ausencia auténtica o incertidumbre se explica por persona, sin fabricar evidencia para declarar el equipo listo.
- La confirmación de presencia es una acción humana explícita y queda vinculada a la propuesta; consulta, preparación automática de coordenadas o edición no la pueden fabricar.
- Fuentes/páginas/slots/hash equivocados, ficha parcial/rotada/desplazada no validable, foto vacía, duplicados y datos faltantes siguen bloqueando donde corresponda.
- Una corrección manual no se pierde al consultar/preparar; una versión vieja no puede aprobarse.

## Archivos previstos

src/devnous/tournaments/core/ctt_batch_photo_alignment.py (helper de ficha separado de foto); src/devnous/copa_telmex/staff_review_plan_builder.py (propuestas automáticas/excepciones); staff_review_workflow.py para conservar la consulta sin staging; staff_review_routes.py para transportar la intención explícita y guardar observaciones técnicas sin fuente seleccionada; static/js/ctt_staff_review.js y templates/registration_review_detail.html (confirmación por equipo/pendientes/versionado de recurso). Pruebas correspondientes bajo tests/unit, tests/browser y pruebas PostgreSQL operacionales ya existentes. Ajustar inventario/hash de fixtures y cohorte CI si se modifica un artefacto registrado. Documentación de cierre en docs/roadmap.

## Pruebas y validación previstas

Pruebas de geometría a escala carta y otras resoluciones, cambios de escala/posición/inclinación dentro del soporte, límites completos, huecos de slots y páginas mal referenciadas; fuera de soporte queda pendiente. Separar verificación del rectángulo de la prueba semántica humana.

Pruebas de builder con fuentes reales sintéticas, geometría y CAS reales; valores/fotos sin cambios, respeto de correcciones y CURP opcional. Prueba browser de equipo completo sin seleccionar rectángulos y excepción navegable, confirmar puestos globalmente sólo por acción explícita, invalidez tras edición, sin aprobación de plan viejo. Prueba PostgreSQL/flujo completo de cuatro UID y WebAuthn sintético con fuentes automáticas; no captura en producción. Verificación privada del expediente real sin mutar sus valores ni firmar por la persona.

Revisión independiente de sólo lectura antes de PR. Formato/tipos, guardias/fixture manifests, cobertura modificada, tests unitarios/integración/browser y gates CI obligatorios. No ocultar fallos ni omisiones. Después de aprobación de diff y la autoridad vigente de publicación: despliegue acotado sobre el release servido, sin finanzas ajenas, verificando servicios/bytehash/recurso versionado; rollback con snapshot previo, sin revertir capturas humanas válidas.

## Canon y límites

Canon unchanged: el mecanismo genera propuestas de geometría que siguen exactamente la validación de bytes y fuente existente. No convierte OCR, una casilla o coordenadas en aprobación. Conserva contraste humano y firma. La frase del canon sobre rectángulos del navegador no restringe las fuentes a ese único origen. Cualquier cambio que altere esto vuelve a especificación y revisión humana; no se editará canon silenciosamente.

Aprobación humana: «ok, dale», 2026-11-04, después de presentar esta especificación y la confirmación global explícita de los puestos. La fase actual es implementación; no se afirma despliegue ni UAT.

## Revisión independiente del diseño

2026-11-04: authority_inventory revisó el documento sin escribir ni consultar producción. Diseño compatible con Gate; dos precisiones de semántica de presencia y alcance del clic global, y la vinculación física específica de staff, incorporadas arriba. Sin bloqueadores adicionales de diseño reportados. La aprobación técnica humana llegó después mediante «ok, dale».

Aprobación de continuidad: 2026-11-04, usuario «ok, dale» tras revisar esta especificación. Historia y especificación aprobadas; empieza implementación. Se conserva autoridad anterior hasta despliegue y la captura humana separada.

## Límites de excepciones de fuente

Una referencia física inválida o duplicada del manifiesto se identifica por persona y bloquea ejecución. La información válida de otras personas se conserva en la vista inicial, pero no se promete reparar una referencia firmada dibujando un recorte. Una página de jugador sin seleccionar sigue siendo rechazada por HTTP; la referencia se corrige en el ingreso gobernado de la fuente. Esto no afecta las referencias válidas del piloto. Los metadatos source_card/manual_fields/presence_proposed/photo_status son calculados por el servidor y no son campos que pueda enviar el navegador.

## Informe de construcción local — 2026-11-04

Implementación local terminada y revisada; CI, integración y despliegue se documentan por recibos posteriores. No captura productiva ni UAT humana. Se conserva el alcance aprobado y no se añaden DDL, dependencias, política, Gate, credenciales ni permisos.

Archivos de producto: staff_review_plan_builder.py, staff_review_routes.py, staff_review_workflow.py, ctt_batch_photo_alignment.py, static/js/ctt_staff_review.js y templates/registration_review_detail.html. Pruebas: test_ctt_staff_review_plan_builder.py, test_ctt_staff_review_routes.py, test_ctt_staff_review_workflow.py, test_ctt_batch_photo_alignment.py, test_ctt_review_workflow_postgres.py y test_ctt_staff_review_browser.py. Este informe completa los trece archivos del cambio.

Resultados finales: 133 unidades (99 builder + 34 workflow), 78 frontera HTTP, 63 geometría/fotos y 23 navegador: todos PASS, sin omisiones. Dos escenarios PostgreSQL operacionales de cuatro UID, strict y national: 2 PASS, sin omisiones. El nacional atraviesa preparación, WebAuthn sintético, Gate, Bus, writer y finalidad con una solicitud que sólo confirma puestos; no envía rectángulos de identidad ni reescribe personas. Cobertura builder 95.72%; geometría 89.01%. Los controles operativos puros adicionales: 23 PASS; no son un activador ni prueban recuperación de systemd.

Comandos: pytest tests/unit/test_ctt_staff_review_plan_builder.py tests/unit/test_ctt_staff_review_workflow.py con COVERAGE_FILE exclusivo y --cov al builder; pytest tests/unit/test_ctt_staff_review_routes.py --no-cov; pytest de test_ctt_batch_photo_alignment.py y las pruebas existentes de staff_capture_geometry/staff_photo_store con cobertura exclusiva; pytest tests/integration/test_ctt_review_workflow_postgres.py -k operational_rpc sobre PostgreSQL efímero; pytest tests/browser/test_ctt_staff_review_browser.py con dependencias FIDO2 existentes. Mypy selectivo de cuatro módulos con entorno de proyecto, Black --check, isort --check-only --profile black, flake8 con exclusiones E203/W503/E501 declaradas por el proyecto, node --check y git diff --check: PASS. Inventario de fixtures, superficie operacional y regresiones aceptadas: PASS. Los casos operacionales y los mínimos de cohortes existentes se conservan; no se modificó ni omitió Gate/fixture para hacerlos pasar.

Verificación privada del ejemplo, sin cambios de negocio: 18 fichas de River Plate (10 en frente incluyendo DT/auxiliar y 8 jugadoras en reverso), ambas páginas originales SHA verificadas, giros 180 rechazados. Repetida independientemente con OpenCV4.12.0/Numpy2.2.6 del entorno productivo: 0.432/0.457 segundos por página. Las ocho funciones y la clase de alineación fotográfica previas conservan AST idéntico al base; se conservan valores y fotos. No se publica imagen ni dato personal en estas pruebas versionadas. Revisión adversaria independiente cerró falsos soportes por papel gris/ruido, líneas interiores desplazadas y bordes exteriores desplazados: matrices interiores/ruido sin aceptaciones falsas y 48 extremos alterados rechazados. Es evidencia de los casos probados, no una certificación de todos los escaneos.

Fallos intermedios corregidos: recuperación que confundía papel con borde, elección de líneas internas, clipping por separadores desplazados y antialias de un píxel. El fixture nacional conservaba un texto técnico de la antigua prueba debajo de la tabla que invadía el marco; se omite sólo ese duplicado nacional, manteniendo el cuerpo técnico real arriba, strict y todas las aserciones. Una colisión de .coverage entre procesos se resolvió con archivos y reportes exclusivos. Una sonda de mypy con los stubs antiguos de producción marcó la rama None de OpenCV como inalcanzable incluso en el módulo ORIGINAL; se probó que HoughLinesP sí retorna None sin líneas. No se añadió supresión; el chequeo canónico del proyecto pasa sin errores y la ejecución real en producción fue verificada por separado. Sin fallos causados por el cambio pendientes al cierre local.

Validación independiente: cross_acceptance revisó backend/HTTP/geometría y repitió River con hash final; business_gate_builder revisó frontend. Root comprobó conjuntamente tipos, controles de liberación y el ejemplo con dependencias productivas. Dictámenes favorables acotados; no validación propia presentada como independiente. Hash de geometría congelado: 0708dee3df0d118dc87bfe88c12765cec82947a538a84a7b4c6c3c6341afdb10.

Precisión de confirmación: toda edición desmarca la confirmación GLOBAL y requiere una nueva autorización del plan. La confirmación individual anterior sólo puede conservarse si ese puesto, valores y fuentes son idénticos; modificarlo la invalida. Nunca sustituye la firma del nuevo plan. Las fotografías realmente ausentes o inciertas conservan la política y el pendiente identificado; no se inventa una imagen para habilitar captura.

Liberación prevista: rama/PR temporal, CI requerido, release acotado sobre c141 y artefacto privado clonado de87 con sólo cuatro módulos aprobados. Conservar bundles y photos/ no versionado mediante inventarios/hash y sincronización durante la pausa, además de todas las claves/política/intérpretes. Actualizar sólo workflow privado y público; restaurar ambos ante fallo previo a promoción pública. El reporte operativo posterior documentará hechos reales y desviaciones; no se promete atomicidad global de systemd ni vigencia hasta fsync.
