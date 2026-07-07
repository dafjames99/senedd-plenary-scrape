# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (uv workspace: Python 3.13+; single lock + venv for all services)
uv sync --dev

# Monorepo layout: services/{data,embeddings,mcp} (uv workspace members),
# apps/web (Next.js, pnpm workspace). See ARCHITECTURE.md. Makefile has the
# canonical task commands (make test / mcp / mcp-http / web / dev / provision).

# Optional extras
uv sync --extra openai             # OpenAI embedding provider
uv sync --extra sentence-transformer  # sentence-transformers provider

# Run tests (all mocked — no DB or GPU required)
uv run pytest tests/
uv run pytest tests/test_semantic_search.py::test_query_prefix_applied  # single test

# Run the pipeline (local dev — main.py composes the three stages end-to-end)
python main.py                      # incremental sync (default): raw acquisition + transform + embed
python main.py --force              # full rebuild from XML (drops all data; schema preserved)
python main.py --mode embed-only    # embedding sweep only
python main.py --mode embed-only --force  # wipe and re-embed for active model
python main.py --mode reprocess     # re-run downstream phases from existing raw_contributions (no network)
python main.py --embed-loop         # loop embedding until all speeches are embedded
python main.py -i                   # interactive prompt between embedding batches

# Run the pipeline stages independently (each concern is its own module + entry point)
python -m senedd_data.acquisition        # RAW only: fetch + XML → source-of-truth tables (no derived)
python -m senedd_data.transformation     # DERIVED only: rebuild from raw (auto-discovers meetings needing it)
python -m senedd_data.transformation --all           # purge_downstream + full rebuild from raw
python -m senedd_data.transformation --meetings 123,456  # transform specific meeting IDs
python -m senedd_embeddings.embed --loop           # embedding sweep only (run manually)

# Semantic search
python scripts/query_speeches.py "NHS waiting times"
python scripts/query_speeches.py "climate policy" --limit 10 --min-similarity 60 --speaker "Jones"

# Historical backfill
python scripts/backfill.py --start 2024-01-01 --end 2024-06-30 --action all
python scripts/backfill.py --start 2024-01-01 --action harvest  # scrape to CSV only
python scripts/backfill.py --start 2024-01-01 --action ingest   # load from existing CSV

# Transcript-fidelity QA (run after ingest/reprocess; not part of the pipeline)
python -m senedd_data.fidelity                 # compute + persist per-speech flags
python -m senedd_data.fidelity --dry-run       # report only, no write
python analysis/wpm_fidelity.py           # read-only charts + suspect CSV (speech level)

# Embedding experiments (see services/embeddings/experiments/README.md for the full procedure)
python -m senedd_embeddings.experiments.runner --config services/embeddings/experiments/configs/baseline-gemma.yaml
python -m senedd_embeddings.experiments.runner --config ... --limit 100   # wiring smoke test (flagged partial)
python -m senedd_embeddings.experiments.runner --list                     # experiment namespaces in the DB
python -m senedd_embeddings.experiments.runner --purge exp:name-hash8     # drop one experiment's vectors

# Retrieval eval scoreboard (live stack; recorded baseline in tests/eval/BASELINE.md)
python -m tests.eval.runner

# Migrations (Alembic lives with the data service)
uv run alembic -c services/data/alembic.ini upgrade head

# MCP server
uv run python -m senedd_mcp                               # stdio
uv run python -m senedd_mcp --transport streamable-http   # HTTP (web app / remote)

# Frontend (apps/web — Next.js + Tailwind; see apps/web/PRD.md)
pnpm install
pnpm --filter @senedd/web dev
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

### Pipeline stages (three separated concerns)

The former monolithic `SeneddPipeline` has been split along the raw→derived seam
(the same seam codified by `purge_downstream_tables`). Each stage is independently
runnable, so raw ingest and derived rebuild can be scheduled/reasoned about apart.
Schema provisioning (`services/data/senedd_data/provisioning.py`, Alembic + procedures) and the shared
session factory (`services/data/senedd_data/session.py`, `get_session`) back all stages. A thin
deprecated `SeneddPipeline` facade remains in `services/data/senedd_data/pipeline.py` for compat.

**1. `AcquisitionPipeline`** (`services/data/senedd_data/acquisition.py`) — **RAW only, network + XML.**
Writes source-of-truth tables only: `raw_contributions`, `meetings`, `members`,
`votes`, `vote_records`, `written_contributions`, plus operational
`sync_checkpoints` / `artifact_watch`. Never builds a derived table.
- `ingest_xml` / `ingest_votes` / `ingest_qnr` — idempotent upserts per artifact.
- `run_incremental(...)` — detect new transcripts, raw-ingest, register + sweep
  Votes/QNR watches, checkpoint. Returns the ingested meeting IDs.
- `acquire_meetings(...)` — explicit-list backfill (transcript + Votes/QNR in one pass).

**2. `TransformationPipeline`** (`services/data/senedd_data/transformation.py`) — **DERIVED only, no network.**
Reads raw, rebuilds derived tables per meeting (all atomic per `meeting_id`):
1. **Clean & classify** → `clean_contributions`, `classified_contributions`, `oral_questions`
2. **Reconstruct speeches** → `speeches`, `speech_parts` — boundary = speaker change OR agenda item change; prefers English translation over verbatim Welsh
3. **Build dimensions** → `members`, `member_job_titles`
4. **Build procedural events** → `procedural_events`
- `transform_meetings(ids=None)` — transform given meetings; with `None`,
  auto-discovers meetings that have raw contributions but no speeches yet.
- `reprocess_all(...)` — `purge_downstream_tables` then rebuild every meeting from raw.
- Depends only on `raw_contributions` (the transcript), never on Votes/QNR — so a
  *late* Votes/QNR attachment needs only re-embedding, not re-transform.

Cascade FK constraints (`ondelete="CASCADE"`) make reprocessing safe — re-running a meeting purges its existing speeches automatically.

**3. `EmbeddingPipeline`** (`services/embeddings/senedd_embeddings/pipeline.py`) — chunks speeches, embeds them, stores vectors.

- Skips speeches under 10 words
- Prepends `"<speaker_name>: "` to each chunk before embedding
- Model-specific prefixes (`doc_prefix`, `query_prefix`) are defined in `MODEL_METADATA_REGISTRY` and must be applied symmetrically at query time

### Embedding providers

Pluggable via `PROVIDER_REGISTER` in `services/embeddings/senedd_embeddings/providers.py`. Each provider must be registered in `MODEL_METADATA_REGISTRY` (`services/embeddings/senedd_embeddings/config.py`) as `"<provider>/<model>"` — the settings validator rejects unknown combinations at startup.

Supported: `sentence-transformers/all-MiniLM-L6-v2`, `ollama/embeddinggemma:300m`, `openai/text-embedding-3-small`.

### Embedding cache

`services/embeddings/senedd_embeddings/cache.py` is a content-addressed cache (`embedding_cache` table) keyed on `sha256(formatted_chunk)` + `model_name`, where `formatted_chunk` is the exact string sent to the provider (`doc_prefix + speaker_prefix + chunk`). The pipeline embeds only cache misses and writes computed vectors back in the same transaction. It has **no FK to `speeches`**, so it survives the delete-and-rebuild of speeches on re-ingest — a backfill re-run (or a reverted chunking experiment) reuses every vector instead of recomputing it. `embed_config_version` is provenance only; correctness rides on the hash. A dev aid (disable via `EMBED_CACHE_ENABLED=false` in prod); wipe with `CALL purge_embedding_cache(...)`.

### Embedding experiments

`services/embeddings/senedd_embeddings/experiments/` is a config-driven harness for comparing chunking/model recipes (`services/embeddings/experiments/configs/*.yaml`). Each run embeds the speech corpus under an isolated namespace — `model_name = "exp:<name>-<confighash8>"` in `speech_embeddings`, invisible to production search — then scores it against the labelled cases in `tests/eval/cases.yaml` using the production ranking CTE with the config's own `query_prefix` (doc/query symmetry per experiment). Quality (MRR, hit@k, recall@k), performance (embed throughput, query latency p50/p95) and storage are appended to `services/embeddings/experiments/runs.jsonl` and ranked in the auto-generated `services/embeddings/experiments/RESULTS.md`; both are committed. The embedding cache is keyed on the provider's *real* model name, so experiments share vectors with production and each other. Procedure, comparability rules, and the promotion path are in `services/embeddings/experiments/README.md`.

### Incremental sync

`DataFetcher` (`services/data/senedd_data/fetcher.py`) scrapes `https://record.senedd.wales/XMLExport`, parses meeting rows filtered by transcript type (default `BilingualTranscript`), and downloads XML for meetings newer than the last `SyncCheckpoint`. The backfill script (`scripts/backfill.py`) walks a date range day-by-day (with rate limiting) and can cache discovered meetings to CSV as a resumability checkpoint.

### Semantic search

`scripts/query_speeches.py` embeds the query using the active provider, then runs a PostgreSQL CTE that ranks all chunks per speech by cosine distance (`<=>` from pgvector) and returns the best-matching chunk per speech. Speaker filtering uses parameterized `ILIKE` to avoid injection.

### Transcript fidelity (QA)

`services/data/senedd_data/fidelity.py` is a derived, on-demand QA pass (not a pipeline phase) that scores each speech for transcript fidelity into the `speech_fidelity` table. Duration is the gap to the next speech's start within the meeting; `wpm` is the served `speech_text` word count over that duration. It is computed at **speech** level deliberately — contribution level is dominated by an interjection artifact (a brief interjection's near-identical timestamp collapses the inferred duration). Two complementary signals: WPM (`flag`: `too_slow`/`too_fast`/`broken_timestamp`/`low_confidence`/`no_duration`/`ok`) and `ends_midsentence` — though em-dash *interruptions* are treated as terminal (the corpus marks them cleanly), so that signal mostly confirms well-formed boundaries rather than finding truncations. `is_suspect` is the coarse consumer flag, surfaced by the MCP's `senedd_get_speech` (joined in `services/mcp/senedd_search/lookups.py`) so an answer can be caveated. The table has a cascade FK to `speeches`, so a reprocess purges it — **re-run `python -m senedd_data.fidelity` after ingest/reprocess**. `analysis/wpm_fidelity.py` is the read-only visual companion (charts + suspect CSV; `--level contribution` shows the artifact). It is a *measurement*, not a remediation: missing source text cannot be recovered.

### SQL procedures

Stored in `services/data/senedd_data/procedures/` and registered at schema creation time. `001_purge_downstream.sql` is called by `reprocess` mode to safely truncate downstream tables before rebuilding. `003_purge_embedding_cache.sql` wipes the embedding cache, with optional `model_name` / `version` / `older_than` filters (NULL = all).
