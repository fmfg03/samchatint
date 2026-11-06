# Fotografía opcional en fase nacional — 6 noviembre 2026

Estado del diseño: regla de negocio, especificación técnica y textos literales de canon aprobados mediante «aprobado» el2026-11-06. Continúa la autorización hasta despliegue del mismo piloto. Los resultados de implementación, CI, despliegue y UAT conservan sus propios recibos; este documento no los anticipa.

## Historia aprobada

Como revisor de Copa Telmex2026, puedo preparar y confirmar la captura nacional de jugadoras sin fotografía, dejando su ausencia visible y preservando las fotografías que sí existen. No debo dibujar recortes vacíos ni confirmar individualmente cada ausencia fotográfica para resolver un requisito opcional de esta fase.

## Hechos comprobados

- Runtime servido: público710021a2f; workflow privado710021a2f; writer87a4. Backend PostgreSQL devnous_db. El checkout principal está atrasado y tiene trabajo financiero ajeno: no se editará.
- El perfil vigente es2.1.0, hash `sha256:74ad5397752c5cfcc2fb0d144a5220b89c8d04f5745d0b2b278e897b2cf033ac`. Tiene `required_photo_kinds:["PLAYER"]`, CURP opcional PLAYER/STAFF y phase_binding NACIONAL con tenant/edición/manifiesto exactos.
- Gate ya admite `required_photo_kinds:[]`; verifica foto aportada y permite photo_path NULL cuando no se exige. El writer verifica las fotos existentes; las columnas fotográficas actuales admiten NULL. No se requiere cambiar Gate, esquema ni instalar dependencias.
- El builder ya admite ausencia opcional, pero la muestra como not_required y la interfaz no presenta una nota para ese estado. Se necesita una indicación explícita de fotografía pendiente, además del perfil correcto.
- Sólo workflow/runtime.json contiene policy_hash y policy_path. Issuer, writer y bus no tienen ese pin de política configurado. No se reiniciarán innecesariamente issuer/bus/V1. Writer requiere workflow: se detendrá/arrancará en orden y conservará código/unidad87a4.
- River tiene16 jugadoras y2 integrantes técnicos,18 fichas/36 referencias. Faltan fotos2/5/7/12/13/15 y son inciertas9/10. La corrección manual autorizada de Berenice ya está aplicada; sus valores y fuentes se preservarán.

## Cambio técnico propuesto

1. Usar el perfil nacional existente, conservando versión de contrato2.1.0, SAL-3, roles, mínimos16/máximos35, requisitos del cuerpo técnico, CURP y phase_binding. Cambiar únicamente required_photo_kinds a[] y recalcular su huella. Perfil candidato ya validado por Gate y builder: `sha256:70d058d16e901a24effbef408fdba5a44be7dfdd0a4ac93a63760ccd036ec205`.
2. Versionar el perfil nacional y la procedencia de la nueva regla; conservar el perfil estricto anterior. El runtime debe fijar la nueva huella tanto en policy.json como en workflow/runtime.json. No basta con quitar avisos de la pantalla.
3. En el builder, cuando el perfil NACIONAL verificado no exija foto a PLAYER y no se haya podido incorporar una foto automática, mostrar un estado calculado `pending`. No agregar PHOTO_MISSING/PHOTO_UNCERTAIN/PHOTO_REQUIRED por esa ausencia opcional. Persistir los campos fotográficos NULL y no producir una fotografía, hash o verificación ficticios. La fotografía válida existente sigue derivándose/verificándose y conserva su hash. Mantener el comportamiento de STAFF que ya era opcional.
4. No ocultar errores de fuente, de geometría manual, de almacenamiento o de integridad. Un recorte manual inválido o una foto aportada inválida mantiene su bloqueo. El perfil estricto y cualquier ámbito distinto conservan las comprobaciones actuales; el navegador no puede elegir la fase, política o exigencia.
5. Mostrar en cada ficha sin foto incorporada: «Sin fotografía — opcional en fase nacional». Es información visible y no un bloqueo ni una confirmación extra por persona. El estado es sólo del servidor; no se acepta como autoridad desde el navegador.
6. Una propuesta firmada con política anterior no se convierte en autorización nueva. Tras activación se preparará una propuesta fresca de River con la nueva huella, conservando las correcciones y la presencia que ya confirmó la persona cuando sus valores/fuentes sean idénticos. No se fabricará una confirmación ni ceremonia WebAuthn.

## Aceptación

- River conserva nombres, fechas, CURP,18 fichas, las36 referencias y las8 fotografías válidas; las otras8 quedan pendientes sin bloqueo fotográfico.
- El estado listo para aprobación sólo se obtiene si todas las demás validaciones pasan. La captura final sigue exigiendo contraste explícito del plan y passkey humana.
- Datos sin fotografía se almacenan NULL en los campos existentes. Fotografías aportadas conservan su verificación; ningún recorte blanco sustituye una ausencia.
- Se rechaza usar el perfil en tenant/edición/manifiesto distintos o cambiar la fase desde el navegador. El perfil estricto sigue bloqueando fotografía exigida ausente.
- No se modifican equipos ya capturados, membresías, credenciales, facultades, vigencias, claves o elegibilidad externa. No se realiza captura real durante pruebas o despliegue.

## Archivos previstos

- deployment/ctt_protected_review/policy-copa-telmex-2026.json y un recibo/versionado de procedencia para la regla de fotografía; perfil estricto sin cambios.
- src/devnous/copa_telmex/staff_review_plan_builder.py.
- static/js/ctt_staff_review.js; template de revisión para actualizar la versión de caché del script.
- Pruebas focalizadas de builder y navegador; ampliación aditiva de la prueba PostgreSQL operacional nacional con foto NULL. Si se añade una cohorte, el gate CI exigirá su ejecución y conservará las anteriores.
- Documento de implementación y los dos canons/registros sólo con los textos literales propuestos abajo y aprobación humana.

No se planean endpoints, tablas, columnas, servicios, dependencias o cambios del código Gate. Si la implementación requiere otra arquitectura o autoridad, se presentará únicamente esa desviación para aprobación.

## Validación requerida

1. Unidades: ausencias y ambigüedad automática opcionales sin bloqueo, perfil estricto intacto, foto existente conservada y fallo real de integridad/almacenamiento/manual no suprimido. Datos/confirmaciones previas y fuentes se preservan.
2. Navegador: ocho notas informativas y ningún aviso de fotografía requerida bajo el perfil nacional; edición/confirmación conjunta y confirmación final conservadas; política y estados no editables por el cliente.
3. PostgreSQL efímero con cuatro UID numéricos distintos: registro sintético completo con16 jugadoras/2staff, ocho fotos de jugadoras NULL, ocho fotos verificadas, CURP opcional NULL, firma/Bus/writer/finalidad reales. Mantener los escenarios estrictos y nacionales anteriores y sus cohortes obligatorias. No usar la persona ni los servicios productivos para esta prueba.
4. Lint/tipos/compilación por archivos afectados, guardas del repo, cobertura requerida, CI obligatorio y validación independiente antes de PR/merge.
5. Lectura firmada de los11 expedientes antes/después, comparación de valores/fotos/fuentes, cuenta/facultad, fuentes CAS, conteos y ausencia de capturas por esta tarea. La preparación del nuevo plan de River queda registrada por el canal canónico; no se solicitan desafíos ni firma.

## Liberación y recuperación

Se conserva la autorización previa hasta despliegue para este ajuste del mismo piloto una vez aprobada su ampliación técnica. Construir release acotado sobre el SHA realmente servido710021a2f, no desplegar main financiero completo. Preservar ambos bundles, photos/, intérpretes y claves.

Preparar artefacto privado de workflow con el builder aprobado, mismo Gate/vendor y demás componentes. Conservar writer87a4, issuer/bus y ambosV1. Sellar antes los bytes/permisos de política y configuración y sus huellas, unidades, release actual y conteos. Quiescencia de intentos/challenges antes y después de la pausa pública. Corte: público→writer→workflow; cambiar únicamente lo aprobado y arrancar workflow→writer, verificar socket LISTEN propio de UID984 y health workflow, y después público. Preservar los otros cuatro PIDs.

Ante fallo previo a promoción pública, restaurar política/configuración/unidad anteriores y arrancar workflow, writer y público, verificando los tres. No borrar propuestas ni fuentes; se conserva la historia de las políticas. Tras promoción, reconciliar estado y posibles efectos humanos antes de cualquier rollback. Invocar una sola vez el promotor público; su curl7 temprano se reconcilia con salud real, nunca con una segunda promoción a ciegas.

## Textos de canon propuestos para revisión explícita

Canon de producto, adición fechada2026-11-06:

> En el perfil operativo nacional de Copa Telmex2026 autorizado por la persona responsable, la fotografía de las jugadoras es opcional. Su ausencia queda visible como pendiente y no bloquea por sí sola la captura; las fotografías aportadas conservan verificación de fuente e integridad. Esta excepción no se extiende al perfil estricto o a la fase estatal y no acredita elegibilidad externa. La confirmación explícita del plan y la passkey humana se conservan.

Canon de ingeniería, adición fechada2026-11-06:

> La obligación fotográfica se evalúa con la política nacional fijada por hash y vinculada a tenant/edición/manifiesto. Builder y Gate aplican la misma regla: una fotografía opcional no incorporada conserva NULL y no genera evidencia ficticia; una fotografía aportada conserva validación y persistencia verificables. Se prepara una propuesta nueva bajo la política vigente, sin reutilizar autoridad de otra política. No cambian las comprobaciones de fuente, identidad, facultad, WebAuthn o finalidad atómica.

La aprobación de esta especificación incluye revisión humana explícita de estos textos; no se editarán los canons ni sus hashes antes. Su fecha/razón/evidencias se añadirán al registro. El sweep report no necesita cambios porque sus hechos históricos no cambian.

## Estado de comprobación previo a la implementación

Canons del checkout leídos e íntegros. Columnas fotográficas NULL comprobadas mediante SELECT READ ONLY. Perfil candidato validado por el validador Gate real y el perfil de identidad del builder: PASS. La primera prueba de candidato intentó importar un serializador desde un módulo equivocado; se corrigió para usar exactamente runtime.canonical, sin efectos en producción. No se ha implementado el nuevo estado, ejecutado la captura sintética ampliada, cambiado política/configuración ni reiniciado servicios.

## Resultado local de implementación

Implementación congelada2026-11-06. Builder:107PASS/0skip,95.82% cobertura, rojo previo demostrado; Mypy/Black/isort/flake8(sistema)PASS. Navegador:25PASS/0skip, dos casos nuevos verifican8notas/8fotosexistentes, confirmación explícita y ausencia de metadatos de autoridad en la petición; rojo previo0notasfrente8esperadas.

Aceptación PostgreSQL: tres escenarios RPC con cuatro UID realmente distintos PASS105.58s, nueve escenarios anteriores PASS63.07s;12/12workflow sin skips. Nuevo caso conserva8fotos con hashes exactos fuente→vista→SQL y almacena8photo_path/photo_data/photo_sha256/photo_ahash NULL; CURP opcionalNULL,16PLAYER+2STAFF, firmas/Bus/writer/finalidad reales y un resultado durable en la base efímera. CheckerCI30PASS; añade tercerRPC obligatorio y eleva11→12 sin reducir cohortes ni modificar Gate/fixtures.

Validaciones de fuente, almacenamiento e integridad y recorte manual inválido mantienen bloqueo. STAFF y perfil estricto conservan comportamiento. Validación estática independiente favorable por revisores de módulos que no escribieron; no se atribuye al revisor una repetición de pruebas del autor. Source builderSHA d1c56be4a6708b180809d6d7ccaefbf8f7b97e3cd7925ca1a2b98f59e7237184; JSSHA8635c888d065ace0acb79f3cbaa7ed40cdddccf20ceff76fa90ef465819e9779 y versión de caché coincidente. Guardas operacional62archivos/regresiones13marcadores e inventarios exactos PASS.

Comandos: pytest unitbuilder conCOVERAGE_FILE exclusivo; pytest browser; pytest integration -k operational_rpc conkitCTT exacto enPGefímero y/root/samchat/.venv/bin/python; pytest integration -k 'not operational_rpc'; pytest test_ctt_ci_fixtures.py. Mypy selectivo delbuilder, black --check, isort --check-only --profileblack, /usr/bin/flake8 --ignoreE203,W503,E501, node --check y git diff --check.

Fallos de entorno corregidos: primerRPC con venvpr516 no encontrócoverage en el proceso hijo4UID; se repitió con el venv existente que contiene el módulo, sin instalar dependencias. El módulo flake8 faltaba en dos venvs; el ejecutable del sistema7.0.0 pasó. Un comando de inventario usó --verify inexistente y se corrigió a la CLI real sin argumentos, que pasó. Ninguno de estos fallos se atribuye al producto ni se omite; los intentos se conservan en las evidencias locales.

No hay desviación de alcance ni Gate/DDL/dependencias nuevas. Canons actualizados únicamente con los textos literales aprobados y hashes registrados; sweep histórico intacto. CI remoto, merge, despliegue y UAT todavía requieren sus recibos reales posteriores. La tarea no ejecuta ceremonia humana ni captura productiva.
