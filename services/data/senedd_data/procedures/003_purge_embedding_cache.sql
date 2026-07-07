-- Wipe the content-addressed embedding cache (src/embeddings/cache.py).
--
-- All parameters are optional filters (NULL = "match everything"), so a bare
-- CALL purge_embedding_cache() clears the whole cache. Combine filters to evict
-- a specific model, a specific config version (an experiment's rows), and/or
-- entries unused for longer than an interval — the latter is the intended
-- production bloat-control sweep, e.g.:
--     CALL purge_embedding_cache(p_older_than => interval '30 days');
CREATE OR REPLACE PROCEDURE public.purge_embedding_cache(
    IN p_model_name text DEFAULT NULL,
    IN p_version text DEFAULT NULL,
    IN p_older_than interval DEFAULT NULL
)
 LANGUAGE plpgsql
AS $procedure$
DECLARE
    deleted_count integer;
BEGIN
    DELETE FROM embedding_cache
     WHERE (p_model_name IS NULL OR model_name = p_model_name)
       AND (p_version    IS NULL OR embed_config_version = p_version)
       AND (p_older_than IS NULL OR last_used_at < now() - p_older_than);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'purge_embedding_cache: removed % cache row(s).', deleted_count;
END;
$procedure$
