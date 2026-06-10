CREATE OR REPLACE PROCEDURE public.purge_downstream_tables(IN clear_dimensions boolean, IN clear_embeddings boolean)
 LANGUAGE plpgsql
AS $procedure$
BEGIN
    RAISE NOTICE 'Starting downstream truncation sequence...';
    TRUNCATE TABLE speech_parts RESTART IDENTITY CASCADE;
    TRUNCATE TABLE speeches RESTART IDENTITY CASCADE;
    TRUNCATE TABLE oral_questions RESTART IDENTITY CASCADE;
    TRUNCATE TABLE classified_contributions RESTART IDENTITY CASCADE;
    TRUNCATE TABLE clean_contributions RESTART IDENTITY CASCADE;
    TRUNCATE TABLE procedural_events RESTART IDENTITY CASCADE;
    IF clear_embeddings THEN
        TRUNCATE TABLE speech_embeddings RESTART IDENTITY CASCADE;
    END IF;
    IF clear_dimensions THEN
        TRUNCATE TABLE member_job_titles RESTART IDENTITY CASCADE;
    END IF;
    RAISE NOTICE 'Downstream tables purged successfully. Raw data lake is safe.';
END;
$procedure$