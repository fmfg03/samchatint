/* Progressive AMEX workspace. Financial decisions remain with the existing handlers. */
(() => {
  'use strict';
  let view = document.getElementById('amex-workspace-view');
  if (!view) return;
  const enhanced = view.dataset.amexRoot === 'true';
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
  let canonical = new URL(view.dataset.statementUrl || location.href, location.href).href;
  let panelUrl = canonical;
  let writing = false;
  let dirty = false;
  let generation = 0;
  let reading = null;
  let moved = null;
  let opener = null;
  let filter = 'all';
  let search = '';
  let dialog;
  let content;
  let status;
  let closeButton;
  let recovery = null;
  let pendingRecovery = null;
  let reviewButton;
  let staleReview = false;
  let draftEpoch = 0;
  let restoringDraft = false;
  const path = url => new URL(url, location.href).pathname.replace(/\/$/, '');
  const statementPath = url => path(url).match(/^\/admin\/contabilidad\/amex\/estados\/([^/]+)\/directo(?:\/|$)/)?.[1];
  const currentId = () => statementPath(canonical);
  const known = url => {
    const target = new URL(url, location.href);
    if (target.origin !== location.origin) return false;
    if (currentId()) return statementPath(target.href) === currentId();
    return /^\/admin\/contabilidad\/amex\/estados\/directo\/(nuevo|previsualizar)$/.test(target.pathname);
  };
  const downloadable = url => /\/(original|exportar\.(xlsx|csv|zip))$/.test(path(url));
  function announce(message, error = false) {
    const node = dialog?.open ? status : $('#amex-workspace-status');
    if (node) {
      node.textContent = message; node.dataset.error = String(error);
      if (!dialog?.open && staleReview && reviewButton) { reviewButton.hidden = false; node.append(document.createElement('br'), reviewButton); }
    }
  }
  function selectedBoxes() {
    return $$('input[type="checkbox"][name="item_ids"]', view).filter(box => !box.disabled && !box.closest('fieldset[disabled]'));
  }
  function visibleBoxes() {
    return selectedBoxes().filter(box => !box.closest('[hidden]'));
  }
  function selection() {
    const selected = selectedBoxes().filter(box => box.checked);
    const budget = selected.filter(box => box.dataset.budgetEligible === 'true');
    $$('[data-amex-selection-required]', view).forEach(button => { button.disabled = !selected.length; });
    const hiddenSelected = selected.filter(box => box.closest('[hidden]')).length;
    const selectionText = `${selected.length} cargos seleccionados` + (hiddenSelected ? ` · ${hiddenSelected} ocultos por el filtro; también se incluirán en la operación.` : '');
    const count = $('#direct-selection-count');
    if (count) count.textContent = selectionText;
    $$('.amex-selection-summary').forEach(node => { node.textContent = selectionText; });
    const button = $('#direct-send-budget');
    if (button) button.disabled = !budget.length;
    const note = $('#direct-budget-selection-count');
    if (note) note.textContent = `${budget.length} cargos listos para enviar a Control Presupuestal.` + (selected.length > budget.length ? ` ${selected.length - budget.length} seleccionados no se enviarán: su tratamiento o etapa no permite este envío.` : '');
    $$('#direct-send-selection,.direct-copy-selection').forEach(target => {
      target.replaceChildren();
      (target.id === 'direct-send-selection' ? budget : selected).forEach(box => {
        const input = document.createElement('input');
        input.type = 'hidden'; input.name = 'item_ids'; input.value = box.value;
        target.append(input);
      });
    });
    const all = $('#direct-select-all');
    if (all) {
      const visible = visibleBoxes();
      const checked = visible.filter(box => box.checked).length;
      all.checked = checked > 0 && checked === visible.length;
      all.indeterminate = checked > 0 && !all.checked;
      all.disabled = !visible.length;
      all.setAttribute('aria-label', 'Seleccionar todos los cargos elegibles visibles');
    }
  }
  function applyFilters() {
    $$('[data-item-id][data-amex-bucket]', view).forEach(row => {
      row.hidden = (filter !== 'all' && row.dataset.amexBucket !== filter) || !(row.dataset.amexSearch || row.textContent).toLocaleLowerCase().includes(search.toLocaleLowerCase());
    });
    $$('[data-amex-filter]', view).forEach(control => {
      if (control instanceof HTMLSelectElement) control.value = filter;
      else control.setAttribute('aria-pressed', String(control.dataset.amexFilter === filter));
    });
    selection();
  }
  function initialize(scope) {
    const project = $('#direct-project', scope);
    const concept = $('#direct-concept', scope);
    if (project && concept && !project.dataset.amexInitialized) {
      project.dataset.amexInitialized = 'true';
      const options = [...concept.options].slice(1).map(option => option.cloneNode(true));
      const update = () => {
        const previous = concept.value;
        const tournament = $('#direct-tournament');
        const code = $('#direct-project-code');
        if (tournament) tournament.value = project.value.startsWith('t:') ? project.value.slice(2) : '';
        if (code) code.value = project.value.startsWith('p:') ? project.value.slice(2) : '';
        concept.replaceChildren(new Option('Selecciona categoría del proyecto', ''));
        options.filter(option => option.dataset.project === project.value).forEach(option => concept.add(option.cloneNode(true)));
        concept.value = previous; concept.disabled = !project.value;
      };
      project.addEventListener('change', update); update();
    }
    const choice = $('#direct-treatment', scope);
    if (choice && !choice.dataset.amexInitialized) {
      choice.dataset.amexInitialized = 'true';
      const update = () => {
        const partner = choice.value === 'partner_receivable';
        const account = $('#direct-debtor');
        const label = $('#direct-debtor-label');
        if (label) label.hidden = !partner;
        if (account) { account.disabled = !partner; account.required = partner; if (!partner) account.value = ''; }
        const projectForm = $('#direct-project-classification');
        if (projectForm) projectForm.open = choice.value === 'project_expense';
      };
      choice.addEventListener('change', update); update();
    }
    initializePreview(scope);
  }
  function initializePreview(scope) {
    const form = $('#amex-direct-intake', scope);
    if (!form || form.dataset.amexInitialized) return;
    form.dataset.amexInitialized = 'true';
    const button = $('#amex-direct-preview', scope);
    const submit = $('#amex-direct-import', scope);
    const output = $('#amex-direct-preview-result', scope);
    let previewGeneration = 0;
    let controller;
    form.addEventListener('change', event => {
      if (['file', 'profile', 'currency', 'card_account_id', 'employee_id', 'period_start', 'period_end', 'company_code', 'receiver_rfc'].includes(event.target.name)) {
        previewGeneration++; controller?.abort(); button.disabled = false; submit.disabled = true; output.replaceChildren();
      }
    });
    button.addEventListener('click', async () => {
      const current = ++previewGeneration;
      controller?.abort(); controller = new AbortController();
      submit.disabled = true; button.disabled = true; output.textContent = 'Validando archivo y perfil…';
      try {
        const response = await fetch('/admin/contabilidad/amex/estados/directo/previsualizar', {method: 'POST', body: new FormData(form), credentials: 'same-origin', signal: controller.signal});
        const data = await response.json();
        if (current !== previewGeneration) return;
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo previsualizar el CSV.');
        output.replaceChildren();
        const summary = document.createElement('p');
        summary.textContent = `${data.rows.length} filas originales. Perfil declarado: ${data.profile}. Revisa signo y tipo antes de confirmar.`;
        if (data.outside_count) summary.textContent += ` ${data.outside_count} filas fuera del período: ${data.min_date} a ${data.max_date}. Corrige el período antes de cargar.`;
        const shell = document.createElement('div'); shell.className = 'direct-table table-shell';
        const table = document.createElement('table');
        const header = table.createTHead().insertRow();
        ['Fila', 'Celda original', 'Importe firmado', 'Tipo', 'Importe del cargo', 'Motivo'].forEach(label => { const cell = document.createElement('th'); cell.textContent = label; header.append(cell); });
        const body = table.createTBody();
        data.rows.forEach(row => {
          const tr = body.insertRow();
          [row.row_number, row.raw_amount, row.raw_signed_amount, ({debit: 'Cargo', credit: 'Crédito', unknown: 'Signo desconocido', skipped: 'Fila omitida'}[row.movement_kind] || 'Tipo pendiente'), row.amount, row.reason_text || ''].forEach(value => { tr.insertCell().textContent = value == null ? 'Pendiente' : String(value); });
        });
        shell.append(table); output.append(summary, shell); submit.disabled = Boolean(data.outside_count);
      } catch (error) { if (current === previewGeneration && error.name !== 'AbortError') output.textContent = error.message; }
      finally { if (current === previewGeneration) button.disabled = false; }
    });
  }
  initialize(view);
  document.addEventListener('change', event => {
    if (event.target.matches('select[data-amex-filter]')) { filter = event.target.value; applyFilters(); return; }
    if (event.target.id === 'direct-select-all') visibleBoxes().forEach(box => { box.checked = event.target.checked; });
    if (event.target.matches('input[type="checkbox"][name="item_ids"],#direct-select-all')) selection();
  });
  if (!enhanced) return;
  view.classList.add('amex-enhanced');
  dialog = document.createElement('dialog'); dialog.className = 'amex-dialog'; dialog.setAttribute('aria-labelledby', 'amex-dialog-title');
  const header = document.createElement('header'); header.className = 'amex-dialog-header';
  const title = document.createElement('h2'); title.id = 'amex-dialog-title';
  closeButton = document.createElement('button'); closeButton.type = 'button'; closeButton.className = 'button secondary'; closeButton.textContent = 'Cerrar'; closeButton.setAttribute('aria-label', 'Cerrar ventana AMEX');
  status = document.createElement('p'); status.className = 'amex-dialog-status'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  content = document.createElement('div'); content.className = 'amex-dialog-content';
  reviewButton = document.createElement('button'); reviewButton.type = 'button'; reviewButton.className = 'button secondary'; reviewButton.textContent = 'Actualizar evidencia y revisar'; reviewButton.hidden = true; reviewButton.dataset.amexReview = '';
  reviewButton.addEventListener('click', () => { if (!writing && recovery) { pendingRecovery = recovery; reviewButton.hidden = true; request(recovery.url); } });
  header.append(title, reviewButton, closeButton); dialog.append(header, status, content); document.body.append(dialog);
  if (!$('#amex-workspace-status')) { const node = document.createElement('p'); node.id = 'amex-workspace-status'; node.setAttribute('role', 'status'); view.prepend(node); }
  function restoreMoved() {
    if (moved) {
      moved.placeholder.replaceWith(moved.node);
      moved.inheritedDisabled.forEach(control => { control.disabled = false; });
      moved = null;
    }
  }
  function returnFocus() {
    if (opener?.isConnected) opener.focus();
    else {
      const match = opener?.dataset.amexOpen;
      const replacement = match ? $$('[data-amex-open]', view).find(node => node.dataset.amexOpen === match) : $('[data-amex-refresh]', view);
      replacement?.focus();
    }
  }
  function close() {
    if (writing) { announce('Espera a que termine la operación.'); return; }
    ++generation; reading?.abort(); reading = null;
    dialog.close(); restoreMoved(); content.replaceChildren(); dirty = false; reviewButton.hidden = true; staleReview = false; returnFocus();
  }
  closeButton.addEventListener('click', close);
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  function open(titleText, drawer = false) {
    if (!dialog.open) opener = document.activeElement;
    title.textContent = titleText;
    header.insertBefore(reviewButton, closeButton);
    reviewButton.hidden = true;
    dialog.classList.toggle('amex-drawer', drawer);
    status.textContent = '';
    if (!dialog.open) dialog.showModal();
    closeButton.focus();
  }
  function state() {
    return {ids: new Set(selectedBoxes().filter(box => box.checked).map(box => box.value)), scroll: window.scrollY, tables: $$('.table-shell', view).map(node => node.scrollLeft)};
  }
  function replaceRoot(next, url) {
    const previous = state();
    restoreMoved();
    view.replaceWith(next); view = next; view.classList.add('amex-enhanced');
    canonical = new URL(view.dataset.statementUrl || url, location.href).href;
    if (view.dataset.amexTitle) {
      const label = view.dataset.amexTitle;
      document.title = label;
      const heading = $('.workspace-hero-main h1');
      if (heading) heading.textContent = label;
      const crumb = $('nav[aria-label="Ruta de navegación"] > span:last-child');
      if (crumb) crumb.textContent = label;
    }
    if (location.pathname !== path(canonical)) history.replaceState(null, '', canonical);
    if (!$('#amex-workspace-status')) { const node = document.createElement('p'); node.id = 'amex-workspace-status'; node.setAttribute('role', 'status'); view.prepend(node); }
    initialize(view);
    selectedBoxes().forEach(box => { box.checked = previous.ids.has(box.value); });
    const input = $('[data-amex-search-input]', view); if (input) input.value = search;
    selection(); applyFilters(); dirty = false;
    window.scrollTo(0, previous.scroll);
    $$('.table-shell', view).forEach((node, index) => { node.scrollLeft = previous.tables[index] || 0; });
  }
  function checkResponse(response, next, allowNew) {
    const url = new URL(response.url || canonical, location.href);
    if (url.origin !== location.origin || !next) throw new Error('La sesión o el contexto cambió. Vuelve a entrar y comprueba el estado.');
    const nextId = statementPath(next.dataset.statementUrl || url.href);
    if (currentId() && (nextId !== currentId() || statementPath(url.href) !== currentId())) throw new Error('La respuesta corresponde a otro estado. Actualiza para comprobar el contexto.');
    if (!currentId() && !(allowNew && nextId) && !known(url.href)) throw new Error('Respuesta fuera del contexto AMEX.');
    if (!['intake', 'statement', 'detail'].includes(next.dataset.amexView)) throw new Error('No se pudo verificar la respuesta AMEX.');
    return url;
  }
  function showSource(key, label, selectionRequired = false) {
      const source = $$('[data-amex-dialog-source]', view).find(node => node.dataset.amexDialogSource === key);
      if (!source) return;
      restoreMoved(); content.replaceChildren();
      const inheritedDisabled = $$('input,select,textarea,button', source).filter(control => !control.disabled && control.matches(':disabled'));
      inheritedDisabled.forEach(control => { control.disabled = true; });
      const placeholder = document.createComment('AMEX dialog source'); source.replaceWith(placeholder);
      moved = {node: source, placeholder, inheritedDisabled};
      if (selectionRequired) {
        const summary = document.createElement('p'); summary.className = 'amex-selection-summary'; summary.setAttribute('role', 'status'); content.append(summary);
      }
      content.append(source); panelUrl = canonical;
      open(label); selection(); initialize(content);
      if (key === 'project') { const project = $('#direct-project-classification', content); if (project) project.open = true; }

  }
  function formIdentity(form) {
    if (!form) return '';
    return (form.getAttribute('action') || '') + '|' + $$('input[type="hidden"]', form).filter(node => ['item_id', 'cfdi_uuid', 'cfdi_id'].includes(node.name)).map(node => `${node.name}:${node.value}`).join('|');
  }
  function captureDraft(scope) {
    return $$('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]),textarea,select', scope).filter(node => !node.closest('[hidden]')).map(node => ({identity: formIdentity(node.form), key: node.name || node.id, value: node.value})).filter(field => field.key);
  }
  function restoreDraft(fields, scope) {
    restoringDraft = true;
    try {
    fields.forEach(field => {
      const node = $$('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]),textarea,select', scope).find(control => (control.name || control.id) === field.key && formIdentity(control.form) === field.identity && !control.disabled);
      if (!node || (node instanceof HTMLSelectElement && ![...node.options].some(option => option.value === field.value))) return;
      node.value = field.value; node.dispatchEvent(new Event('change', {bubbles: true}));
    });
    $$('input[type="checkbox"][name="confirmed"]', scope).forEach(node => { node.checked = false; });
    } finally { restoringDraft = false; }
  }
  function recoveryPlan() {
    return {key: moved?.node.dataset.amexDialogSource, label: title.textContent, url: moved ? canonical : panelUrl, draft: [...(moved ? captureDraft(moved.node) : []), ...captureDraft(content)]};
  }
  function offerRecovery(plan) {
    recovery = plan; staleReview = true; reviewButton.hidden = false;
    $$('input[type="checkbox"][name="confirmed"]', content).forEach(node => { node.checked = false; });
  }
  async function request(url, options = {}, allowNew = false) {
    const mutation = options.method === 'POST';
    const requestedRecovery = pendingRecovery; pendingRecovery = null;
    const draft = recoveryPlan();
    reviewButton.hidden = true;
    if (writing) return;
    const requestGeneration = ++generation;
    const requestDraftEpoch = draftEpoch;
    reading?.abort(); reading = null;
    const controller = mutation ? null : new AbortController();
    if (controller) reading = controller;
    const controls = mutation ? $$('button,input:not([type="hidden"]),select,textarea', dialog.open ? dialog : view).filter(node => !node.matches(':disabled')) : [];
    if (mutation) {
      writing = true; closeButton.disabled = true; controls.forEach(node => { node.disabled = true; });
    }
    announce(mutation ? 'Guardando. Espera a que termine la operación…' : 'Consultando…');
    try {
      const response = await fetch(url, {...options, credentials: 'same-origin', signal: controller?.signal});
      if (requestGeneration !== generation || (!mutation && requestDraftEpoch !== draftEpoch)) return;
      if ((response.headers.get('Content-Type') || '').includes('json')) {
        const payload = await response.json();
        if (requestGeneration !== generation || (!mutation && requestDraftEpoch !== draftEpoch)) return;
        if (response.status === 409) offerRecovery(draft);
        throw new Error(typeof payload.detail === 'string' ? payload.detail : 'No se pudo completar la operación. Comprueba tu sesión y el estado actual.');
      }
      const html = await response.text();
      if (requestGeneration !== generation || (!mutation && requestDraftEpoch !== draftEpoch)) return;
      const doc = new DOMParser().parseFromString(html, 'text/html');
      if ($$('#amex-workspace-view', doc).length !== 1) throw new Error('La sesión o el contexto cambió. Vuelve a entrar y comprueba el estado.');
      const next = $('#amex-workspace-view', doc);
      const responseUrl = checkResponse(response, next, allowNew);
      $$('script,style,link', next).forEach(node => node.remove());
      if (!response.ok && response.status !== 409) throw new Error('No se pudo completar la operación. Comprueba el estado actual antes de volver a guardar.');
      const errorMessage = responseUrl.searchParams.get('error_msg');
      const message = responseUrl.searchParams.get('msg');
      if (next.dataset.amexView === 'statement' || next.dataset.amexView === 'intake') {
        const retry = requestedRecovery || (mutation && errorMessage ? draft : null);
        if (retry && !retry.key && errorMessage) { offerRecovery(retry); announce(errorMessage, true); return; }
        staleReview = false;
        replaceRoot(next, responseUrl.href);
        if (retry?.key) {
          if (dialog.open) dialog.close(); content.replaceChildren();
          showSource(retry.key, retry.label, true); restoreDraft(retry.draft, content);
          dirty = true; announce(errorMessage || 'Evidencia actualizada. Revisa y confirma de nuevo.', Boolean(errorMessage)); return;
        }
        if (dialog.open) { dialog.close(); content.replaceChildren(); returnFocus(); }
        announce(errorMessage || message || 'Estado actualizado.', Boolean(errorMessage));
      } else {
        // Keep the original source detached until close, so retry forms never duplicate its IDs.
        staleReview = false;
        next.removeAttribute('id'); content.replaceChildren(next); panelUrl = responseUrl.href;
        initialize(content);
        if (requestedRecovery || errorMessage || response.status === 409) restoreDraft((requestedRecovery || draft).draft, content);
        if (errorMessage) announce(errorMessage, true);
        else if (response.status === 409) announce('El estado cambió. Revisa los datos actuales antes de confirmar de nuevo.', true);
        else announce(message || 'Revisa los datos antes de confirmar.');
        dirty = false;
        if (!dialog.open) open('Revisar cargo', /\/cfdi(?:\/|$)/.test(responseUrl.pathname));
      }
    } catch (error) {
      if (error.name !== 'AbortError' && requestGeneration === generation) announce(error.message + (mutation ? ' Si hubo un fallo de red, comprueba el estado antes de volver a enviar; la operación puede haberse guardado.' : ''), true);
    } finally {
      if (mutation) { writing = false; closeButton.disabled = false; controls.filter(node => node.isConnected || moved?.node.contains(node)).forEach(node => { node.disabled = false; }); selection(); }
      if (staleReview) {
        reviewButton.hidden = false;
        $$('button[type="submit"],button:not([type]),input[type="submit"]', content).forEach(button => { button.disabled = true; });
      }
      if (requestGeneration === generation) reading = null;
    }
  }
  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-amex-open]');
    if (trigger && view.contains(trigger)) {
      event.preventDefault(); if (writing) return;
      ++generation; reading?.abort(); reading = null;
      showSource(trigger.dataset.amexOpen, trigger.dataset.amexTitle || trigger.textContent.trim(), trigger.hasAttribute('data-amex-selection-required'));
      return;
    }
    const refresh = event.target.closest('[data-amex-refresh]');
    if (refresh && view.contains(refresh)) { event.preventDefault(); if (dialog.open || writing || dirty) { announce('Cierra la revisión o guarda los cambios antes de actualizar.'); return; } request(canonical); return; }
    const tab = event.target.closest('[data-amex-filter]');
    if (tab && !(tab instanceof HTMLSelectElement) && view.contains(tab)) { event.preventDefault(); filter = tab.dataset.amexFilter; applyFilters(); return; }
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download') || (!view.contains(link) && !dialog.contains(link))) return;
    const base = dialog.contains(link) ? panelUrl : canonical;
    const url = new URL(link.getAttribute('href'), base);
    if (!known(url.href) || downloadable(url.href)) return;
    event.preventDefault(); if (writing) return;
    if (path(url.href) === path(canonical)) { request(url.href); return; }
    restoreMoved(); content.replaceChildren(); panelUrl = url.href;
    open(link.dataset.amexTitle || link.textContent.trim() || 'Revisar cargo', /\/cfdi(?:\/|$)/.test(url.pathname));
    request(url.href);
  });
  function markDraft() {
    if (restoringDraft) return;
    dirty = true; draftEpoch++;
    if (reading && !writing) {
      ++generation; reading.abort(); reading = null;
      announce('Consulta cancelada para conservar tu captura. Puedes volver a consultar cuando termines de editar.');
    }
  }
  document.addEventListener('input', event => {
    if (event.target.matches('[data-amex-search-input]')) { search = event.target.value; applyFilters(); return; }
    if ((view.contains(event.target) || dialog.contains(event.target)) && event.target.matches('input:not([type="hidden"]):not([name="item_ids"]):not(#direct-select-all),textarea,select:not([data-amex-filter])')) markDraft();
  });
  document.addEventListener('change', event => {
    if ((view.contains(event.target) || dialog.contains(event.target)) && event.target.matches('select:not([data-amex-filter]),input[type="checkbox"]:not([name="item_ids"]):not(#direct-select-all),input[type="file"]')) markDraft();
  });
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || (!view.contains(form) && !dialog.contains(form))) return;
    const base = dialog.contains(form) ? panelUrl : canonical;
    const action = event.submitter?.getAttribute('formaction') || form.getAttribute('action') || base;
    const url = new URL(action, base);
    if (!known(url.href) || downloadable(url.href)) return;
    event.preventDefault(); if (writing || !form.reportValidity()) return;
    selection();
    const body = new FormData(form, event.submitter || undefined);
    const method = (event.submitter?.getAttribute('formmethod') || form.method || 'GET').toUpperCase();
    if (method === 'GET') { url.search = new URLSearchParams(body).toString(); if (!dialog.open && path(url.href) !== path(canonical)) open('Revisar cargo', /\/cfdi(?:\/|$)/.test(url.pathname)); request(url.href); }
    else if (method === 'POST') request(url.href, {method, body}, view.dataset.amexView === 'intake');
  });
  setInterval(async () => {
    if (document.hidden || writing || reading || dialog.open || dirty || !currentId() || !(view.dataset.pendingBudget === 'true' || Number(view.dataset.pendingBudget) > 0)) return;
    const snapshot = generation;
    const pollUrl = new URL(canonical, location.href); pollUrl.searchParams.set('workspace_poll', '1');
    const controller = new AbortController(); reading = controller;
    try {
      const response = await fetch(pollUrl, {credentials: 'same-origin', signal: controller.signal});
      if (!response.ok || response.redirected || !(response.headers.get('Content-Type') || '').includes('json')) return;
      const payload = await response.json();
      if (snapshot !== generation || writing || dirty || dialog.open || document.hidden) return;
      if (payload.source_version != null && Number(payload.source_version) !== Number(view.dataset.sourceVersion)) { reading = null; await request(canonical); announce('Avance actualizado desde Control Presupuestal.'); }
    } catch (error) { if (error.name !== 'AbortError') announce('No se pudo actualizar el avance. Puedes usar Actualizar.', true); }
    finally { if (reading === controller) reading = null; }
  }, 20000);
  selection(); applyFilters();
})();
