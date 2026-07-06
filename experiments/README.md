# Embedding experiments

A repeatable procedure for answering one question: **which combination of
embedding model and chunking strategy retrieves Senedd speeches best, at what
cost?** Every run is validated against the same labelled case set the
production baseline uses, measured for speed and storage, and logged durably —
so decisions are made from a leaderboard, not from vibes.

## How it works

An experiment is a YAML config (`experiments/configs/*.yaml`) naming a model
and a chunking recipe. The runner:

1. **Embeds** every speech in the corpus under an isolated vector namespace —
   `model_name = "exp:<name>-<confighash8>"` in the existing
   `speech_embeddings` table. Production search filters on real registry model
   names, so experiment vectors are invisible to it; several experiments
   coexist; purging one is a single delete. The config hash covers every
   embedding-affecting field, so a renamed-but-identical recipe keeps its
   identity and a changed recipe can never silently overwrite an old run.
2. **Evaluates** the namespace against the labelled retrieval cases
   (`tests/eval/cases.yaml`) with the same best-chunk-per-speech ranking CTE
   production search uses, applying the experiment's own `query_prefix` so
   query and document sides stay symmetric. Metrics: MRR, hit-rate@k,
   recall@k — identical definitions to `tests/eval/BASELINE.md`, so numbers
   are directly comparable.
3. **Measures** performance alongside quality: embed wall time and
   chunks/second, cache hits vs. true provider calls, per-query latency
   (mean/p50/p95, embed and search split), vector count and approximate
   storage.
4. **Records** the run: one JSON object appended to `experiments/runs.jsonl`
   (append-only machine log, committed to git) and a regenerated
   `experiments/RESULTS.md` leaderboard (latest record per run id, ranked by
   MRR).

The content-addressed embedding cache is keyed on the provider's *real* model
name, so chunks whose exact formatted text was embedded before — by
production, or by another experiment with the same model — are served from
cache. Reverting to a previously tried recipe costs nearly nothing.

## Running an experiment

Prereqs: a Postgres corpus with transformed speeches (a dev copy is fine, but
see "comparability" below) and the config's provider available (Ollama
running, or `OPENAI_API_KEY` set, or the sentence-transformers extra
installed).

```bash
# The control: run this first on any corpus you experiment on
uv run python -m src.experiments.runner --config experiments/configs/baseline-gemma.yaml

# A candidate
uv run python -m src.experiments.runner --config experiments/configs/gemma-small-chunks.yaml

# Smoke-test a config end-to-end without embedding the whole corpus
# (flagged "partial" — quality numbers are not comparable)
uv run python -m src.experiments.runner --config ... --limit 100

# Housekeeping
uv run python -m src.experiments.runner --list                      # namespaces in DB
uv run python -m src.experiments.runner --purge exp:name-a1b2c3d4   # drop one
uv run python -m src.experiments.runner --config ... --no-keep      # purge on success
uv run python -m src.experiments.runner --config ... --no-cache     # true provider timings
```

## The procedure

1. **State a hypothesis.** One sentence in the config's `notes`: what should
   change, and why. A config without a hypothesis is a fishing trip.
2. **Change one thing.** Copy the nearest existing config and vary a single
   axis (chunk size, overlap, strategy, model, prefixing). If you change two
   things, you learn nothing when the number moves.
3. **Anchor with the control.** `baseline-gemma.yaml` reproduces production
   behaviour; run it once per corpus so every comparison is same-corpus,
   same-cases. Its numbers should reproduce `tests/eval/BASELINE.md` (MRR
   0.903 on the original 12-case corpus).
4. **Run full-corpus.** `--limit` runs are for wiring checks only — ranking
   quality depends on the distractor pool, so partial runs are flagged and
   must not drive decisions.
5. **Read the leaderboard, then the record.** `RESULTS.md` for the ranking;
   the run's entry in `runs.jsonl` for per-case detail — a higher MRR that
   comes from one case flipping while another regresses is a coin toss, not a
   win. With 12 cases, treat differences under ~0.05 MRR as noise unless the
   per-case picture is uniformly better.
6. **Weigh cost.** Quality first (retrieval sets the ceiling — PLAN.md), then
   query latency against the MCP's interactive budget, then storage/embed
   cost. A +0.01 MRR that doubles vector count is usually a bad trade.
7. **Commit the registry.** `runs.jsonl` + `RESULTS.md` changes go in the same
   PR as the config that produced them.
8. **Promote or purge.** Purge losing namespaces (`--purge`). To promote a
   winner to production:
   - add/adjust its model entry in `MODEL_METADATA_REGISTRY`
     (`src/embeddings/config.py`) — including `max_chunk_words` if the winning
     chunk size differs, since the production pipeline chunks at the registry
     value;
   - if the winning overlap/min-words differ from `chunk_text` defaults, wire
     those through `EmbeddingPipeline._run_source` (today it passes only
     `max_words`);
   - set `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`, re-embed
     (`python main.py --mode embed-only --force`), re-run
     `python -m tests.eval.runner`, and update `tests/eval/BASELINE.md`.

## Comparability rules

- **Same corpus, same cases.** MRR is only comparable between runs on the same
  speeches and the same `cases.yaml`. Growing the corpus or editing cases
  starts a new comparison era — re-run the control first. (The record stores
  corpus size so eras are distinguishable after the fact.)
- **Latency numbers are environment-bound.** Ollama-on-laptop vs. OpenAI API
  timings are not comparable with each other, only within the same provider
  and machine. Use `--no-cache` when you care about true provider throughput.
- **The eval set is speech-only and small (12 cases).** It catches regressions
  and large wins; it cannot resolve small differences. Growing `cases.yaml`
  (especially with vote/written cases once those corpora thicken — see
  PLAN.md Phase 4 caveat) raises the resolution of every future experiment.

## What this is not

- Not part of the production pipeline: nothing here runs in the scheduled
  sync, and production search never sees `exp:` vectors.
- Not a hyperparameter optimiser: it executes and records the runs you ask
  for. The judgement between runs is yours.
