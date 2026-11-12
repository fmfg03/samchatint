# Captura por lotes CTT: integración y condiciones de operación

Fecha: 2026-11-09. Estado: candidato en implementación y aceptación aislada.

La historia, especificación y texto de canon aprobados están en
`ctt-bulk-capture-20261109-story.md`, `ctt-bulk-capture-20261109.md` y
`ctt-bulk-capture-20261109-canon.md`. Esta nota describe la conexión del candidato;
no acredita un despliegue, admisión de fuentes ni facultad productiva.

## Conexión pública

`CTT_BATCH_REVIEW_ENABLED=0` es el valor predeterminado. La configuración explícita
requiere `CTT_BATCH_REVIEW_URL` (HTTP sobre una dirección loopback con puerto) y
`CTT_BATCH_REVIEW_KEY_PATH` (archivo privado con los controles del canal vigente).
El transporte tiene cliente y cierre propios. No modifica el cliente individual.

La entrada es `/registration-review-batches/new`; consulta el catálogo privado de
fuentes previamente admitidas. Todas las acciones vuelven a comprobar la cuenta,
el acceso y las facultades privadas; las mutaciones requieren origen y CSRF.
Los originales se muestran mediante un proxy PNG privado. Las rutas nunca exponen
rutas locales del CAS, firmas ni claves.

## Conexión privada

`assemble_batch_review_runtime` en `staff_batch_runtime.py` compone almacenes, proveedor
de fuentes, constructor de planes, verificador, cliente RPC del emisor, adaptador de captura y
canal. La importación y composición no ejecutan SQL, modelos, enrolamiento ni
capturas. El arranque de tareas es explícito. Los pools del flujo, verificador,
emisor y escritor mantienen sus funciones separadas; las migraciones no conceden
automáticamente permisos de ejecución ni facultades personales.

El emisor conserva su proceso y UID independiente. El workflow no recibe su
clave de firma ni su conexión SQL; usa sólo el canal autenticado y claves
públicas. Únicamente el launcher del emisor carga ese material privado.

Cada política conserva su hash completo y su manifiesto exacto. El perfil común
puede contener varias políticas vinculadas a distintos manifiestos. El escritor
selecciona la variante tipada V3; el flujo individual V2 conserva su contrato.

La fuente original, su página física, el índice lógico y el render se conservan
en cada referencia. El hash de la extracción admitida es distinto del hash del
plan final. El proveedor comprueba los originales antes y después de preparar
o verificar un plan, incluso al reutilizar observaciones retenidas.

Las observaciones estructurales del manifiesto fijan plantilla, categoría,
referencias y casillas físicas antes del análisis. Las dos vistas se rederivan
desde rectángulos del perfil y píxeles CAS; todos los espacios deben estar
contabilizados. Una foto omitida necesita evidencia de blanco en su región
fijada; una foto aportada se rederiva y compara con su región o casilla original.
Un resultado dudoso permanece como excepción.

## Secuencia de operación

1. Preparar y comprobar los manifiestos V2 y sus archivos exactos. La admisión
   firmada independiente sólo conserva fuentes; no crea equipos.
2. Configurar perfil, políticas, revisión del proveedor y precio/límites
   explícitos. Cada llamada real requiere reserva atómica previa. Un resultado
   de coste incierto conserva la reserva hasta conciliación.
3. Crear el recorrido idempotente y preparar análisis y borradores en la cola.
   Un análisis ausente permanece pendiente; no se sustituye por datos inventados.
4. Revisar la muestra determinista y las excepciones. Una discrepancia crítica
   invalida el grupo afectado y detiene su avance. Las correcciones parciales
   conservan las lecturas originales y no acreditan a toda una persona como
   revisada por un humano.
5. Autorizar mediante UV auténtica el bloque exacto. El primer bloque admite
   hasta cinco equipos; los siguientes, hasta cincuenta. El rendimiento mínimo
   del 80% usa la cohorte inicial congelada, no una precisión OCR declarada.
6. Consultar resultados por intento y conciliación. Un resultado incierto se
   consulta antes de reintentar; pausa y revocación respetan las capturas ya
   confirmadas. No se crean concesiones de elegibilidad externas.

## Validación y promoción

La CLI `scripts/prepare_ctt_batch_sources.py` recibe `--pdf-root`,
`--output-root` nuevo, `--edition`, `--observations`, `--profile` y
`--max-total-bytes` explícito. La edición es el objeto V2 completo; su campo
`phase` usa el identificador de manifiesto `national`. Las observaciones deben
cubrir exactamente todas las páginas originales. La CLI conserva copias privadas,
segmenta sin asumir pares, escribe manifiestos de hasta 300 documentos y conserva
las excepciones en `preparation-report.json`. No invoca proveedores, firma
admisiones ni modifica los originales.

`scripts/reconcile_ctt_batch_capture.py` compara manifiestos con exportaciones
privadas completas (`--run-export`, `--results-export`, uno o más `--manifest` y
`--output`). Rechaza paginación incompleta y resultados duplicados; informa
resultados faltantes, pendientes y excepciones. Es una comprobación de
consistencia de exportaciones, no una consulta a la base vigente ni una aceptación
humana. El reporte lo identifica explícitamente con `database_verified=false`.

La composición en memoria, las pruebas de navegador y la instalación SQL aislada
no sustituyen la aceptación integrada con UV, escritor y finalidad reales.
La hoja de extracción con API y su configuración requieren terminarse antes de
ofrecer el recorrido con los consolidados reales. No se han ejecutado llamadas
de IA ni capturas productivas durante la construcción.

El editor actual resuelve excepciones textuales con fuentes fijas. La resolución
humana V3 de presencia, fotografía o geometría dudosas sigue pendiente; esos
casos permanecen bloqueados. Una plantilla con espacios interiores no compatibles
con el índice físico fijado tampoco se compacta ni se adivina: requiere mejorar
el perfil o su recorrido de revisión.

Una promoción requiere el diff final y sus comprobaciones, la aprobación que
corresponda a commit/publicación/despliegue y un plan operativo con reversión.
La autoridad del revisor y la admisión exacta de fuentes se comprueban aparte.


## Preparación previa al documento: ajuste aprobado 2026-11-12

El ajuste y texto literal están en `ctt-source-preparation-20261112.md` y su
recibo `ctt-source-preparation-20261112-approval.json`. La elección de reutilizar
la credencial existente y el techo compartido de US$25 ya están aprobados.
Las fuentes/perfiles/quotes exactos, migraciones, promoción y capturas reales
mantienen sus controles independientes. No se han gastado fondos por aprobar
el diseño.

La CLI offline admite `--inventory-only --render-profile <json>` para conservar
cada PDF y render original con su censo completo. El contrato de inventario no
contiene equipos o documentos de captura y su identidad es el SHA-256 de sus
bytes JSON canónicos. Nunca se identifica una categoría por el filename.

Después de la admisión por el operador, el modo privado `--prepare-sources`
observa las páginas usando el presupuesto SQL compartido. Cada número impreso
necesita su rectángulo ORIGINAL y píxeles; un reverso sin encabezado conserva
sus valores brutos nulos y una referencia explícita al frente. Plantilla,
casillas o continuidad inciertas quedan como excepciones y todas las páginas
permanecen en el censo. Los detectores de rejilla no acreditan por sí solos
cobertura completa. El perfil y sus muestras de calibración reales aún deben
calificarse; las pruebas sintéticas no acreditan rendimiento del corpus.

Para generar manifiestos propuestos desde la exportación privada:

```text
python scripts/prepare_ctt_batch_sources.py --pdf-root <inventory-root>/originals --output-root <new-private-package> --edition <full-edition-json> --observations <export>/source-observations.json --source-inventory <export>/source-inventory.json --profile <export>/structural-profile.json --max-total-bytes <explicit-limit>
```

El envelope, perfil e inventario deben coincidir por hash, edición, censo,
render y bytes de los originales. El paquete retiene esos artefactos y la
procedencia/observaciones de cada expediente; admite como máximo 300 documentos
por manifiesto. Las observaciones desconocidas no se borran ni se convierten
en equipos. El resultado no se admite automáticamente ni activa capturas.

La nueva migración privada es `20261112_ctt_batch_source_preparation.sql` y su
rollback correspondiente. Conserva el owner NOLOGIN y las firmas de reserva
legacy. Las corridas que solapen cualquier PDF del inventario compartido no
pueden recurrir al presupuesto legacy si omiten el vínculo nuevo, aunque el
presupuesto esté vencido/revocado. Vincular exige el perfil de extracción real,
precio exacto y cero reservas previas; el vínculo es inmutable. Las reservas
inciertas se mantienen completas y el rollback rechaza evidencia utilizada.

Límite de convivencia ya observado: un intento individual V2 que pierde una
carrera contra el lote por la misma fuente queda `uncertain`; SQL rechaza el
conflicto físico y su reintento, sin duplicar equipo o personas. No se afirma
que V2 recupere un resultado ajeno ni cambie a `stale`.
