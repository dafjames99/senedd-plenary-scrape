# Retrieval eval — recorded baseline

Reference numbers for the semantic-search stack, so later changes (the Phase 1
service-layer refactor, query-strategy tuning, noise removal, etc.) can be shown
to be non-regressive. Re-run and compare:

```bash
uv run python -m tests.eval.runner --k 20
```

## Baseline — 2026-07-09 (production: openai/text-embedding-3-small)

- **Stack:** `openai/text-embedding-3-small`, local Postgres `senedd_db`
  (40 meetings, 4,749 speeches, 2026-03-02 .. 2026-06-09). Chosen over gemma
  and text-embedding-3-large via `services/embeddings/experiments/` (see
  `RESULTS.md`); recipe = sentence-window, no query/doc prefix, registry
  default chunk cap (4000 words — never actually triggered, longest speech in
  the corpus is 1,930 words), speaker prefix, overlap 50 / min-words 20.
- **Entry point:** `tests.eval.runner` → `senedd_search.service.semantic_search`.
- **Cases:** 12 (see `cases.yaml`), retrieval depth 20.

| Metric | Value |
|---|---|
| MRR | 0.819 |
| hit-rate@1 | 0.75 |
| hit-rate@3 | 0.83 |
| hit-rate@5 | 1.00 |
| recall@1 | 0.61 |
| recall@3 | 0.75 |
| recall@5 | 0.93 |

No case was a complete miss. This MRR is genuinely below the gemma baseline
(0.958, see below) — confirmed not a labeling artifact by per-case forensic
comparison (same three under-labeled cases already fixed; two residual gaps
are real: `child-poverty-strategy` ranks two other-session speeches above the
same-session answer, `building-safety-bill-principles` is a harder
same-speaker/same-Bill disambiguation). Chosen anyway: short chunks (avg
speech 143 words) mean a bigger model buys little, cost/latency favour
OpenAI, and the residual gap looks fixable at the MCP/context layer rather
than the embedding layer — see `PLAN.md` Phase 5 / `PRODUCTION.md` §1b.
This corresponds to `PRODUCTION.md` rollout step 3 ("validate the embedding
recipe"); step 4 (historic backfill + bulk re-embed + HNSW index on Neon)
remains pending.

> **Prior baseline (gemma) — 2026-07-08, relabeled:** `ollama/embeddinggemma:300m`,
> same corpus/cases. MRR 0.958, hit-rate@1 0.92, hit-rate@3/5 1.00,
> recall@1/3/5 0.68/0.88/0.93. Still the local-dev fallback; both models'
> vectors coexist in `speech_embeddings` keyed by `model_name`. This in turn
> moved from the original 2026-06-17 recording (MRR 0.903) after an audit of
> `cases.yaml` found three cases under-labeled: `gp-surgery-closures`,
> `child-poverty-strategy`, and `parent-advocacy-child-protection` each had
> genuinely-relevant same-exchange speeches missing from `relevant_speech_ids`
> (found while investigating the gemma/openai gap — the labeling gap was
> inflating the apparent gap between models, not just gemma's score).
>
> If this baseline drops again, that means retrieval quality regressed; if
> `cases.yaml` changes again, re-run and re-record instead of comparing
> against this baseline. When Phase 1 re-points retrieval at `src/search/`,
> MRR/hit-rate should be identical (same model, same SQL semantics) for a
> fixed `cases.yaml` — a drop means the refactor changed retrieval behaviour
> and needs investigation.
