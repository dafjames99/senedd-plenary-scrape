CREATE OR REPLACE PROCEDURE public.purge_all_tables()
 LANGUAGE plpgsql
AS $procedure$
BEGIN
    RAISE WARNING 'CRITICAL OPERATION: Commencing complete truncation of ALL Senedd tables...';
    TRUNCATE TABLE meetings RESTART IDENTITY CASCADE;
    TRUNCATE TABLE members RESTART IDENTITY CASCADE;
    TRUNCATE TABLE sync_checkpoints RESTART IDENTITY CASCADE;
    TRUNCATE TABLE speech_embeddings RESTART IDENTITY CASCADE;
    RAISE NOTICE 'Total database reset successful. Every table is now empty.';
END;
$procedure$