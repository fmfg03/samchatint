# PR #487 — cierre para merge, 2026-10-16

## Alcance y aprobaciones

El usuario autorizó terminar el PR #487 y fusionarlo. Se conservan las reglas aprobadas del candidato `budget-distribution-20261014-candidate.md`. El seguimiento corrige el P1 de identidad financiera y sincroniza `main` en `03fa1fecef756ab181eb4cbef4aa321bd89f645d`; no introduce reglas de negocio, permisos, dependencias, tablas ni migraciones.

La revisión automática rechazó inicialmente modificar los canons sin revisión explícita de su contenido combinado. Se preparó un diff y archivos completos en `/tmp/samchat-pr487-canon-review-20261016/`. El usuario aprobó **«Apruebo la integración exacta del canon»**. Se aplicaron esos bytes exactos; validación independiente acreditó que cada canon conserva todo `main` más el apéndice presupuestal aprobado, sin reglas nuevas. Producto SHA-256 `3f92bc667e2b152334a500f2cf9427cebdd4ed9c0b9b10538e075596842f435a`; ingeniería `b98ba82dade2cc148b06b787c9b0415479a4c68e8109bf6c5f2402dcbe5228e1`. Sweep íntegro `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985`. El registro conserva las evidencias de ambos padres y sus hashes actuales.

## Corrección y archivos

El selector operativo entrega correctamente la revisión aprobada para consultar su calendario. Sin embargo, Cuentas por Cobrar y el aviso semanal usaban su UUID para buscar facturas todavía vinculadas a la raíz financiera original. Ahora reutilizan `distribution.financial_context` para resolver la raíz validada antes de esas lecturas. El calendario sigue leyendo la revisión; los vínculos fiscales leen la raíz. Un linaje inválido falla cerrado. No se altera el selector global ni se migran vínculos.

- `src/devnous/gastos/routes/user_routes.py`: tres lecturas de CFDI/vínculos usan la raíz; se conservan líneas de la revisión.
- `src/devnous/gastos/services/income_billing_alert_service.py`: conserva la planeación de la revisión y consulta lo facturado en la raíz.
- `tests/unit/gastos/test_income_distribution_financial_root.py`: seis regresiones de vista real, versión ordinaria, linaje inválido y aviso con planeado100/facturado70/pendiente30 e identidad semanal estable.
- `src/samchat/budgets/service.py`: resolución del conflicto conserva tanto el SQL/locks de AMEX directo de `main` como la exclusión de revisiones de distribución en asignación financiera.
- Canons/registro: integración exacta aprobada.
- `docs/private-plugin/route-inventory.json` y `route-matrix.csv`: regenerados mediante el owner;605 rutas,574 live,44 acciones,sin parse gaps.
- Este reporte y el seguimiento del candidato documentan cierre, evidencia y límites.

## Comprobaciones

El builder ejecutó54 pruebas específicas PASS; mypy del aviso PASS; formato y lint estrictos del test nuevo PASS. La primera colección PostgreSQL aislada encontró un ciclo de importación preexistente al cargar presupuestos antes del paquete de gastos. La corrida final importa primero el paquete canónico `devnous.gastos`, como el runtime; no cambia código para ocultar un fallo. La base es PostgreSQL15 desechable propia, con el DSN ficticio explícito admitido por el fixture CI; cada caso usa esquema único. No se accede a producción.

Runner final: `PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps /root/samchat/.venv/bin/python /tmp/samchat-pr487-run-regressions.py`. Lista exacta de archivos: `/tmp/samchat-pr487-regression-files.json`. Log: `/tmp/samchat-pr487-final-regressions.log`. Cobertura con configuración thread/greenlet de `main`, sin excluir líneas ni alterar el umbral85%. Contrato estricto del quality gate y `git diff --check`: PASS.

**Resultado final local: 634 PASS, 11 warnings heredados, 133.77s; incluye los siete casos PostgreSQL nativos. Cobertura fresca: 322/376 instrucciones ejecutables modificadas (85.64%). Ambos lectores corregidos alcanzan 4/4 instrucciones modificadas.** Evidencia y lista de comandos en `docs/evidence/budget-distribution-20261016/verification-summary.json`; cobertura por archivo en `changed-source-coverage.json`. Validación independiente final se registra en `budget-distribution-20261016-validation.md`. Los controles de GitHub deben pasar en el head publicado antes de merge. Los resultados de CI de2026-10-14 se conservan como históricos, no como prueba del candidato integrado actual.

## Riesgos y límites

Sin desviación de las reglas aprobadas. Se conserva la plantilla fija de26 categorías como N/D hasta que Finanzas apruebe su crosswalk. Torneo/versión del incidente real, datos productivos, despliegue y UAT autenticada permanecen separados. Este PR no requiere migración. El cierre técnico no acredita aceptación de negocio ni corrige datos históricos por sí mismo.

## Compatibilidad del selector AMEX — seguimiento previo a merge

La comprobación final del módulo AMEX incorporado por `main` detectó un segundo lector incompatible con el guard financiero ya aprobado. El selector de Control Presupuestal listaba líneas de revisiones de distribución porque no leía su `source`; al enviarlas, el owner canónico las rechazaba correctamente. Se deshabilitó el merge automático mientras se corrigió esta oferta de opciones inválidas.

`amex_direct_budget_service.py` ahora proyecta `v.source AS version_source` en las dos lecturas de partida y excluye `distribution_revision` en el predicado compartido del selector y la asignación bajo lock. Originales financieros, estados ordinarios, alcance, concepto, dirección, edición y política draft permanecen intactos. No cambia el guard canónico ni el ownership. `test_amex_direct_budget_display.py` añade doce casos: copias approved/frozen no ofrecidas, originales disponibles, envío directo de copia rechazado sin owner/evento/mutación y ocho políticas ordinarias conservadas.

137 pruebas específicas PASS; Black, isort, Flake8, mypy y diff check PASS. Validador independiente ejecutó14 pruebas PASS y declaró CLEAN_WITH_NOTES sin hallazgos. La continuidad de aprobación se mantiene: hace cumplir el guard financiero existente, sin regla nueva ni nuevo texto canónico. La corrida conjunta y cobertura frescas se registran en el recibo de compatibilidad; la evidencia anterior de634 casos corresponde a `b11648411`.

Resultado final integrado de compatibilidad: **648 PASS**,70 warnings heredados,134.40s;36 targets, nueve casos PostgreSQL15 (siete de distribución y dos de catálogo/retorno AMEX). Cobertura fresca: **323/377 (85.68%)**; predicado AMEX1/1 y lectores P1 4/4 cada uno. Huellas de los dos archivos verificadas independientemente; CLEAN_WITH_NOTES sin hallazgos. Recibos: `compatibility-verification-summary.json` y `compatibility-source-coverage.json`. Nuevos checks requeridos sobre el head corregido antes de merge.
