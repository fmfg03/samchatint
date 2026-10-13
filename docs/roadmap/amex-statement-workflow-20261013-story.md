# Propuesta: AMEX desde estado de cuenta hasta pólizas

Estado: historia y recorrido aprobados por el usuario el 2026-10-13 con «aprobada»; especificación técnica y textos de canon aprobados explícitamente por el usuario el 2026-10-13. Sin implementación ni cambios productivos. Amplía el alcance de PR480 únicamente para ordenar navegación y separar pendientes de comprobación de la vía por informe. La aprobación anterior de PR480 no cubre este rediseño.

Usuario confirmó: retirar informes y partidas de Odilón de pendientes AMEX, manteniendo visibles los cargos bancarios. Pide un camino claro entre captura de estado y póliza. Runtime confirmado4b971ab3f, canon íntegro y servicio sano.

Historia: como responsable de Contabilidad quiero trabajar un estado de cuenta por tarjeta/periodo en una sola ruta ordenada, ver solo los pendientes que requieren mi intervención y descargar las pólizas listas, para no repetir la comprobación realizada en cuentas de gastos ni navegar entre múltiples pantallas.

Flujo propuesto:
1. Cargar estado: tarjeta, periodo y archivo; resumen de cargos/total y resultado de importación. Botón Revisar pendientes. No origina póliza por importación.
2. Revisar pendientes: pendientes reales como soporte fiscal, clasificación/cuenta o identidad. Cada bloqueo explica causa y ofrece acción contextual; asuntos ya resueltos salen del contador. Comprobación de Odilón se atiende desde sus cuentas/informes, fuera de esta cola. Estado bancario y soporte por informe conservan identidades separadas; ocultar de cola no significa conciliado, pagado ni contabilizado.
3. Preparar y confirmar pólizas: vista completa de informes/fuentes, movimientos contables, total y bloqueos. Finanzas confirma explícitamente y descarga archivo o paquete COI. Conserva una póliza inicial completa por INFORME y sus ajustes; no promete una única póliza por estado bancario ni una creación silenciosa.

Inicio: tabla de estados por tarjeta/periodo con avance y un botón Continuar. Etapas Cargado/Pendientes/Listo para pólizas/Pólizas generadas; exportación descargada no se presenta como importación aceptada por COI. Sin salto automático de autoridad o evidencia.

Aceptación de exclusión:
- Beneficiario efectivo confirmado de Odilón no aparece como informe/partida a conciliar ni suma al contador de pendientes en móduloAMEX.
- Sus informes conservan acceso desde Cuentas de gastos/Informes para trabajo contable; datos, fiscalidad, cortes y auditoría se preservan.
- Cargos importados del banco permanecen visibles y conservan su estado real; sin matching por importe/fecha ni marcas ficticias.
- Exclusión usa identidad explícita del beneficiario, no nombre textual, capturista ni aprobador. Beneficiario faltante/ambiguo permanece visible con motivo.
- Gastos de otros beneficiarios mantienen atención normal; el control bancario no se borra.

Evidencia read-only: nueve de los diez informes aportados tienen beneficiario de documento y cuenta JOSE ODILON TRUJILLO MACEDO (87 partidas marcadasAMEX); el titular/capturista de esas cuentas es Alicia. I-138907 tiene beneficiaria BIBIANA RAQUEL ROMAN ARGUELLES (3 partidasAMEX). No debe excluirse como Odilón sin una corrección/decisión explícita aparte. La consulta inicial de identidad intentó unaccent, que no está instalado; se repitió con ILIKE acotado sin cambios de esquema, y terminó correctamente.

Puntos de implementación a especificar tras aprobar historia: política explícita de atención por beneficiario; filtro común en descubrimiento/contadores/selectores; acceso al soporte fuera de cola; navegación por estado y conservación de bloqueos; relación de estado importado con múltiples informes/pólizas sin cambios de granularity. Fuentes actuales: amex_conciliacion_view y _load_amex_bank_report_context en user_routes.py, amex_accounting_reports en admin_amex_accounting_routes.py, propietario financiero y cortecanonical. Pruebas actuales: AMEX identity/routes y report-first PostgreSQL; añadir negativas de capturista/beneficiario ambiguo/otro beneficiario y browser del recorrido.

Canon: evaluar en especificación; el nuevo recorrido y la regla de atención requerirán revisión humana si modifican el contrato productivo. No editar canon automáticamente. Sin reparaciones históricas, creación de pólizas de prueba, DDL, publicación Git ni deploy en esta etapa.
