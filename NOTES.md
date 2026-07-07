# Working notes — monorepo migration + frontend (2026-07)

Running log of assumptions and decisions made autonomously during the
monorepo migration and frontend build, per the session brief.

## Environment constraints hit in this session

- **Senedd hosts are egress-blocked from this dev container**
  (`record.senedd.wales`, `senedd.tv`, `senedd.wales` — proxy CONNECT 403).
  Live acquisition/backfill could not run here. The frontend is demoed
  against a clearly-labelled synthetic fixture meeting
  (`scripts/seed_fixture.py`); on a normal network, `scripts/backfill.py`
  populates real meetings and the frontend works unchanged.
- **GitHub release downloads are blocked** → no CPython 3.14 standalone
  build available; system Python is 3.13. The full suite passes on 3.13, so
  `requires-python` was lowered to `>=3.13` (CI still resolves newer). No
  3.14-only feature was in use.
- Local Postgres 16 + pgvector 0.6.0 was provisioned in-container
  (`senedd_dev` DB) and the moved Alembic tree verified with a full
  downgrade-to-base/upgrade-to-head round-trip.

## Decisions (autonomous, reversible)

- `psycopg2` (source build) kept rather than switching to `psycopg2-binary`
  — matches the previous lockfile behaviour; container just needed
  `libpq-dev`.
- The deprecated `SeneddPipeline` facade moved with `senedd-data` untouched.
- `senedd_data/__init__.py` keeps the old `src/__init__.py` lazy surface but
  drops the `EmbeddingPipeline` re-export (nothing used it; it crossed
  package boundaries).
- Model metadata registry moved to `senedd_data.model_registry` (see
  ARCHITECTURE.md) with a re-export shim at `senedd_embeddings.config` so
  call sites keep their natural name.
- Historical docs (PLAN.md, PRODUCTION.md) had their *command/path*
  references updated to the new layout; their narrative was left intact.
- pnpm pinned via `packageManager` to the container's pnpm (10.12.1).

## Phase 3 build notes (frontend)

- **Verified end-to-end in this sandbox** against the fixture meeting: meeting
  search API, transcript API (startPos + clip base URL correct), mock ask flow
  (markers ↔ citation cards 1:1), click-speech → iframe jumps to `startPos`,
  citation click → jump, and the virtual-clock follow advanced the highlight
  across speech boundaries. Headless-Chromium screenshots in the session log.
- **`/api/ask` live mode** uses a manual Anthropic tool-use loop (not the SDK
  tool runner) because we harvest `[speech:ID]` citations and need the tool
  results + final text separately. Model `claude-opus-4-8` by default
  (`ANTHROPIC_MODEL` to override; PLAN.md's Haiku-class guardrail applies to
  the future public demo, not this authenticated dev surface).
- **Mock mode is a first-class path**: without `ANTHROPIC_API_KEY` +
  `SENEDD_MCP_URL` the route answers from keyword retrieval over real DB rows
  through the same block grammar, clearly labelled — so the citation UI is
  developable/demoable without keys (as in this sandbox).
- The app builds with the DB unreachable (all data pages are
  `force-dynamic`) — confirmed here, so Vercel builds need no DB access.
- **Vercel deployment**: set the project Root Directory to `apps/web` (Vercel
  auto-detects the pnpm workspace), env vars `DATABASE_URL` (Neon pooled
  string) and optionally `ANTHROPIC_API_KEY`/`SENEDD_MCP_URL`/`ANTHROPIC_MODEL`
  + `NEXT_PUBLIC_VIDEO_MODE`. No vercel.json needed.
- senedd.tv could not be reached from this sandbox, so iframe embeddability
  remains unverified (PRD §2 risk) — the pane shows the proxy's block page
  here; `NEXT_PUBLIC_VIDEO_MODE=link` is the one-variable fallback.
