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

## Phase 1 — Retrieval service layer  *(unblocks MCP; no schema dependency)*

- [ ] Extract the core of `scripts/query_speeches.py::semantic_search` → `src/search/service.py`.
- [ ] Push filters into the CTE `WHERE` (pre-filter before ranking, not Python post-filter):
      `speaker`, `date_from`, `date_to`, `agenda_item`, and a `source_type`-ready hook.
- [ ] Return typed dataclasses carrying full **citation metadata**: `speech_id`, `meeting_date`,
      `speaker_name`, `agenda_item_id`, SeneddTV URL, official record URL.
- [ ] Add service functions that the MCP will wrap:
  - [ ] `get_speech(speech_id)` — full text.
  - [ ] `filter_speeches(...)` — non-semantic structured listing (chronological use cases).
  - [ ] `find_member(name)` / `get_member(member_id)` — entity resolution + role history.
  - [ ] `list_meetings(...)` / `get_meeting(meeting_id)`.
  - [ ] `get_agenda_thread(speech_id | meeting_id, agenda_item_id)` — ordered Q→answer→follow-up
        reconstruction (solves "answered the question without using the keyword").
- [ ] Reduce `scripts/query_speeches.py` to a thin CLI over the service.
- [ ] Re-run the eval harness — confirm zero regression vs the 0A baseline.

---

## Phase 2 — MCP MVP (speeches only)  *(shippable; delivers the headline use case)*

- [ ] **Invoke the `mcp-builder` skill** before writing server code.
- [ ] `src/mcp_server/` with FastMCP; expose **stdio + streamable-HTTP** from one codebase.
- [ ] Tools (thin wrappers over Phase 1): `search_speeches`, `get_speech`, `get_agenda_thread`,
      `filter_speeches`, `find_member`, `get_member`, `list_meetings`, `get_meeting`.
- [ ] **Tool descriptions** explicitly state: names/dates/agenda go in **filter params**, the
      `query` string is the topical residue only (don't embed metadata).
- [ ] Result sizing: return **excerpts + ids** by default; full text only via `get_speech`
      (controls context size, cost, latency).
- [ ] Resources: data dictionary / schema description, member roster, available date range,
      active embedding model.
- [ ] Prompts: `search-strategy` (extract filters → 2–4 focused searches → widen if weak →
      synthesise **with citations**, claim nothing un-retrieved) and `position-over-time`.
- [ ] Connect with a **read-only Postgres role**.
- [ ] Wire into Claude Desktop config; manual Loop-B smoke testing.

---

## Phase 3 — New data via additive migrations  *(Alembic makes these cheap)*

### 3A. Polymorphic embeddings
- [ ] Migration: add `source_type` + `source_id` to `speech_embeddings`; backfill
      `source_type='speech'`, `source_id=speech_id`.
- [ ] Make the embedding pipeline + search source-aware so one query spans speeches + written QNR.
- [ ] Decide indexing: a **fixed-dim, indexed** column for the prod model vs. the current
      dimensionless `Vector` column (which can't be ANN-indexed). See Phase 5.

### 3B. Votes  *(pure data; high analytical value; easiest)*
- [ ] Schema: `votes` (motion-level, natural key = `Contribution_ID`, FK → `raw_contributions`,
      tallies, result EN/CY, vote_name EN/CY, agenda_item) and `vote_records`
      (per-member: `vote_id` FK, `member_id` FK, result For/Against/Abstain).
- [ ] `parse_votes_xml` in the parser.
- [ ] Fetcher: handle the `"Votes"` transcript type (Literal already anticipates it).
- [ ] Ingestion phase + routing by artifact type.
- [ ] Embed `vote_name_english` for semantic vote discovery.

### 3C. QNR  *(written Q&A)*
- [ ] Schema: `written_contributions` (`qa_role` = question|answer, `pair_id` linking Q↔A,
      `meeting_id`, `agenda_item_id`, `speaker_id` nullable — **answers are attributed by job
      title with no `Member_Id`**).
- [ ] `parse_qnr_xml` + clean step that handles the **double-escaped HTML** (`&amp;lt;p&amp;gt;`).
- [ ] Fetcher: handle the `"QNR"` transcript type.
- [ ] Embed via polymorphic embeddings (`source_type='written'`).

### 3D. Late-publication sync  *(cross-cutting — without this, Votes/QNR never attach)*
- [ ] `SyncCheckpoint` is a single global date and won't revisit already-ingested meetings.
      Add **per-artifact tracking** *or* a **trailing re-scan window** (~last 30 days) for
      `Votes`/`QNR` so they get attached after the transcript was processed.

---

## Phase 4 — Vote/QNR tools + query-strategy hardening

- [ ] Tools: `get_vote`, `find_votes`, `get_member_voting_record`, `get_votes_for_speech`
      (rhetoric↔vote bridge via `contribution_id`); `get_written_answers`; `search_speeches`
      gains a `source` filter (spoken | written).
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
