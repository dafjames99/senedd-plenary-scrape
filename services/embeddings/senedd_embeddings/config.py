"""Model metadata registry — canonical home is ``senedd_data.model_registry``.

The registry is pure metadata consumed by settings validation (senedd-data),
the chunker/pipeline (this package) and query embedding (senedd-search). It
lives in the foundation package so the dependency graph stays acyclic; this
module re-exports it under the embeddings-side name its call sites use.
"""
from senedd_data.model_registry import MODEL_METADATA_REGISTRY

__all__ = ["MODEL_METADATA_REGISTRY"]
