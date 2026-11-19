# Piloto aislado Batiseñal

Este candidato integra el recurso de soporte de tres herramientas en una instalación de prueba separada. No monta Batiseñal en `copa_telmex_dashboard.py` ni cambia `samchat-gastos.service`. El diseño aprobado está en `docs/roadmap/batisenal-real-pilot-20261109-spec.md`; la procedencia del portado está en `port-source-manifest.json`.

## Evidencia y límites

El código y sus pruebas técnicas no acreditan una conexión de Chatty. A1 y A2 reales requieren una conversación del cliente registrado, login individual y consentimiento nativo, correlacionados con el recibo PostgreSQL y el navegador del mismo release. No se permite renombrar el stub ni usar una cuenta administrativa del host como autoridad de negocio.

Las propuestas permanecen privadas. La atestación conserva la prohibición de publicación incluso tras revocar, apagar o retirar el piloto. No contiene permisos de pago, aprobación ni modificación del informe.

## Construcción y verificación técnica

El ensayo usa los requisitos de pruebas ya existentes, incluyendo `requirements-browser.txt` y `requirements-private-plugin-postgres.txt`. El driver síncrono `psycopg` está declarado sólo para pruebas: su eventual instalación como dependencia de ejecución necesita el plan operativo correspondiente. No se modificaron las dependencias runtime para ocultar esa diferencia respecto de la hipótesis inicial.

En un usuario de pruebas sin privilegios, con PostgreSQL16 y Firefox de pruebas disponibles:

```sh
python scripts/batisenal/run_isolated_acceptance.py --tier technical --browser firefox
```

El runner rechaza variables de conexión externa, crea su propio clúster Unix-only y lo detiene al terminar. Los tests importan código integrado de `src`, no el checkout histórico. La evidencia se guarda en `artifacts/batisenal-real/`. El recibo distingue resultados técnicos de aceptación real pendiente. Un fallo, test omitido o cobertura insuficiente impide declarar completa la validación.

## Instalación accesible pendiente

Antes de instalar, presentar un manifiesto exacto con destino exclusivamente sintético, roles owner/runtime separados, actor legítimo, caso/informe/torneo, cliente y callback del registro real, hash de registro, issuer/resource, referencias de custodia, política de evidencia, release y plan de recuperación. No copiar contraseñas, cookies, claves ni perfiles productivos.

La aprobación de construcción no registra un MCP, crea identidades reales, abre red, inicia servicios, aplica SQL a un destino existente ni consiente por la persona. La tabla de auditoría nativa del login necesita preparación owner explícita; el ajuste de auditoría fue aprobado expresamente y su modo aislado impide DDL durante el login y elimina la sesión cuando falla la auditoría.

## Reversión

Apagar, revocar por el owner y retirar únicamente la composición aislada. Preservar la atestación, filtros de privacidad, fuentes, borradores, operaciones, recibos y auditoría. No eliminar tablas ni volver a una revisión que publique o enumere los tickets privados.

La selección de Firefox es explícita y usa su sandbox predeterminado. Chromium se intentó con sandbox activado y falló por restricciones de este host; no se desactivó el sandbox ni se cambió la política del servidor. Esa evidencia se conserva separada.

El manifiesto de activación debe corresponder a un recibo de build con el objeto commit Git y el árbol completo; la fábrica verifica fuente local y política antes de conectar. Un plan del operador se liga a la raíz realmente ejecutada y a un respaldo PostgreSQL cuya restauración se comprobó en otro destino aislado autorizado.
