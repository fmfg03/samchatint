# Actualización v2

La evidencia de v2 incluye 14 casos adicionales de autoridad y 45 pruebas del
candidato. Los logs y manifest del paquete v2 contienen la ejecución final.
Ver [expanded-read-authority-v2-20261016.md](expanded-read-authority-v2-20261016.md).
Las cifras siguientes pertenecen al paquete v1, conservado sin sobrescribir.

# Verificación del candidato 2026-10-16

Base `a983be79e1ad8a6b5aa56affe27a945160ac8166`. Ver alcance, contratos, permisos
y rollback en [el candidato](expanded-read-candidate-20261016.md).

## Resultados reales locales

| Verificación | Resultado | Límite |
| --- | --- | --- |
| Candidato, runner independiente del bootstrap de aplicación | 31 tests, 31 aprobados | Política y fuentes sintéticas explícitas; ninguna conexión productiva |
| Cobertura de instrucciones de los seis módulos nuevos de implementación | 505 / 547 = 92,32 % | Excluye owners heredados y fixtures del denominador; no equivale a cobertura de integración productiva |
| SOLICITUD | SQL real SQLite con authorizer que deniega escrituras | Datos sintéticos; no valida políticas de instalación ni todas las diferencias PostgreSQL |
| Payment Run | SELECT canónico real PostgreSQL/WASM; parámetros de UUID/torneo/período, ambos montos NULL, READ ONLY que rechaza UPDATE | Cluster desechable; no demuestra grants productivos ni cancelación de driver real |
| Presupuesto/CxC | Owners/helpers/SQL reales con datos sintéticos PostgreSQL/WASM, incluyendo IVA, períodos, versiones, parcialidades/reversos, moneda y límites | No acredita driver async nativo, resolver ni schema productivo |
| AMEX | Getter y modelo canónicos con Session.get real SQLite y proyección mínima | Binding de autoridad sintético; callback productivo bloqueado hasta integrar guards legacy/direct |
| Executor histórico | Bundle39/39 archivos hash verificados; ocho comprobaciones del validador real worker/contracts.py | Sintaxis offline, no jobs, permisos ni state machine |
| Identidad/scopes | Usuario, objeto, tipo, torneo, versión; expiración, cambios de generación/política, revocación durante lectura/auditoría | Resolver de autoridad productivo todavía no implementado |
| Catálogo INFORME | Exactamente cinco tools y rechazo de nuevas operaciones por ReportOnlyGateway | Adicionalmente se conserva byte a byte LF el código de composición/owners aceptados |
| Paginación/validación | HMAC, identidad, query, snapshot, expiración, límites, inyección/actor/SQL, tipos, NaN/Infinity, moneda faltante | No representa snapshot MVCC de todo el negocio |
| Cancelación/timeouts | asyncio cancel/timeout niegan resultados; sesión controlada hace rollback | Sesión fake para la limpieza; native driver Linux pendiente |
| Diagnóstico/código | Manifiesto/versionado, límites, redacción, deny auditable y revocación final | Código revisado no acredita runtime ni despliegue |
| Desarrollo | create/start/resume/status/artifact/cancel, owner/generación/revisión, archivos/checks cerrados, sin efecto de publicación | Contratos puros, no ejecución del executor Agentius |

Los tests de código nuevo están en `tests/unit/readonly_gateway/test_expanded_reads.py`
, `test_expanded_postgres.py` y `test_expanded_real_owners.py`. Comando:

```text
python scripts/readonly_gateway/run_expanded_tests.py
```

Runner stdlib unittest; requiere SQLAlchemy para SQL real, Node y PGlite para
PostgreSQL/WASM. El fixture PostgreSQL acepta `SAMCHAT_TEST_NODE_PATH` y
`SAMCHAT_TEST_PGLITE_PATH` del entorno de tests. El runner crea namespaces de
paquetes para no ejecutar inicializadores de la aplicación, cargar configuración
privada ni conectar servicios. No modifica el bootstrap real.

Cobertura:

```text
python -m coverage run --source=samchat.readonly_gateway.expanded,samchat.readonly_gateway.expanded_catalog,samchat.readonly_gateway.expanded_adapters,samchat.readonly_gateway.technical_contracts,samchat.readonly_gateway.development_contracts,devnous.gastos.services.connector_read_service scripts/readonly_gateway/run_expanded_tests.py
python -m coverage report -m
```

## Regresión heredada: comparación contra la misma base

Se creó otro worktree de fuentes limpias en la misma base y se usó el mismo
harness, intérprete y entorno de fixtures. No se corrigieron tests heredados
ni se alteró el perímetro de seguridad para hacerlos pasar.

| Suite | Run | Pass | Fail | Error | Skip |
| --- | ---: | ---: | ---: | ---: | ---: |
| Base sin ampliación | 78 | 45 | 2 | 19 | 12 |
| Base + candidato | 109 | 76 | 2 | 19 | 12 |

Los **21 IDs de fallos/errores son idénticos** en ambos runs. Los doce skips
requieren socket PostgreSQL nativo Linux. Los fallos de este entorno corresponden
a APIs UID/GID POSIX ausentes en Windows, fixture PGlite cuyo session_user no
coincide con el rol exigido, y pruebas existentes de subprocess/patch del bridge.
Se conserva el detalle en `baseline-tests.log` y `all-tests-final.log` del paquete.

Esto acredita ausencia de nuevos fallos observados en el entorno comparable;
**no acredita una suite completa verde ni sustituye la aceptación previa de INFORME**.
Repetir en Linux con PostgreSQL nativo, UID/GID y driver/bridge reales antes
de integrar o activar. No se declara cerrada esa validación.

## Revisión independiente

Revisor `/root/independent_review`, sin edición ni producción. Revisión inicial
identificó cinco P2: importe faltante convertido a cero, semántica de períodos,
agregados sin moneda, números no finitos en strings y auditoría técnica incompleta.
Se corrigieron y el revisor ejecutó 22 pruebas en ese punto, todas aprobadas.
Resultado de la primera pasada: **sin P1/P2 pendientes en el candidato aislado**.
El seguimiento ejecuta 31 pruebas. La segunda revisión independiente reprodujo
el defecto heredado de cobros múltiples de CxC; se agregó retención explícita
de totales y regresiones reales. Ver el seguimiento de integración y el recibo
final de revisión incluido en el paquete.
El revisor confirmó el cierre del P2 y ejecutó de nuevo las 31 pruebas, todas
OK; no quedan P1/P2 identificados en el candidato aislado. La autoridad fresca
fuera del snapshot financiero está documentada y aún requiere prueba nativa.
La comparación legacy contra base es evidencia del implementador, no una segunda
ejecución independiente del revisor. No hay aprobación de publicación o activación.

## Entregable y preparación de PR

El paquete externo `samchat-connector-candidate.zip` contiene diff aplicable,
archivos cambiados, manifest de SHA-256, logs y cobertura JSON. Excluye entornos
virtuales, `.coverage`, credenciales, datos reales y archivos de otras ramas.
El manifest incluye hashes LF de archivos aceptados conservados y hash del diff.
No se creó commit, push ni PR.

Título propuesto: `feat: prepare scoped connector reads and diagnostic contracts`.
Descripción propuesta: añadir lecturas candidatas explícitas de SOLICITUD,
presupuesto, CxC, Payment Run y metadatos AMEX, junto con catálogo, validación,
diagnóstico por manifiesto y contratos de trabajo. Resolver/executor/activación
permanecen pendientes; conservar el servicio aceptado de INFORME. Validación:
31 tests nuevos aprobados; cobertura nueva en el manifest; misma deuda legacy de
Windows que la base; revisión independiente sin P1/P2 pendientes.

**Canon unchanged:** preserva reglas existentes, no habilita privilegios,
acciones de negocio ni runtime nuevo. La futura PR debe conservar esta nota y
la matriz de pendientes; publicación necesita autorización posterior.
