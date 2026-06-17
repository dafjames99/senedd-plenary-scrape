# Retrieval eval — recorded baseline

Reference numbers for the semantic-search stack, so later changes (the Phase 1
service-layer refactor, query-strategy tuning, noise removal, etc.) can be shown
to be non-regressive. Re-run and compare:

```bash
uv run python -m tests.eval.runner --k 20
```

## Baseline — 2026-06-17

- **Stack:** `ollama/embeddinggemma:300m`, local Postgres `senedd_db`
  (40 meetings, 4,749 speeches, 2026-03-02 .. 2026-06-09).
- **Entry point:** `scripts.query_speeches.semantic_search` (pre-Phase-1).
- **Cases:** 12 (see `cases.yaml`), retrieval depth 20.

| Metric | Value |
|---|---|
| MRR | 0.903 |
| hit-rate@1 | 0.83 |
| hit-rate@3 | 1.00 |
| hit-rate@5 | 1.00 |
| recall@1 | 0.74 |
| recall@3 | 0.97 |
| recall@5 | 1.00 |

No case was a complete miss (every target appeared within depth 20), confirming
the seed set is well-formed. The two sub-rank-1 cases were
`parent-advocacy-child-protection` (rank 3) and `building-safety-bill-principles`
(rank 2) — both close-but-not-top, useful signal for later tuning.

> When Phase 1 re-points retrieval at `src/search/`, MRR/hit-rate should be
> identical (same model, same SQL semantics). A drop means the refactor changed
> retrieval behaviour and needs investigation.
