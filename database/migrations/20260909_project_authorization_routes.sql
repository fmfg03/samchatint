CREATE TABLE IF NOT EXISTS authorization_positions (
    position_key VARCHAR(100) PRIMARY KEY,
    label VARCHAR(200) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS authorization_position_assignments (
    position_key VARCHAR(100) NOT NULL REFERENCES authorization_positions(position_key),
    empleado_id UUID NOT NULL REFERENCES empleados(id),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (position_key, empleado_id)
);

CREATE TABLE IF NOT EXISTS project_authorization_rules (
    tournament_id UUID PRIMARY KEY REFERENCES tournaments(id),
    eligible_position_keys JSONB NOT NULL,
    requires_operations_reference BOOLEAN NOT NULL DEFAULT FALSE,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS documento_authorization_routes (
    documento_id UUID PRIMARY KEY REFERENCES documentos(id) ON DELETE CASCADE,
    eligible_position_keys JSONB NOT NULL,
    eligible_empleado_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    requires_operations_reference BOOLEAN NOT NULL DEFAULT FALSE,
    source VARCHAR(100) NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Existing deployments created this table before route holders were snapshotted.
-- CREATE TABLE IF NOT EXISTS does not evolve that schema.
ALTER TABLE documento_authorization_routes
    ADD COLUMN IF NOT EXISTS eligible_empleado_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

INSERT INTO authorization_positions(position_key, label) VALUES
  ('director_operaciones', 'Director de Operaciones'),
  ('direccion_administracion_finanzas', 'Dirección de Administración y Finanzas'),
  ('direccion_goat', 'Dirección GOAT'),
  ('direccion_general', 'Dirección General')
ON CONFLICT (position_key) DO NOTHING;

-- Initial holders are data, not routing logic.  Future replacements are made
-- by changing this assignment table; project rules never match a person name.
INSERT INTO authorization_position_assignments(position_key, empleado_id)
SELECT 'director_operaciones', id FROM empleados
WHERE nombre = 'JOSE ODILON TRUJILLO MACEDO'
ON CONFLICT (position_key, empleado_id) DO UPDATE SET active = TRUE;

INSERT INTO authorization_position_assignments(position_key, empleado_id)
SELECT 'direccion_administracion_finanzas', id FROM empleados
WHERE nombre = 'LUIS ANGEL OROZCO COLIN'
ON CONFLICT (position_key, empleado_id) DO UPDATE SET active = TRUE;

INSERT INTO authorization_position_assignments(position_key, empleado_id)
SELECT 'direccion_goat', id FROM empleados
WHERE nombre = 'JORGE OLOF GOENAGA MARGAIN'
ON CONFLICT (position_key, empleado_id) DO UPDATE SET active = TRUE;

INSERT INTO authorization_position_assignments(position_key, empleado_id)
SELECT 'direccion_general', id FROM empleados
WHERE nombre = 'FEDERICO GONZALEZ Y VEGA'
ON CONFLICT (position_key, empleado_id) DO UPDATE SET active = TRUE;

-- Releases created before holder snapshots retain the route's position keys.
-- Reconstruct their eligible employees before the new authorization checks run.
UPDATE documento_authorization_routes route
SET eligible_empleado_ids = COALESCE((
    SELECT jsonb_agg(assignment.empleado_id::text ORDER BY assignment.empleado_id)
    FROM authorization_position_assignments assignment
    WHERE assignment.active = TRUE
      AND assignment.position_key = ANY(
        ARRAY(SELECT jsonb_array_elements_text(route.eligible_position_keys))
      )
), '[]'::jsonb)
WHERE route.eligible_empleado_ids = '[]'::jsonb;

INSERT INTO project_authorization_rules(tournament_id, eligible_position_keys, requires_operations_reference)
SELECT id, '["director_operaciones"]'::jsonb, TRUE
FROM tournaments WHERE name IN (
  'Copa Telmex Telcel de Fútbol', 'Liga Telmex Telcel de Béisbol',
  'De la Calle a la Cancha', 'Homeless World Cup México', 'Futbolito Bimbo',
  'La Merced', 'Becarios Telmex (México Siglo XXI)',
  'Gastos Administrativos - Operaciones'
)
ON CONFLICT (tournament_id) DO UPDATE SET
  eligible_position_keys = EXCLUDED.eligible_position_keys,
  requires_operations_reference = EXCLUDED.requires_operations_reference,
  active = TRUE;

INSERT INTO project_authorization_rules(tournament_id, eligible_position_keys)
SELECT id, '["direccion_administracion_finanzas"]'::jsonb
FROM tournaments WHERE name IN (
  'Gastos Administrativos - Administración y Finanzas',
  'Gastos Administrativos - Dirección General', 'Promoción de Negocios'
)
ON CONFLICT (tournament_id) DO UPDATE SET
  eligible_position_keys = EXCLUDED.eligible_position_keys,
  requires_operations_reference = FALSE,
  active = TRUE;

-- Reprocess only the two explicitly requested live requests.  They retain
-- Control Presupuestal; this supplies the new route and its required RO.
INSERT INTO documento_authorization_routes(
    documento_id, eligible_position_keys, eligible_empleado_ids,
    requires_operations_reference, source
)
SELECT
  d.id,
  rule.eligible_position_keys,
  COALESCE((
    SELECT jsonb_agg(assignment.empleado_id::text ORDER BY assignment.empleado_id)
    FROM authorization_position_assignments assignment
    WHERE assignment.active = TRUE
      AND assignment.position_key = ANY(
        ARRAY(SELECT jsonb_array_elements_text(rule.eligible_position_keys))
      )
  ), '[]'::jsonb),
  rule.requires_operations_reference,
  'migration_reprocess'
FROM documentos d
JOIN project_authorization_rules rule ON rule.tournament_id = d.torneo_id
WHERE d.numero_referencia IN ('S-26000200', 'S-26000201')
ON CONFLICT (documento_id) DO UPDATE SET
  eligible_position_keys = EXCLUDED.eligible_position_keys,
  eligible_empleado_ids = EXCLUDED.eligible_empleado_ids,
  requires_operations_reference = EXCLUDED.requires_operations_reference,
  source = EXCLUDED.source,
  resolved_at = NOW();

SELECT pg_advisory_xact_lock(5842910472931);
WITH next_reference AS (
    SELECT COALESCE(MAX(CAST(referencia_operaciones AS BIGINT)), 0) AS value
    FROM documentos WHERE referencia_operaciones ~ '^[0-9]+$'
), targets AS (
    SELECT d.id, ROW_NUMBER() OVER (ORDER BY d.creado_en, d.id) AS sequence
    FROM documentos d
    JOIN documento_authorization_routes route ON route.documento_id = d.id
    WHERE d.numero_referencia IN ('S-26000200', 'S-26000201')
      AND route.requires_operations_reference = TRUE
      AND COALESCE(d.referencia_operaciones, '') = ''
)
UPDATE documentos d
SET referencia_operaciones = CAST(next_reference.value + targets.sequence AS TEXT)
FROM targets CROSS JOIN next_reference
WHERE d.id = targets.id;

INSERT INTO project_authorization_rules(tournament_id, eligible_position_keys)
SELECT id, '["direccion_goat", "direccion_general"]'::jsonb
FROM tournaments WHERE name IN ('Gestión de Patrocinios', 'Gestión RRSS y Transmisiones')
ON CONFLICT (tournament_id) DO UPDATE SET
  eligible_position_keys = EXCLUDED.eligible_position_keys,
  requires_operations_reference = FALSE,
  active = TRUE;
