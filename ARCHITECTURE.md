# Architecture

How the monorepo is laid out, why the tooling is what it is, and how the
pieces talk to each other. The migration rationale/history is in
MONOREPO_PLAN.md; production/deployment posture is in PRODUCTION.md.

## Layout

```
├── apps/
│   └── web/                      # Next.js frontend (@senedd/web) — see apps/web/PRD.md
├── services/
│   ├── data/                     # senedd-data — database/ETL service
│   │   ├── senedd_data/          #   schema, session, settings, model registry,
│   │   │                         #   parser, fetcher, acquisition, transformation,
│   │   │                         #   fidelity QA, SQL procedures
│   │   ├── alembic/ alembic.ini  #   all DDL, versioned migrations
│   ├── embeddings/               # senedd-embeddings — embedding service
│   │   ├── senedd_embeddings/    #   providers, chunker, cache, pipeline, sweep,
│   │   │   └── experiments/      #   experiment harness (code)
│   │   └── experiments/          #   experiment configs + runs.jsonl + RESULTS.md
│   └── mcp/                      # senedd-mcp — retrieval + MCP surface
│       ├── senedd_search/        #   semantic search + structured lookups
│       └── senedd_mcp/           #   FastMCP tools/resources/prompts
├── scripts/                      # cross-service CLIs (backfill, query_speeches, …)
├── analysis/                     # read-only analysis charts (dev-only deps)
├── tests/                        # cross-service suite + eval harness (tests/eval)
├── main.py                       # local-dev orchestrator (acquire → transform → embed)
├── pyproject.toml uv.lock        # uv workspace root — single lock + venv
├── package.json pnpm-workspace.yaml
└── Makefile                      # thin task runner (one canonical command per task)
```

## Tooling: uv workspaces + pnpm workspaces, no Turborepo/Nx

- **Python — uv workspaces.** Three packages (`senedd-data`,
  `senedd-embeddings`, `senedd-mcp`) each own their dependency set, so a
  deployed MCP service doesn't carry `pandas`/`matplotlib`, while one shared
  lockfile and venv keep the local workflow a single `uv sync`. The root
  project depends on all members; `main.py`, `scripts/` and `tests/` run
  against the root venv and can compose everything — which is the reason this
  is a monorepo and not a polyrepo.
- **JS — pnpm workspaces only.** There is exactly one JS package
  (`apps/web`). Turborepo/Nx pay off with many packages and cross-package
  task graphs; here they would be scaffolding beyond the MVP (repo
  convention: minimal abstraction). Adding Turborepo later is additive, not a
  restructure.
- **Cross-language tasks** live in a thin `Makefile` (`make dev` runs the MCP
  HTTP server and the web dev server together), not a build system.

## Package dependency graph (acyclic)

```
senedd-data  ←  senedd-embeddings  ←  senedd-mcp
     ↑                                    ↑
     └──────────── senedd-mcp ────────────┘
apps/web  → Postgres directly (read-only queries)
          → senedd-mcp over streamable HTTP (LLM/agent queries)
```

- `senedd-data` is the foundation: SQLAlchemy schema, session factory,
  settings, and the model-metadata registry (`senedd_data.model_registry` —
  it lives here, not in embeddings, so settings validation doesn't create a
  package cycle; `senedd_embeddings.config` re-exports it).
- `senedd-embeddings` reads/writes vectors and owns the experiment harness.
- `senedd-mcp` owns retrieval (`senedd_search`) and the MCP surface
  (`senedd_mcp`); search needs embeddings to embed queries symmetrically.
- `apps/web` holds its own thin SQL layer (read-only) rather than importing
  Python — the DB is the contract. Swapping local Postgres → Neon is a
  `DATABASE_URL` change on both sides. Rationale in `apps/web/PRD.md`.

## Entry points

| Task | Command |
|---|---|
| Incremental sync (all stages) | `uv run python main.py` |
| Raw acquisition only | `uv run python -m senedd_data.acquisition` |
| Derived rebuild only | `uv run python -m senedd_data.transformation` |
| Embedding sweep | `uv run python -m senedd_embeddings.embed --loop` |
| Fidelity QA | `uv run python -m senedd_data.fidelity` |
| MCP server (stdio / HTTP) | `uv run python -m senedd_mcp [--transport streamable-http]` |
| Semantic search CLI | `uv run python scripts/query_speeches.py "query"` |
| Experiments | `uv run python -m senedd_embeddings.experiments.runner --config services/embeddings/experiments/configs/….yaml` |
| Migrations (CLI) | `uv run alembic -c services/data/alembic.ini …` |
| Tests / eval | `uv run pytest tests/ -q` / `uv run python -m tests.eval.runner` |
| Web dev server | `pnpm --filter @senedd/web dev` (or `make web`) |
| Tool development combo | `make dev` (MCP HTTP + web) |

## Conventions that constrain changes

- **DDL belongs to Alembic** (`services/data/alembic`); pipelines only do DML.
- **Raw vs derived seam**: acquisition writes source-of-truth tables only;
  transformation rebuilds derived tables per meeting, atomically, and is
  always safe to re-run (cascade FKs purge stale derived rows).
- **Doc/query prefix symmetry**: anything that embeds a query must apply the
  active model's `query_prefix` from the registry — the search service is the
  single home for this; don't re-implement retrieval elsewhere.
- **Tool results carry citations** (speech_id, speaker, date, SeneddTV URL);
  excerpts by default, full text on demand.
- Tests are fully mocked (no DB/network/GPU) and span all services from the
  repo root; the eval harness (`tests/eval`) is the quality gate for any
  retrieval-affecting change.
