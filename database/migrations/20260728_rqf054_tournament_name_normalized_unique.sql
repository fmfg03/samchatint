-- RQF-054: make the canonical local tournament-name identity race-safe.
--
-- This migration intentionally refuses to choose winners or rename data.  Any
-- existing lower/trim collision must be adjudicated before the unique index is
-- installed.

DO $$
DECLARE
    duplicate_names TEXT;
BEGIN
    SELECT string_agg(
        format('%s (%s rows)', normalized_name, row_count),
        ', ' ORDER BY normalized_name
    )
    INTO duplicate_names
    FROM (
        SELECT lower(btrim(name)) AS normalized_name, count(*) AS row_count
        FROM tournaments
        GROUP BY lower(btrim(name))
        HAVING count(*) > 1
    ) AS collisions;

    IF duplicate_names IS NOT NULL THEN
        RAISE EXCEPTION
            'Cannot install normalized tournament-name uniqueness; resolve duplicates first: %',
            duplicate_names
            USING ERRCODE = '23505';
    END IF;
END
$$;

DO $$
DECLARE
    existing_definition TEXT;
    existing_unique BOOLEAN;
    existing_table REGCLASS;
    existing_expression TEXT;
    existing_predicate TEXT;
    existing_valid BOOLEAN;
    existing_ready BOOLEAN;
    existing_access_method NAME;
    expected_table REGCLASS := to_regclass('tournaments');
    expected_index REGCLASS := to_regclass(
        format('%I.%I', current_schema(), 'ux_tournaments_name_normalized')
    );
BEGIN
    SELECT
        pg_get_indexdef(indexrelid),
        indisunique,
        indrelid,
        regexp_replace(
            pg_get_expr(indexprs, indrelid),
            E'\\s|::text|\\(|\\)',
            '',
            'g'
        ),
        pg_get_expr(indpred, indrelid),
        indisvalid,
        indisready,
        am.amname
    INTO existing_definition, existing_unique, existing_table, existing_expression,
         existing_predicate, existing_valid, existing_ready, existing_access_method
    FROM pg_index idx
    JOIN pg_class index_class ON index_class.oid = idx.indexrelid
    JOIN pg_am am ON am.oid = index_class.relam
    WHERE idx.indexrelid = expected_index;

    IF existing_definition IS NULL THEN
        EXECUTE 'CREATE UNIQUE INDEX ux_tournaments_name_normalized '
                'ON tournaments (lower(btrim(name)))';
    ELSIF existing_unique IS NOT TRUE
       OR existing_table <> expected_table
       OR existing_expression <> 'lowerbtrimname'
       OR existing_predicate IS NOT NULL
       OR existing_valid IS NOT TRUE
       OR existing_ready IS NOT TRUE
       OR existing_access_method <> 'btree' THEN
        RAISE EXCEPTION
            'Index ux_tournaments_name_normalized exists with an incompatible definition: %',
            existing_definition
            USING ERRCODE = '55000';
    END IF;
END
$$;
