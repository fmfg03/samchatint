# Puesta en servicio del piloto de revisión protegida

Preparado el 31 de octubre de 2026. Este documento acompaña al candidato de
código; su existencia no acredita una instalación, una persona ni una captura.
El informe final y sus hashes identifican el conjunto revisable. La revisión
humana de canon y la autorización operativa preceden cualquier promoción.

## Resultado esperado

Una revisión incompleta se conserva con sus faltantes y fuentes. Prepararla no
crea equipos, personas ni elegibilidad. Una revisión completa permite confirmar
los valores, demostrar presencia del revisor mediante su passkey y solicitar
una captura protegida. Jugadores, técnicos, finalidad, resultado y estado
`committed` de la sesión se confirman en una misma transacción. El navegador
solamente muestra captura confirmada después de leer ese resultado duradero.

El piloto real sigue siendo el manifiesto privado recuperado de 11 expedientes
y sus siete PDF originales. No sustituir nombres, hashes o páginas por archivos
parecidos. Su admisión pública anterior para revisión no acredita el catálogo
privado de fuentes: el operador debe revisar y firmar la admisión exacta con las
herramientas de `staff_source_admission`, conservando recibo, bytes CAS y límites.
No firmar ni admitir fuentes reales durante una prueba de código.

## Comprobación previa de promoción

Comprobar el SHA y WorkingDirectory realmente servidos por
`samchat-gastos.service`, su único `50-current-release.conf`, salud, reinicios y
journal; reconciliar contra el candidato antes de instalar. Preservar cambios
financieros ajenos. Preparar respaldo restaurable de esquema/datos y permisos,
inventario de facultades/roles existentes y plan de reversión. No ejecutar
`create_all` sobre tablas gobernadas para sustituir las migraciones.

Las migraciones del candidato se instalan antes de conceder capacidades de
servicio, en este orden:

1. `20261028_ctt_staff_security.sql`
2. `20261028_ctt_staff_review_state.sql`
3. `20261030_ctt_capture_anchors.sql`
4. `20261030_ctt_signed_assertion_guard.sql`
5. `20261030_ctt_capture_payloads.sql`
6. `20261030_ctt_staff_effect_gate.sql`
7. `20261030_ctt_transport_nonces.sql`
8. `20261030_ctt_platform_passkey.sql`
9. `20261030_ctt_assertion_issuer.sql`
10. `20261030_ctt_source_admission.sql`
11. `20261030_ctt_review_plan_scope.sql`
12. `20261030_ctt_legacy_write_boundary.sql`
13. `20261030_ctt_photo_visibility.sql`
14. `20261031_ctt_capture_finality.sql`
15. `20261031_ctt_passkey_enrollment.sql`
16. `20261031_ctt_assertion_proof_recovery.sql`
17. `20261031_ctt_review_proposals.sql`

En una base parcialmente migrada, verificar huellas y dependencias exactas;
no volver a ejecutar DDL a ciegas. Si existen grants de servicios anteriores,
preparar un corte explícito de esos grants exactos antes del preflight y su
admisión posterior. Las migraciones rechazan ACL, propietarios, dependencias o
funciones desconocidas; no relajar el guard para conseguir que instalen.
Los rollback homónimos se aplican en orden inverso y rechazan evidencia usada.
Después de una captura no borrar pruebas ni personas para simular una reversión.

## Configuración de procesos

Mantener separados el app público, el servicio privado de propuestas, el
enrolamiento, el emisor WebAuthn, el escritor transaccional, el operador y el Bus.
Sus pools/roles tienen sólo los EXECUTE y permisos de fila documentados por sus
adaptadores. El app público nunca recibe INSERT de facultades, credenciales,
pruebas o equipos con cuerpo técnico. No reutilizar claves entre los dominios
de identidad, revisión, efecto, firma, Bus o transporte. No incluir secretos
en repositorios, JSON de ejemplo, prompts, URLs o logs.

El ensamblado concreto del proceso privado está en `staff_review_runtime.py`:
`assemble_review_issuer` exige retención del comprobante firmado dentro de la
transacción que guarda su auditoría; `assemble_review_workflow` ensambla Store,
constructor de evidencia, enrolamiento, emisor y cliente del escritor. El canal
se monta con `create_staff_review_operations_app` y nonces durables de PostgreSQL.
Usar loopback y puertos explícitos. Mantener `fido2.server` en WARNING para evitar
que la dependencia escriba identificadores de credenciales. FIDO2 2.2.1 fue
probado en un entorno aislado; no instalar dependencias productivas sin revisar
el entorno y el paquete aprobado.

Proporcionar política de negocio acreditada y pinneada: mínimos/máximos,
puestos requeridos, fotos y evidencia. No promover la política sintética de las
pruebas. El constructor exige como límite estructural al menos 16 jugadores y
como máximo 35; el formato físico de este piloto conserva los slots originales
de sus dos páginas. No afirmar soporte de otra plantilla de 35 slots.

La configuración pública permanece apagada por defecto:

```text
CTT_STAFF_REVIEW_ENABLED=0
CTT_STAFF_REVIEW_URL=http://127.0.0.1:<puerto-privado-aprobado>
CTT_STAFF_REVIEW_KEY_PATH=<ruta-absoluta-privada-de-clave-de-canal>
```

Después de instalar y verificar el servicio privado, habilitar explícitamente
con `CTT_STAFF_REVIEW_ENABLED=1`. La clave debe estar fuera del repositorio y
webroot, con archivo 0400/0600 y directorio privado 0700, sin enlaces. El inicio
rechaza configuración habilitada inválida. El cierre libera el cliente HTTP.
No registrar secretos ni crear claves o roles desde requests públicos.

## Enrolamiento de la revisora autorizada

Resolver el UUID exacto de la cuenta revisora indicada por el usuario en el
backend servido y comprobar identidad, actividad y ausencia de suplantación;
el correo no es una facultad ni se usa como sustituto del principal protegido.
El operador separado prepara una invitación para ese UUID, principal, tenant,
edición y rol exactos, con vigencias explícitas. La CLI
`scripts/ctt_passkey_enrollment_operator.py` produce una vista previa por defecto;
`--execute` requiere un operador SQL previamente admitido. No usar el rol del
app como operador.

La persona inicia sesión en `https://sam.chat`, abre el expediente y pulsa
«Registrar passkey del dispositivo». Debe completar personalmente el diálogo
del teléfono o computadora. El resultado es una candidata pendiente de
activación y su fingerprint completo; no concede facultades. El operador
contrasta el fingerprint y activa exclusivamente la candidata/cuenta/principal/
edición/rol/vigencias revisados. El operador no registra una passkey en nombre
de la persona. Una revocación aumenta la versión protegida e invalida intentos;
la recuperación requiere otra candidata y nueva supervisión. No hay fallback
por contraseña, correo, QR de Google Authenticator u OTP.

## Revisión del expediente y recuperación

Abrir el detalle, comparar equipo, jugadores y los dos puestos técnicos con
las páginas originales. Corregir valores y seleccionar rectángulos pendientes;
«Preparar revisión» conserva otra propuesta privada inmutable. Una casilla no
convierte una caja candidata en evidencia verificada. La política de incidencias
se recalcula con valores, fotos y duplicados actuales; las incidencias no se
eliminan para poder aprobar. Las ausencias requieren evidencia y sólo son
admisibles si la política pinneada permite ese puesto ausente.

Confirmar el plan congelado y usar «Aprobar y capturar con passkey». El desafío
dura como máximo cinco minutos y empieza después de preparar la fuente. Si la
passkey se cancela, no se solicita captura. Si se pierde una respuesta, consultar
o reintentar el mismo intento; el comprobante queda privado y no se vuelve a
crear una operación para resolver incertidumbre. La pantalla bloquea otra
aprobación hasta un resultado confirmado o un estado terminal verificado por
SQL. Un recibo del Bus puede pertenecer a una transacción revertida.

Las fotografías preparadas permanecen privadas. Su lectura publicada requiere
la referencia exacta de una captura confirmada con finalidad; no basta con que
el archivo exista. Una corrección posterior a la captura no queda autorizada
por el alta anterior.

## Aceptación y límites

Verificar con la cuenta real, dispositivo real, invitación/candidata/fingerprint
y facultad reales; después revisar un expediente y comprobar la sesión y sus
filas exactas. Esa prueba humana y la aceptación deportiva siguen separadas de
los ensayos técnicos sintéticos. No importar automáticamente los más de 300
equipos ni los once expedientes a partir de un PASS de CI.
