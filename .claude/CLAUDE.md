# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (requires Python 3.14+)
uv sync

# Optional extras
uv sync --extra openai             # OpenAI embedding provider
uv sync --extra sentence-transformer  # sentence-transformers provider

# Run tests (all mocked — no DB or GPU required)
uv run pytest tests/
uv run pytest tests/test_semantic_search.py::test_query_prefix_applied  # single test

# Run the pipeline
python main.py                      # incremental sync (default)
python main.py --force              # full rebuild from XML (drops all tables)
python main.py --mode embed-only    # embedding sweep only
python main.py --mode embed-only --force  # wipe and re-embed for active model
python main.py --mode reprocess     # re-run downstream phases from existing raw_contributions (no network)
python main.py --embed-loop         # loop embedding until all speeches are embedded
python main.py -i                   # interactive prompt between embedding batches

# Semantic search
python scripts/query_speeches.py "NHS waiting times"
python scripts/query_speeches.py "climate policy" --limit 10 --min-similarity 60 --speaker "Jones"

# Historical backfill
python scripts/backfill.py --start 2024-01-01 --end 2024-06-30 --action all
python scripts/backfill.py --start 2024-01-01 --action harvest  # scrape to CSV only
python scripts/backfill.py --start 2024-01-01 --action ingest   # load from existing CSV

# Transcript-fidelity QA (run after ingest/reprocess; not part of the pipeline)
python -m src.db.fidelity                 # compute + persist per-speech flags
python -m src.db.fidelity --dry-run       # report only, no write
python analysis/wpm_fidelity.py           # read-only charts + suspect CSV (speech level)
```

## Configuration

Copy `.env` and set:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./sqlite_database.db` | Use PostgreSQL URL in practice |
| `EMBEDDING_PROVIDER` | `sentence-transformer` | `sentence-transformer`, `ollama`, `openai` |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Must match a key in `MODEL_METADATA_REGISTRY` |
| `OLLAMA_URL` | `http://localhost:11434` | Required if using `ollama` provider |
| `HF_TOKEN` | — | HuggingFace token for gated models |
| `OPENAI_API_KEY` | — | Required if using `openai` provider |
| `EMBED_BATCH_SIZE` | `250` | Speeches per embedding batch |
| `EMBED_CACHE_ENABLED` | `true` | Content-addressed embedding cache (dev aid; disable in prod) |
| `LOG_LEVEL` | `INFO` | |

## Architecture

### Two pipelines

**`SeneddPipeline`** (`src/db/pipeline.py`) — ingests Senedd XML and reconstructs speeches.

Six sequential phases per meeting (all run atomically per `meeting_id`):
1. **Ingest XML** → `raw_contributions`, `meetings`, `members` (idempotent upserts)
2. **Clean & classify** → `clean_contributions`, `classified_contributions`, `oral_questions`
3. *(part of phase 2)*
4. **Reconstruct speeches** → `speeches`, `speech_parts` — boundary = speaker change OR agenda item change; prefers English translation over verbatim Welsh
5. **Build dimensions** → `members`, `member_job_titles`
6. **Build procedural events** → `procedural_events`

Cascade FK constraints (`ondelete="CASCADE"`) make reprocessing safe — re-running a meeting purges its existing speeches automatically.

**`EmbeddingPipeline`** (`src/embeddings/pipeline.py`) — chunks speeches, embeds them, stores vectors.

- Skips speeches under 10 words
- Prepends `"<speaker_name>: "` to each chunk before embedding
- Model-specific prefixes (`doc_prefix`, `query_prefix`) are defined in `MODEL_METADATA_REGISTRY` and must be applied symmetrically at query time

### Embedding providers

Pluggable via `PROVIDER_REGISTER` in `src/embeddings/providers.py`. Each provider must be registered in `MODEL_METADATA_REGISTRY` (`src/embeddings/config.py`) as `"<provider>/<model>"` — the settings validator rejects unknown combinations at startup.

Supported: `sentence-transformers/all-MiniLM-L6-v2`, `ollama/embeddinggemma:300m`, `openai/text-embedding-3-small`.

### Embedding cache

`src/embeddings/cache.py` is a content-addressed cache (`embedding_cache` table) keyed on `sha256(formatted_chunk)` + `model_name`, where `formatted_chunk` is the exact string sent to the provider (`doc_prefix + speaker_prefix + chunk`). The pipeline embeds only cache misses and writes computed vectors back in the same transaction. It has **no FK to `speeches`**, so it survives the delete-and-rebuild of speeches on re-ingest — a backfill re-run (or a reverted chunking experiment) reuses every vector instead of recomputing it. `embed_config_version` is provenance only; correctness rides on the hash. A dev aid (disable via `EMBED_CACHE_ENABLED=false` in prod); wipe with `CALL purge_embedding_cache(...)`.

### Incremental sync

`DataFetcher` (`src/db/fetcher.py`) scrapes `https://record.senedd.wales/XMLExport`, parses meeting rows filtered by transcript type (default `BilingualTranscript`), and downloads XML for meetings newer than the last `SyncCheckpoint`. The backfill script (`scripts/backfill.py`) walks a date range day-by-day (with rate limiting) and can cache discovered meetings to CSV as a resumability checkpoint.

### Semantic search

`scripts/query_speeches.py` embeds the query using the active provider, then runs a PostgreSQL CTE that ranks all chunks per speech by cosine distance (`<=>` from pgvector) and returns the best-matching chunk per speech. Speaker filtering uses parameterized `ILIKE` to avoid injection.

### Transcript fidelity (QA)

`src/db/fidelity.py` is a derived, on-demand QA pass (not a pipeline phase) that scores each speech for transcript fidelity into the `speech_fidelity` table. Duration is the gap to the next speech's start within the meeting; `wpm` is the served `speech_text` word count over that duration. It is computed at **speech** level deliberately — contribution level is dominated by an interjection artifact (a brief interjection's near-identical timestamp collapses the inferred duration). Two complementary signals: WPM (`flag`: `too_slow`/`too_fast`/`broken_timestamp`/`low_confidence`/`no_duration`/`ok`) and `ends_midsentence` — though em-dash *interruptions* are treated as terminal (the corpus marks them cleanly), so that signal mostly confirms well-formed boundaries rather than finding truncations. `is_suspect` is the coarse consumer flag, surfaced by the MCP's `senedd_get_speech` (joined in `src/search/lookups.py`) so an answer can be caveated. The table has a cascade FK to `speeches`, so a reprocess purges it — **re-run `python -m src.db.fidelity` after ingest/reprocess**. `analysis/wpm_fidelity.py` is the read-only visual companion (charts + suspect CSV; `--level contribution` shows the artifact). It is a *measurement*, not a remediation: missing source text cannot be recovered.

### SQL procedures

Stored in `src/db/procedures/` and registered at schema creation time. `001_purge_downstream.sql` is called by `reprocess` mode to safely truncate downstream tables before rebuilding. `003_purge_embedding_cache.sql` wipes the embedding cache, with optional `model_name` / `version` / `older_than` filters (NULL = all).
