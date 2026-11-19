"""HTML views for the scoped Direction tournament dossier."""

from jinja2 import Environment

DOSSIER_PATH = "/direccion/expediente-vivo"
PHASES = {"estatal": "Estatal", "nacional": "Nacional"}
DOMAIN_LABELS = {
    "registration": "Inscripciones",
    "operations": "Operaciones",
    "finance": "Finanzas",
    "marketing": "Mercadotecnia",
}
STATUS_LABELS = {
    "available": "Disponible",
    "active": "Vigente",
    "confirmed": "Confirmado",
    "partial": "Cobertura parcial",
    "documented": "Documentado",
    "unsupported": "Sin fuente configurada",
    "not_configured": "Sin fuente configurada",
    "scope_missing": "Sin vínculo de edición",
    "scope_ambiguous": "Vínculo de edición ambiguo",
    "scope_inactive": "Edición inactiva",
    "empty": "Sin registros",
    "missing": "Sin captura",
    "no_records": "Sin registros",
    "capture_required": "Sin captura",
    "pending": "Pendiente",
    "unlinked": "Sin vínculo",
    "needs_link": "Sin vínculo",
    "payment_confirmed": "Pago confirmado",
    "payment_without_proof": "Pago registrado sin comprobante vigente",
    "requires_reconciliation": "Vínculo por conciliar",
    "unavailable": "Fuente no disponible",
    "edition_unavailable": "Fuente no disponible para la edición",
    "source_failed": "Fuente no disponible",
    "access_denied": "Acceso restringido",
    "restricted": "Acceso restringido",
    "denied": "Acceso restringido",
}
SOURCE_LABELS = {
    "postgres_registration": "Padrón de Copa Telmex · PostgreSQL",
    "tournament_soul_snapshot": "SOUL · Operaciones de torneos",
    "documents": "Documentos de Gastos",
    "payment_run": "Finanzas · Payment Run",
    "samchat_dossier_records": "Captura en SamChat",
    "dossier_capture": "Captura en SamChat",
}
FIELD_LABELS = {
    "age": "Edad",
    "age_cutoff": "Corte de edad",
    "ages": "Edades al corte",
    "phase_label": "Fase de origen",
    "kind": "Tipo de documento",
    "document_status": "Estado del documento",
    "supplier": "Proveedor",
    "requested_at": "Fecha de solicitud",
    "payment_recorded_at": "Fecha de registro del pago",
    "operations_reference": "Referencia de Operaciones",
    "home_entity": "Entidad local",
    "away_entity": "Entidad visitante",
    "amount": "Importe",
    "away_score": "Goles visitante",
    "away_team": "Visitante",
    "branch": "Categoría de inscripción",
    "category": "Categoría",
    "currency": "Moneda",
    "date": "Fecha",
    "description": "Descripción",
    "document_type": "Tipo de documento",
    "effective_payment_date": "Fecha efectiva de pago",
    "evidence_status": "Estado de la evidencia",
    "expense_date": "Fecha del gasto",
    "expense_dates": "Fechas de los gastos",
    "expenses_by_currency": "Gastos por moneda",
    "field_number": "Cancha",
    "gender": "Género",
    "home_score": "Goles local",
    "home_team": "Local",
    "identified_people": "Personas únicas identificadas",
    "identifiable_people": "Personas únicas identificables",
    "match_date": "Fecha del partido",
    "paid_amount": "Importe pagado",
    "payment_date": "Fecha efectiva de pago",
    "payment_status": "Estado del pago",
    "person_count": "Personas únicas identificadas",
    "players": "Inscripciones de jugadores",
    "players_count": "Inscripciones de jugadores",
    "position": "Posición",
    "reference": "Referencia",
    "registrations": "Inscripciones",
    "registrations_count": "Inscripciones",
    "requested_amount": "Importe solicitado",
    "round": "Ronda",
    "state": "Estado",
    "team": "Equipo",
    "teams": "Equipos",
    "team_count": "Equipos",
    "teams_count": "Equipos",
    "unique_people": "Personas únicas identificadas",
    "unidentified_registrations": "Inscripciones sin identidad acreditada",
    "unique_people_identified": "Personas únicas identificadas",
    "registrations_without_verified_identity": "Inscripciones sin identidad acreditada",
    "amount_document": "Importe del documento",
    "amount_requested": "Importe solicitado",
    "amount_paid": "Importe pagado",
    "proof_available": "Comprobante de pago vigente",
}


def _label(value):
    return FIELD_LABELS.get(value, value.replace("_", " ").capitalize())


def _status(value):
    if value is None or value == "":
        return "Sin estado"
    key = str(value).strip().lower()
    return STATUS_LABELS.get(key, key.replace("_", " ").capitalize())


def _source(value):
    if value is None or value == "":
        return "Sin fuente identificada"
    return SOURCE_LABELS.get(value, value)


def _value(value):
    if value is None or value == "":
        return "Sin dato"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (tuple, list)):
        return ", ".join(str(_value(item)) for item in value) or "Sin registros"
    return value


_TEMPLATES = Environment(autoescape=True)
_TEMPLATES.filters.update(
    label=_label, dossier_status=_status, dossier_source=_source, dossier_value=_value
)
_VIEW = _TEMPLATES.from_string("""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Expediente Vivo · SamChat</title>
  <link rel="stylesheet" href="/static/direction_dossier.css">
</head>
<body>
  <a class="skip" href="#contenido">Ir al contenido</a>
  <header class="top">
    <strong>SamChat · Dirección</strong>
    <nav aria-label="Dirección">
      <a href="/direccion/inicio">Inicio</a>
      <a href="/direccion/tableros">Tableros</a>
      <a href="/direccion/reportes">Reportes</a>
    </nav>
  </header>
  <main id="contenido">
    <header class="page-head">
      <div>
        <h1>Expediente Vivo</h1>
        {% if tournament and edition_year %}
        <p>{{ tournament.name }} · Edición {{ edition_year }}</p>
        {% endif %}
      </div>
      {% if saved %}<p class="feedback" role="status">Registro guardado.</p>{% endif %}
    </header>

    {% macro options(values, selected) %}
      {% for value, label in values %}
      <option value="{{ value }}"{% if value == selected %} selected{% endif %}>{{ label }}</option>
      {% endfor %}
    {% endmacro %}

    {% macro scope_inputs(input, entity_options, category_options, phases, kind) %}
      <label>Fase<select name="phase" required>{{ options(phases, input.phase) }}</select></label>
      {% if kind == 'operations_edition' %}
      <input type="hidden" name="entity" value="">
      <input type="hidden" name="category" value="">
      {% else %}
      {% if kind == 'participant' and not input.id %}
      <label>Entidad
        <input name="entity" list="entidades-captura" maxlength="120"
               value="{{ input.entity or '' }}" required>
      </label>
      <datalist id="entidades-captura">{{ options(entity_options, input.entity) }}</datalist>
      {% else %}
      <label>Entidad
        <select name="entity">
          <option value="">Sin entidad asignada</option>
          {{ options(entity_options, input.entity) }}
        </select>
      </label>
      {% endif %}
      <label>Categoría
        <select name="category">
          <option value="">Sin categoría asignada</option>
          {{ options(category_options, input.category) }}
        </select>
      </label>
      {% endif %}
    {% endmacro %}

    {% macro capture_form(kind, config, item) %}
      <form method="post" action="{{ path }}/captura" class="form capture-form">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <input type="hidden" name="tournament_id" value="{{ tournament.id }}">
        <input type="hidden" name="edition_year" value="{{ edition_year }}">
        <input type="hidden" name="kind" value="{{ kind }}">
        {% if item.id %}
        <input type="hidden" name="record_id" value="{{ item.id }}">
        <input type="hidden" name="expected_revision" value="{{ item.revision }}">
        {% endif %}
        {% if item.id %}
        <input type="hidden" name="phase" value="{{ item.phase }}">
        <input type="hidden" name="entity" value="{{ item.entity }}">
        <input type="hidden" name="category" value="{{ item.category }}">
        {% else %}
        {{ scope_inputs(item, entity_options, category_options, config.phase_options, kind) }}
        {% endif %}
        {% for key, field in config.fields.items() %}
          <label>{{ field.label }}
          {% if field.choices is defined %}
          <select name="field_{{ key }}"{% if field.required %} required{% endif %}>
            <option value="">Selecciona</option>
            {% set value = item.fields.get(key) %}
            {% if field.type == 'boolean' %}{% set value = value | string | lower %}{% endif %}
            {{ options(field.choices, value) }}
          </select>
          {% elif field.suggestions is defined %}
          {% set list_id = 'vinculo-' ~ (item.id if item.id else kind) ~ '-' ~ key %}
          <input name="field_{{ key }}" list="{{ list_id }}" maxlength="36"
                 value="{{ item.fields.get(key) or '' }}"
                 {% if field.required %}required{% endif %}>
          <datalist id="{{ list_id }}">
            {{ options(field.suggestions, item.fields.get(key)) }}
          </datalist>
          {% else %}
          {% set value = item.fields.get(key) %}
          <input name="field_{{ key }}" type="{{ field.html_type }}"
                 value="{{ value if value is not none else '' }}"
                 {% if field.type in ('integer', 'positive_integer', 'money') %}
                 min="{{ field.min | default(0) }}"
                 step="{{ '0.01' if field.type == 'money' else '1' }}"
                 {% else %}maxlength="{{ field.max_length | default(240) }}"{% endif %}
                 {% if field.required %}required{% endif %}>
          {% endif %}
          </label>
        {% endfor %}
        <label class="full">Referencia de respaldo
          <input name="evidence_reference" value="{{ item.evidence_reference or '' }}"
                 maxlength="240" required>
        </label>
        <button type="submit" name="intent" value="preview">Revisar captura</button>
      </form>
    {% endmacro %}

    {% macro source_meta(item) %}
      <p class="source">{{ item.source | dossier_source }} · {{ item.status | dossier_status }}
      {% if item.updated_at %} · Actualizado {{ item.updated_at }}{% endif %}</p>
    {% endmacro %}
    {% macro item_view(item) %}
      <article class="fact" data-search="{{ item.search_text }}">
        <header>
          <h4>{{ item.label }}</h4>
          {{ source_meta(item) }}
        </header>
        {% if item.details %}
        <dl>
        {% for label, value in item.details %}
          <div><dt>{{ label }}</dt><dd>{{ value | dossier_value }}</dd></div>
        {% endfor %}
        </dl>
        {% endif %}
        {% if item.image_url %}
          <img src="{{ item.image_url }}" alt="{{ item.label }}" loading="lazy">
        {% endif %}
        {% if writable and item.kind in capture_config %}
          <details class="edit">
            <summary>Editar registro</summary>
            {{ capture_form(item.kind, capture_config[item.kind], item) }}
          </details>
        {% endif %}
      </article>
    {% endmacro %}

    {% if not tournaments %}
      <p class="empty">No hay torneos activos en el alcance asignado.</p>
    {% else %}
      <section class="panel" aria-label="Torneo y edición">
        <form action="{{ path }}" method="get" class="form" id="torneo-edicion">
          <label>Torneo
            <select name="tournament_id" required>
              {{ options(tournaments, tournament.id) }}
            </select>
          </label>
          <label>Edición
            <input name="edition_year" type="number" inputmode="numeric" min="2000" max="2100"
                   list="ediciones" value="{{ edition_year if edition_year else '' }}">
          </label>
          <datalist id="ediciones">{{ options(editions, edition_year) }}</datalist>
          <button type="submit">Consultar</button>
        </form>
        {% if not edition_year %}
        <p class="empty">Selecciona una edición para consultar sus fuentes.</p>
        {% endif %}
      </section>
    {% endif %}

    {% if sources %}
    <nav class="jump" aria-label="Secciones del expediente">
      {% for section in sections %}
      <a href="#seccion-{{ loop.index }}">{{ section.name }}</a>
      {% endfor %}
      {% if writable and capture_config %}<a href="#captura">Capturas</a>{% endif %}
    </nav>
    <section class="panel" aria-labelledby="filtrar">
      <h2 id="filtrar">Filtrar expediente</h2>
      <form action="{{ path }}" method="get" class="form">
        <input type="hidden" name="tournament_id" value="{{ tournament.id }}">
        <input type="hidden" name="edition_year" value="{{ edition_year }}">
        <label>Entidad
          <select name="entity">
            <option value="">Todas las entidades</option>
            {{ options(entity_options, selected.entity) }}
          </select>
        </label>
        <label>Fase
          <select name="phase">
            <option value="">Todas las fases</option>
            {{ options(phase_options, selected.phase) }}
          </select>
        </label>
        <label>Categoría
          <select name="category">
            <option value="">Todas las categorías</option>
            {{ options(category_options, selected.category) }}
          </select>
        </label>
        <button type="submit">Aplicar filtros</button>
      </form>
      <label class="search" for="buscar">Buscar por sección, categoría o fuente
        <input id="buscar" type="search" maxlength="100" autocomplete="off">
      </label>
      <p class="empty" id="sin-resultados" role="status" hidden>
        No hay registros que coincidan con la búsqueda.
      </p>
    </section>

    <section class="panel" aria-labelledby="fuentes">
      <h2 id="fuentes">Fuentes de la edición</h2>
      {% if queried_at %}<p class="source">Consultado {{ queried_at }}</p>{% endif %}
      <dl class="sources">
        {% for name, source in sources %}
        <div><dt>{{ name }}</dt><dd>{{ source_meta(source) }}</dd></div>
        {% endfor %}
      </dl>
      {% if registration_summary %}
      <div class="fact-group">
        <h3>Totales del padrón para la edición</h3>
        {{ item_view(registration_summary) }}
      </div>
      {% endif %}
    </section>

    {% for section in sections %}
    <section class="panel dossier-section" aria-labelledby="seccion-{{ loop.index }}">
      <h2 id="seccion-{{ loop.index }}">{{ section.name }}</h2>
      {% if not section.groups %}
      <p class="empty">Sin registros en el alcance consultado.</p>
      {% endif %}
      {% for group in section.groups %}
      <div class="fact-group">
        <h3>{{ group.label }}</h3>
        {% for item in group.rows %}{{ item_view(item) }}{% endfor %}
        {% if group.pending %}
        <p class="empty">{{ group.pending_label }}</p>
        <ul class="pending">
          {% for label in group.pending %}<li>{{ label }}</li>{% endfor %}
        </ul>
        {% endif %}
        {% if not group.rows and not group.pending %}
        <p class="empty">Sin registros en el alcance consultado.</p>
        {% endif %}
      </div>
      {% endfor %}
    </section>
    {% endfor %}

    {% if writable and capture_config %}
    <section class="panel" aria-labelledby="captura">
      <h2 id="captura">Capturar o vincular datos</h2>
      {% for kind, config in capture_config.items() %}
      <details>
        <summary>{{ config.label }}</summary>
        {{ capture_form(kind, config, blank) }}
      </details>
      {% endfor %}
    </section>
    {% endif %}
    {% endif %}
  </main>
  <script nonce="{{ nonce }}">
    const search = document.getElementById("buscar");
    if (search) {
      const facts = [...document.querySelectorAll(".fact[data-search]")];
      const noResults = document.getElementById("sin-resultados");
      const normalize = (value) => value.toLocaleLowerCase("es")
        .normalize("NFD").replace(/[\u0300-\u036f]/g, "");
      search.addEventListener("input", () => {
        const term = normalize(search.value.trim());
        let matches = 0;
        facts.forEach((fact) => {
          fact.hidden = !normalize(fact.dataset.search).includes(term);
          if (!fact.hidden) matches++;
        });
        noResults.hidden = !term || matches > 0;
      });
    }
  </script>
</body>
</html>
""")

_PREVIEW = _TEMPLATES.from_string("""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Revisar captura · Expediente Vivo</title>
  <link rel="stylesheet" href="/static/direction_dossier.css">
</head>
<body>
  <header class="top">
    <strong>SamChat · Dirección</strong>
    <nav aria-label="Dirección"><a href="{{ return_url }}">Volver al expediente</a></nav>
  </header>
  <main>
    <header class="page-head">
      <div>
        <h1>Revisar captura</h1>
        <p>{{ tournament }} · Edición {{ edition_year }}</p>
      </div>
    </header>
    <section class="panel">
      <h2>{{ label }}</h2>
      <p>Comprueba el alcance y la referencia antes de guardar.</p>
      <dl class="sources">
        {% for name, value in details %}
        <div><dt>{{ name }}</dt><dd>{{ value | dossier_value }}</dd></div>
        {% endfor %}
      </dl>
      <form method="post" action="{{ path }}/captura">
        {% for key, value in hidden.items() %}
        <input type="hidden" name="{{ key }}" value="{{ value }}">
        {% endfor %}
        <button name="intent" value="confirm" type="submit">Confirmar y guardar</button>
      </form>
    </section>
  </main>
</body>
</html>
""")


def render_dossier(**context) -> str:
    """Render an escaped dossier with no public identifiers for protected data."""
    return _VIEW.render(path=DOSSIER_PATH, **context)


def render_capture_preview(**context) -> str:
    """Render the values bound to the pending confirmation receipt."""
    return _PREVIEW.render(path=DOSSIER_PATH, **context)
