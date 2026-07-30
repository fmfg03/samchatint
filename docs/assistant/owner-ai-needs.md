# Owner AI Needs - Tournament Knowledge Folders

Status: CANONICAL_INPUT
Source: Plataforma Sports owner request
Scope: assistant product requirements for tournament/entity/national-final intelligence

## Product reading

The owner is asking SamChat to produce and maintain business folders, not isolated dashboard answers.

For every tournament, SamChat must be able to assemble a folder per participating entity. Each folder should combine operations, finance, evidence, and daily partial progress. For national phases, SamChat must also assemble a tournament-level folder covering operations, finance, and marketing.

This requirement reinforces the product canon: SamChat is an operational assistant that inspects context, uses tools, creates artifacts, tracks missing evidence, and prepares auditable outputs.

## Original Spanish vocabulary

Use these owner terms as retrieval anchors for Spanish user requests:

- carpeta por entidad;
- torneo;
- operaciones;
- finanzas;
- nombre de la entidad;
- encargado de Plataforma Sports;
- encargado en la entidad;
- equipos esperados;
- equipos reales participantes;
- categoria y genero;
- jugadores por categoria, edad y genero;
- equipos que superan cada ronda;
- fase estatal;
- cuotas de arbitrajes y transportes;
- fase nacional;
- entrega de uniformes;
- viajes ida y vuelta al nacional;
- clasificacion final;
- primera ayuda al operador;
- pagos sucesivos;
- uniformes, balones, equipamiento y utilera;
- visitas de responsables AZ y CL;
- gastos de cada visita;
- hoteles contratados y camas-noche;
- desayunos, comidas, box lunch y cenas;
- unidad deportiva;
- canchas;
- servicios medicos;
- accidentes con traslado;
- pagos a hoteles;
- anticipos y liquidaciones;
- proveedores diversos;
- seguros;
- mercadotecnia;
- activacion de marcas;
- visitantes del patrocinador;
- fotografias.

## Per-tournament entity folder

Create one folder for each participating entity.

### Operations

Each entity folder should contain:

1. Entity name.
2. Plataforma Sports person responsible for the entity.
3. Entity contact person, including:
   - phone;
   - email;
   - date of birth;
   - spouse/partner name;
   - spouse/partner date of birth.
4. Expected number of teams separated by category and/or gender.
5. Number and names of real participating teams, separated by category and/or gender, with day-by-day partial visibility when available.
6. Number of players by category, age, and gender.
7. Names of teams advancing each round.
8. Brief description of how the entity organizes the state phase, when applicable, including arbitration and/or transportation fee details.
9. Names of teams advancing to the national phase.
10. Date and place for state-phase uniform delivery.
11. National travel departure and return dates.
12. Final classification place for each team.

### Finance

Each entity folder should contain:

1. Date and amount transferred for the first operator support payment and successive payments.
2. Cost of uniforms, balls, equipment, and supplies delivered to the entity.
3. Reports/results of visits made by entity owners/responsibles, including AZ and CL when applicable.
4. Amount of expenses incurred in each visit.

## National phase folder

Create one folder for each national phase of each tournament.

### Operations

The national phase folder should contain:

1. Tournament, category, opening date, closing/final-games date, duration, and host city.
2. Contracted hotels with bed-night count.
3. Contracted meals, separated into breakfasts, lunches, box lunches, and dinners.
4. Sports venue/unit where the event takes place.
5. Number and types of courts/fields used.
6. Description of on-site medical services.
7. Accident list requiring transfer.

### Finance

The national phase folder should contain:

1. Cost of Plataforma Sports staff trips to the finals venue.
2. Hotel payments, separating advances and liquidations and specifying service paid: lodging, meals, room/salon usage, etc.
3. Payments to miscellaneous providers related to finals.
4. Medical-service costs, including doctors, ambulances, and medical supplies.
5. Insurance costs.

### Marketing

The national phase folder should contain:

1. Providers physically attending brand activations.
2. Visitor names directly involved with sponsors.
3. Activity report and result, including photographs.

## Assistant behavior required

For this owner requirement, the assistant should:

1. Create or resume a persistent work case for the requested tournament/entity/fase.
2. Identify whether the request is entity-level or national-phase-level.
3. Inspect live tournament, operations, finance, document, and media sources.
4. Build the folder as an artifact with sections and evidence.
5. Mark missing fields explicitly instead of inventing.
6. Distinguish expected teams from real teams and partial day-by-day progress.
7. Link financial amounts to source requests, advances, expense reports, CFDI, or payment documents when possible.
8. Link photographs and visit reports as evidence, not prose-only claims.
9. Produce a preview/diff before creating or updating durable folders.
10. Require explicit authority before any durable write or external publication.

## Evidence types

The assistant should cite or trace these evidence categories when answering:

- `tournament`: tournament/category/phase metadata;
- `entity`: entity contact/responsible information;
- `team`: team roster and round progression;
- `player`: player counts by category, age, and gender;
- `document`: payment request, advance, expense account, CFDI, receipt, contract, report;
- `media`: photographs and visit evidence;
- `finance`: transfers, provider payments, equipment costs, visit expenses;
- `marketing`: sponsor/provider activation evidence;
- `memory`: prior decisions or unresolved missing fields.

## Quality rule

The assistant must prefer an incomplete but evidence-backed folder over a complete-looking invented folder.
