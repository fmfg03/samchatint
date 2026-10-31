# Piloto de revisión protegida: autorización y evidencia de liberación

Fecha: 2026-10-31. La autorización humana vigente es «aprobado hasta el deploy».
Cubre el código final, los cambios de canon literales entregados para revisión,
commit, publicación, integración y despliegue acotado. El paquete revisado fue
`ctt-pilot-final-review-20261031.tar.gz`, SHA-256
`83186473fe5e3e3277811effcdd4bbb55e5bfa510b2f98594c8d349ad70bd358`.
Las aprobaciones anteriores de historia, especificación, finalidad, alcance de
cuerpo técnico y perfil de passkey de plataforma continúan para el mismo objeto.

Los textos de canon aprobados constan en
`ctt-protected-review-canon-20261031.md`; se incorporaron con su fecha, evidencia
y autorización explícita y se recalcularon los hashes del registro. El cambio
habilita revisión y captura protegidas; no acredita una identidad humana ni
concede elegibilidad deportiva.

## Alcance y reconciliación

El backend realmente servido al preparar la liberación era
`adb0d92229131d7e71258721f35f21da880be0fb`. El main observado para publicación era
`458ba5c067c7127cbc379e8c45c1d98b931e0fdf`. Contiene cambios financieros ajenos
sin desplegar. La promoción preserva los archivos financieros del backend
servido y aplica únicamente el alcance CTT aprobado; el recibo operativo debe
vincular SHA publicado/integrado, SHA acotado desplegado y huellas efectivas.

Zaubern main observado `5e9234368c8f0cc6b8d2f30d7637f8c291ecc2ae` retiró rutas
antiguas. El runtime CTT V2 se publica como artefacto aditivo de bytes pinneados,
sin restaurar servicios retirados ni modificar EB-3. La instalación mantiene
Gate/Bus V1 y su journal intactos y asigna al Bus V2 su propio journal.

El cierre operativo agrega launchers y configuración explícita de cuatro
procesos privados, transportes autenticados para evidencia/finalidad, plantillas
systemd y fixtures CI exactas sin secretos ni datos. Corrige el ordenamiento de
huellas de catálogo con COLLATE C en el guard SQL, verificado contra las dos
localizaciones; no cambia el esquema de negocio esperado. Las llamadas de Gate
que publican evidencia se apartan del event loop, conservando la operación y
sus fuentes mientras se espera. No se introduce una regla deportiva nueva.

## Evidencia técnica previa

El paquete final conservó 1,299 pruebas SamChat aprobadas, dos casos socket
probados aparte y un XFAIL estricto de semántica de vencimiento documentada;
Zaubern tuvo 573 PASS y su caso socket aparte. La prueba del navegador completó
seis casos con FIDO2 virtual. Eso no sustituye una ceremonia humana real.
Las pruebas adicionales del kit y sus resultados finales quedan vinculados al
commit y a CI; no se presume aprobación de un check pendiente.

Un respaldo completo del PostgreSQL servido se restauró en clúster aislado sin
listener de red. La copia original conserva propietarios/ACL; la prueba de
restauración utilizó --no-owner/--no-privileges. El recibo privado preserva rutas,
hashes y conteos; no se publican datos personales ni secretos. El preflight
validó el esquema real y los guards antes de aplicar migraciones.

Los siete PDF originales y las páginas de once expedientes coincidieron con
las fuentes públicas revisadas. El manifiesto normalizado tiene SHA-256
`9a4bcaa149bccaafbc479ce7b70b225b6df155a97761ee56c2c079d202c3ee67`.
La admisión privada y sus copias CAS requieren su propio recibo de operador.

## Política y límite de aceptación

La configuración pinneada se apoya en la convocatoria oficial 2026:
https://copatelmextelcel.com.mx/convocatoria.pdf . La composición publicada
requiere 16–35 jugadores y dos integrantes técnicos; los jugadores incluyen
foto, nombre, fecha de nacimiento y CURP. La procedencia y el hash de política
constan en deployment/ctt_protected_review/policy-source-20261031.json. No se
inventó un hash del PDF cuando la descarga directa respondió HTTP 403.
Las ausencias del draft se conservan y bloquean si incumplen esa política.

La cuenta exacta solicitada por el usuario se resolvió en privado, activa, y se
prepara para enrolamiento. No se activa una credencial ni facultad sin ceremonia
y contraste humano del fingerprint. No hay passkey simulada para esa persona,
fallback OTP ni captura automática de los once expedientes o más de 300 equipos.
El perfil de plataforma permite sincronización y contador cero según aprobación.

El modelo aprobado protege frente al rol SQL público y conserva al administrador
del host como confiable. El UID root preexistente del backend público queda como
límite de infraestructura; esta liberación no endurece ni cambia el proceso
financiero para ampliar ese modelo de amenaza. La UAT autenticada con dispositivo
real y la aceptación deportiva siguen pendientes hasta que la persona las haga.
