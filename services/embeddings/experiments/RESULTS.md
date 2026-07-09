# Embedding experiment leaderboard

Auto-generated from `runs.jsonl` by `services/embeddings/senedd_embeddings/experiments/results.py` — do not
edit by hand. Latest record per run id, ranked by MRR. Runs flagged
`partial` embedded only part of the corpus: their quality numbers are not
comparable (fewer distractors) and they rank for bookkeeping only.

| rank | run | model | strategy | words/overlap | MRR | hit@3 | recall@5 | p95 query (s) | chunks | embed (s) | ~MB | recorded |
|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | baseline-gemma-418c19a8 | ollama/embeddinggemma:300m | sentence-window | 1200/50 | 0.958 | 1.00 | 0.93 | 0.295 | 0 | 0.06 | 11.3 | 2026-07-08 |
| 2 | gemma-whole-speech-6d530136 | ollama/embeddinggemma:300m | whole-item | 1200/0 | 0.958 | 1.00 | 0.93 | 0.2388 | 0 | 0.05 | 11.3 | 2026-07-08 |
| 3 | gemma-small-chunks-e4d876b5 | ollama/embeddinggemma:300m | sentence-window | 200/40 | 0.896 | 0.92 | 0.93 | 0.2528 | 6457 | 6.71 | 18.92 | 2026-07-08 |
| 4 | openai-small-1279d63c | openai/text-embedding-3-small | sentence-window | 4000/50 | 0.819 | 0.83 | 0.93 | 0.2672 | 0 | 0.05 | 22.46 | 2026-07-08 |
