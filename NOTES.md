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
