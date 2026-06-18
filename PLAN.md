# Senedd MCP — Plan of Action

Roadmap for turning the existing scrape/embed pipeline into a hosted MCP server over
Senedd Plenary data (semantic + structured retrieval), and extending the data model to
cover **Votes** and **QNR** (written questions/answers not reached).

Captured from the design discussion on 2026-06-17.

---

## North-star architecture

Four concerns, kept deliberately separate (today they're tangled in `src/db/pipeline.py`):

1. **Schema structure (DDL)** → owned by **Alembic** (versioned, data-preserving migrations).
2. **Data lifecycle (DML)** → owned by the pipeline (ingest, clean, classify, reconstruct,
   embed, reprocess, the `purge_*` SQL procedures). Operates *within* a fixed schema.
3. **Retrieval service** → a shared `src/search/` layer used by both the CLI and the MCP.
4. **MCP surface** → thin, typed tools/prompts/resources over the service layer.

Two principles that shape every decision below:

- **The MCP client is the orchestrator.** We don't build a RAG loop; we give the model
  clean filtered-search primitives + a strategy prompt and let it multi-query/re-search.
- **Retrieval sets the ceiling.** Invest in primitives + measurement first; model
  capability only governs how reliably the model reaches that ceiling.

Sequencing rule: **measurement + migrations before schema changes; service layer before
MCP; speech-only MVP before Votes/QNR.** Don't reorder.

---

## Phase 0 — Foundations (no behaviour change)

You can't tell if anything improves without measurement, and you can't safely change schema
without migrations. Both come first.

### 0A. Retrieval eval harness  *(DONE — `tests/eval/`, baseline in BASELINE.md)*
- [x] Create `tests/eval/` with a labelled set: `(query, expected_speech_ids[])` pairs,
      hand-built from the existing ~1–2 months of gemma-embedded data.
- [x] Implement metrics: hit-rate@k, recall@k, MRR.
- [x] Runner: pytest (metrics, offline) + a CLI scoreboard (`runner.py`, live).
- [x] Record the **current baseline**: MRR 0.903, hit@1 0.83, @3 1.00 (`BASELINE.md`).
- [x] Runs against the **local gemma DB** — no LLM calls, no API spend.

### 0B. Alembic adoption + pipeline decoupling  *(DONE)*
- [x] `uv add alembic`; `alembic init`.
- [x] Configure `env.py`: `Base.metadata`, URL from settings (`-x db_url=` override),
      `compare_type=True`; logging guard so programmatic runs don't clobber the app logger.
- [x] `CREATE EXTENSION vector` lives in the baseline migration's `upgrade()`.
- [x] Baseline migration captures the current schema; existing populated DB `stamp`ed at it.
      (Verified the migration builds 12 tables + extension and round-trips on a throwaway DB.)
- [x] Decouple `pipeline.py`:
  - [x] `create_schema()` → `run_migrations()` (`alembic upgrade head`) + `_load_procedures()`;
        no more `create_all()`.
  - [x] `--force` / `run_full_pipeline()` re-scoped to **rebuild data** (`CALL purge_all_tables()`),
        schema preserved.
  - [x] `purge_*` procedures and all transformation phases unchanged.
- [x] Per-change workflow documented in `alembic/README`.
- [x] `before_create` extension listener left as a harmless safety net for test `create_all`.

---

## Phase 1 — Retrieval service layer  *(DONE — unblocks MCP)*

- [x] Extract `semantic_search` + `SearchResult` → `src/search/service.py`.
- [x] Push filters into the CTE `WHERE` (pre-filter before ranking): `speaker`,
      `date_from`, `date_to`, `agenda_item`, and a commented `source_type` hook for Phase 3.
- [x] Typed dataclasses with **citation metadata**: `speech_id`, `meeting_date`,
      `speaker_name`, `agenda_item_id`, `agenda_item_english`, SeneddTV URL.
      *(Official record URL deferred — no reliable column; needs a URL scheme, not fabricated.)*
- [x] Structured lookups in `src/search/lookups.py` (the MCP will wrap these):
  - [x] `get_speech(speech_id)` — full text + context.
  - [x] `filter_speeches(...)` — non-semantic structured listing (chronological).
  - [x] `find_member(name)` / `get_member(member_id)` — resolution + role history.
  - [x] `list_meetings(...)` / `get_meeting(meeting_id)` (with agenda items).
  - [x] `get_agenda_thread(speech_id | meeting_id+agenda_item_id)` — ordered conversation.
- [x] `scripts/query_speeches.py` reduced to a thin CLI over the service.
- [x] Eval harness re-run: **zero regression, MRR 0.903** unchanged.
- Note: fixed an `agenda_item_id`-collision bug — the id repeats across same-date
  meetings, so the agenda-title lookup is now scoped by `meeting_id` too.

---

## Phase 2 — MCP MVP (speeches only)  *(DONE — `src/mcp_server/`)*

- [x] Invoked the `mcp-builder` skill.
- [x] `src/mcp_server/` with FastMCP (`senedd_mcp`); stdio + streamable-HTTP via `--transport`.
- [x] 8 tools wrapping Phase 1: `senedd_search_speeches`, `senedd_get_speech`,
      `senedd_get_agenda_thread`, `senedd_filter_speeches`, `senedd_find_member`,
      `senedd_get_member`, `senedd_list_meetings`, `senedd_get_meeting`. All read-only.
- [x] Tool descriptions state: names/dates/agenda go in **filter params**, `query` is topic-only.
- [x] Result sizing: search/threads carry **excerpts + ids**; full text only via `senedd_get_speech`.
- [x] Resources: `senedd://data-dictionary`, `senedd://corpus-stats` (date range + active model),
      `senedd://members`.
- [x] Prompts: `senedd_search_strategy`, `senedd_position_over_time`.
- [x] Verified end-to-end via FastMCP dispatch (tools/resources/prompts, error + validation paths);
      run/registration docs in `src/mcp_server/README.md`.
- [ ] **Read-only Postgres role** — deferred to deployment (Phase 5); local dev uses the dev role.
- [ ] Manual Loop-B smoke test in a real client (Claude Desktop) — for you to run.
- Follow-up: centralise DB connection pooling before the HTTP transport serves multiple clients.

---

## Phase 3 — New data via additive migrations  *(DONE — `phase-3-votes-qnr`)*

> Embedding the new sources + the cross-source search were **re-scoped to Phase 4**:
> embedding written/votes has no user value until the search surfaces them, and the
> search source-filter already lived in Phase 4, so the two ship together there.

### 3A. Polymorphic embeddings  *(DONE)*
- [x] Migration: add `source_type` + `source_id` to `speech_embeddings`; backfill
      `source_type='speech'`, `source_id=speech_id`. (4772 gemma rows backfilled; legacy
      `speech_id` + cascade FK kept this release as a keep-then-drop rollback safety net.)
- [x] Make the embedding pipeline + search **source-aware** (write/search key on
      `(source_type, source_id)`; eval MRR 0.903 unchanged).
      *(Actually embedding/spanning the other sources → Phase 4.)*
- [ ] Decide indexing: a **fixed-dim, indexed** column for the prod model vs. the current
      dimensionless `Vector` column (which can't be ANN-indexed). See Phase 5.

### 3B. Votes  *(DONE)*
- [x] Schema: `votes` (motion-level, natural key = `Contribution_ID`, FK → `raw_contributions`,
      tallies, result EN/CY, vote_name EN/CY, agenda_item) and `vote_records`
      (per-member: `vote_id` FK, `member_id` FK, result **For/Against/Abstain/DidNotVote** —
      the source has a 4th value the original spec missed).
- [x] `parse_votes_xml` in the parser (dedup by `Contribution_ID`; `Vote_Name` is junk, use EN).
- [x] Fetcher: handle the `"Votes"` transcript type (filename-suffix bug fixed).
- [x] Ingestion (`ingest_votes`): member upsert; defers votes whose motion contribution
      isn't ingested yet (retried idempotently) rather than failing the FK.
- → Embed `vote_name_english` for semantic vote discovery: **moved to Phase 4.**

### 3C. QNR  *(DONE)*
- [x] Schema: `written_contributions` — **no `Contribution_ID` in the feed**, so synthetic
      `(meeting_id, order_index)` key; `qa_role` question|answer; deterministic positional
      `pair_id`; `speaker_id` nullable (**answers attributed by job title, no `Member_Id`**).
- [x] `parse_qnr_xml` + clean step handling the **double-escaped HTML** (`&amp;lt;p&amp;gt;`).
- [x] Fetcher: handle the `"QNR"` transcript type.
- → Embed via polymorphic embeddings (`source_type='written'`): **moved to Phase 4.**

### 3D. Late-publication sync  *(DONE)*
- [x] `artifact_watch` table: a transcript ingest opens pending votes/qnr watches with a
      deadline = `meeting_date + ARTIFACT_WATCH_DAYS` (default 14). Each incremental run sweeps
      pending watches, attaching any now-available artifact idempotently and **expiring stale
      ones silently** — availability is *structural* (most plenary days never get Votes/QNR),
      so a forward-only cursor would block forever. Sweep uses the portal's default listing
      (date-param URLs return a linkless page). Verified end-to-end against the live portal.

---

## Phase 4 — Vote/QNR embedding + tools + query-strategy hardening

- [ ] **Embedding (moved from Phase 3):** generalise the embedding pipeline to embed
      `written_contributions` (`source_type='written'`) and `vote_name_english`
      (`source_type='vote'`); make `semantic_search` span those sources (resolving each
      source's citation metadata) behind a `source` filter (spoken | written | vote).
      Re-run the eval harness afterwards to confirm no speech regression.
- [ ] **Purge-procedure cascade mitigation:** the generic `source_id` carries no FK, so add
      explicit non-speech embedding cleanup on reprocess to `purge_*` (needed once non-speech
      vectors exist).
- [ ] Tools: `get_vote`, `find_votes`, `get_member_voting_record`, `get_votes_for_speech`
      (rhetoric↔vote bridge via `contribution_id`); `get_written_answers`.
- [ ] Prompts: `stance-vs-vote`, `issue-briefing`, `compare-speakers`.
- [ ] Iterate the search strategy against the eval harness.
- [ ] **Only if eval shows recall gaps:** add server-side multi-query fusion (RRF) or HyDE
      (note: a hypothetical *document* should use doc-side framing, not the `query_prefix`).

---

## Phase 5 — Production + demo

- [ ] Neon (cloud Postgres). Set `EMBEDDING_MODEL=openai/text-embedding-3-small`.
- [ ] One-time **bulk re-embed** against OpenAI into a **fixed-dim column + HNSW index**.
      (Local dev stays on gemma — coexist by `model_name`; pure config switch.)
- [ ] Deploy the MCP server in **HTTP mode**; secrets via env (`DATABASE_URL`, `OPENAI_API_KEY`).
- [ ] Demo app (you host the model + agent loop, since the visitor has no MCP client):
  - [ ] Web UI + backend tool-use loop against a **tool-competent mid-tier model** (Haiku-class)
        — cheapest models hallucinate citations, which is reputationally bad for a parliamentary tool.
  - [ ] **Show-your-sources** panel (excerpts + SeneddTV/record links): trust feature *and* the
        best showcase of the semantic search.
  - [ ] Guardrails: per-IP/session rate limit, max tool-calls + result-size caps per query,
        daily global spend ceiling (kill switch), cached query embeddings.
  - [ ] CTA copy: *"demo limited to N queries — add the MCP to your own provider for full value."*
  - [ ] **Pick the demo model via Loop-B eval** before exposing it publicly.

---

## Adjacent tasks / side concerns

- **`pipeline.py` cleanup** (alongside 0B): the DDL/DML decoupling is the substantive change;
  the verbose "manifest/compilation" logging and naming can be tidied opportunistically (low priority).
- **Party-data gap:** `Member` has no party affiliation (XML only gives sortcode + biog URL).
  Blocks all party-level tools — needs member-page scraping or an external mapping. Scope as
  its own task before Phase 4 party features.
- **Search-quality noise** (`todo.md`): drop sub-threshold parliamentary boilerplate
  ("Thank you", "Motion moved") from embedding — directly improves eval scores.
- **Tool-result sizing discipline** — excerpts + ids by default; full text on demand (cost + context).
- **Vector indexing** — the dimensionless `embedding_vector` column can't be ANN-indexed; fix when
  the prod model is committed (Phase 5).
- **Secrets** — `OPENAI_API_KEY` in gitignored `.env` locally; platform secret store for deploy.
- **README** is stale (`todo.md`) — refresh once the MCP MVP lands.

---

## Dependency order (don't reorder)

```
0A eval ─┐
0B alembic ─┼─→ 1 service ─→ 2 MCP MVP ─→ 3 migrations (3A→3B/3C→3D) ─→ 4 tools+strategy ─→ 5 prod+demo
         │                         ▲
         └─ measurement feeds ─────┴─ every quality decision from here on
```

The only quasi-irreversible cost is the Phase 5 production re-embed — and that's cents.
