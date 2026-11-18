# Propuesta acotada: observación visual antes de admitir expedientes

2026-11-10. Estado: propuesta pendiente de revisión y aprobación del ajuste afectado. No concede presupuesto, admisión de fuentes, facultad de captura ni despliegue. La reutilización de la credencial existente ya está autorizada y no se pregunta otra vez.

## Problema comprobado

La especificación aprobada exige inventario → segmentación → extracción. `scripts/prepare_ctt_batch_sources.py` ya conserva todos los originales y prepara manifiestos a partir de observaciones estructurales completas, pero todavía exige que esas observaciones existan. El productor visual nuevo opera después de admitir documentos agrupados. `batch_reserve_budget` sólo reserva para un `review_batch_run` ligado a manifiestos de documentos admitidos. No puede financiar legítimamente la lectura visual previa que permite construirlos.

En el corpus de 16 PDF/771 páginas, el texto nativo no recupera de forma fiable las cabeceras, categorías y casillas completas. Las 259 páginas no clasificadas del preflight son una limitación de aquella heurística; no son equipos inexistentes. No se solucionará suponiendo pares, usando el nombre del archivo para el género/categoría, creando un equipo por página ni rellenando numeraciones ausentes.

## Comportamiento propuesto

1. Preparar un inventario privado de PDF originales, hash, tamaño, número de páginas y hashes de render/geometría. Usar un contrato distinto `ctt.batch.source-inventory.v1`: no tiene equipos, documentos de captura, política nacional ni conteos de equipos. La integridad se verifica antes y después de cada lectura.
2. El operador admite para observación sólo ese inventario, un perfil estructural versionado, modelo/cotización y un límite monetario explícito. La preparación no requiere ni crea consentimiento WebAuthn de captura. No puede producir hijos, calificar equipos ni escribir personas.
3. Un modo privado de preparación del workflow existente ejecuta lecturas visuales limitadas sobre páginas/cabeceras originales, conserva las respuestas y referencias exactas, y clasifica cabecera/continuación/otra fuente/desconocida. El texto nativo aporta anclas verificables. La confianza del modelo nunca sustituye evidencia de plantilla, casillas o continuidad. Una agrupación ambigua queda como excepción; los originales y todas sus páginas permanecen contabilizados.
4. El segmentador determinista existente consume esas observaciones y genera los manifiestos v2 propuestos. El operador admite por separado los manifiestos/políticas exactos resultantes. La etapa posterior existente conserva muestra congelada, dos lecturas de campos, candidato, UV por bloque y captura transaccional. Ninguna observación previa se transforma por sí sola en autoridad de captura.
5. Reutilizar la persistencia y el propietario SQL privados existentes para un límite compartido de preparación y extracción. Registrar un presupuesto admitido por hash y su asociación explícita a las preparaciones/corridas. Reservar bajo bloqueo antes de cada petición; la suma de costes conocidos y reservas pendientes de todas las etapas no supera el límite global. Un coste desconocido conserva la reserva completa. No usar un segundo libro monetario local ni un presupuesto nuevo por reinicio/corrida.

## Cambio respecto del detalle técnico aprobado

Añade un contrato de inventario y estado privado de preparación previo a la admisión de documentos; amplía el alcance de reserva presupuestaria para esa etapa de sólo observación. La especificación §5 limita actualmente el presupuesto común por corrida: un techo compartido entre preparación y varias corridas es también parte explícita de este ajuste, no una interpretación de la aprobación anterior. Esto cambia el modelo de datos y ciclo de preparación, aunque conserva los controles de captura ya aprobados. Requiere aprobar únicamente este ajuste conforme a Approval Continuity de AGENTS.md; no repetir la aprobación de historia, especificación original ni reutilización de credencial.

No requiere nuevas dependencias, servicios, roles públicos o proveedores. El verificador privado existente conserva la ejecución API; el operador admite inventario/presupuesto. Nuevas funciones tipadas distinguen preparación de captura y no aceptan un identificador de preparación donde se exige una corrida/miembro/consentimiento.

## Alcance de archivos propuesto

- `scripts/prepare_ctt_batch_sources.py`: modos explícitos de inventario y consumo de observaciones; conservar el modo actual sin llamadas como default.
- Nuevos módulos privados `staff_batch_inventory.py` y `ctt_batch_structural_extraction.py`: integridad, observaciones estrictas, llamadas acotadas y caché por todos los hashes/versiones.
- `staff_batch_source_service.py`, `staff_batch_store.py`, `staff_batch_analysis_runtime.py` y runtime privado: montaje de preparación explícita sin activar captura.
- Migración aditiva nueva y rollback seguro para inventarios/preparaciones y vínculo presupuestario compartido. Conservar evidencia previa y contratos existentes; las reservas sólo pueden pertenecer a una etapa tipada y un presupuesto admitido.
- Pruebas unitarias y PostgreSQL correspondientes, documentación de operación y revisión humana del texto de canon si el cambio de ciclo lo requiere.

## Criterios de aceptación del ajuste

- Cada página original aparece exactamente una vez en el censo; originales alterados, páginas extra o manifiestos cambiados rechazan la lectura.
- Presupuesto/perfil/inventario ausentes, revocados, vencidos o sin vínculo exacto: cero llamadas. Reservas concurrentes, reinicio y varias corridas no duplican el límite. Tier Standard explícito, reintentos automáticos cero y coste incierto retenido.
- Continuidad entre PDF sólo con evidencia; desconocido es excepción. Ninguna inferencia por filename ni truncado de casillas. Pruebas con orden alterado, páginas impares, campos vacíos y cabeceras fragmentadas.
- La preparación jamás crea `review_batch_members`, equipos, personas, calificaciones, capacidades o consentimientos. Los endpoints y SQL de captura rechazan sus identificadores.
- Manifiestos generados siguen requiriendo admisión separada. La muestra y los mínimos operativos no se alteran para ocultar las páginas ambiguas.
- Un ensayo real con gasto autorizado produce sólo informe de clasificación y agrupación propuesta. No se declara el número real de equipos ni rendimiento automático antes de medirlo.

## Techo de consumo propuesto para esta misma aprobación; admisión operativa posterior

No se han realizado llamadas pagadas. Se propone cotizar un snapshot vigente de OpenAI con imagen y salidas estructuradas, sin reutilizar un alias ni un modelo retirado. Antes de la primera petición se fijan el snapshot exacto, precios, límite máximo por petición y techo total aprobado; se entrega un recibo con costes y reservas. No se promete que un techo concreto alcance para las 771 páginas o los 300 equipos antes del ensayo.

### Propuesta concreta de techo inicial (no admitida)

Para una aprobación acotada, proponer **US$25 de consumo API calculado con la cotización admitida**, compartidos por observación estructural y extracción, con parada antes de excederlos. No implica que todo el corpus quepa en esa cantidad. Si se agota, se entrega progreso, coste y reservas pendientes sin renovar el techo.

Candidato comprobado en documentación oficial: `gpt-5.4-mini-2026-03-17`, Standard, contexto máximo 400.000 tokens; precios por millón de tokens de entrada US$0,75, entrada cacheada US$0,075 y salida US$4,50. Referencias: [modelo](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [precios](https://developers.openai.com/api/docs/pricing). La cotización se vuelve a verificar y admite por hash antes del ensayo; no se usa un alias ni se cambia el snapshot automáticamente.

Representación propuesta: moneda USD, unidad microUSD, techo compartido 25.000.000; entrada 750.000/cache 75.000/salida 4.500.000 microUSD por millón. Con salida máxima 8.000 y cota conservadora de entrada de 400.000, cada llamada requiere reservar al menos 336.000 microUSD (US$0,336). El precio del uso real verificable libera la diferencia; sin recibo coherente, se conserva esa reserva y se detiene el lector. Se fijan endpoint oficial, nivel `default`, sin reintentos automáticos y vencimiento explícito de la admisión. Esto no autoriza cambiar ajustes de facturación de la cuenta.

## Textos literales de canon propuestos para la misma revisión

No aplicados. Si se aprueba este ajuste, se agregarán como enmiendas fechadas 2026-11-10 con motivo «resolver preparación visual anterior a la admisión de documentos», preservando el texto anterior y actualizando sus hashes en el registro. La aprobación del texto no afirma implementación, gasto ejecutado, admisión productiva ni despliegue.

### Producto — adición literal

El diseño de captura por lotes contempla una preparación visual privada del inventario original antes de agrupar expedientes. Esa etapa puede proponer límites y continuidad de cédulas; no crea equipos, personas, elegibilidad, facultades o consentimientos. Las páginas no resueltas permanecen visibles como excepciones. Los manifiestos y políticas resultantes requieren admisión independiente antes de calificar o capturar; el contraste visual previo no sustituye la muestra humana ni la autorización por bloque.

### Ingeniería — adición literal

La preparación previa usa un contrato de inventario tipado, separado del manifiesto de documentos, y observaciones ligadas a PDF, página, render y perfil exactos. No crea miembros, candidatos de calificación o autorizaciones de captura. El presupuesto admitido puede vincular esa preparación y las corridas de extracción posteriores a un techo monetario único: las reservas y costes se acumulan entre etapas y corridas bajo bloqueo SQL, sin reiniciar el límite al reintentar o crear otra corrida. La ausencia, revocación, vencimiento o discordancia de admisión detiene las llamadas. El uso incierto conserva su reserva. El proveedor, snapshot, cotización y cotas se fijan antes del primer uso; no se sustituyen silenciosamente. El segmentador conserva ambigüedades y el censo completo; ninguna salida del modelo admite fuentes por sí sola.

### Sweep/evidencia — adición literal

La revisión del incremento de lector API y correcciones no textuales identificó un hueco entre el inventario físico y la admisión de documentos agrupados: el texto nativo no basta para observar de forma fiable la estructura de todo el corpus. El ajuste de preparación visual y presupuesto compartido es un diseño sujeto a aprobación e implementación separadas. Las pruebas sintéticas del lector, fotografías y correcciones con nueva UV no prueban calidad o rendimiento del corpus real. Los recibos posteriores distinguirán implementación, prueba documental, coste, admisión, calificación y captura.
