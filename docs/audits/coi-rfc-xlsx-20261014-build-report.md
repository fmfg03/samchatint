# Corrección RFC en Excel de pólizas COI

Fecha: 2026-10-14. Base `71247de778ba2c69783cd68427a7084c01e7c42f`; rama aislada `fix/coi-rfc-xlsx-whitespace-20261014`. Usuario solicita eliminar el espacio adicional del RFC que impide importar los Excel de SOLICITUD e INFORME en COI. Corrección de formato localizada, sin nueva regla fiscal/financiera ni cambio de esquema. Implementación local terminada; publicación/deploy requieren autorización propia.

## Resultado y archivos

- `src/devnous/gastos/services/coi_poliza_exporter.py`: únicamente `generate_coi_poliza_xlsx`. Las celdas F/G del registro CFDI se escriben sin whitespace exterior y con la protección de texto ya existente. Reconocimiento estructural de INICIO_CFDI evita confundir descripción de póliza con control de layout.
- `tests/unit/gastos/test_coi_poliza_exporter_rfc.py`: 20 regresiones de XLSX real para SOLICITUD, INFORME y DTO AMEX; RFC limpios/padded con espacios, tabuladores/NBSP, ausentes, ZIP, CSV estable, colisión de marcador e input que pudiera convertirse en fórmula.
- `tests/unit/gastos/test_amex_cut_exports.py`: una regresión del adaptador real de corte congelado; RFC limpios con snapshot y CSV idénticos antes/después.
- Este reporte registra validación y límites.

Se incluyen los XLSX generados dentro de ZIP y los lotes que reutilizan el renderer. No se modifica el constructor compartido CSV, RFC almacenado, UUID, impuesto, movimiento, cuenta, granularidad, snapshot o header/metadata C3. No hay cambios de rutas, migración, reparación de datos o dependencia nueva.

## Evidencia y comandos

1. Harness `/tmp/samchat-coi-rfc-xlsx-repro-20261014.py`: cuatro casos de solicitudes/informes reprodujeron RFC con espacio incluso con entrada limpia. Regresiones iniciales: 17 FAIL y 1 PASS.
2. La revisión independiente detectó una regresión de fórmula al quitar padding; test real `data_type='f'` reprodujo FAIL. Se corrigió reutilizando `_safe_cell_text`; repetición verde.
3. `PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps COVERAGE_FILE=/tmp/samchat-coi-rfc-final.coverage /root/samchat/.venv/bin/python -m pytest -q tests/unit/gastos -k 'coi or amex' --cov=devnous.gastos.services.coi_poliza_exporter --cov-report=xml:/tmp/samchat-coi-rfc-final-coverage.xml --cov-fail-under=0 --tb=short`: **604 PASS**, 1647 deselected. Cobertura medida de las cuatro líneas ejecutables modificadas **4/4 = 100%**; umbral global desactivado en este comando porque el criterio es cobertura del cambio.
4. Verificación independiente RFC/descripción/corte congelado: **63 PASS**, sin hallazgos pendientes. Recibo `/tmp/samchat-coi-rfc-xlsx-validation-20261014.md`.
5. Black check, isort del archivo nuevo, flake8 max88 del archivo nuevo y `git diff --check`: PASS.
6. Mypy focal: dos errores actuales idénticos a dos del baseline; flake8 del renderer: dos diagnósticos actuales idénticos a dos del baseline. **Cero nuevos**, sin declaración de tipos/lint global limpios. Comparación `/tmp/samchat-coi-rfc-static-comparison-20261014.json`.

## Fallos resueltos y límites

Un helper de test inicial confundía una descripción literal INICIO_CFDI con el marcador; el lector de prueba ahora identifica el registro por UUID, y comprueba que el header conserva su texto y los importes siguen numéricos. El hallazgo de fórmula se corrigió y revalidó. Deprecaciones heredadas quedan en logs. No hay fallos funcionales pendientes.

Son archivos sintéticos y pruebas automatizadas. La importación autenticada de un archivo real en COI no se ejecutó. Después de publicar, se deberán volver a descargar los Excel; no se reescriben archivos ya descargados. Canon unchanged: se elimina padding del RFC únicamente en XLSX y se conserva identidad fiscal, layout, estados y autoridad financiera. Hashes de canons coinciden con registro.

La historia AMEX directa SAT/proyecto/categoría/presupuesto/póliza/pago permanece separada y pendiente de aprobación; este parche no la implementa.

## Autorización y promoción acotada

El usuario aprobó explícitamente commit/push/PR/merge/despliegue del RFC. Main avanzó a `68e5ada6f` por PR485 de sugerencias de cuentas, mientras el runtime observado sigue en `71247de77`. El PR integra la corrección en main; el release de esta autorización se prepara desde el head aprobado sobre el runtime vigente, incluyendo sólo los cuatro archivos de RFC/reporte/pruebas. No se admite PR485 a producción por esta autorización. Comparar bytes/delta y registrar por separado merge SHA y commit realmente servido. Sin modificación del checkout primario ni de cambios ajenos.
