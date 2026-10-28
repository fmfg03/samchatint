# Cashflow001 final release build

Base exacta:f8c9c11b0. Fuente aprobada y revisada:PR513/40eb92864 (runtime idéntico a a43ececee). Las tres observaciones de revisión están corregidas y los hilos resueltos tras publicar la corrección. El release conserva la corrección contable concurrente y los propietarios de CxC, presupuesto, CFDI y contabilidad histórica.

Ocho archivos runtime: tres módulos cashflow; sólo los handlers cashflow en admin_routes/user_routes; statement ejecutivo; dos renderers cashflow de finance_read_answer; y exclusivamente helper/imports+dos ramas cashflow de finance_read_adapter. El resto del AST de archivos compartidos coincide con producción. No hay migración, escritura financiera, nueva dependencia ni ampliación de permisos. Canon unchanged, hashes verificados.

Validación de este release:259pruebas aprobadas,1omisión documentada del candidato futuro multiempresa no instalado;3PostgreSQL aisladas y2navegador;91.73%cobertura medida incluyendo adapter.135private-plugin aprobadas, inventario613/578/44/sin gaps. El encoder también se probó con el intérprete de producción; guardaDecimal fuente y produce JSON estricto para NaN/Inf. Ambos guards de release pasaron.234regresiones de propietarios preservados y20casos de la corrección contable fresca pasaron en las preparaciones previas, cuyos owners se conservan byte a byte.

Pruebas nuevas o actualizadas cubren importes exactos, no doble conteo, faltantes/N/D, moneda/empresa/año-mes, bancos completos, aislamiento transaccional, planes semanales efectivos, filtros HTML/XLSX, contexto de edición, proyectos sin obligaciones, JSON/JSONB finito/no finito y navegación móvil/escritorio. Comandos completos y logs están en los recibos acceptance/private-plugin, verify_acceptance.py y provenance del mismo directorio.

Fallos causados por la tarea y corregidos: conteo del índice durante merge; navegación/clase de tablas; edición descartada por formulario; opciones limitadas a obligaciones; Decimal/no finitos en JSON del asistente. La deuda global de tipos/lint no se declara limpia; las comprobaciones de tipos/formato se acotaron al cambio. No hay desviación de reglas financieras aprobadas. T04 registró el consumidor JSON antes de editarlo.

Pendientes previos a completar entrega: nuevo CI global, merge, selección canónica de este release y verificación de salud/readiness/autenticación/journal. UAT autenticada de Finanzas y aceptación de datos reales siguen separadas. Apertura/cierre y totales AR sin evidencia acreditada permanecen N/D. Rollback de código/configuración conserva la versión f8c9; no se requiere reversión de datos.

Corrección posterior exclusiva de pruebas: reconocer JSONB nativo de SQLAlchemy2.1 sin procesador de enlace, verificando el tipo real y JSON estricto en ambos drivers. La declaración y el código runtime no cambian; el nuevo CI sigue siendo requisito previo al merge.
