/* Private fixed-case review. No creation, publication, capture or browser storage. */
(() => {
  'use strict';
  const panel = document.querySelector('#bs-panel');
  if (!panel || panel.dataset.bound) return;
  panel.dataset.bound = 'true';
  const open = document.querySelector('#bs-open');
  const minimize = document.querySelector('#bs-minimize');
  const refreshButton = document.querySelector('#bs-refresh');
  const content = document.querySelector('#bs-content');
  const live = document.querySelector('#bs-live');
  const notes = document.querySelector('#bs-notes');
  const reference = document.querySelector('#bs-reference');
  const endpoint = '/soporte/batisenal/piloto/case';
  let controller = null, generation = 0, timer = null, attempts = 0;
  let ended = content.dataset.state === 'ended';
  function rememberReference() {
    const value = content.querySelector('[data-operation-reference]')?.textContent;
    if (value) reference.textContent = `Última referencia: ${value}`;
  }
  function status() {
    const messages = {
      pending: 'Pendiente de conexión o de una consulta explícita desde Chatty.',
      stored: 'Borrador privado guardado. No publicado.',
      stale: 'El contexto cambió. Requiere una nueva consulta explícita.',
      ended: 'Acceso terminado. El historial se conserva.'
    };
    live.textContent = messages[content.dataset.state] || 'Resultado por confirmar.';
  }
  function stop() {
    clearTimeout(timer); timer = null;
    controller?.abort(); controller = null; generation++;
  }
  function schedule() {
    clearTimeout(timer);
    if (!ended && !panel.hidden && !document.hidden && attempts < 120) {
      timer = setTimeout(() => refresh(false), 10000);
    }
  }
  async function refresh(focus = false) {
    stop();
    if (panel.hidden || document.hidden || ended) return;
    const current = generation;
    const request = new AbortController(); controller = request;
    const timeout = setTimeout(() => request.abort(), 8000);
    attempts++;
    rememberReference();
    // Never expose the old DOM while reauthorizing a reopened panel.
    content.replaceChildren();
    content.setAttribute('aria-busy', 'true');
    live.textContent = 'Consultando el estado del caso. Esto no inicia una consulta de Chatty.';
    refreshButton.disabled = true;
    try {
      const response = await fetch(endpoint, {
        credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        signal: request.signal, headers: {'Accept': 'text/html'}
      });
      if (current !== generation || panel.hidden) return;
      if ([401, 403, 404, 410].includes(response.status)) {
        ended = true; content.dataset.state = 'ended';
        notes.value = ''; reference.textContent = ''; status(); return;
      }
      if (!response.ok) throw new Error('unavailable');
      const body = await response.text();
      if (current !== generation || panel.hidden) return;
      const parsed = new DOMParser().parseFromString(body, 'text/html');
      const next = parsed.querySelector('#bs-content');
      if (!next || !['pending', 'stored', 'stale', 'ended'].includes(next.dataset.state)) {
        throw new Error('invalid_response');
      }
      content.dataset.state = next.dataset.state;
      ended = next.dataset.state === 'ended';
      if (ended) { notes.value = ''; reference.textContent = ''; }
      content.replaceChildren(...Array.from(next.childNodes).map(node => document.importNode(node, true)));
      rememberReference(); status();
      if (focus) minimize.focus();
    } catch (error) {
      if (current !== generation || panel.hidden) return;
      live.textContent = 'Resultado por confirmar. Conservamos tus notas y referencia. Consulta de nuevo para recuperar el estado; no envíes otra propuesta automáticamente.';
    } finally {
      clearTimeout(timeout);
      if (current === generation) {
        controller = null; content.removeAttribute('aria-busy');
        refreshButton.disabled = ended; schedule();
      }
    }
  }
  minimize.addEventListener('click', () => {
    rememberReference(); stop(); content.replaceChildren();
    panel.hidden = true; open.hidden = false; open.setAttribute('aria-expanded', 'false'); open.focus();
  });
  open.addEventListener('click', () => {
    panel.hidden = false; open.hidden = true; open.setAttribute('aria-expanded', 'true');
    attempts = 0;
    if (ended) { status(); minimize.focus(); } else { refresh(true); }
  });
  refreshButton.addEventListener('click', () => { attempts = 0; refresh(false); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { stop(); content.replaceChildren(); }
    else if (!panel.hidden && !ended) refresh(false);
  });
  window.addEventListener('pagehide', () => { stop(); content.replaceChildren(); });
  rememberReference(); status(); refreshButton.disabled = ended; schedule();
})();
