# Historia aprobada: selector contextual AMEX

Fecha: 2026-10-14. Historia y especificación aprobadas explícitamente por el usuario. Base investigada: `117dd8c1527f302f59e77060fb8831af8319f0c0`.

Como Finanzas, quiero que el selector de informes del estado de cuenta ofrezca sólo INFORMEs aprobados con al menos una partida activa AMEX de la tarjeta declarada y fecha dentro de su período, para preparar el informe correcto sin mezclar tarjetas o períodos.

## Aceptación aprobada

- Tarjeta explícita y fecha inclusiva coinciden en la misma partida, contra el catálogo declarado; reconocimiento con UUID contradictorio no constituye una opción elegible.
- No se infiere identidad por capturista, titular, persona, monto o CFDI. La política de beneficiario existente permanece intacta.
- Una coincidencia habilita la opción; se prepara el INFORME completo, incluidas sus otras tarjetas, fechas y bloqueos. Ninguna póliza se divide.
- Selección GET nueva se revalida con el mismo predicado. Fuentes históricas de vínculos persistidos conservan lectura y exportación sin ampliar el selector.
- Estado o tarjeta inválidos no producen un listado amplio como fallback. Se mantiene acceso a partidas sin tarjeta y a las excepciones existentes.
- Cargos originales, importes, asociaciones, contadores bancarios y owners financieros POST no cambian. GET no escribe.

Especificación vinculante: `docs/roadmap/amex-card-period-selector-20261014-spec.md`.

Canon unchanged: el cambio reduce descubrimiento de opciones mediante identidad y período ya declarados, sin cambiar autoridad, fuente contable report-first, política por beneficiario, reconocimiento único ni granularidad del INFORME completo. No requiere DDL, dependencias ni reparación productiva.

Implementación, validación, publicación, despliegue y aceptación Finance tienen evidencia separada. Esta copia documenta la aprobación de alcance, no la ejecución de efectos externos.
