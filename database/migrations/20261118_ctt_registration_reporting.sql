-- Apply as schema owner. Authorization assignments are made separately by CTT.
BEGIN;
DO $$
BEGIN
    IF current_user = 'devnous_user' THEN
        RAISE EXCEPTION 'La instalación del catálogo requiere al propietario de esquema';
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS public.copa_telmex_report_settings (
    edition_id UUID PRIMARY KEY REFERENCES public.copa_telmex_tournament_editions(id),
    age_cutoff DATE NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE
);
COMMENT ON COLUMN public.copa_telmex_report_settings.enabled IS
    'Habilitar por edición después de configurar y verificar los alcances autorizados.';

INSERT INTO public.copa_telmex_report_settings (edition_id, age_cutoff)
SELECT id, DATE '2026-12-31'
FROM public.copa_telmex_tournament_editions
WHERE roster_slug = 'copa-telmex-2026'
  AND edition_year = 2026
ON CONFLICT (edition_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.copa_telmex_edition_access (
    edition_id UUID NOT NULL REFERENCES public.copa_telmex_tournament_editions(id),
    empleado_id UUID NOT NULL REFERENCES public.empleados(id),
    state_code VARCHAR(2) NOT NULL,
    can_edit BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (edition_id, empleado_id, state_code),
    CHECK (state_code = '' OR state_code ~ '^(0[1-9]|[12][0-9]|3[0-2])$')
);
COMMENT ON COLUMN public.copa_telmex_edition_access.state_code IS
    'Clave INEGI de entidad. Un valor vacío otorga alcance nacional explícito.';
REVOKE ALL ON TABLE public.copa_telmex_edition_access, public.copa_telmex_report_settings
    FROM PUBLIC, devnous_user;
GRANT SELECT ON TABLE public.copa_telmex_edition_access, public.copa_telmex_report_settings
    TO devnous_user;
DO $$
DECLARE catalog REGCLASS;
BEGIN
    FOREACH catalog IN ARRAY ARRAY[
        'public.copa_telmex_edition_access'::regclass,
        'public.copa_telmex_report_settings'::regclass
    ] LOOP
        IF has_table_privilege('devnous_user', catalog, 'INSERT,UPDATE,DELETE,TRUNCATE')
            OR has_any_column_privilege('devnous_user', catalog, 'INSERT,UPDATE') THEN
            RAISE EXCEPTION 'El runtime tiene permisos efectivos de escritura sobre %', catalog;
        END IF;
    END LOOP;
END $$;
COMMIT;
