/* Browser proposals and WebAuthn ceremonies. Authority stays in the private service. */
(() => {
    'use strict';

    const TEAM = {
        name: 'Equipo', state: 'Estado', municipality: 'Municipio', gender: 'Rama',
        category: 'Categoría', league: 'Liga', representative_name: 'Representante',
        contact_phone: 'Teléfono de contacto (opcional)', contact_email: 'Correo de contacto (opcional)',
    };
    const VALUES = {
        first_name: 'Nombre', last_name: 'Apellidos', birth_date: 'Fecha de nacimiento', curp: 'CURP',
    };
    const SOURCES = {
        full_name: 'Nombre completo', birth_date: 'Fecha de nacimiento', curp: 'CURP',
        photo_box: 'Fotografía', absence_box: 'Constancia de ausencia',
    };
    const clone = value => JSON.parse(JSON.stringify(value));

    function decode(value) {
        if (typeof value !== 'string' || value.length > 100000) throw new Error('Opciones de passkey inválidas.');
        const text = value.replace(/-/g, '+').replace(/_/g, '/');
        const raw = atob(text + '='.repeat((4 - text.length % 4) % 4));
        return Uint8Array.from(raw, character => character.charCodeAt(0));
    }

    function encode(value) {
        if (value === null || value === undefined) return null;
        let text = '';
        for (const byte of new Uint8Array(value)) text += String.fromCharCode(byte);
        return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    function options(payload, registration) {
        const offered = payload.options?.publicKey || payload.publicKey;
        if (!offered || typeof offered !== 'object') throw new Error('No se recibió un desafío de passkey.');
        const publicKey = clone(offered);
        publicKey.challenge = decode(publicKey.challenge);
        if (registration) publicKey.user.id = decode(publicKey.user.id);
        for (const key of ['allowCredentials', 'excludeCredentials']) {
            if (publicKey[key]) publicKey[key] = publicKey[key].map(item => ({ ...item, id: decode(item.id) }));
        }
        return { publicKey };
    }

    function serialize(credential, registration) {
        if (!credential || credential.type !== 'public-key') throw new Error('No se recibió una passkey.');
        const response = { clientDataJSON: encode(credential.response.clientDataJSON) };
        if (registration) response.attestationObject = encode(credential.response.attestationObject);
        else {
            response.authenticatorData = encode(credential.response.authenticatorData);
            response.signature = encode(credential.response.signature);
            response.userHandle = encode(credential.response.userHandle);
        }
        const result = {
            id: credential.id, rawId: encode(credential.rawId), type: credential.type,
            response, clientExtensionResults: credential.getClientExtensionResults(),
        };
        if (credential.authenticatorAttachment) result.authenticatorAttachment = credential.authenticatorAttachment;
        return result;
    }

    class ReviewPanel {
        constructor(root) {
            this.root = root;
            this.sessionId = root.dataset.sessionId;
            this.csrf = root.dataset.csrf;
            this.base = `/api/registration-review/${encodeURIComponent(this.sessionId)}`;
            this.busy = false;
            this.dirty = false;
            this.attemptId = null;
            this.capturePending = false;
            this.enrollmentId = null;
            this.pollTimer = null;
            this.canUsePasskey = Boolean(window.isSecureContext && window.PublicKeyCredential
                && navigator.credentials && typeof navigator.credentials.get === 'function'
                && typeof navigator.credentials.create === 'function');
            this.find('[data-review-edit]').addEventListener('click', () => {
                this.editing = true;
                this.find('[data-review-confirm]').checked = false;
                this.controls();
                this.message('Corrige la propuesta y prepara una nueva revisión antes de confirmar.');
            });
            this.find('[data-review-prepare]').addEventListener('click', () => this.prepare());
            this.find('[data-review-refresh]').addEventListener('click', () => this.refresh());
            this.find('[data-review-confirm]').addEventListener('change', () => this.controls());
            this.find('[data-review-team-confirm]').addEventListener('change', () => this.confirmTeam());
            this.find('[data-review-approve]').addEventListener('click', () => this.approve());
            this.find('[data-review-result]').addEventListener('click', () => this.poll(0));
            this.find('[data-review-retry]').addEventListener('click', () => this.retryCapture());
            this.find('[data-enrollment-begin]').addEventListener('click', () => this.enroll());
            this.find('[data-enrollment-status-button]').addEventListener('click', () => this.enrollmentStatus());
            this.find('[data-source-target]').addEventListener('change', () => this.selectTarget());
            this.find('[data-source-page]').addEventListener('change', () => this.changePage());
            this.find('[data-source-apply]').addEventListener('click', () => this.applyCoordinates());
            this.find('[data-source-clear]').addEventListener('click', () => this.setRectangle(null));
            this.find('[data-review-fields]').addEventListener('input', event => {
                const input = event.target;
                const staffIndex = input.dataset.kind === 'staff' && input.dataset.value !== undefined
                    ? Number(input.dataset.index) : null;
                this.changed(staffIndex);
            });
            this.pointerEvents();
            window.addEventListener('pagehide', () => clearTimeout(this.pollTimer));
            try { this.applyView(JSON.parse(this.find('[data-review-initial]').textContent)); }
            catch (_) {
                this.view = { state: 'unavailable', session_id: this.sessionId };
                this.controls();
                this.failure('No se pudo cargar el plan de revisión. Consulta nuevamente.');
            }
        }

        find(selector) { return this.root.querySelector(selector); }
        message(text) { this.find('[data-review-status]').textContent = text; }
        failure(text) {
            const area = this.find('[data-review-error]');
            area.textContent = text;
            area.hidden = false;
        }
        clearError() { this.find('[data-review-error]').hidden = true; }

        async request(path, body) {
            const config = { credentials: 'same-origin', cache: 'no-store', headers: {} };
            if (body !== undefined) {
                config.method = 'POST';
                config.headers = { 'Content-Type': 'application/json', 'X-CTT-CSRF': this.csrf };
                config.body = JSON.stringify(body);
            }
            const response = await fetch(this.base + path, config);
            let payload;
            try { payload = await response.json(); }
            catch (_) { throw new Error('No se recibió un estado verificable de la revisión.'); }
            if (!response.ok) {
                const detail = payload.detail || payload;
                const error = new Error(detail.message || 'No se pudo completar esta acción de revisión.');
                error.status = response.status;
                error.code = detail.error;
                throw error;
            }
            return payload;
        }

        applyView(payload) {
            const view = payload.staff_review_view || payload;
            if (!view || view.session_id !== this.sessionId) throw new Error('La revisión no corresponde a este expediente.');
            if (payload.staff_review_csrf) this.csrf = payload.staff_review_csrf;
            this.view = clone(view);
            this.draft = clone(view);
            this.dirty = false;
            this.editing = !view.proposal_id || view.ready_to_approve !== true;
            this.attemptId = view.attempt_id || null;
            this.capturePending = view.capture_state === 'uncertain';
            this.enrollmentId = view.passkey?.enrollment_id || this.enrollmentId;
            this.find('[data-review-confirm]').checked = false;
            this.find('[data-review-team-confirm]').checked = false;
            this.globalConfirmedSlots = [];
            this.changedStaffSlots = new Set();
            this.renderFields();
            this.renderSources();
            this.showPasskey(view.passkey || {});
            const blockers = this.find('[data-review-blockers]');
            blockers.replaceChildren();
            for (const blocker of view.blockers || []) {
                const item = document.createElement('li');
                const location = blocker.navigation_target || blocker;
                const kind = location.kind === 'PLAYER' ? 'players' : location.kind === 'STAFF' ? 'staff' : null;
                const members = kind ? this.draft[kind] || [] : [];
                const index = members.findIndex(member => member.slot === location.slot);
                const field = location.field === 'photo' ? 'photo_box'
                    : location.field === 'presence' ? 'absence_box'
                        : ['first_name', 'last_name', 'source_page_index', null, undefined].includes(location.field)
                            ? 'full_name' : location.field;
                const targetIndex = this.targets.findIndex(target => target.kind === kind && target.index === index && target.field === field);
                const prefix = index >= 0 ? `${this.memberLabel(kind, members[index])}${SOURCES[field] ? ` · ${SOURCES[field]}` : ''}: ` : '';
                const message = prefix + (blocker.message || 'Dato o fuente pendiente de revisar.');
                if (targetIndex >= 0 && this.view.proposal_id) {
                    const button = document.createElement('button'); button.type = 'button';
                    button.textContent = message; button.addEventListener('click', () => {
                        this.find('[data-source-target]').value = String(targetIndex);
                        this.selectTarget(location.source_page_index || undefined); this.find('[data-source-target]').focus();
                    });
                    item.appendChild(button);
                } else item.textContent = message;
                blockers.appendChild(item);
            }
            this.controls();
            this.message(view.state === 'committed' ? 'La revisión quedó capturada.'
                : this.editing ? 'Revisa los valores y sus fuentes y prepara el plan.'
                    : 'Este es el plan preparado. Compáralo con las fuentes antes de confirmarlo.');
            if (!this.canUsePasskey) this.failure('Este navegador necesita un contexto seguro y soporte de passkeys para confirmar.');
            if (['uncertain', 'capturing'].includes(view.capture_state) && this.attemptId) this.poll(3);
        }

        optionalCurp(kind) {
            const policy = this.view?.identity_policy;
            const personKind = kind === 'players' ? 'PLAYER' : kind === 'staff' ? 'STAFF' : null;
            return policy?.phase === 'NACIONAL' && Array.isArray(policy.optional_curp_kinds)
                && policy.optional_curp_kinds.includes(personKind);
        }

        input(container, label, key, value, kind, index) {
            if (key === 'curp' && this.optionalCurp(kind)) label += ' (opcional en fase nacional)';
            const wrapper = document.createElement('label');
            wrapper.textContent = label;
            const input = document.createElement('input');
            input.type = key === 'birth_date' ? 'date' : key === 'contact_email' ? 'email' : 'text';
            input.value = value === null || value === undefined ? '' : String(value);
            input.dataset.value = key;
            input.dataset.kind = kind;
            if (index !== undefined) input.dataset.index = index;
            input.autocomplete = 'off';
            wrapper.appendChild(input);
            container.appendChild(wrapper);
        }

        renderFields() {
            const team = this.find('[data-review-team]');
            team.replaceChildren();
            const heading = document.createElement('h4'); heading.textContent = 'Equipo'; team.appendChild(heading);
            const fields = document.createElement('div'); fields.className = 'ctt-review-fields'; team.appendChild(fields);
            for (const [key, label] of Object.entries(TEAM)) this.input(fields, label, key, this.draft.team?.[key], 'team');
            const members = this.find('[data-review-members]'); members.replaceChildren();
            for (const kind of ['players', 'staff']) {
                for (const [index, member] of (this.draft[kind] || []).entries()) {
                    const card = document.createElement('article'); card.className = 'ctt-review-member';
                    const title = document.createElement('h4');
                    title.textContent = this.memberLabel(kind, member);
                    card.appendChild(title);
                    if (typeof member.photo_preview === 'string' && /^data:image\/(png|jpeg);base64,/.test(member.photo_preview)) {
                        const image = document.createElement('img'); image.src = member.photo_preview;
                        image.alt = `Fotografía propuesta: ${title.textContent}`; image.className = 'ctt-review-photo'; card.appendChild(image);
                    }
                    const photoMessages = {
                        missing: 'No se detectó una fotografía en esta casilla; revisa la fuente.',
                        uncertain: 'La fotografía necesita revisión en la fuente.',
                        error: 'No se pudo preparar la fotografía. Vuelve a preparar la revisión; se conserva el recorte.',
                    };
                    if (Object.hasOwn(photoMessages, member.photo_status)) {
                        const note = document.createElement('p'); note.className = 'ctt-review-note';
                        note.textContent = photoMessages[member.photo_status]; card.appendChild(note);
                    }
                    const values = document.createElement('div'); values.className = 'ctt-review-fields'; card.appendChild(values);
                    for (const [key, label] of Object.entries(VALUES)) this.input(values, label, key, member[key], kind, index);
                    if (kind === 'staff') {
                        const label = document.createElement('label'); label.textContent = 'Presencia observada';
                        const presence = document.createElement('select'); presence.dataset.presence = index;
                        for (const [value, text] of Object.entries({ UNCERTAIN: 'Pendiente de revisar', PRESENT: 'Presente en la fuente', ABSENT: 'Ausencia observada en la fuente' })) {
                            const option = document.createElement('option'); option.value = value; option.textContent = text; presence.appendChild(option);
                        }
                        presence.value = ['PRESENT', 'ABSENT'].includes(member.presence) ? member.presence : 'UNCERTAIN';
                        label.appendChild(presence); values.appendChild(label);
                        const confirmation = document.createElement('label'); confirmation.className = 'ctt-review-check';
                        const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.dataset.presenceConfirmed = index;
                        checkbox.checked = member.presence_confirmed === true;
                        confirmation.appendChild(checkbox);
                        confirmation.appendChild(document.createTextNode('He contrastado la presencia o ausencia de este puesto con su fuente.'));
                        card.appendChild(confirmation);
                        presence.addEventListener('change', () => { checkbox.checked = false; this.changed(index); });
                        checkbox.addEventListener('change', () => this.changed());
                    }
                    members.appendChild(card);
                }
            }
        }

        memberLabel(kind, member) {
            return kind === 'players' ? `Jugador ${member.slot}` : member.slot === 1 ? 'Director técnico' : 'Auxiliar';
        }

        renderSources() {
            this.targets = [];
            const target = this.find('[data-source-target]'); target.replaceChildren();
            for (const kind of ['players', 'staff']) {
                for (const [index, member] of (this.draft[kind] || []).entries()) {
                    const keys = kind === 'staff' ? Object.keys(SOURCES) : Object.keys(SOURCES).filter(key => key !== 'absence_box');
                    for (const field of keys) {
                        const option = document.createElement('option'); option.value = this.targets.length;
                        option.textContent = `${this.memberLabel(kind, member)} · ${SOURCES[field]}${field === 'curp' && this.optionalCurp(kind) ? ' (opcional)' : ''}`;
                        target.appendChild(option); this.targets.push({ kind, index, field });
                    }
                }
            }
            const pages = this.find('[data-source-page]'); pages.replaceChildren();
            const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Selecciona una página'; pages.appendChild(empty);
            for (const page of this.view.pages || []) {
                const option = document.createElement('option'); option.value = page.page_index;
                option.textContent = `Página ${page.pdf_page ?? page.page_index} del documento`;
                pages.appendChild(option);
            }
            this.selectTarget();
        }

        selection() {
            const target = this.targets[Number(this.find('[data-source-target]').value)];
            return target ? { ...target, member: this.draft[target.kind][target.index] } : null;
        }

        rectangle(selection = this.selection()) {
            if (!selection) return null;
            return ['photo_box', 'absence_box'].includes(selection.field)
                ? selection.member[selection.field] : selection.member.boxes?.[selection.field];
        }

        selectTarget(pageOverride) {
            const selection = this.selection();
            const pageIndex = pageOverride || selection?.member.source_page_index;
            this.find('[data-source-page]').value = pageIndex || '';
            const image = this.find('[data-source-image]');
            const stage = this.find('[data-source-stage]');
            if (!selection || !this.view.proposal_id || !pageIndex) { stage.hidden = true; this.draw(); return; }
            const path = `${this.base}/staff-approval/source/${encodeURIComponent(this.view.proposal_id)}/${pageIndex}`;
            image.onload = () => {
                if (image.naturalWidth * image.naturalHeight > 25000000) { stage.hidden = true; this.failure('La página supera el tamaño permitido para revisar.'); return; }
                const canvas = this.find('[data-source-canvas]'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
                stage.hidden = false; this.draw();
            };
            image.onerror = () => { stage.hidden = true; this.failure('No se pudo verificar la página. Consulta nuevamente la revisión.'); };
            if (image.getAttribute('src') === path && image.complete && image.naturalWidth) { stage.hidden = false; this.draw(); }
            else { stage.hidden = true; image.src = path; }
        }

        changePage() {
            const selection = this.selection();
            const value = Number(this.find('[data-source-page]').value);
            if (!value) return;
            if (!selection || !this.editing) return this.selectTarget(value);
            selection.member.source_page_index = value;
            selection.member.boxes = {};
            selection.member.photo_box = null;
            if (selection.kind === 'staff') selection.member.absence_box = null;
            this.changed(selection.kind === 'staff' ? selection.index : null); this.selectTarget();
            this.message('La página cambió. Vuelve a seleccionar las fuentes de esta persona.');
        }

        setRectangle(rectangle) {
            const selection = this.selection();
            if (!selection || !this.editing || this.busy) return;
            if (['photo_box', 'absence_box'].includes(selection.field)) selection.member[selection.field] = rectangle;
            else { selection.member.boxes ||= {}; selection.member.boxes[selection.field] = rectangle; }
            this.changed(selection.kind === 'staff' ? selection.index : null); this.draw();
        }

        applyCoordinates() {
            const image = this.find('[data-source-image]');
            const pageIndex = Number(this.find('[data-source-page]').value);
            const rect = { page_index: pageIndex };
            for (const key of ['x', 'y', 'width', 'height']) rect[key] = Number(this.find(`[data-rect="${key}"]`).value);
            if (!pageIndex || !Object.values(rect).every(Number.isInteger) || rect.x < 0 || rect.y < 0 || rect.width < 1 || rect.height < 1
                || rect.x + rect.width > image.naturalWidth || rect.y + rect.height > image.naturalHeight) {
                this.failure('La selección debe quedar dentro de la página original.'); return;
            }
            this.setRectangle(rect);
        }

        pointerEvents() {
            const canvas = this.find('[data-source-canvas]'); let start = null;
            const point = event => {
                const bounds = canvas.getBoundingClientRect();
                return { x: Math.max(0, Math.min(canvas.width, Math.round((event.clientX - bounds.left) * canvas.width / bounds.width))),
                    y: Math.max(0, Math.min(canvas.height, Math.round((event.clientY - bounds.top) * canvas.height / bounds.height))) };
            };
            const rectangle = end => ({ page_index: Number(this.find('[data-source-page]').value),
                x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y) });
            canvas.addEventListener('pointerdown', event => {
                if (!this.editing || this.busy || !this.selection()) return;
                event.preventDefault(); start = point(event); canvas.setPointerCapture(event.pointerId);
            });
            canvas.addEventListener('pointermove', event => { if (start) this.draw(rectangle(point(event))); });
            canvas.addEventListener('pointerup', event => {
                if (!start) return;
                const rect = rectangle(point(event)); start = null;
                if (rect.width > 0 && rect.height > 0) this.setRectangle(rect);
            });
            canvas.addEventListener('pointercancel', () => { start = null; this.draw(); });
        }

        draw(proposed) {
            const canvas = this.find('[data-source-canvas]'); const context = canvas.getContext('2d');
            context.clearRect(0, 0, canvas.width, canvas.height);
            const rect = proposed || this.rectangle();
            for (const key of ['x', 'y', 'width', 'height']) this.find(`[data-rect="${key}"]`).value = rect?.[key] ?? '';
            const preview = this.find('[data-source-preview]'); preview.hidden = true;
            if (!rect || rect.page_index !== Number(this.find('[data-source-page]').value)) return;
            context.strokeStyle = '#1d4ed8'; context.fillStyle = 'rgba(29,78,216,0.16)'; context.lineWidth = Math.max(1, canvas.width / 400);
            context.fillRect(rect.x, rect.y, rect.width, rect.height); context.strokeRect(rect.x, rect.y, rect.width, rect.height);
            const image = this.find('[data-source-image]');
            if (image.complete && image.naturalWidth && rect.width > 0 && rect.height > 0) {
                const crop = document.createElement('canvas'); crop.width = rect.width; crop.height = rect.height;
                crop.getContext('2d').drawImage(image, rect.x, rect.y, rect.width, rect.height, 0, 0, rect.width, rect.height);
                preview.src = crop.toDataURL('image/jpeg'); preview.hidden = false;
            }
        }

        confirmTeam() {
            const checked = this.find('[data-review-team-confirm]').checked;
            if (checked && (this.view?.staff || []).some((member, index) =>
                (member.presence !== 'PRESENT' && this.find(`[data-presence="${index}"]`)?.value === 'PRESENT')
                || (member.presence === 'PRESENT' && this.changedStaffSlots.has(index)))) {
                this.find('[data-review-team-confirm]').checked = false;
                this.message('Confirma individualmente los puestos modificados y prepara la revisión antes de confirmar el equipo.');
                return;
            }
            const slots = [];
            for (const [index, member] of (this.view?.staff || []).entries()) {
                const presence = this.find(`[data-presence="${index}"]`);
                const confirmation = this.find(`[data-presence-confirmed="${index}"]`);
                if (member.presence === 'PRESENT' && member.presence_proposed === true && presence?.value === 'PRESENT' && confirmation) {
                    confirmation.checked = checked;
                    slots.push(index);
                }
            }
            this.globalConfirmedSlots = checked ? slots : [];
            this.dirty = true;
            this.find('[data-review-confirm]').checked = false;
            this.controls();
        }

        changed(staffIndex = null) {
            if (Number.isInteger(staffIndex)) {
                this.changedStaffSlots.add(staffIndex);
                const confirmation = this.find(`[data-presence-confirmed="${staffIndex}"]`);
                if (confirmation) confirmation.checked = false;
            }
            if (this.find('[data-review-team-confirm]').checked) {
                this.find('[data-review-team-confirm]').checked = false;
                for (const index of this.globalConfirmedSlots || []) {
                    const confirmation = this.find(`[data-presence-confirmed="${index}"]`);
                    if (confirmation) confirmation.checked = false;
                }
                this.globalConfirmedSlots = [];
            }
            this.dirty = true;
            this.find('[data-review-confirm]').checked = false;
            this.controls();
        }

        changes() {
            const result = { team: {}, players: [], staff: [] };
            if (this.find('[data-review-team-confirm]').checked) result.confirm_present_staff = true;
            for (const input of this.root.querySelectorAll('[data-value]')) {
                if (input.dataset.kind === 'team') result.team[input.dataset.value] = input.value;
            }
            for (const kind of ['players', 'staff']) {
                for (const [index, member] of (this.draft[kind] || []).entries()) {
                    const value = { slot: member.slot, source_page_index: member.source_page_index, boxes: member.boxes || {} };
                    if (member.photo_box !== undefined) value.photo_box = member.photo_box;
                    for (const input of this.root.querySelectorAll(`[data-kind="${kind}"][data-index="${index}"]`)) value[input.dataset.value] = input.value;
                    if (kind === 'staff') {
                        value.presence = this.find(`[data-presence="${index}"]`).value;
                        value.presence_confirmed = this.find(`[data-presence-confirmed="${index}"]`).checked;
                        if (member.absence_box !== undefined) value.absence_box = member.absence_box;
                    }
                    result[kind].push(value);
                }
            }
            return result;
        }

        controls() {
            const closed = !this.view || ['unavailable', 'committed'].includes(this.view.state) || this.capturePending;
            this.find('[data-review-fields]').disabled = this.busy || !this.editing || closed;
            this.find('[data-review-edit]').disabled = this.busy || closed || this.editing;
            this.find('[data-review-prepare]').disabled = this.busy || closed || !this.editing;
            this.find('[data-review-refresh]').disabled = this.busy;
            this.find('[data-review-team-confirm]').disabled = this.busy || !this.editing || closed;
            const active = String(this.view?.passkey?.status || '').toLowerCase() === 'active';
            this.find('[data-review-approve]').disabled = this.busy || closed || this.editing || this.dirty
                || this.view.ready_to_approve !== true || !this.view.proposal_id || !active || !this.canUsePasskey
                || !this.find('[data-review-confirm]').checked;
            this.find('[data-review-confirm]').disabled = this.busy || closed || this.editing || this.dirty || this.view?.ready_to_approve !== true;
            this.find('[data-enrollment-begin]').disabled = this.busy || closed || !this.canUsePasskey;
            this.find('[data-source-page]').disabled = this.busy || closed;
            for (const selector of ['[data-source-apply]', '[data-source-clear]', '[data-rect]']) {
                for (const element of this.root.querySelectorAll(selector)) element.disabled = this.busy || !this.editing || closed;
            }
            this.find('[data-review-result]').hidden = !this.attemptId;
            this.find('[data-review-result]').disabled = this.busy;
            this.find('[data-review-retry]').hidden = !this.capturePending || !this.attemptId;
            this.find('[data-review-retry]').disabled = this.busy;
            this.find('[data-enrollment-status-button]').hidden = !this.enrollmentId;
        }

        async refresh() {
            this.clearError(); this.busy = true; this.controls();
            try { this.applyView(await this.request('/staff-approval')); }
            catch (error) { this.failure(error.message); }
            finally { this.busy = false; this.controls(); }
        }

        async prepare() {
            this.clearError(); this.busy = true; this.controls();
            try {
                const view = await this.request('/edit', { expected_proposal_id: this.view.proposal_id || null, changes: this.changes() });
                this.applyView(view);
            } catch (error) { this.failure(error.message); }
            finally { this.busy = false; this.controls(); }
        }

        async approve() {
            if (this.find('[data-review-approve]').disabled) return;
            this.clearError(); this.busy = true; this.controls();
            let sent = false;
            try {
                const challenge = await this.request('/staff-approval/challenge', { proposal_id: this.view.proposal_id, confirmed: true });
                this.attemptId = challenge.attempt_id;
                const credential = await navigator.credentials.get(options(challenge, false));
                const assertion = serialize(credential, false);
                sent = true;
                this.capturePending = true;
                const result = await this.request('/staff-approval/assertion', { attempt_id: this.attemptId, assertion });
                this.captureState(result);
            } catch (error) {
                if (error.name === 'NotAllowedError' || error.name === 'AbortError') this.message('La passkey se canceló. No se solicitó la captura.');
                else if (sent && (!error.status || error.status >= 500)) {
                    this.message('Se está verificando el resultado del mismo intento. No prepares otra captura.');
                    this.poll(4);
                } else this.failure(error.message);
            } finally { this.busy = false; this.controls(); }
        }

        captureState(result) {
            if (result.session_id && result.session_id !== this.sessionId) throw new Error('El resultado no corresponde a esta revisión.');
            if (result.state === 'committed') {
                this.capturePending = false;
                clearTimeout(this.pollTimer); this.view.state = 'committed'; this.editing = false;
                this.message('La revisión quedó capturada. Consulta el expediente para ver su estado final.');
                this.find('[data-review-confirm]').checked = false;
            } else if (['expired', 'stale'].includes(result.state)) {
                this.capturePending = false;
                clearTimeout(this.pollTimer);
                this.find('[data-review-confirm]').checked = false;
                this.message('Este intento venció o cambió. Consulta la revisión y confirma el plan nuevamente.');
            } else {
                this.capturePending = true;
                this.message('Se está verificando el resultado del mismo intento.');
                this.poll(4);
            }
        }

        async poll(remaining) {
            if (!this.attemptId) return;
            clearTimeout(this.pollTimer);
            try {
                const result = await this.request(`/staff-approval/status?attempt_id=${encodeURIComponent(this.attemptId)}`);
                if (['committed', 'expired', 'stale'].includes(result.state)) { this.captureState(result); this.controls(); return; }
                this.message('El resultado aún no está confirmado. Consulta este mismo intento nuevamente.');
            } catch (error) { this.failure(error.message); }
            if (remaining > 0) this.pollTimer = setTimeout(() => this.poll(remaining - 1), 1500);
        }

        async retryCapture() {
            if (!this.capturePending || !this.attemptId || this.busy) return;
            this.clearError(); this.busy = true; this.controls();
            try {
                const result = await this.request('/staff-approval/assertion', {
                    attempt_id: this.attemptId, assertion: null
                });
                this.captureState(result);
            } catch (error) {
                this.failure(error.message);
                this.poll(0);
            } finally { this.busy = false; this.controls(); }
        }

        showPasskey(value) {
            const active = String(value.status || value.state || '').toLowerCase() === 'active';
            this.find('[data-enrollment-status]').textContent = active
                ? 'Tu passkey está acreditada. Se solicitará al aprobar este plan.'
                : 'La credencial candidata necesita activación independiente del responsable antes de capturar.';
            this.find('[data-enrollment-fingerprint]').textContent = value.fingerprint || '';
        }

        async enroll() {
            if (!this.canUsePasskey || this.busy) return;
            this.clearError(); this.busy = true; this.controls();
            try {
                const start = await this.request('/staff-passkey/begin', {});
                this.enrollmentId = start.enrollment_id;
                const enrollmentState = String(start.status || start.state || '').toLowerCase();
                const existingStates = {
                    pending_operator_review: 'La credencial candidata ya está registrada. Compara su huella con el responsable para solicitar la activación.',
                    pending_activation: 'La credencial candidata ya está registrada. Compara su huella con el responsable para solicitar la activación.',
                    active: 'Tu passkey ya está acreditada. Se solicitará al aprobar este plan.',
                    expired: 'El registro de passkey venció. Solicita una nueva invitación al responsable.',
                    not_enrolled: 'El registro de passkey venció o fue revocado. Consulta con el responsable.',
                    revoked: 'La passkey fue revocada. Consulta con el responsable.',
                };
                if (Object.hasOwn(existingStates, enrollmentState)) {
                    if (['pending_operator_review', 'pending_activation', 'active'].includes(enrollmentState)) this.showPasskey(start);
                    else {
                        this.find('[data-enrollment-status]').textContent = existingStates[enrollmentState];
                        this.find('[data-enrollment-fingerprint]').textContent = '';
                    }
                    this.message(existingStates[enrollmentState]);
                    return;
                }
                const credential = await navigator.credentials.create(options(start, true));
                const candidate = await this.request('/staff-passkey/complete', { enrollment_id: this.enrollmentId, registration: serialize(credential, true) });
                this.showPasskey(candidate);
                this.message('La credencial candidata quedó registrada. Compara su huella con el responsable para solicitar la activación.');
            } catch (error) {
                if (error.name === 'NotAllowedError' || error.name === 'AbortError') this.message('Se canceló el registro de passkey. No se activó ninguna credencial.');
                else this.failure(error.message);
            } finally { this.busy = false; this.controls(); }
        }

        async enrollmentStatus() {
            if (!this.enrollmentId) return;
            this.clearError();
            try {
                const state = await this.request('/staff-passkey/status', { enrollment_id: this.enrollmentId });
                this.showPasskey(state);
                if (String(state.state || state.status).toLowerCase() === 'active') await this.refresh();
            } catch (error) { this.failure(error.message); }
        }
    }

    document.querySelectorAll('#ctt-staff-review').forEach(root => new ReviewPanel(root));
})();
