# Corrección de UI AMEX

Fecha: 2026-10-14. Base: `fcb3e4bc14d4b05bc001aee8b79ba0d391c18e1d` (#482). Rama aislada: `fix/amex-platform-ui-20261014`.

Se corrige la desviación visual reportada por el usuario dentro del recorrido aprobado. Los estados, la carga, los pendientes, las pólizas y el detalle canónico reutilizan el marco de Contabilidad: navegación con actor autenticado, breadcrumbs, encabezado, superficies y botones. Los tres pasos conservan sus operaciones. El informe incrustado se extrae mediante límites únicos controlados; no duplica navegación ni tracker.

## Archivos y pruebas

Dos routers AMEX actualizados solo en presentación y propagación de actor al renderizador. Tres suites unitarias y dos suites de navegador actualizadas para verificar navegación compartida, scripts confiables/escaping, fragmento único y formularios. Inventario JSON y matriz CSV regenerados por cambios de hash/línea de rutas. Sin servicios, modelos, SQL, dependencias ni datos modificados.

- Reproducción inicial: cuatro pantallas fallaron por ausencia del marco compartido.
- `PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps /root/samchat/.venv/bin/python -m pytest -q tests/unit/gastos -k amex --no-cov --tb=short`: 466 PASS. Log `/tmp/samchat-amex-ui-unit-final-20261014.log`.
- Suites focales de construcción: 127 PASS; verificación independiente: 92 PASS.
- Playwright recorrido, paginación e identidad, escritorio/móvil: 6 PASS. Log `/tmp/samchat-amex-ui-browser-final.log`.
- `python scripts/private_plugin/inventory.py`: 590 rutas, 44 acciones, cero parse gaps. Pruebas finales `pytest -c /dev/null -q tests/unit/private_plugin/test_inventory.py`: 9 PASS.
- `git diff --check`: PASS. Formato/imports verificados por constructor. Comparación estática: flake8 44 diagnósticos base y 41 actuales; cero nuevos o en líneas modificadas. No se afirma lint global limpio.
- Capturas sintéticas inspeccionadas: `/tmp/samchat-amex-ui-20261014/{estados,polizas}-{1440,390}.png`. No acreditan UAT autenticada productiva.

## Fallos y límites

Cuatro fixtures inicialmente omitían el nombre del actor requerido por la navegación compartida; se corrigieron y se repitieron las pruebas. El entorno offline de inventario requiere `-c /dev/null` porque no instala los plugins de pytest generales. Mypy señala el mismo error del baseline en el generador de exportación legacy, cuyo handler permanece intacto; comparación base=1/actual=1/nuevos=0 en `/tmp/samchat-amex-ui-static-comparison.json`; no se afirma validación de tipos global completa. Deprecaciones existentes siguen visibles.

Sin desviaciones financieras ni expansión de alcance. El selector del paso 3 conserva su filtro existente: informes aprobados con partidas AMEX activas y atención manual visible; no filtra automáticamente por tarjeta/período. Publicación y aceptación visual productiva pendientes.

**Canon unchanged:** esta reparación aplica presentación compartida al recorrido ya aprobado; no cambia arquitectura, reglas financieras, autoridad ni contratos. Los tres hashes coinciden con el registro. No se requiere migración.
