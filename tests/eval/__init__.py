"""Retrieval evaluation harness for Senedd semantic search.

Two layers, deliberately separated:

* ``metrics`` — pure ranking metrics (hit-rate@k, recall@k, MRR). No database or
  embedding model is touched, so they run in the default mocked test suite.
* ``runner`` — drives the *live* retrieval stack (Postgres + active embedding
  provider) over a labelled case set and prints a scoreboard. This requires the
  real stack and is invoked manually, never collected by ``pytest tests/``.
"""
