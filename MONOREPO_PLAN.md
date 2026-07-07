# Monorepo Migration Plan

Phase 0 deliverable: the proposed layout and the reasoning, written **before**
executing the migration. ARCHITECTURE.md (written after) records the final
state; this doc records the intent and the trade-offs.

## Goal

Restructure the repo into a monorepo housing four composite services —
database/ETL, embeddings, MCP server, and a new Next.js frontend — while
keeping the local dev workflow (DB + MCP running together for tool
development) intact and every existing test green.

## Tooling decision

**Python: uv workspaces. JS: pnpm workspaces. No Turborepo/Nx.**

- The repo is already a uv project; uv workspaces give per-service
  `pyproject.toml` (own dependency sets, so a deployed MCP image doesn't drag
  in `pandas`/`matplotlib`) with a **single shared lockfile and venv** — which
  is exactly the "easy to run DB + MCP together" property we're protecting.
- The JS side is **one** Next.js app. Turborepo/Nx earn their keep with many
  JS packages and cross-package task graphs; here they'd be framework
  scaffolding beyond what the MVP needs (repo convention: minimal
  abstraction). pnpm workspaces alone provide the `apps/*` structure; if more
  JS packages appear later, Turborepo can be added without restructuring.
- Cross-language orchestration stays a thin root `Makefile` + documented
  commands, not a build system.

## Proposed layout

```
├── apps/
│   └── web/                      # Next.js frontend (pnpm) — Phase 2/3
├── services/
│   ├── data/                     # senedd-data: schema, session, settings,
│   │   ├── senedd_data/          #   parser, fetcher, acquisition,
│   │   ├── alembic/  alembic.ini #   transformation, fidelity, procedures
│   ├── embeddings/               # senedd-embeddings: providers, chunker,
│   │   ├── senedd_embeddings/    #   cache, pipeline, embed entrypoint,
│   │   └── experiments/          #   experiments harness + configs/results
│   └── mcp/                      # senedd-mcp: retrieval service + MCP surface
│       ├── senedd_search/        #   (search stays with the MCP per PLAN.md:
│       └── senedd_mcp/           #   "service layer before MCP")
├── scripts/                      # cross-service CLIs (backfill, query, …) — unchanged location
├── analysis/                     # read-only analysis (root dev group) — unchanged
├── tests/                        # cross-service suite + eval harness — unchanged location
├── main.py                       # local-dev orchestrator — unchanged location
├── pyproject.toml                # uv workspace root (depends on all members)
├── package.json, pnpm-workspace.yaml
└── ARCHITECTURE.md, NOTES.md, PLAN.md, PRODUCTION.md, README.md
```

### Which directory becomes which package

| Today | Becomes | Import rename |
|---|---|---|
| `src/db/` | `services/data/senedd_data/` | `src.db.X` → `senedd_data.X` |
| `src/embeddings/` | `services/embeddings/senedd_embeddings/` | `src.embeddings.X` → `senedd_embeddings.X` |
| `src/experiments/` | `services/embeddings/senedd_embeddings/experiments/` | `src.experiments.X` → `senedd_embeddings.experiments.X` |
| `src/search/` | `services/mcp/senedd_search/` | `src.search.X` → `senedd_search.X` |
| `src/mcp_server/` | `services/mcp/senedd_mcp/` | `src.mcp_server.X` → `senedd_mcp.X` |
| `experiments/` (configs/results) | `services/embeddings/experiments/` | paths in tests/docs |
| `alembic/`, `alembic.ini` | `services/data/` | `alembic -c services/data/alembic.ini` |
| `src/__init__.py` surface | `senedd_data/__init__.py` | `from src import settings` → `from senedd_data import settings` |

Package dependency graph (strictly acyclic):
`senedd-data` ← `senedd-embeddings` ← `senedd-mcp` (mcp also ← data); web
talks to Postgres directly (decided in the Phase 2 doc, PRD_FRONTEND.md).

### What deliberately does NOT move

- **`tests/` stays at the root.** The suite spans all three services and the
  eval harness is referenced as `python -m tests.eval.runner` by `eval.yml`
  and imported by the experiments framework. Splitting it per-service buys
  nothing at this scale and breaks those seams.
- **`scripts/`, `analysis/`, `main.py` stay at the root.** They compose
  multiple services (that's their job); the root workspace project depends on
  all members, so they keep working unchanged apart from imports.

## Decisions & flagged ambiguities

1. **Layering fix required**: `src/db/settings.py` imports
   `src/embeddings/config.py` (`MODEL_METADATA_REGISTRY`) while embeddings
   imports db — a cycle at package level. The registry is pure metadata, so it
   moves into `senedd_data` (as `senedd_data.model_registry`);
   `senedd_embeddings.config` re-exports it so embedding-side call sites keep
   their natural import.
2. **Git history**: preserved via `git mv` in-place (single repo, no
   filter-repo needed — we're reorganising, not extracting). `git log
   --follow` tracks every file across the move.
3. **Python floor lowered to 3.13** (`requires-python >=3.13`, was `>=3.14`).
   The full suite passes on 3.13; the pin excluded environments (like this
   remote dev container, where GitHub release downloads are egress-blocked and
   3.14 is unavailable) for no feature we use. CI continues to resolve the
   newest interpreter.
4. **`src.experiments` → inside the embeddings package**, not a fourth
   service: it exists only to compare embedding recipes and shares the
   embed/eval code. It imports `tests.eval.loader`, which means the
   experiments runner must run from the repo root — true today, unchanged.
5. **The brief references CONVENTIONS.md and PRDs; neither exists** in the
   repo. Conventions were taken from `.claude/CLAUDE.md` + PLAN.md
   (domain-separated structure, human-editable config, minimal abstraction).
6. **The deprecated `SeneddPipeline` facade** (`src/db/pipeline.py`) moves
   with the data service and stays deprecated; nothing new depends on it.

## Execution order

1. `git mv` the trees into place; add per-service `pyproject.toml`s and the
   workspace root; mechanical import rename; fix path-relative code
   (provisioning, alembic env, experiment config paths, logging formatter's
   `src.` prefix match).
2. `uv lock` + `uv sync`; run the full suite — the gate is **127 passed**.
3. Smoke-test provisioning end-to-end against local Postgres
   (`Provisioner(...).create_schema()`) — validates the moved alembic tree.
4. Update `.github/workflows/*` (paths + module names, add a web job later),
   `Makefile`, `.claude/CLAUDE.md`, READMEs, MCP registration docs.
5. Commit. Then scaffold `apps/web` (Phase 2 doc first, then Phase 3 build).

## Risks

- Mocked-test patch strings (`patch("src.search.service...")`) rename with the
  same sed as real imports — verified by the suite itself.
- `.env` loading: settings resolve `.env` relative to CWD/package root; all
  entry points run from the repo root as before, and the settings module's
  explicit `ROOT_DIR` anchor is updated for its new depth.
- Anything importing `src.*` outside this repo (none known) breaks — the old
  names are gone, no shim is kept.
