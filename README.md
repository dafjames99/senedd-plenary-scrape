# Senedd Plenary Platform

Turns Senedd Cymru (Welsh Parliament) plenary records into a queryable,
semantically searchable database — with an MCP server for LLM-driven research
and a web frontend for humans.

A monorepo of four composite services (see [ARCHITECTURE.md](ARCHITECTURE.md)):

| Service | Where | What it does |
|---|---|---|
| **Database / ETL** | `services/data` | Scrapes plenary XML (transcripts, votes, QNR), reconstructs speeches with full lineage, owns the Postgres schema (Alembic + pgvector) |
| **Embeddings** | `services/embeddings` | Chunks + embeds speeches/votes/written Q&A (pluggable providers), content-addressed cache, experiment harness |
| **MCP server** | `services/mcp` | Read-only retrieval service (semantic search + structured lookups) exposed as MCP tools/resources/prompts |
| **Web app** | `apps/web` | Next.js frontend: meeting search, video + synced transcript, LLM query with citation blocks |

## Quick start

```bash
# Python side (uv workspace: one lock, one venv, all services editable)
uv sync --dev
cp .env.template .env          # set DATABASE_URL (Postgres + pgvector)

# Provision the schema (Alembic head + SQL procedures)
DATABASE_URL=postgresql://... make provision

# Ingest data
uv run python main.py                          # incremental sync (network)
uv run python scripts/backfill.py --start 2024-01-01 --end 2024-01-31 --action all

# Run things
make test        # offline suite (no DB/GPU needed)
make mcp         # MCP server on stdio
make dev         # MCP over HTTP + Next.js dev server together

# Web app only
pnpm install && pnpm --filter @senedd/web dev
```

Per-service docs: [MCP server](services/mcp/senedd_mcp/README.md) ·
[experiments](services/embeddings/experiments/README.md) ·
[migrations](services/data/alembic/README) · [frontend PRD](apps/web/PRD.md)

Project history and roadmap: [PLAN.md](PLAN.md) ·
[PRODUCTION.md](PRODUCTION.md) · [MONOREPO_PLAN.md](MONOREPO_PLAN.md)

## Data model in one paragraph

`raw_contributions` is the verbatim XML source of truth (with per-contribution
timestamps and SeneddTV URLs). Transformation rebuilds derived tables per
meeting, atomically and idempotently: cleaned/classified rows →
**reconstructed speeches** (`speeches` + `speech_parts` lineage) → members,
procedural events. Votes and written Q&A (QNR) attach to the same meetings.
`speech_embeddings` stores polymorphic vectors (`source_type` ∈ speech |
written | vote) per model; semantic search ranks the best chunk per item with
cosine distance and returns citation metadata. `speech_fidelity` is a QA
signal over transcript quality — re-run `make fidelity` after any
ingest/reprocess.

## License & attribution

Senedd data is public record used under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
