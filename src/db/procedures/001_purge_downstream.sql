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
    -- Polymorphic-embedding safety net. Speech-sourced vectors are removed by the
    -- speeches TRUNCATE above (ON DELETE CASCADE via the legacy speech_id FK), but
    -- vote/written vectors key on the generic, FK-less source_id and so survive
    -- any deletion of their owning row. Reap orphaned non-speech vectors explicitly
    -- so reprocess can never leave a vector pointing at a vanished source.
    DELETE FROM speech_embeddings se
     WHERE se.source_type = 'vote'
       AND NOT EXISTS (SELECT 1 FROM votes v WHERE v.vote_id = se.source_id);
    DELETE FROM speech_embeddings se
     WHERE se.source_type = 'written'
       AND NOT EXISTS (SELECT 1 FROM written_contributions w WHERE w.id = se.source_id);
    RAISE NOTICE 'Downstream tables purged successfully. Raw data lake is safe.';
END;
$procedure$
