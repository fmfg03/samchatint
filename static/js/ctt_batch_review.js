/* Exact-set proposals and a genuine batch WebAuthn ceremony; no child authority here. */
(() => {
    'use strict';
    const API = '/api/registration-review-batches';
    const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
    const SHA = /^[0-9a-f]{64}$/;
    const ACTIVE = new Set(['challenged', 'active', 'running', 'executing', 'consented', 'uncertain']);
    const CLOSED = new Set(['completed', 'committed', 'revoked', 'expired', 'failed']);
    const LABELS = {
        analysis_pending: 'Preparando y contrastando fuentes', preparing: 'Preparando fuentes', ready: 'Listo para preparar', prepared: 'Bloque preparado',
        pending: 'Pendiente', auto_ready: 'Apto automáticamente', automatic_verified: 'Validado automáticamente',
        autoapt: 'Apto automáticamente', qualified: 'Calificado', exception: 'Requiere contraste',
        excluded: 'Excluido del bloque', already_captured: 'Ya capturado', active: 'En ejecución',
        running: 'En ejecución', executing: 'En ejecución', consented: 'Autorizado', paused: 'Pausado',
        expired: 'Autorización vencida', revoked: 'Autorización revocada', uncertain: 'Resultado por consultar',
        committed: 'Capturado', completed: 'Completado', failed: 'No completado', blocked: 'Pendiente de resolver', suspended: 'Grupo suspendido para recalificación',
        candidate: 'Contraste propuesto; pendiente de passkey', proposed: 'Contraste propuesto; pendiente de passkey', challenged: 'Passkey pendiente; consulta el mismo intento', activated: 'Contraste acreditado',
    };
    const validUuid = value => typeof value === 'string' && UUID.test(value)
        && value !== '00000000-0000-0000-0000-000000000000';
    const clone = value => JSON.parse(JSON.stringify(value));
    const stateOf = value => value?.status || value?.state || 'pending';
    const label = value => LABELS[value] || 'Pendiente de comprobar';
    const displayValue = value => typeof value === 'string' ? ({ PRESENT: 'Presente', ABSENT: 'Ausente', UNCERTAIN: 'Por contrastar' }[value] || value) : value === null || value === undefined ? 'No proporcionado' : 'Dato pendiente de interpretar';
    const number = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
    function node(tag, text, className) {
        const result = document.createElement(tag);
        if (text !== undefined) result.textContent = text;
        if (className) result.className = className;
        return result;
    }
    function decode(value) {
        if (typeof value !== 'string' || value.length > 100000 || !/^[A-Za-z0-9_-]+$/.test(value)) throw new Error('El desafío de passkey no es válido.');
        return Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4)), c => c.charCodeAt(0));
    }
    function encode(value) {
        if (value === undefined || value === null) return null;
        let text = '';
        for (const byte of new Uint8Array(value)) text += String.fromCharCode(byte);
        return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }
    function assertionOptions(response) {
        const offered = response.options?.publicKey || response.publicKey;
        if (!offered || offered.rpId !== 'sam.chat' || offered.userVerification !== 'required') throw new Error('No se recibió un desafío verificable de passkey.');
        const publicKey = clone(offered);
        publicKey.challenge = decode(publicKey.challenge);
        if (publicKey.allowCredentials) publicKey.allowCredentials = publicKey.allowCredentials.map(item => ({ ...item, id: decode(item.id) }));
        return { publicKey };
    }
    function serialize(credential) {
        if (!credential || credential.type !== 'public-key') throw new Error('No se recibió una passkey.');
        const result = {
            id: credential.id, rawId: encode(credential.rawId), type: credential.type,
            response: {
                clientDataJSON: encode(credential.response.clientDataJSON),
                authenticatorData: encode(credential.response.authenticatorData),
                signature: encode(credential.response.signature),
                userHandle: encode(credential.response.userHandle),
            },
            clientExtensionResults: credential.getClientExtensionResults(),
        };
        if (credential.authenticatorAttachment) result.authenticatorAttachment = credential.authenticatorAttachment;
        return result;
    }

    class BatchPanel {
        constructor(root) {
            this.root = root; this.csrf = root.dataset.csrf; this.busy = false;
            this.offset = 0; this.limit = 50; this.pollCount = 0; this.pollTimer = null;
            this.sequence = 0; this.appliedSequence = 0; this.pending = false;
            this.attemptId = null; this.blockId = null; this.closed = new Map(); this.completed = new Set();
            this.proposedObservations = new Map(); this.operationKeys = new Map();
            this.correctionDetail = null; this.correctionDraft = new Map(); this.correctionDirty = false; this.correctionPending = false; this.pendingMemberMutation = null; this.invalidatedBlocks = new Set(); this.detailSerial = 0;
            this.nativePasskey = Boolean(window.isSecureContext && window.PublicKeyCredential && navigator.credentials?.get);
            for (const [selector, action] of [
                ['[data-batch-save-corrections]', () => this.saveCorrections()],
                ['[data-batch-discard-corrections]', () => this.discardCorrections()],
                ['[data-batch-report-quality]', () => this.reportQuality()],
                ['[data-batch-create-button]', () => this.create()],
                ['[data-batch-save-sample]', () => this.saveSample()],
                ['[data-batch-prepare]', () => this.prepare()],
                ['[data-batch-approve]', () => this.approve()],
                ['[data-batch-query]', () => this.refresh(true)],
                ['[data-batch-pause]', () => this.transition('pause')],
                ['[data-batch-resume]', () => this.transition('resume')],
                ['[data-batch-revoke]', () => this.transition('revoke')],
                ['[data-batch-previous]', () => this.paginate(-1)],
                ['[data-batch-next]', () => this.paginate(1)],
            ]) this.find(selector).addEventListener('click', action);
            this.find('[data-batch-confirm]').addEventListener('change', () => this.controls());
            this.find('[data-batch-edition]').addEventListener('change', () => this.renderCatalog());
            this.find('[data-batch-quality-reason]').addEventListener('input', () => this.controls());
            window.addEventListener('pagehide', () => clearTimeout(this.pollTimer));
            try {
                const initial = JSON.parse(this.find('[data-batch-initial]').textContent);
                if (initial.view) this.apply(initial.view);
                else if (initial.catalog) this.catalog(initial.catalog);
                else throw new Error('No hay un estado disponible para este lote.');
            } catch (error) { this.failure(error.message); this.controls(); }
        }
        find(selector) { return this.root.querySelector(selector); }
        status(message) { this.find('[data-batch-status]').textContent = message; }
        failure(message) { const area = this.find('[data-batch-error]'); area.textContent = message; area.hidden = false; }
        clearError() { this.find('[data-batch-error]').hidden = true; }
        key(action) {
            if (!this.operationKeys.has(action)) this.operationKeys.set(action, crypto.randomUUID());
            return this.operationKeys.get(action);
        }
        async request(path, body) {
            const config = { credentials: 'same-origin', cache: 'no-store', headers: {} };
            if (body !== undefined) {
                config.method = 'POST'; config.headers = { 'Content-Type': 'application/json', 'X-CTT-BATCH-CSRF': this.csrf };
                config.body = JSON.stringify(body);
            }
            const response = await fetch(API + path, config);
            let value;
            try { value = await response.json(); } catch (_) { throw new Error('No se recibió un estado verificable. Consulta antes de reintentar.'); }
            if (!response.ok) {
                const detail = value.detail || value; const error = new Error(detail.message || 'No se pudo completar la acción. Consulta el estado del lote.'); error.status = response.status; error.code = detail.error; throw error;
            }
            if (value.batch_review_csrf) this.csrf = value.batch_review_csrf;
            return value;
        }
        async runAction(action) {
            if (this.busy) return;
            this.busy = true; this.clearError(); this.controls();
            try { await action(); }
            catch (error) {
                if (this.correctionPending && this.pendingMemberMutation?.kind === 'correction' && [400, 403, 409, 413, 415].includes(error.status)) { this.correctionPending = false; this.pendingMemberMutation = null; }
                this.failure(error.name === 'NotAllowedError' ? 'La passkey se canceló o no estuvo disponible. Consulta el estado; no se enviará otra aprobación automáticamente.' : error.message);
            }
            finally { this.busy = false; this.controls(); }
        }
        catalog(value) {
            this.catalogView = clone(value); this.find('[data-batch-create]').hidden = false;
            this.find('[data-batch-run]').hidden = true;
            const editions = this.find('[data-batch-edition]'); editions.replaceChildren();
            const seen = new Set();
            for (const item of value.source_manifests || []) {
                if (!validUuid(item.edition_id) || !SHA.test(item.manifest_sha256) || seen.has(item.edition_id)) continue;
                seen.add(item.edition_id); const option = node('option', item.edition_label || 'Copa Telmex 2026'); option.value = item.edition_id; editions.append(option);
            }
            this.renderCatalog();
            const runs = this.find('[data-batch-existing]'); runs.replaceChildren();
            for (const item of value.runs || []) {
                if (!validUuid(item.run_id)) continue;
                const row = node('p'); const link = node('a', `${item.label || 'Preparación de equipos'} · ${label(stateOf(item))}`);
                link.href = `/registration-review-batches/${item.run_id}`; row.append(link); runs.append(row);
            }
            if (!runs.childElementCount) runs.append(node('p', 'No hay preparaciones anteriores.', 'batch-empty'));
            this.status('Selecciona las fuentes disponibles para preparar el conjunto.'); this.controls();
        }
        renderCatalog() {
            const area = this.find('[data-batch-catalog]'); area.replaceChildren();
            for (const item of this.catalogView?.source_manifests || []) {
                if (item.edition_id !== this.find('[data-batch-edition]').value || !SHA.test(item.manifest_sha256)) continue;
                const row = node('label'); const input = node('input'); input.type = 'checkbox'; input.value = item.manifest_sha256; input.dataset.batchManifest = ''; input.addEventListener('change', () => this.controls());
                row.append(input, document.createTextNode(item.label || 'Fuentes acreditadas'));
                row.append(node('small', `${number(item.pdf_count)} PDF · ${number(item.document_count)} expedientes`)); area.append(row);
            }
            if (!area.childElementCount) area.append(node('p', 'No hay fuentes habilitadas para tu cuenta en esta edición.', 'batch-empty'));
            this.controls();
        }
        async create() {
            await this.runAction(async () => {
                const sources = [...this.root.querySelectorAll('[data-batch-manifest]:checked')].map(input => input.value);
                const result = await this.request('/runs', { edition_id: this.find('[data-batch-edition]').value, source_manifest_hashes: sources, idempotency_key: this.key('create') });
                if (!validUuid(result.run_id)) throw new Error('No se recibió una referencia válida de preparación.');
                window.location.assign(`/registration-review-batches/${result.run_id}`);
            });
        }
        selectedBlock() {
            const blocks = this.view?.blocks || [];
            return blocks.find(block => ACTIVE.has(stateOf(block)) || stateOf(block) === 'paused')
                || [...blocks].reverse().find(block => ['ready', 'prepared'].includes(stateOf(block)))
                || blocks[blocks.length - 1] || null;
        }
        apply(payload) {
            const view = payload.batch_review_view || payload;
            if (!view || !validUuid(view.run_id) || (this.view && view.run_id !== this.view.run_id)
                || !Number.isSafeInteger(view.version) || view.version < 0) throw new Error('El estado no corresponde a este lote.');
            if (this.view && view.version < this.view.version) return;
            const incoming = clone(view);
            for (const block of incoming.blocks || []) {
                if (!validUuid(block.block_id) || !Number.isSafeInteger(block.version)) throw new Error('El bloque no tiene una referencia verificable.');
                const old = this.closed.get(block.block_id);
                if (old && (block.version < old.version || !CLOSED.has(stateOf(block)))) Object.assign(block, old);
                if (CLOSED.has(stateOf(block))) this.closed.set(block.block_id, clone(block));
            }
            this.view = incoming;
            this.find('[data-batch-create]').hidden = true; this.find('[data-batch-run]').hidden = false;
            this.block = this.selectedBlock();
            if (this.block && this.blockId !== this.block.block_id) {
                this.blockId = this.block.block_id; this.attemptId = null; this.pending = false;
                this.find('[data-batch-confirm]').checked = false;
            }
            if (this.block?.attempt_id && validUuid(this.block.attempt_id)) this.attemptId = this.block.attempt_id;
            if (this.block && (CLOSED.has(stateOf(this.block)) || stateOf(this.block) === 'paused' || ['active', 'running', 'executing', 'consented'].includes(stateOf(this.block)))) this.pending = false;
            this.renderCounts(); this.renderQuality(); this.renderSample(); this.renderSelection(); this.renderBlock(); this.renderMembers();
            this.status(`${label(stateOf(this.view))}. ${this.pending ? 'Consulta del mismo intento pendiente.' : 'Los resultados confirmados se conservan.'}`);
            this.controls();
            if (this.block && ['completed', 'committed'].includes(stateOf(this.block))) this.notice(this.block);
            if ((this.block && ACTIVE.has(stateOf(this.block))) || ['analysis_pending', 'preparing'].includes(stateOf(this.view))) this.schedule();
        }
        renderCounts() {
            const counts = this.view.counts || {}; const area = this.find('[data-batch-counts]'); area.replaceChildren();
            for (const [title, keys] of [['Identificados', ['identified', 'total']], ['Capturados nuevos', ['new_confirmed', 'committed']], ['Ya capturados', ['already_captured']], ['Excepciones', ['excluded', 'exceptions']]]) {
                const card = node('div', undefined, 'batch-metric'); const value = keys.map(key => counts[key]).find(item => Number.isSafeInteger(item));
                card.append(node('strong', String(number(value))), node('span', title)); area.append(card);
            }
        }
        renderQuality() {
            const area = this.find('[data-batch-quality]'); area.replaceChildren(); const metrics = this.view.metrics || this.view.qualification?.metrics || {};
            const docNumerator = metrics.teams?.numerator ?? metrics.autoaptos_al_corte ?? metrics.auto_ready_documents ?? metrics.autoaptos_initial;
            const docDenominator = metrics.teams?.denominator ?? metrics.expedientes_pendientes_identificados_al_corte ?? metrics.pending_documents ?? metrics.pending_initial;
            const pageNumerator = metrics.pages?.numerator ?? metrics.paginas_unicas_de_autoaptos_al_corte ?? metrics.auto_ready_pages;
            const pageDenominator = metrics.pages?.denominator ?? metrics.paginas_de_cedulas_pendientes_al_corte ?? metrics.pending_pages;
            const ratio = (a, b) => Number.isSafeInteger(a) && Number.isSafeInteger(b) && b > 0 ? `${a}/${b} (${(a * 100 / b).toFixed(1)}%)` : 'Todavía sin medición';
            area.append(node('p', `Equipos sin corrección manual: ${ratio(docNumerator, docDenominator)}`), node('p', `Páginas cubiertas automáticamente: ${ratio(pageNumerator, pageDenominator)}`));
            const qualification = this.view.qualification || {};
            area.append(node('p', `Contraste: ${label(stateOf(qualification))}`));
            if (metrics.cutoff || metrics.cutoff_at) area.append(node('p', `Medición inicial: ${metrics.cutoff || metrics.cutoff_at}`, 'batch-caption'));
        }
        sourceButtons(container, item) {
            if (!SHA.test(item.document_id || '')) return;
            const pages = item.source_pages || item.pages || [{ page_index: 1 }];
            for (const page of pages) {
                const index = Number(page.page_index ?? page);
                if (!Number.isSafeInteger(index) || index < 1 || index > 4) continue;
                const button = node('button', `Ver fuente ${index}`); button.type = 'button'; button.addEventListener('click', () => this.source(item, index)); container.append(button);
            }
        }
        renderSample() {
            const area = this.find('[data-batch-sample]'); area.replaceChildren(); const sample = this.view.sample || [];
            for (const item of sample) {
                if (!SHA.test(item.document_id || '')) continue;
                const row = node('tr'); const title = node('td'); title.append(node('strong', item.team_name || 'Cédula de muestra')); this.sourceButtons(title, item);
                const selectCell = node('td'); const select = node('select'); select.dataset.sampleDocument = item.document_id; select.setAttribute('aria-label', `Contraste de ${item.team_name || 'cédula'}`);
                for (const [value, text] of [['', 'Selecciona un resultado'], ['matches', 'Sin falsa admisión crítica'], ['critical_error', 'Detecté una falsa admisión crítica'], ['exception_confirmed', 'Correctamente apartado como excepción']]) { const option = node('option', text); option.value = value; select.append(option); }
                const notesCell = node('td'); const notes = node('textarea'); notes.maxLength = 2000; notes.setAttribute('aria-label', `Observación de ${item.team_name || 'cédula'}`);
                const prior = this.proposedObservations.get(item.document_id) || item.observation;
                if (prior) { select.value = prior.outcome || ''; notes.value = prior.notes || ''; }
                const changed = () => { this.proposedObservations.set(item.document_id, { outcome: select.value, notes: notes.value }); this.controls(); };
                select.addEventListener('change', changed); notes.addEventListener('input', changed);
                selectCell.append(select); notesCell.append(notes); row.append(title, selectCell, notesCell); area.append(row);
            }
            if (!area.childElementCount) { const row = node('tr'); const cell = node('td', 'La muestra aparecerá al terminar la preparación automática.'); cell.colSpan = 3; row.append(cell); area.append(row); }
            this.find('[data-batch-sample-state]').textContent = this.view.qualification?.candidate_id || this.view.qualification?.candidate_hash ? 'Contraste propuesto guardado. La passkey acreditará la declaración vinculada al conjunto.' : 'Guardar el contraste no crea equipos ni declara una revisión humana de todos los datos.';
        }
        nextMembers() {
            if (Array.isArray(this.view?.next_member_ids)) return this.view.next_member_ids.filter(validUuid).slice(0, this.nextLimit());
            const eligible = (this.view?.members || []).filter(item => item.auto_ready === true || ['auto_ready', 'automatic_verified', 'qualified', 'autoapt', 'ready'].includes(stateOf(item)));
            const manifest = eligible[0]?.manifest_sha256;
            return eligible.filter(item => item.manifest_sha256 === manifest && validUuid(item.member_id)).slice(0, this.nextLimit()).map(item => item.member_id);
        }
        nextLimit() {
            if (this.view?.next_block_limit === 5 || this.view?.next_block_limit === 50) return this.view.next_block_limit;
            return (this.view?.blocks || []).some(block => ['completed', 'committed'].includes(stateOf(block))) ? 50 : 5;
        }
        renderSelection() {
            const ids = new Set(this.nextMembers()); const area = this.find('[data-batch-selection]'); area.replaceChildren();
            this.find('[data-batch-selection-summary]').textContent = `${ids.size} equipos propuestos; límite de este bloque: ${this.nextLimit()}. La preparación fijará la selección exacta.`;
            const list = node('ul');
            for (const item of this.view.members || []) if (ids.has(item.member_id)) list.append(node('li', item.team_name || 'Equipo con fuente identificada'));
            area.append(list);
        }
        renderBlock() {
            const area = this.find('[data-batch-block-summary]'); area.replaceChildren();
            if (!this.block) { area.append(node('p', 'Todavía no hay un bloque preparado.')); return; }
            area.append(node('p', `${label(stateOf(this.block))} · ${number(this.block.member_count)} equipos`));
            if (this.block.expires_at) area.append(node('p', `Vence: ${this.block.expires_at}`));
            const names = this.block.members || [];
            if (names.length) { const list = node('ul'); for (const item of names) { const counts = Number.isSafeInteger(item.players_count) && Number.isSafeInteger(item.staff_count) ? ` · ${item.players_count} jugadores y ${item.staff_count} integrantes del cuerpo técnico` : ''; list.append(node('li', (item.team_name || 'Equipo del bloque') + counts)); } area.append(list); }
            if (this.block.root_hash) { const fingerprint = node('p', `Huella del conjunto: ${this.block.root_hash}`, 'batch-caption'); fingerprint.style.overflowWrap = 'anywhere'; area.append(fingerprint); }
            if (!this.nativePasskey) area.append(node('p', 'Para autorizar, abre esta página en un navegador seguro con tu passkey disponible.', 'batch-muted'));
        }
        renderMembers() {
            const area = this.find('[data-batch-members]'); area.replaceChildren();
            for (const item of this.view.members || []) {
                const row = node('tr'); const name = node('td', item.team_name || 'Equipo con fuente identificada'); const status = node('td', label(stateOf(item))); const detail = node('td');
                for (const issue of item.issues || []) detail.append(node('p', typeof issue === 'string' ? issue : issue.message || 'Requiere contraste de fuente.'));
                this.sourceButtons(detail, item);
                if (validUuid(item.member_id)) { const button = node('button', stateOf(item) === 'exception' ? 'Revisar esta excepción' : 'Contrastar datos de esta cédula'); button.type = 'button'; button.addEventListener('click', () => this.openMember(item.member_id)); detail.append(button); }
                if (validUuid(item.result_id)) detail.append(node('span', 'Captura confirmada; comprobante registrado.'));
                row.append(name, status, detail); area.append(row);
            }
            const total = number(this.view.total_members ?? this.view.counts?.identified ?? this.view.counts?.total);
            this.find('[data-batch-page]').textContent = total ? `${this.offset + 1}–${Math.min(this.offset + this.limit, total)} de ${total}` : 'Sin expedientes en esta página';
            this.find('[data-batch-previous]').disabled = this.busy || this.offset === 0;
            this.find('[data-batch-next]').disabled = this.busy || this.offset + this.limit >= total;
        }
        source(item, pageIndex) {
            if (this.correctionDetail && this.correctionDetail.document_id !== item.document_id) {
                if (this.correctionDirty) { this.failure('Guarda o descarta los cambios locales antes de cambiar de cédula.'); return; }
                this.correctionDetail = null; this.correctionDraft.clear(); this.find('[data-batch-corrections]').hidden = true; ++this.detailSerial; this.controls();
            }
            const panel = this.find('[data-batch-source-panel]'); panel.hidden = false;
            this.find('[data-batch-source-title]').textContent = item.team_name || 'Fuente original de la cédula';
            const image = this.find('[data-batch-source-image]'); image.src = `${API}/runs/${this.view.run_id}/sources/${item.document_id}/pages/${pageIndex}`;
            image.onerror = () => this.failure('No se pudo leer esta fuente. Conserva el caso pendiente; no lo autorices por suposición.');
            const pages = this.find('[data-batch-source-pages]'); pages.replaceChildren(); this.sourceButtons(pages, item);
            const fields = this.find('[data-batch-source-fields]'); fields.replaceChildren();
            for (const field of item.fields || []) { fields.append(node('dt', field.label || field.name || 'Dato propuesto'), node('dd', displayValue(field.value))); }
            if (!fields.childElementCount) fields.append(node('dd', 'Contrasta los datos propuestos de esta cédula con la página original.'));
            panel.scrollIntoView({ block: 'start', behavior: 'auto' });
        }
        async openMember(memberId) {
            if (this.busy || this.correctionPending) return;
            if (this.correctionDirty) { this.failure('Guarda o descarta los cambios locales antes de cambiar de cédula.'); return; }
            const serial = ++this.detailSerial;
            try {
                const detail = await this.request(`/runs/${this.view.run_id}/members/${memberId}`);
                if (serial !== this.detailSerial) return;
                if (detail.run_id !== this.view.run_id || detail.member_id !== memberId || !SHA.test(detail.document_id || '') || !Number.isSafeInteger(detail.version)) throw new Error('La cédula no corresponde al lote.');
                this.correctionDetail = clone(detail); this.correctionDirty = false; this.correctionDraft.clear();
                this.resetCorrectionDraft();
                this.find('[data-batch-quality-reason]').value = '';
                this.source(detail, Number(detail.source_pages?.[0]?.page_index || 1));
                this.renderCorrections(); this.controls();
            } catch (error) { this.failure(error.message); }
        }
        resetCorrectionDraft() {
            this.correctionDraft.clear();
            for (const field of this.correctionDetail?.fields || []) this.correctionDraft.set(field.field_id, {
                value: typeof field.value === 'string' ? field.value : '',
                rectangle: field.rectangle || field.card_bounds ? clone(field.rectangle || field.card_bounds) : null,
                geometryChanged: false,
            });
        }
        changedCorrection(field) {
            const draft = this.correctionDraft.get(field.field_id);
            return draft && (draft.geometryChanged || draft.value !== (typeof field.value === 'string' ? field.value : ''));
        }
        markCorrectionDirty() {
            this.correctionDirty = (this.correctionDetail?.fields || []).some(field => this.changedCorrection(field));
            this.find('[data-batch-confirm]').checked = false; this.controls();
        }
        rectangleValid(field, rect) {
            const keys = ['page_index', 'x', 'y', 'width', 'height'];
            if (!rect || Object.keys(rect).length !== 5 || !keys.every(key => Number.isSafeInteger(rect[key]))) return false;
            const card = field.card_bounds; const page = this.correctionDetail?.source_pages?.find(item => item.page_index === rect.page_index);
            if (!card || Object.keys(card).length !== 5 || !keys.every(key => Number.isSafeInteger(card[key])) || rect.page_index !== card.page_index || !page || !Number.isSafeInteger(page.width) || !Number.isSafeInteger(page.height)) return false;
            return rect.x >= 0 && rect.y >= 0 && rect.width > 0 && rect.height > 0 && rect.x + rect.width <= page.width && rect.y + rect.height <= page.height
                && rect.x >= card.x && rect.y >= card.y && rect.x + rect.width <= card.x + card.width && rect.y + rect.height <= card.y + card.height;
        }
        correctionValue(field) {
            const draft = this.correctionDraft.get(field.field_id); const kind = field.kind || 'source_field';
            if (!draft) throw new Error('No hay una propuesta verificable para este dato.');
            if (kind === 'source_field') {
                if (!draft.geometryChanged) return draft.value || null;
                if (!this.rectangleValid(field, draft.rectangle)) throw new Error('El recorte debe usar píxeles enteros dentro de la página y del espacio original de esta persona.');
                return { kind: 'source_field', value: draft.value || null, rectangle: clone(draft.rectangle) };
            }
            if (!['PRESENT', 'ABSENT'].includes(draft.value) || (draft.value === 'ABSENT' && field.allow_absent === false)) throw new Error('La fuente conserva datos o una fotografía aportada; no puede marcarse ausente.');
            if (kind === 'presence') return { kind: 'presence', presence: draft.value };
            if (kind === 'photo') {
                if (!this.rectangleValid(field, draft.rectangle)) throw new Error('El recorte fotográfico debe quedar dentro del espacio y página originales de esta persona.');
                return { kind: 'photo', presence: draft.value, rectangle: clone(draft.rectangle) };
            }
            throw new Error('Este tipo de excepción requiere resolución del responsable.');
        }
        previewCorrection(field, draft, canvas, message) {
            if (!this.rectangleValid(field, draft.rectangle)) { canvas.hidden = true; message.textContent = 'Recorte pendiente: usa píxeles enteros dentro del espacio de esta persona.'; return; }
            const detail = this.correctionDetail; const rect = clone(draft.rectangle); const image = new Image();
            message.textContent = 'Leyendo el recorte del original…';
            image.onload = () => {
                if (detail !== this.correctionDetail || !canvas.isConnected || JSON.stringify(rect) !== JSON.stringify(draft.rectangle)) return;
                if (rect.x + rect.width > image.naturalWidth || rect.y + rect.height > image.naturalHeight) { message.textContent = 'El original no coincide con las dimensiones declaradas. Mantén la excepción pendiente.'; return; }
                const scale = Math.min(1, 800 / rect.width, 800 / rect.height);
                canvas.width = Math.max(1, Math.round(rect.width * scale)); canvas.height = Math.max(1, Math.round(rect.height * scale));
                canvas.getContext('2d').drawImage(image, rect.x, rect.y, rect.width, rect.height, 0, 0, canvas.width, canvas.height);
                canvas.hidden = false; message.textContent = `Recorte de la página ${rect.page_index}: ${rect.width} × ${rect.height} píxeles del original.`;
            };
            image.onerror = () => { canvas.hidden = true; message.textContent = 'No se pudo leer el original. Conserva la excepción pendiente.'; };
            image.src = `${API}/runs/${detail.run_id}/sources/${detail.document_id}/pages/${rect.page_index}`;
        }
        renderCorrectionGeometry(field, draft, row, enabled) {
            if (!draft.rectangle || !field.card_bounds) return;
            const kind = field.kind || 'source_field'; const area = node('div', undefined, 'batch-correction-geometry');
            const inputs = node('div', undefined, 'batch-pixel-grid');
            const message = node('p', '', 'batch-caption'); message.setAttribute('aria-live', 'polite');
            const canvas = node('canvas'); canvas.hidden = true; canvas.dataset.correctionPreview = field.field_id;
            canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', `Recorte original: ${field.label || field.field_id}`);
            const box = field.card_bounds;
            area.append(node('p', `Página fija ${box.page_index}. Espacio original: x ${box.x}, y ${box.y}, ancho ${box.width}, alto ${box.height} píxeles.`, 'batch-caption'));
            let toggle = null;
            if (kind === 'source_field') {
                const label = node('label'); toggle = node('input'); toggle.type = 'checkbox'; toggle.dataset.correctionGeometry = field.field_id; toggle.checked = draft.geometryChanged; toggle.disabled = !enabled;
                label.append(toggle, document.createTextNode('Proponer un ajuste del recorte de este dato')); row.append(label);
                toggle.addEventListener('change', () => { draft.geometryChanged = toggle.checked; for (const input of inputs.querySelectorAll('input')) input.disabled = !enabled || !toggle.checked; this.markCorrectionDirty(); });
            }
            for (const [key, caption] of [['x', 'X'], ['y', 'Y'], ['width', 'Ancho'], ['height', 'Alto']]) {
                const label = node('label', caption); const input = node('input'); input.type = 'number'; input.step = '1'; input.min = key === 'x' || key === 'y' ? '0' : '1'; input.value = draft.rectangle[key]; input.dataset.correctionCoordinate = key; input.dataset.correctionOwner = field.field_id; input.dataset.requiresGeometry = kind === 'source_field' ? 'true' : 'false'; input.disabled = !enabled || (toggle && !toggle.checked);
                input.addEventListener('input', () => {
                    draft.rectangle[key] = input.value.trim() === '' ? null : Number(input.value); if (kind === 'photo') draft.geometryChanged = true;
                    this.markCorrectionDirty(); if (!canvas.hidden) this.previewCorrection(field, draft, canvas, message);
                });
                label.append(input); inputs.append(label);
            }
            const preview = node('button', 'Ver recorte del original'); preview.type = 'button'; preview.dataset.correctionPreviewButton = field.field_id;
            preview.addEventListener('click', () => this.previewCorrection(field, draft, canvas, message));
            area.append(inputs, preview, message, canvas); row.append(area);
        }
        renderCorrections() {
            const detail = this.correctionDetail;
            const panel = this.find('[data-batch-corrections]'); panel.hidden = !detail;
            if (!detail) return;
            this.find('[data-batch-corrections-title]').textContent = detail.team_name || 'Contrastar cédula';
            const fields = this.find('[data-batch-correction-fields]'); fields.replaceChildren();
            const editable = stateOf(detail) === 'exception';
            for (const field of detail.fields || []) {
                const kind = field.kind || 'source_field'; const enabled = editable && field.editable === true && ['source_field', 'photo', 'presence'].includes(kind) && (field.value === null || field.value === undefined || typeof field.value === 'string');
                const draft = this.correctionDraft.get(field.field_id); const row = node('fieldset', undefined, 'batch-correction-field'); row.dataset.correctionEditable = enabled ? 'true' : 'false';
                row.append(node('legend', field.label || field.field_id || 'Dato propuesto'));
                const label = node('label', kind === 'photo' ? 'Fotografía en este espacio' : kind === 'presence' ? 'Persona en este espacio admitido' : 'Valor según el original');
                const input = node(kind === 'source_field' ? 'input' : 'select'); input.dataset.correctionField = field.field_id; input.disabled = !enabled;
                if (kind === 'source_field') { input.type = 'text'; input.maxLength = 500; input.value = draft.value; }
                else {
                    input.append(node('option', 'Selecciona lo observado'));
                    input.firstChild.value = '';
                    for (const [value, caption] of [['PRESENT', 'Presente'], ['ABSENT', 'Ausente']]) { const option = node('option', caption); option.value = value; option.disabled = value === 'ABSENT' && field.allow_absent === false; input.append(option); }
                    input.value = draft.value;
                }
                input.addEventListener(kind === 'source_field' ? 'input' : 'change', () => { draft.value = input.value; this.markCorrectionDirty(); });
                label.append(input); row.append(label);
                if (field.allow_absent === false && kind !== 'source_field') row.append(node('p', 'La fuente conserva datos o una fotografía aportada. Su ausencia no puede proponerse.', 'batch-caption'));
                if (kind !== 'presence') this.renderCorrectionGeometry(field, draft, row, enabled);
                fields.append(row);
            }
            this.find('[data-batch-quality-report-panel]').hidden = editable;
            this.find('[data-batch-correction-state]').textContent = editable ? 'Corrección propuesta; se confirmará con la passkey del bloque. Guardarla invalida la revisión anterior y exige recalificar.' : 'Esta cédula no admite correcciones bajo su aprobación actual. Si detectas una discrepancia crítica, informa el problema para detener el grupo afectado.';
        }
        discardCorrections() {
            if (!this.correctionDetail || this.correctionPending) return;
            this.resetCorrectionDraft();
            this.correctionDirty = false; this.find('[data-batch-confirm]').checked = false; this.renderCorrections(); this.controls();
        }
        async saveCorrections() {
            const detail = this.correctionDetail;
            if (!detail || stateOf(detail) !== 'exception' || !this.correctionDirty || this.correctionPending) return;
            await this.runAction(async () => {
                const edits = (detail.fields || []).filter(field => field.editable === true && this.changedCorrection(field)).map(field => ({ field_id: field.field_id, value: this.correctionValue(field) }));
                if (!edits.length) throw new Error('No hay cambios propuestos para guardar.');
                this.correctionPending = true; this.pendingMemberMutation = { member_id: detail.member_id, version: detail.version, kind: 'correction' }; for (const block of this.view.blocks || []) this.invalidatedBlocks.add(block.block_id); this.find('[data-batch-confirm]').checked = false; this.controls();
                const result = await this.request(`/runs/${this.view.run_id}/members/${detail.member_id}/corrections`, { expected_version: detail.version, edits });
                if (result.run_id !== this.view.run_id || result.member_id !== detail.member_id || !Number.isSafeInteger(result.version) || result.version <= detail.version || result.status !== 'analysis_pending') throw new Error('No se recibió un estado verificable de la corrección. Consulta antes de continuar.');
                this.correctionDirty = false; this.correctionPending = false; this.pendingMemberMutation = null; this.correctionDetail = null; this.correctionDraft.clear(); this.find('[data-batch-corrections]').hidden = true;
                await this.refresh(); this.status('Corrección propuesta guardada. Se recalificará y necesitará una nueva passkey sobre el bloque exacto; todavía no se ha capturado.');
            });
        }
        async reportQuality() {
            const detail = this.correctionDetail; const reason = this.find('[data-batch-quality-reason]').value.trim();
            if (!detail || stateOf(detail) === 'exception' || !reason || this.correctionPending) return;
            await this.runAction(async () => {
                this.correctionPending = true; this.pendingMemberMutation = { member_id: detail.member_id, version: detail.version, kind: 'quality' }; for (const block of this.view.blocks || []) this.invalidatedBlocks.add(block.block_id); this.find('[data-batch-confirm]').checked = false; this.controls();
                const result = await this.request(`/runs/${this.view.run_id}/members/${detail.member_id}/quality-report`, { expected_version: detail.version, reason });
                if (result.run_id !== this.view.run_id || result.member_id !== detail.member_id || !Number.isSafeInteger(result.version) || result.version <= detail.version || !['suspended', 'analysis_pending', 'blocked'].includes(stateOf(result))) throw new Error('No se recibió confirmación verificable del informe. Consulta el estado.');
                this.correctionPending = false; this.pendingMemberMutation = null; this.correctionDetail = null; this.find('[data-batch-corrections]').hidden = true;
                await this.refresh(); this.status('Discrepancia crítica informada. El grupo queda detenido para recalificación; las capturas confirmadas se conservan.');
            });
        }
        async saveSample() {
            await this.runAction(async () => {
                const observations = (this.view.sample || []).map(item => ({ document_id: item.document_id, ...this.proposedObservations.get(item.document_id) }));
                await this.request(`/runs/${this.view.run_id}/sample`, { expected_run_version: this.view.version, observations });
                await this.refresh();
            });
        }
        async prepare() {
            await this.runAction(async () => {
                const result = await this.request(`/runs/${this.view.run_id}/prepare`, { expected_run_version: this.view.version, member_ids: this.nextMembers(), idempotency_key: this.key(`prepare:${this.view.version}`) });
                if (result.run_id !== this.view.run_id || !validUuid(result.block_id)) throw new Error('No se recibió un bloque verificable. Consulta el estado.');
                this.pending = false; this.find('[data-batch-confirm]').checked = false; await this.refresh();
            });
        }
        async approve() {
            if (!this.block || ['analysis_pending', 'preparing', 'suspended', 'blocked'].includes(stateOf(this.view)) || this.invalidatedBlocks.has(this.block.block_id) || this.pending || this.correctionDirty || this.correctionPending || !this.find('[data-batch-confirm]').checked || !this.nativePasskey || !['prepared', 'ready'].includes(stateOf(this.block))) return;
            await this.runAction(async () => {
                const block = clone(this.block); this.pending = true; this.controls();
                const offered = await this.request(`/blocks/${block.block_id}/challenge`, { expected_block_version: block.version, confirmed: true });
                if (offered.block_id !== block.block_id || !validUuid(offered.attempt_id)) throw new Error('El desafío no corresponde a este bloque. Consulta el estado.');
                this.attemptId = offered.attempt_id;
                const credential = await navigator.credentials.get(assertionOptions(offered));
                const result = await this.request(`/blocks/${block.block_id}/assertion`, { attempt_id: this.attemptId, assertion: serialize(credential) });
                if (result.block_id !== block.block_id) throw new Error('La respuesta no corresponde a este bloque. Consulta el estado.');
                this.find('[data-batch-confirm]').checked = false; this.pollCount = 0; await this.refresh();
            });
        }
        async refresh(manual = false) {
            if (!this.view) return;
            clearTimeout(this.pollTimer); const sequence = ++this.sequence;
            try {
                if (manual && this.block && validUuid(this.attemptId)) {
                    const attempt = await this.request(`/blocks/${this.block.block_id}/attempts/${this.attemptId}`);
                    if (attempt.block_id !== this.block.block_id || attempt.attempt_id !== this.attemptId) throw new Error('La consulta no corresponde al intento actual.');
                    if (['failed', 'expired', 'rejected', 'cancelled', 'revoked', 'completed', 'committed'].includes(stateOf(attempt))) this.pending = false;
                }
                if (manual && this.correctionPending && this.pendingMemberMutation) {
                    const pending = this.pendingMemberMutation;
                    const latest = await this.request(`/runs/${this.view.run_id}/members/${pending.member_id}`);
                    if (latest.run_id !== this.view.run_id || latest.member_id !== pending.member_id) throw new Error('El estado no corresponde a la corrección consultada.');
                    if (Number.isSafeInteger(latest.version) && latest.version > pending.version) {
                        this.correctionPending = false; this.pendingMemberMutation = null; this.correctionDirty = false; this.correctionDetail = null; this.correctionDraft.clear(); this.find('[data-batch-corrections]').hidden = true;
                    }
                }
                const response = await this.request(`/runs/${this.view.run_id}?offset=${this.offset}&limit=${this.limit}`);
                if (sequence < this.appliedSequence) return;
                this.appliedSequence = sequence; this.clearError(); this.apply(response);
            } catch (error) { this.failure(error.message); this.controls(); }
        }
        async transition(action) {
            const allowed = { pause: ['active', 'running', 'executing', 'consented'], resume: ['paused'], revoke: ['prepared', 'ready', 'challenged', 'active', 'running', 'executing', 'consented', 'paused'] };
            if (!this.block || !allowed[action]?.includes(stateOf(this.block)) || CLOSED.has(stateOf(this.block))) return;
            await this.runAction(async () => {
                const block = this.block;
                const result = await this.request(`/blocks/${block.block_id}/${action}`, { expected_version: block.version, idempotency_key: this.key(`${action}:${block.block_id}:${block.version}`) });
                if (result.block_id !== block.block_id) throw new Error('La respuesta no corresponde a este bloque.');
                this.pending = false; this.find('[data-batch-confirm]').checked = false; await this.refresh();
            });
        }
        async paginate(direction) { this.offset = Math.max(0, this.offset + direction * this.limit); await this.refresh(); }
        schedule() {
            clearTimeout(this.pollTimer);
            if (++this.pollCount > 60) { this.status('La ejecución sigue pendiente. Usa Consultar estado para continuar su seguimiento.'); return; }
            this.pollTimer = setTimeout(() => this.refresh(), 2000);
        }
        notice(block) {
            if (this.completed.has(block.block_id)) return;
            this.completed.add(block.block_id); clearTimeout(this.pollTimer); this.pending = false;
            const area = this.find('[data-batch-complete]'); area.replaceChildren(node('strong', 'Bloque capturado'), node('p', `${number(block.member_count)} equipos confirmados. Sus capturas no se repetirán al continuar.`));
            area.hidden = false; this.clearError(); area.focus({ preventScroll: true }); area.scrollIntoView({ block: 'center', behavior: 'auto' });
        }
        controls() {
            const selectedSources = this.root.querySelectorAll('[data-batch-manifest]:checked').length;
            this.find('[data-batch-create-button]').disabled = this.busy || !selectedSources;
            const sample = this.view?.sample || [];
            this.find('[data-batch-save-sample]').disabled = this.busy || !sample.length || !sample.every(item => ['matches', 'critical_error', 'exception_confirmed'].includes(this.proposedObservations.get(item.document_id)?.outcome));
            const state = stateOf(this.block); const open = ACTIVE.has(state) || state === 'paused';
            const analysisReady = !['analysis_pending', 'preparing', 'suspended', 'blocked'].includes(stateOf(this.view));
            const ready = Boolean(analysisReady && this.block && ['prepared', 'ready'].includes(state) && this.block.ready_to_approve !== false && !this.invalidatedBlocks.has(this.block.block_id));
            this.find('[data-batch-prepare]').disabled = !analysisReady || this.busy || this.pending || this.correctionDirty || this.correctionPending || open || !this.nextMembers().length;
            this.find('[data-batch-confirm]').disabled = this.busy || this.pending || this.correctionDirty || this.correctionPending || !ready;
            this.find('[data-batch-approve]').disabled = this.busy || this.pending || this.correctionDirty || this.correctionPending || !ready || !this.nativePasskey || !this.find('[data-batch-confirm]').checked;
            this.find('[data-batch-pause]').disabled = this.busy || !['active', 'running', 'executing', 'consented'].includes(state);
            this.find('[data-batch-resume]').disabled = this.busy || state !== 'paused';
            this.find('[data-batch-revoke]').disabled = this.busy || !(open || ['prepared', 'ready'].includes(state));
            this.find('[data-batch-query]').disabled = this.busy || !this.view;
            for (const row of this.find('[data-batch-correction-fields]').querySelectorAll('[data-correction-editable]')) {
                const enabled = row.dataset.correctionEditable === 'true' && !this.busy && !this.correctionPending && stateOf(this.correctionDetail) === 'exception';
                for (const input of row.querySelectorAll('input,select')) {
                    const draft = this.correctionDraft.get(input.dataset.correctionOwner);
                    input.disabled = !enabled || (input.dataset.requiresGeometry === 'true' && !draft?.geometryChanged);
                }
            }
            this.find('[data-batch-save-corrections]').disabled = this.busy || this.correctionPending || !this.correctionDirty || !this.correctionDetail || stateOf(this.correctionDetail) !== 'exception' || open;
            this.find('[data-batch-discard-corrections]').disabled = this.busy || this.correctionPending || !this.correctionDirty;
            this.find('[data-batch-report-quality]').disabled = this.busy || this.correctionPending || !this.correctionDetail || stateOf(this.correctionDetail) === 'exception' || !this.find('[data-batch-quality-reason]').value.trim();

        }
    }
    for (const root of document.querySelectorAll('[data-batch-review]')) new BatchPanel(root);
})();
