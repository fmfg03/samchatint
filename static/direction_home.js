/* Direction home: no financial calculations or tool dispatch in the browser. */
(() => {
  'use strict';
  const dataNode = document.getElementById('home-data');
  if (!dataNode) return;
  const data = JSON.parse(dataNode.textContent);
  const snapshot = data.snapshot;
  const byId = id => document.getElementById(id);
  let selected = snapshot.indicators[0];
  let conversationId = null;
  let analysisToken = null;
  let pending = null;
  let generation = 0;
  const money = metric => metric.formatted_value;
  const context = () => `${selected.label} · ${selected.period} · ${snapshot.tournaments.map(t => t.name).join(', ') || 'Sin torneos'} · corte ${snapshot.as_of}`;
  function select(id) {
    const found = snapshot.indicators.find(m => m.id === id);
    if (!found) return;
    if (pending) pending.abort();
    generation += 1;
    selected = found;
    analysisToken = null;
    byId("home-report-status").textContent = "El reporte conserva las cifras reales de este corte.";
    document.querySelectorAll('[data-metric]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.metric === id)));
    byId('home-context').textContent = context();
    byId('home-evidence').hidden = true;
    byId('home-error').hidden = true;
    byId('home-answer').textContent = `${selected.label}: ${money(selected)}. ${selected.definition}`;
    byId('home-send').disabled = false;
  }
  function evidence() {
    const fields = {Valor: money(selected), Definición: selected.definition, Fórmula: selected.formula,
      Fuente: selected.source, Periodo: selected.period, Corte: selected.as_of,
      Cobertura: `${selected.coverage.covered}/${selected.coverage.total} torneos · ${selected.status}`,
      'Responsable propuesto de validación': selected.validator, Brechas: selected.gaps.join('; ') || 'Conciliación de negocio pendiente'};
    const area = byId('home-evidence');
    area.replaceChildren();
    Object.entries(fields).forEach(([label, value]) => {
      const dt = document.createElement('dt'); dt.textContent = label;
      const dd = document.createElement('dd'); dd.textContent = value;
      area.append(dt, dd);
    });
    area.hidden = false;
  }
  async function ask(question, scenario = null) {
    if (pending) pending.abort();
    const requestGeneration = ++generation;
    const metricId = selected.id;
    const requestContext = context();
    const controller = new AbortController();
    pending = controller;
    byId('home-send').disabled = true;
    byId('home-error').hidden = true;
    byId('home-answer').textContent = 'Consultando la evidencia de esta cifra…';
    try {
      const response = await fetch('/direccion/tableros/asistente/consulta', {
        method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: {'Content-Type': 'application/json', 'X-Direction-CSRF': data.csrf},
        body: JSON.stringify({context_token: data.token, metric_id: metricId, question, conversation_id: conversationId, scenario, analysis_token: analysisToken})
      });
      if (response.redirected) throw new Error('La sesión expiró. Vuelve a entrar.');
      const payload = await response.json();
      if (requestGeneration !== generation) return;
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'No se pudo verificar la consulta. Actualiza el tablero.');
      if (payload.snapshot_id !== snapshot.snapshot_id || payload.metric_id !== metricId) throw new Error('El contexto cambió. Actualiza el tablero.');
      conversationId = payload.conversation_id;
      analysisToken = payload.analysis_token || null;
      byId("home-report-status").textContent = payload.report_requested ? "Reporte preparado con este contexto. Descarga PDF o Excel." : (payload.scenario ? "El reporte incluye este escenario separado de las cifras reales." : "El reporte incluye la conclusión de esta consulta.");
      byId('home-answer').textContent = payload.assistant_message;
      const article = document.createElement('article');
      const heading = document.createElement('strong'); heading.textContent = question;
      const cut = document.createElement('p'); cut.className = 'muted'; cut.textContent = requestContext;
      const text = document.createElement('p'); text.textContent = payload.assistant_message;
      article.append(heading, cut, text);
      byId('home-history').prepend(article);
    } catch (error) {
      if (error.name !== 'AbortError' && requestGeneration === generation) {
        byId('home-error').textContent = error.message;
        byId('home-error').hidden = false;
        byId('home-answer').textContent = 'Sin respuesta verificable. La cifra del tablero conserva su corte.';
      }
    } finally {
      if (requestGeneration === generation) { pending = null; byId('home-send').disabled = false; }
    }
  }
  async function download(format) {
    const status = byId('home-report-status');
    status.textContent = 'Generando reporte con el mismo alcance y corte…';
    const exportGeneration = generation;
    try {
      const response = await fetch(`/direccion/reportes/exportar/${format}`, {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-Direction-CSRF': data.csrf},
        body: JSON.stringify({context_token: data.token, analysis_token: analysisToken})
      });
      if (response.redirected) throw new Error('La sesión expiró. Vuelve a entrar.');
      if (!response.ok) {
        const error = await response.json();
        throw new Error(typeof error.detail === 'string' ? error.detail : 'No se pudo generar el reporte.');
      }
      if (response.headers.get('X-Direction-Snapshot') !== snapshot.snapshot_id) throw new Error('El reporte no coincide con el contexto.');
      const blob = await response.blob();
      if (generation !== exportGeneration) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url;
      link.download = `consejo-${snapshot.snapshot_id.slice(0,12)}.${format}`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status.textContent = 'Reporte descargado. Cifras y escenario conservan el corte del tablero.';
    } catch (error) { status.textContent = error.message; }
  }
  function scenarioConcepts() {
    const select = byId('scenario-concept'); select.replaceChildren();
    const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Total de la base seleccionada'; select.append(empty);
    const row = snapshot.tournaments.find(r => r.id === byId('scenario-tournament').value);
    if (row && row.concepts && row.concepts.status === 'available') row.concepts.rows.forEach(c => {
      const option = document.createElement('option'); option.value = c.id; option.textContent = c.label; select.append(option);
    });
  }
  byId('scenario-tournament').addEventListener('change', scenarioConcepts);
  byId('home-scenario').addEventListener('submit', e => {
    e.preventDefault();
    const kind = byId('scenario-kind').value;
    const spec = {kind, basis: byId('scenario-basis').value,
      tournament_id: byId('scenario-tournament').value || null, concept_id: kind === 'expense_reduction' ? byId('scenario-concept').value || null : null};
    if (kind === 'payment_delay') spec.days = Number(byId('scenario-days').value);
    else spec.percent = byId('scenario-percent').value;
    ask('Calcula este escenario con los supuestos seleccionados', spec);
  });
  document.querySelectorAll('[data-question]').forEach(b => b.addEventListener('click', () => ask(b.dataset.question)));
  document.querySelectorAll('[data-export]').forEach(b => b.addEventListener('click', () => download(b.dataset.export)));
  document.querySelectorAll('[data-metric]').forEach(b => b.addEventListener('click', () => select(b.dataset.metric)));
  document.querySelectorAll('[data-priority]').forEach(b => b.addEventListener('click', () => {
    select(b.dataset.priority); byId('home-question').focus();
  }));
  byId('home-source').addEventListener('click', evidence);
  byId('home-explain').addEventListener('click', () => ask('¿Qué explica esto?'));
  byId('home-compare').addEventListener('click', () => ask('Compara esta cifra entre los torneos autorizados'));
  byId('home-chat').addEventListener('submit', e => { e.preventDefault(); const text = byId('home-question').value.trim(); if (text) ask(text); });
  document.querySelector('.home-filters').addEventListener('submit', () => { generation += 1; if (pending) pending.abort(); });
  document.querySelector('[name=edition_year]').addEventListener('change', e => {
    const year = e.target.value;
    const first = document.querySelector('[name=date_from]');
    const last = document.querySelector('[name=date_to]');
    if (first.value.slice(0, 4) !== year) { first.value = `${year}-01-01`; last.value = `${year}-12-31`; }
  });
  // An old tournament selection must never survive a portfolio change silently.
  document.querySelector('[name=portfolio_id]').addEventListener('change', () => { document.querySelector('[name=tournament_id]').value = ''; });
  scenarioConcepts();
  select(selected.id);
})();
