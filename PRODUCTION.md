# Production & Deployment Plan

Turns PLAN.md's one-line Phase 5 into an actionable deployment plan for the two
things that need to exist in production: the **database** (Postgres + pgvector,
continuously synced) and the **MCP server** (read-only, streamable HTTP).
Researched 2026-07; sources at the bottom.

The system's shape drives every choice here: ingestion is a **batch job**
(minutes per day, zero compute between runs), the corpus is **small** by vector
standards (~4.8k speeches today; a full six-year Senedd term is roughly
100–150k speeches ≈ well under 1 GB of vectors), and the read path is a
**stateless, read-only** service over public data. Nothing needs to be big,
and almost nothing needs to be always-on.

---

## 1. Recommended stack

| Concern | Recommendation | Monthly cost |
|---|---|---|
| Database | **Neon** (serverless Postgres, pgvector) | $0 free tier → $19 Launch when corpus/latency demands |
| Embeddings | **openai/text-embedding-3-small** (per PLAN.md Phase 5) | < $1 one-off re-embed; pennies/month incremental |
| Ingestion scheduler | **GitHub Actions cron** (`.github/workflows/sync.yml`, already in repo) | $0 (public repo) |
| MCP server | **Render** web service (FastMCP streamable HTTP), Fly.io as alternative | $0 hobby → ~$7 starter |
| Secrets | GitHub Actions secrets (ingest) + Render env vars (MCP) | — |

### 1a. Database: Neon

Why Neon over the alternatives:

- **pgvector supported natively**, like every managed Postgres now — not a
  differentiator, just a requirement.
- **Scale-to-zero matches the workload.** The database is written to for a few
  minutes a day and read sporadically (MCP queries). Neon suspends idle
  compute (default after 5 min) and bills only active time; a Supabase
  project costs $25/mo flat regardless of usage. Cold starts are
  ~300–800 ms, p99 ≈ 500 ms — acceptable for a scheduled batch job, and
  tolerable for early MCP usage. If/when cold starts annoy interactive users,
  a paid plan can lengthen the suspend timeout or disable suspension —
  a config change, not a migration.
- **Branching is genuinely useful here**: a copy-on-write DB branch gives the
  embedding-experiment framework (`services/embeddings/experiments/README.md`) a full production
  corpus to run against without touching production vectors, then gets
  deleted.
- **Free tier is a real staging environment**: 0.5 GB storage / 100 CU-hours
  per month covers the current corpus (~4.8k speeches, 768-dim gemma ≈
  15 MB of vectors) many times over. The historic-backfill corpus with
  1536-dim vectors will eventually cross 0.5 GB — that is the trigger to move
  to Launch ($19/mo), not a day-one cost.

Alternatives considered:

- **Supabase** — equivalent pgvector support and a fine choice, but its value
  is the bundled auth/storage/realtime platform, none of which this project
  uses. No scale-to-zero; $25/mo flat for Pro. Prefer it only if the Phase 5
  demo app later wants its bundled auth.
- **Railway/Render Postgres** — fine databases, but hobby-tier Postgres there
  is a smaller managed offering (backup/PITR/extension surface) than either
  Neon or Supabase; use them for the *app*, not the data.
- **Self-managed (Fly volumes / VPS)** — cheapest at scale, but this corpus
  never reaches the scale that justifies owning backups, upgrades, and
  pgvector builds.

### 1b. Embedding model: openai/text-embedding-3-small

Already the registry's production candidate. 1536-dim, 8k-token context, no
instruction prefixes to keep symmetric. Cost is a non-issue at this corpus
size: the full historic backfill is on the order of 30M tokens ≈ **$0.60**
one-off at $0.02/1M; a week of new plenaries is fractions of a cent. The
gemma/Ollama path stays the local-dev default (free, offline) — coexistence by
`model_name` is already how the store works.

**Do not commit the re-embed until the experiment framework has validated the
recipe.** Run `services/embeddings/experiments/configs/openai-small.yaml` (and chunk-size variants)
against a full corpus first — the winning config's chunk parameters get
promoted into `MODEL_METADATA_REGISTRY` before the production re-embed. The
one-time cost of being wrong is cents, but the eval baseline reset is not free:
`tests/eval/BASELINE.md` must be re-recorded under the new model either way.

### 1c. Ingestion: GitHub Actions cron

There is no always-on ingestion component to host. `sync.yml` (in this repo)
runs the incremental pipeline Tue–Fri evenings — acquisition, transform, embed
sweep, fidelity QA — against `secrets.DATABASE_URL`. It is armed by setting
the `SYNC_ENABLED` repo variable to `true`, and supports `workflow_dispatch`
for manual/embed-only runs. Actions minutes are free for public repos, and a
sync run is minutes long. Failure notifications come free with Actions
(email/UI); no extra monitoring layer is warranted at this scale.

Rejected: a worker dyno/machine polling on a timer (pay to sleep), and
pg_cron (the pipeline needs Python + network, not SQL).

### 1d. MCP server: Render (Fly.io as alternative)

The MCP server is already transport-ready (`--transport http`, streamable
HTTP). It is stateless and read-only, so any container PaaS works; Render is
the lowest-friction fit: deploy from the GitHub repo, it injects `PORT`, and
the service is reachable at `https://<service>.onrender.com/mcp` — exactly the
shape MCP clients expect for a custom connector. Fly.io is the alternative if
we later want multi-region or finer machine control; Railway if usage-based
billing is preferred.

Operational requirements for the service (from PLAN.md follow-ups):

1. **Read-only Postgres role** (deferred from Phase 2 — now due):

   ```sql
   CREATE ROLE mcp_reader LOGIN PASSWORD '...';
   GRANT CONNECT ON DATABASE senedd_db TO mcp_reader;
   GRANT USAGE ON SCHEMA public TO mcp_reader;
   GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_reader;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_reader;
   ```

   The MCP's `DATABASE_URL` uses this role; the ingest role stays exclusive
   to GitHub Actions. A compromised/buggy MCP can then never mutate data.
2. **Connection pooling** (Phase 2 follow-up): the service layer builds an
   engine per URL via `services/data/senedd_data/session.get_engine` (lru_cached) — verify pool
   sizing under concurrent HTTP clients and put Neon's pooled connection
   string (PgBouncer) in `DATABASE_URL` rather than the direct one.
3. **Auth**: the data is public record (OGL v3.0), so the server can launch
   unauthenticated — the MCP spec only mandates OAuth 2.1 *for protected*
   servers. Add per-IP rate limiting at the platform level from day one.
   If it later becomes a listed/submitted Claude connector, revisit OAuth
   (Claude supports DCR; callback `https://claude.ai/api/mcp/auth_callback`).
4. **Health check**: expose a trivial endpoint for the platform's health
   probe (keeps deploys honest and restarts hung processes).
5. **Query-embedding cost control**: every search embeds the query via
   OpenAI. The `embedding_cache` table already hashes query strings safely
   (content-addressed, model-keyed) — enabling cache lookups on the *query*
   path in the MCP is the cheap Phase 5 guardrail from PLAN.md.

### 1e. What stays out of scope here

The public **demo app** (web UI + hosted agent loop, guardrails, model choice
via Loop-B eval) remains PLAN.md Phase 5's second half — it depends on
everything above existing first and is unchanged by this plan.

---

## 2. Schema work before the production re-embed

The current `embedding_vector` column is a **dimensionless** `Vector`, which
pgvector cannot ANN-index — fine at 5k vectors (sequential scan), wrong for a
100k+ backfilled corpus. Committing to the production model fixes the
dimension, unlocking an index. Plan (one additive Alembic migration + one
follow-up):

1. **Add a typed column** `embedding_halfvec halfvec(1536)` to
   `speech_embeddings`. `halfvec` (16-bit floats) halves storage/RAM for
   negligible recall loss and is the standard "start with it from day one"
   advice; 1536 is text-embedding-3-small's dimension, comfortably under the
   2,000-dim HNSW limit.
2. **Write path**: the embedding pipeline populates both columns when the
   active model's registry entry declares `dimensions == 1536` (dev/gemma
   rows keep the legacy column only).
3. **Partial HNSW index** so dev-model rows never bloat it:

   ```sql
   CREATE INDEX CONCURRENTLY ix_se_halfvec_hnsw
   ON speech_embeddings USING hnsw (embedding_halfvec halfvec_cosine_ops)
   WHERE model_name = 'openai/text-embedding-3-small';
   ```

   Defaults (`m=16, ef_construction=64`) are fine at this scale; build after
   the bulk re-embed, not before it.
4. **Read path**: the search CTEs use `embedding_halfvec <=> :q` when the
   active model is the production one (registry-driven), else the legacy
   column. Verify with `EXPLAIN` that the partial index is chosen and re-run
   the eval harness — expect identical or near-identical metrics.
5. **Later cleanup** (separate migration, after the keep-then-drop window):
   drop the legacy dimensionless column once nothing reads it.

---

## 3. Rollout sequence

Each step is independently verifiable; nothing is irreversible except spending
cents on the re-embed.

1. **Provision Neon** (free tier). Create the project, get direct + pooled
   connection strings. Run
   `python -c "from senedd_data.provisioning import Provisioner; Provisioner('<url>').create_schema()"`
   — same path CI's migration job exercises. Create `mcp_reader` (§1d.1).
2. **Indexing migration** (§2) — land it before bulk data so the backfill
   writes both columns.
3. **Validate the embedding recipe** — experiment runs per
   `services/embeddings/experiments/README.md` against a Neon branch (or local copy) of a
   backfilled corpus; promote the winner into the registry.
4. **Backfill + bulk re-embed** — `scripts/backfill.py` for the historic
   range, then the embed sweep with `EMBEDDING_PROVIDER=openai`. Build the
   HNSW index. Run `python -m senedd_data.fidelity`. Re-record
   `tests/eval/BASELINE.md` via `eval.yml` and commit.
5. **Arm the sync** — set repo secrets (`DATABASE_URL` = ingest role, pooled;
   `OPENAI_API_KEY`) and repo variables (`EMBEDDING_PROVIDER=openai`,
   `EMBEDDING_MODEL=text-embedding-3-small`, `SYNC_ENABLED=true`). Watch the
   first scheduled run; `eval.yml` after it as a spot check.
6. **Deploy the MCP** — Render web service off this repo,
   `uv run python -m senedd_mcp --transport http`, env:
   `DATABASE_URL` (= `mcp_reader`, pooled), `OPENAI_API_KEY`,
   `EMBEDDING_PROVIDER/MODEL`. Platform rate limiting on. Smoke-test from
   Claude (custom connector → `https://<service>.onrender.com/mcp`) with the
   MCP_TESTING.md checklist.
7. **Decide the cold-start posture** — if interactive latency through the MCP
   feels bad (Neon resume ≈ 0.5 s on the odd first query), lengthen the
   suspend timeout or move to Launch and disable suspension.

## 4. Costs at a glance

| Item | Now (staging) | Full corpus, public MCP |
|---|---|---|
| Neon | $0 (free tier) | $19/mo (Launch) when >0.5 GB or always-on wanted |
| Render MCP service | $0 (hobby, sleeps) | $7/mo (starter, no sleep) |
| OpenAI embeddings | ~$0.05 (current corpus) | ~$0.60 one-off backfill + pennies/mo |
| GitHub Actions | $0 (public repo) | $0 |
| **Total** | **≈ $0** | **≈ $26/mo + cents** |

## 5. Operational notes

- `EMBED_CACHE_ENABLED=false` in the sync workflow (already set): no backfill
  re-runs in prod, so the cache is write-only bloat there. Experiments and
  local dev keep it on.
- Backups: Neon free tier has point-in-time restore (short window); Launch
  extends it. The database is also fully reconstructible from source
  (`scripts/backfill.py` + re-embed for ~$1), which is the real disaster
  story.
- The `sync.yml` fidelity step and `eval.yml` are the ongoing QA loop:
  fidelity guards transcript quality, eval guards retrieval quality after any
  promoted change.
- Rotate the two DB roles' credentials independently; neither the MCP host
  nor Actions ever holds the other's.

## Sources

- Neon vs Supabase pricing/features: [designrevision.com](https://designrevision.com/blog/supabase-vs-neon), [bytebase.com](https://www.bytebase.com/blog/neon-vs-supabase/), [vela.simplyblock.io](https://vela.simplyblock.io/neon-vs-supabase/)
- Neon scale-to-zero & cold-start latency: [neon.com/docs/connect/connection-latency](https://neon.com/docs/connect/connection-latency), [neon.com/docs/guides/benchmarking-latency](https://neon.com/docs/guides/benchmarking-latency)
- Neon pgvector: [neon.com/docs/extensions/pgvector](https://neon.com/docs/extensions/pgvector)
- pgvector HNSW / halfvec practice: [pgvector README](https://github.com/pgvector/pgvector/blob/master/README.md), [dbi-services pgvector index guide (2026)](https://www.dbi-services.com/blog/pgvector-a-guide-for-dba-part-2-indexes-update-march-2026/), [Supabase HNSW docs](https://supabase.com/docs/guides/ai/vector-indexes/hnsw-indexes)
- MCP hosting on PaaS: [render.com — building and hosting MCP servers](https://render.com/articles/building-and-hosting-mcp-servers-a-complete-guide), [railway.com/deploy/fastmcp](https://railway.com/deploy/fastmcp), [PaaS comparison](https://techsy.io/en/blog/railway-vs-render-vs-fly-io)
- Remote MCP auth expectations: [Claude help — custom connectors via remote MCP](https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers), [MCP connector docs](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)
