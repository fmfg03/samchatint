# AMEX: aprobación de la cuenta de gastos sin revisión duplicada

Estado: historia revisada aprobada explícitamente por el usuario el 2026-10-13; especificación técnica aprobada; implementación candidata en verificación. Sustituye la propuesta de captura desde cargo explícito tras aclaraciones del usuario. Implementación candidata preparada; no mutación productiva. Baseline3619ebcab586d96d6740c5dda2a578dd74bd83aa y canon íntegro ya verificados.

Hechos de negocio confirmados por usuario: Alicia concilia por CuentaDeGastos al aprobar su INFORME en SamChat. Los gastos pagados con AMEX ya se marcan como AMEX al hacer la comprobación. Los10informes indicados pertenecen aOdilon segúnusuario, no segúnqueryprod. No es petición de vínculo automáticamenteinferido con estado de cuenta.

Como responsable de Contabilidad quiero tomar la cuenta de gastos aprobada y sus partidas marcadas AMEX como fuente documental de la póliza, para no repetir la comprobación/conciliación que ya realizó quien la aprobó.

Porción afectada propuesta:
1. Reutilizar aprobación real de INFORME+actor+fecha como evidencia de comprobación de la cuenta. Identificar autorización por permisos/ruta vigente, no por nombresAlicia/Odilon. No falsificar que aprobadoroperativo seaFinance ni crear revisionescontables atribuidas aotroactor.
2. Al preparar póliza mostrar de forma consolidada cuenta/informe/aprobador/fecha/importe/partidas/métodos/tarjetas/evidencia, con decisionescontables declaradas y excepciones. Finanzas confirma el informe completo en la acción contable existente sin repetir marcado/revisión individual. No inventar gastoempresa vs cargosocio, cuenta, IVA o tarjeta si no están definidos.
3. Reconocimiento del informe aprobado no exigirá relacionar antes cada partida con un cargo importado. El informe y sus partidas serán origen contable real y auditado; no fabricar cargos, reutilizar UUIDfalso, ni declarar cargo bancario conciliado sin identidad/evidencia.
4. Estado de cuenta continúa como fuente de control bancario. Importarlo no genera segundo gasto/póliza por el mismo consumo del informe. Un vínculo posterior debe reutilizar reconocimiento existente; cargos realmente ajenos al informe requieren excepción explícita, no heurísticafecha/importe.
5. Una sola póliza completa por INFORME, corteimmutable, reintentosidempotentes, ajustesconmotivo, fiscalidad/mappings/periodo y permisos vigentes. Errores mantienenpendienteconmotivo, jamás datos faltantes como cero.
6. Nuevas aprobaciones registrarán evidencia de fuente y versiones según especa definir. Aprobaciones antiguas y cortesactuales se conservan; no backfill ni fakeattestation histórica. Para informesyaaprobados sin corte sólo preview/confirmación realFinance futuro trasmapeo/evidencia suficiente, no mutaciónmasiva ahora.

Fuera: emparejamiento por monto/fecha, captura desde cargos del diseño anterior, hardcodedexcepciónpersonal, aprobación/pólizaautomática sin autoridad, regularización de14históricos, nuevasreglas deducibilidad/impuestos, eliminar auditoría/estado bancario/importados.

Aceptación:
- Informe aprobado marcadoAMEX llega a preparación de póliza sin repetir comprobaciónporpartida/seleccióncadacargo.
- Falta card/cuenta/tratamiento/fiscalidad produce excepción explícita, no suposición.
- Aprobaciónoperativa se muestra como evidencia original, confirmacióncontable atribuyeFinance real.
- Importación/bankbindingposterior no crea doble gasto/póliza; identidad sin inventarse.
- Concurrencia/replay conservan un reconocimiento y corte; cortes previos inmutables.
- GastosnoAMEX/rutasordinaryapproval mantienencomportamiento.

Investigación: aprobación actual Aprobacion sóloactor/accion/comentario/fecha, no tratamiento/fiscal snapshot; cutactual requiere reviewallfresh y representaciónimportadaforall. Modelo currentconsumption imported_expense_id NOTNULLunique no sourcealternativo. Por ello nuevoorigenreportfirst requiereespectécnica y probablemente migración; no silenciar validadores existentes para aparentar cumplimiento. Contrato docs/sprints/rqf-accounting-amex-cut-20261001-spec.md:11,19-23 define separaciónoperativaFinance y banklinkque cambian en laporciónpropuesta.

Canon: evaluar amendmentexplícito en espec, nunca ediciónautomática sinrevisiónhumana. La aprobación de historia no equivale a aprobar DDL, nuevospermisos o deploy. Alcanceprevio parcial enworktree fuente queda en cuarentena sinpublicación. Investigación y spec siguientes no acceden filaspod ni suponen propietario/rol.
