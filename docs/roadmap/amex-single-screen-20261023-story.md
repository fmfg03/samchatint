# AMEX: un estado, una pantalla

Fecha: 2026-10-23. Base investigada: f15e9a47650913097cfb63b2859dfe76d17e7f1c. El usuario aprobó la propuesta de tres etapas y después «aprobado hasta despliegue». Historia y ejecución integral aprobadas; la especificación deriva exclusivamente ese alcance. No se solicitará nuevamente autorización dentro de él.

Como operador de Contabilidad, quiero cargar un estado, resolver cargos y obtener su póliza dentro de la misma pantalla, mediante panel lateral y ventanas de revisión, conservando el trabajo que llevo, para completar AMEX sin recorrer páginas auxiliares ni recibir instrucciones técnicas. Juan Pablo conserva su acceso separado para asignar partidas.

## Aceptación

1. La carga mantiene archivo, tarjeta, titular, período, moneda, perfil de signos, previsualización y confirmación explícitos. Su resultado reemplaza la zona de trabajo sin recargar el documento; la URL adopta la identidad canónica del estado recién creado.
2. Una tabla central conserva todos los movimientos, sus disposiciones y evidencia. Filtros, selección elegible por ID y posición se conservan al consultar, guardar o actualizar; no se restauran versiones/hashes antiguos.
3. Las sugerencias/búsqueda/paginación de CFDI se resuelven en un panel lateral dentro del mismo estado. Consultar vigencia requiere el consentimiento existente; vincular conserva la revisión de factura, importes, propina/uso compartido, motivo y confirmación.
4. Tratamiento individual/lote, retiro, proyecto cuando corresponda y Sin CFDI usan ventanas. Sólo las filas seleccionadas se envían. Se mantienen preview, motivo, nonce, versiones, idempotencia y confirmaciones existentes.
5. Prestaciones conservan el catálogo instalado de administrativas y viáticos, sin proyecto operativo. Deudores no pasan a presupuesto. Los controles que no correspondan al tratamiento permanecen fuera de su formulario.
6. Contabilidad envía las prestaciones/proyectos elegibles y permanece en el estado. Juan Pablo conserva su cola y autoridad separadas. La lectura de avance en primer plano refleja su asignación sin ejecutar efectos financieros.
7. La revisión de póliza, bloqueos, fecha, motivo, confirmación y descargas se resuelven en el estado. Sólo el owner contable decide disponibilidad; no hay nueva póliza parcial ni edición de fuente congelada.
8. Un error, pérdida de sesión, conflicto o respuesta tardía se muestra dentro del contexto correcto. No se pierden datos de captura; los conflictos obligan a revisar evidencia/versión actual y nunca reenvían un POST automáticamente.
9. Ventanas accesibles con título/foco/cierre/Escape, foco de retorno, botones pendientes deshabilitados y mensajes visibles. No se permite fingir cancelación de una mutación en curso. Escritorio y móvil conservan el estilo de SamChat.
10. Prueba técnica con navegador: carga → cargos con/sin CFDI → prestación/deudor/proyecto → intervención CP desde otro acceso → póliza/Excel, sin navegación del documento principal salvo la entrada inicial. Evidencia sintética/aislada no se presentará como UAT autenticada de Daniel ni aceptación COI externa.

## Fuera de alcance

Reglas fiscales/matching/signos, automatización PASE, nuevos permisos/cuentas/catálogos, cambios de Juan Pablo, pago bancario, backend financiero, esquemas/migraciones/dependencias/frameworks, reclasificación o pólizas de prueba en producción.
