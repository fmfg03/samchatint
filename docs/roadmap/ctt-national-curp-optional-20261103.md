# Especificación aprobada: CURP opcional en fase nacional

Fecha2026-11-03. Investigación y propuesta, sin implementación ni cambio de política productiva.

## Regla y alcance

La persona responsable aclara: «en la fase nacional del torneo la CURP no es obligatoria». El contrato desplegado2.0exige CURP tanto de jugadoras como de integrantes técnicos presentes. Teléfono y correo ya son opcionales y pertenecen al contacto del equipo.

Historia: como revisor acreditado de Copa Telmex2026 en fase nacional, puedo preparar y confirmar una plantilla cuyos datos disponibles se contrastaron con sus fuentes, aunque falte la CURP que esa fase no exige. El valor faltante queda ausente, sin inferencia y sin bloqueo por ese único motivo. La captura operativa no acredita elegibilidad externa.

Alcance confirmado por la persona: «Jugadoras, DT y auxiliar». La varianteB se convierte en la única propuesta: CURP opcional para PLAYER y STAFF en el perfil nacional del piloto.

## Especificación acotada propuesta

1. Nuevo perfil explícito de política por fase, tenant samchat-prod y edición47bcc601-929a-4a8a-9005-99f10ee1aa58. El catálogo actual sólo identifica torneo/año, por lo que la fase no se deduce del año ni de la petición del navegador. La asignación nacional se fija en el artefacto operativo revisado y su hash, vinculada además al manifiesto admitido del piloto9a4bcaa149bccaafbc479ce7b70b225b6df155a97761ee56c2c079d202c3ee67; Gate y builder revalidan edición/tenant/manifiesto/perfil. El ReviewScope ya contiene manifest_sha256, por lo que no se infiere que todas las fuentes del mismo año pertenezcan a la misma fase. Un manifiesto ajeno al perfil nacional se rechaza; ampliar fuentes requiere asignación operativa revisada.
2. Conservar el perfil2.0estricto. El nuevo perfil nacional declara exactamente qué clases de persona permiten CURP ausente. Fuera de ese perfil no hay excepción ni fallback. Fase desconocida, pin incompleto o hash contradictorio bloquean. La regla estatal conserva su exigencia actual.
3. CURP ausente se representa como NULL canónico en el efecto y la persistencia, nunca cadena vacía o valor ficticio. No se emite una decisión que afirme haber verificado una CURP inexistente. CURP aportada mantiene validación y su evidencia de fuente. Nombre y fecha de nacimiento conservan su evidencia obligatoria.
4. Builder y Gate independiente aplican exactamente la misma regla. La propuesta y el scope firmado ya contienen el hash de política; usar ese vínculo existente, sin ampliar el wire SQL si las pruebas integrales demuestran que basta. No se propone DDL nuevo: modelos admiten NULL. Cualquier necesidad de ampliar esquema/autoridad se presenta antes de implementarse.
5. La interfaz identifica CURP opcional sólo para el perfil nacional recibido del servicio privado; otros perfiles mantienen el requisito. Faltantes no se convierten en valores y se conservan como ausencia revisable.
6. Se conservan passkey, verificación local, firmas, consumo único, versionado, fuentes CAS, geometría y fotos, dos puestos técnicos, controles actuales de duplicados de fotografía, bloqueo SQL y transacción atómica. La unicidad de CURP no detecta duplicados cuando no existe; no se afirma un control equivalente ni se añade una coincidencia automática por nombre.
7. Publicar el Gate real y su fixture idénticos, nuevo artefacto privado y política con hashes. Mantener las claves y credencial humana activada. Preflight de cero capturas en curso o reconciliación exacta antes de promover; planes con política anterior requieren nueva preparación/confirmación y no reutilizan comprobantes.

## Alcance expreso del contrato de garantía

El contrato local actual etiqueta SAL-3 y exige tres campos. Esta propuesta modifica ese contrato local únicamente bajo el perfil nacional autorizado: la verificación de campos obligatorios se hace sobre nombre y fecha de nacimiento; CURP se verifica cuando se aporta. Se conserva el resto de las comprobaciones del perfil y la etiqueta/versionado debe describir expresamente esta excepción aprobada. No se afirma que dos campos equivalgan universalmente a SAL-3 ni que se haya verificado una CURP ausente. Si la validación descubre un requisito formal incompatible en el servicio real, se detiene la parte afectada y se presenta antes de cambiarlo.

## Aceptación necesaria

- Dos jugadoras sin CURP: preparación, firma, Gate y commit real PostgreSQL con NULL, sin colisión por cadena vacía; sin datos fabricados.
- Caso con CURP presente: verificaciones actuales y evidencia; valor inválido o fuente ausente siguen bloqueando.
- Fase estatal/perfil anterior: CURP faltante sigue bloqueando. Fase ausente, ajena o manipulada se rechaza.
- Casos PLAYER y STAFF (jugadoras, DT y auxiliar); ausencia técnica, campos obligatorios, foto duplicada o revisión no confirmada mantienen sus bloqueos.
- Cambio de política invalida planes/comprobantes anteriores; passkey real permanece acreditada y no se ejecuta una captura humana para probar despliegue.
- Navegador muestra opcionalidad correcta y permite confirmar sólo un plan preparado y sin los demás bloqueos.

## Canon literal aprobado

Producto: «Para la fase nacional fijada explícitamente en el perfil operativo de Copa Telmex2026, la CURP es opcional para las jugadoras, el director técnico y el auxiliar. Su ausencia se conserva como dato no proporcionado y no bloquea por sí sola la captura operativa. Una CURP aportada conserva validación y evidencia de fuente. La regla no se extiende a la fase estatal ni acredita elegibilidad externa.»

Ingeniería: «El perfil nacional opcional vincula tenant/edición/fase y clases de persona mediante la política fijada por bytes/hash. Builder y Gate independiente verifican la misma obligación por campo. CURP ausente se persiste NULL y no genera una decisión de verificación ficticia; el perfil estricto conserva la exigencia anterior. El scope firmado liga la política y el plan nuevo; no se reutiliza autoridad de una revisión con política anterior. Permanecen los controles de fuente, passkey, versiones, fotografía y finalidad atómica.»

Razón: corrección de requisito de negocio aclarado por la persona responsable el2026-11-03. Evidencia: mensaje de la conversación y nueva aceptación integral. El texto no se incorpora a los canones hasta revisión humana expresa y actualización de sus hashes en el registro.

## Fuera de alcance

No importación automática de los300equipos, no copia de CURP de otra persona, no reconstrucción por nombre/fecha, no elegibilidad externa ni cambio de facultad o vigencia de la passkey. No cambios de finanzas ni de los serviciosV1.

## Autorización vigente

La regla de negocio y su alcance por persona quedaron expresamente confirmados. El usuario aprobó esta especificación, los dos textos literales de canon y su implementación/publicación/despliegue. La autorización no confirma ningún plan de equipo ni ejecuta una captura en nombre de la persona. La aprobación se recibió antes de modificar código o políticas.

## Aprobación

El usuario aprobó expresamente esta especificación, sus textos de canon y ejecución hasta despliegue el2026-11-03 mediante «aprobado». La investigación terminó; implementación y verificación en curso. Sweep canon unchanged: conserva el inventario histórico y no se añaden afirmaciones de despliegue o aceptación.

## Informe de compilación y validación local

Archivos: builder privado, Gate probado/su fixture, política nacional y copia estricta original, procedencia de política, panel JS/etiquetas, pruebas de builder/Gate/SQL workflow/navegador, inventario de fixtures/verificador y selección CI, dos canones y registro. No DDL, nuevas dependencias, cambio de claves o nueva facultad. La credencial acreditada y sus fechas se conservan.

Pruebas: builder y full-runtime98PASS, cobertura builder96.07%; reejecución4PASS tras reforzar aserción. Gate195PASS, cobertura94.05%, mypy/flake8/BlackPASS. Workflow nacional completo11PASS,0omisión, incluido4UID con16PLAYER+2STAFF ySQLNULL2+2. Configuración+workflowunit128PASS, navegador13PASS, verificadorfixture27PASS. PaqueteGate562PASS,1omisión previa por endpointPGframingno configurado; la ruta modificada está cubierta porGateyworkflowPGrealesaislados. No se atribuye validación universal del repositorio a estos resultados.

Comandos: pytest tests/unit/test_ctt_staff_review_plan_builder.py tests/integration/test_ctt_full_runtime_postgres.py; pytest tests/integration/test_ctt_review_workflow_postgres.py con CTT_SCHEMA_ONLY_DIRECTORY=/tmp/ctt-schema-only-20261031-predeploy y dependenciaFIDO2aprobada; pytest tests/browser/test_ctt_staff_review_browser.py; pytest deployment/ctt_protected_review/tests/test_runtime_config.py tests/unit/test_ctt_staff_review_workflow.py; pytest tests/unit/test_ctt_ci_fixtures.py; scripts/ci/check-ctt-test-fixtures.py; scripts/ci/check-pr-quality-gate.py; scripts/check_runtime_packaging_consistency.py; scripts/ci/check-registration-operational-surface.py; scripts/ci/check-accepted-regressions.py; black/flake8/mypyselectivos, node--check y gitdiff--check.

Hallazgos corregidos: inventariofixture/hashGateobsoletos yhelpernacionalsinregistro; ahora4helpers exactos, guardverificado. La primera pruebaSQLnacional entraba directamente alGate; se añadiócohorteoperacional conbuilder/propuestasSQL/desafío/firma/serviciosrealesconUIDdistintos. CI ejecuta explícitamente ambos casos operacionalestrictoynacional yrechazaausencia,omisión,fallo,ejecuciónnacionalduplicada. La primera cobertura builder quedó83.93% por cambiosconcurrentesdemapeodelíneas; sobre fuente final96.07%,no seaceptó laprimera comocierre.

Revisión independiente read-only cerró sus dos hallazgos y no encontróbloqueantes adicionales enalcance. Los XMLdeaceptación yrecibos de hash se conservan fuera de la fuente versionada. Publicación ydespliegue se registran al efectuarse; las pruebas son técnicas/sintéticas, no UATreal ni capturaautomática.

Riesgos: sinCURP nohay deduplicación porunicidadCURP; se mantienen controlesfotográficos existentes. Los demás camposobligatorios, susevidencias, lapresenciatécnica ylasfotografías pueden seguirbloqueando. El perfilnacional sóloadmiteeltenant/edición/manifiesto autorizado; nuevasfuentes requierenasignaciónoperativa revisada. La facultad humana vence7noviembre2026,14:04Ciudad deMéxico; no se amplía enesta entrega.

Gate real publicado: Zaubern PR58, mergec9e5230e3b6e93a8501b6b54b78a5ea0e7477aec, artefactoreleases/ctt-protected-review-v2-20261103,34runtimefilesyGateSHA256afa749df613cebacf1c897c6ad3f6db0919127552a2890a609e9a40006ad2506. CIdeeserepoSKIPlegítimoporconfigmanual; nopruebacompletadaatribuida.
