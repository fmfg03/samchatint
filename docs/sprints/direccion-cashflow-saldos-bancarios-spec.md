# Saldos bancarios en Flujo de efectivo de Dirección

Fecha 2026-11-18. Alcance funcional y especificación técnica aprobados por el usuario. Candidato de implementación para revisión final.

## Resultado esperado

Dirección puede consultar en `/direccion/inicio` la apertura, los abonos, los cargos y el cierre bancario del periodo seleccionado. El cierre debe cuadrar contra el banco y cada cifra debe indicar su fuente, moneda y periodo. Si falta evidencia o autorización, el reporte muestra el motivo y deja pendiente la cifra afectada.

## Fuentes verificadas

- `samchat-gastos.service` sirve Dirección desde `gastos-prod-715036dac-ctt-bulk-532`. `client_executive/home.py` genera los reportes a partir del periodo sin suministrar datos bancarios a `report_layouts.py`.
- En el PostgreSQL del servicio activo, una consulta agregada de solo lectura encontró 1,658 movimientos con `saldo`, de una cuenta, en siete lotes. Hay movimientos en febrero, julio, agosto, septiembre y octubre de 2026. El último movimiento es del 30 de octubre. Las fechas de movimiento por sí solas dejan sin acreditar las fechas de cobertura de cada extracto.
- Tesorería ya importa los movimientos en `bank_movements`, con fecha, hora, renglón, signo, importe, saldo y referencia al lote. `accounting_import_runs` registra el estado, el hash y la auditoría de la carga. El importador identifica filas por archivo y renglón al reimportar, por lo que una certificación debe vincularse al hash y al contenido vigente del lote. La tabla bancaria de producción carece de `company_id`, `bank_account_id` y `currency`.

## Contrato de evidencia y autorización

1. Reutilizar los estados de cuenta y los movimientos importados en PostgreSQL. Añadir un registro acotado de extractos acreditados por Tesorería, con migración aditiva y reversión. Cada registro vincula un lote bancario aplicado y completado, el hash del archivo, una huella de su serie bancaria, la identidad interna de la cuenta y empresa, la moneda, las fechas de cobertura certificadas, las anclas de apertura y cierre que constan en el extracto o su referencia documental, y actor y fecha de acreditación. El registro permite revocación auditada. El acceso ordinario de la aplicación a este registro es de lectura. La carga inicial queda sujeta a autorización operativa aparte.
2. Vincular cada certificación al alcance exacto de una cartera activa y al conjunto de torneos cuyo saldo cubre, con atribución avalada por Tesorería. Reutilizar identidades canónicas de empresa y cuenta cuando estén disponibles y exigir evidencia de titularidad en el piloto legado. Un conjunto de torneos autorizado es requisito para la consulta, pero por sí solo no acredita la atribución de una cuenta. Una cuenta compartida queda fuera del saldo de cualquier subconjunto de torneos. Si el saldo acreditado cubre una cuenta, mostrarlo como saldo de esa cuenta con identificación enmascarada. Etiquetar como total del alcance únicamente cuando Tesorería haya acreditado que el conjunto de cuentas cubre dicho total.
3. Consultar sólo después de resolver los UUID autorizados y el permiso financiero. Una denegación específica impide cargar el dato bancario. La supervisión de `superadmin` sigue el mismo requisito de atribución. En Sam y exportaciones, revalidar alcance, permiso y vigencia de la acreditación en el servidor. Si cambió la evidencia, pedir actualizar el tablero. Usar referencias opacas, fechas, moneda e identificación enmascarada en el resultado. Mantener los CSV, números de cuenta completos y filas bancarias originales fuera del contexto de navegador, Sam, exportaciones y logs.
4. Si se vuelve a importar un lote, la proyección debe comprobar que el lote certificado sigue siendo el vigente y que su serie coincide con la huella acreditada. Evidencias repetidas se cuentan una sola vez. Evidencias contradictorias, filas de orden indeterminado o movimientos solapados que no se puedan identificar de manera inequívoca dejan el periodo pendiente. El registro y la validación deben ser idempotentes por identidad de la fuente y alcance certificado.

## Reglas de cálculo

- Trabajar en MXN con `Decimal` a centavos. Una fuente de otra moneda requiere su propio soporte y queda pendiente en esta vista. El formato de Dirección convierte a miles de MXN una sola vez, después de calcular y cuadrar el periodo.
- Para la apertura, usar el saldo inicial acreditado para esa fecha o el cierre anterior verificado de la misma cuenta, siempre que la cobertura intermedia sea continua. Para un corte dentro de un extracto acreditado, avanzar desde su apertura hasta el día anterior al corte usando la serie validada. Los días sin movimiento sólo conservan el saldo cuando están dentro de una cobertura acreditada. La primera o la última fila por sí sola carece de valor como prueba de cobertura.
- Ordenar los movimientos según la secuencia acreditada del extracto. Verificar que cada saldo de línea concuerda con el saldo anterior y el importe con su signo. Sumar abonos y cargos del periodo una vez por movimiento. Cuadrar `apertura + abonos − cargos` contra el saldo bancario al corte. Usar el cierre explícito del extracto cuando coincide con el corte, o el último saldo de línea dentro de una cobertura continua acreditada. Conservar cero como saldo válido y dejar pendiente la cifra sin respaldo. Un periodo con apertura probada y cierre fuera de cobertura puede mostrar la apertura con los demás importes pendientes.
- Atribuir los totales al mismo conjunto de cuentas y fechas. Para periodos consecutivos, verificar el enlace entre el cierre y la siguiente apertura. En acumulados, tomar saldos de los extremos del intervalo y sumar sólo los movimientos con cobertura de ese intervalo.

## Integración prevista

- Añadir un lector bancario acotado en `src/samchat/cashflow/`, cerca de las fuentes existentes, y actualizar el modelo de persistencia en `src/devnous/gastos/models.py` y `database/migrations/`. El lector recibe periodo y alcance ya resuelto, consulta en lote y devuelve importes, estado, motivo y evidencia reducida. La cuenta y el lote se verifican en el servidor. Sin dependencias nuevas ni trabajos de fondo.
- Pasar la proyección a `src/samchat/client_executive/home.py` antes de firmar el snapshot. En `report_layouts.py` y `home_ui.py`, mostrar el puente con **Saldo inicial**, **Abonos bancarios**, **Cargos bancarios** y **Saldo final**, más las brechas por valor. El puente puede acreditarse con totales bancarios aunque las seis partidas de orígenes y aplicaciones sigan pendientes de clasificación. Mantener esas partidas disponibles para las fuentes que las acrediten y reutilizar el contrato de periodos explícitos para las comparaciones.
- Mantener las rutas actuales de Dirección. Ajustar el consumo de la evidencia en `client_executive_routes.py`, `src/samchat/executive/exporter.py` y, si el consumidor lo requiere, `client_executive/conversation.py`. PDF, Excel y Sam consumen el snapshot reducido firmado en la misma unidad y con las mismas brechas que la pantalla. No se introduce un endpoint de carga ni un formulario de captura en Dirección.
- Las respuestas de Sam sobre una celda bancaria acreditada se mantienen en la consulta contextual firmada. El historial general del asistente carece de las verificaciones de alcance y revocación de Dirección. Excel conserva como texto exacto los saldos que exceden la precisión de una celda numérica.
- El alcance de esta entrega es Flujo de efectivo en Dirección y sus salidas existentes. La clasificación por concepto, la publicación de reportes, los cambios al importador, la conciliación contable, los tableros operativos de Tesorería y Presupuesto vs Real quedan fuera del alcance aprobado.

## Verificación de aceptación

| Caso | Evidencia esperada |
| --- | --- |
| AC-01 Extracto completo | Importar filas representativas en PostgreSQL aislado. La apertura, los abonos, los cargos y el cierre reproducen el extracto al centavo y conservan la moneda y el corte. |
| AC-02 Corte y continuidad | Verificar un corte dentro del extracto, apertura igual a cero, un intervalo cubierto sin movimientos, enlace de meses contiguos y un acumulado sin suma de saldos. Un hueco o un periodo futuro sin respaldo conserva las brechas. |
| AC-03 Calidad y reimportación | Verificar duplicados, lote reemplazado, dato faltante, orden ambiguo, importe inválido y desacuerdo con el cierre. Sin doble conteo ni total certificado cuando el dato sea inconsistente. |
| AC-04 Autorización | Probar cartera permitida, denegación financiera, cuenta compartida en un torneo, empresa o moneda distinta, revocación y cambio de atribución con snapshot ya emitido. Verificar que la información bancaria completa no sale del servidor. |
| AC-05 Consistencia | Comparar los valores y las brechas de pantalla, PDF, Excel y Sam. Revisar datos previos sin certificación y conservar la respuesta pendiente correspondiente. |

Ejecutar pruebas unitarias de cálculo y contratos, integración PostgreSQL para migración y consultas, y aceptación de la ruta y sus salidas. Para el primer periodo real, Tesorería verifica extracto, moneda, alcance y saldos contra la lectura en modo de solo lectura. La presencia de filas entre dos fechas es un inventario, no una acreditación del extracto.

## Despliegue, reversión y canon

Preparar y probar la migración y su reversión en una base aislada. Aplicación de esquema, acreditación inicial y despliegue productivo requieren sus autorizaciones operativas. El proceso web carece de DDL al iniciar. Para revertir la aplicación, restablecer la versión anterior y conservar los registros auditados sin lectura desde Dirección. Si se autoriza retirar el esquema, exportar primero las certificaciones y aplicar la reversión controlada. La migración no modifica los movimientos importados.

### Acreditación de un extracto

1. El propietario de la base aplica `database/migrations/20261118_direction_bank_statement_certifications.sql`. En la misma sesión establece `samchat.direction_bank_reader` con el rol web verificado y comprueba que ese rol tiene `SELECT` sobre el registro. Mantener esa configuración al reaplicar la migración.
2. Tesorería coteja el original con el SHA-256 del lote aplicado. Registra las fechas que declara el extracto, la cuenta, su titular, la moneda, los renglones en el orden del archivo, las anclas respaldadas y la referencia documental privada. Comprueba pertenencia activa de los torneos a la cartera y acredita si la cuenta cubre el conjunto completo o es sólo una cuenta del alcance.
3. Para registrar un extracto autorizado, usar el SHA-256 de la cuenta normalizada guardada en `bank_movements` como `account_fingerprint`. Calcular `series_sha256` con `bank_series_sha256()` sobre las filas vigentes de ese lote y cuenta leídas desde PostgreSQL, usando `source_row_number`, `fecha`, `hora`, `signo`, `importe` y `saldo`. Si el archivo trae el movimiento más reciente primero, registrar `descending_rows = true`. Registrar `scope_account_ids` sólo para un total acreditado y consignar el mismo conjunto completo en todas las certificaciones de dicho total.
4. Cualquier reemplazo cambia la revisión del reporte. Para rectificar una certificación, registrar quién la revoca, cuándo y por qué. Acreditar el lote vigente bajo un nuevo identificador. Si se autorizó retirar el esquema con registros, la reversión exige un archivo verificado y la confirmación de sesión `samchat.direction_bank_archive_verified = 'on'`.

Canon unchanged. La propuesta aplica los principios vigentes sobre fuentes acreditadas, visibilidad de Dirección por alcance, distinción entre ausencia y cero y separación entre caja real y previsión. La autoridad de Tesorería y los usos autorizados de Dirección se mantienen conforme al canon.
