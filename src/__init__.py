"""Top-level package surface.

Only lightweight, leaf modules (settings, session helpers) are imported eagerly.
The pipeline classes are exposed lazily via ``__getattr__`` so that running a
stage as a module (``python -m src.db.acquisition`` / ``src.db.transformation``)
does not trigger a transitive import of that same stage through this package
init — which otherwise emits a spurious runpy double-import warning.
"""
from src.db.settings import settings, setup_logging
from src.db.session import get_engine, get_session, get_sessionmaker

__all__ = [
    "settings",
    "setup_logging",
    "get_session",
    "get_engine",
    "get_sessionmaker",
    "AcquisitionPipeline",
    "TransformationPipeline",
    "Provisioner",
    "SeneddPipeline",
    "EmbeddingPipeline",
]


def __getattr__(name):
    """Lazily resolve the pipeline classes on first access."""
    if name == "AcquisitionPipeline":
        from src.db.acquisition import AcquisitionPipeline
        return AcquisitionPipeline
    if name == "TransformationPipeline":
        from src.db.transformation import TransformationPipeline
        return TransformationPipeline
    if name == "Provisioner":
        from src.db.provisioning import Provisioner
        return Provisioner
    if name == "SeneddPipeline":
        from src.db.pipeline import SeneddPipeline
        return SeneddPipeline
    if name == "EmbeddingPipeline":
        from src.embeddings.pipeline import EmbeddingPipeline
        return EmbeddingPipeline
    raise AttributeError(f"module 'src' has no attribute {name!r}")
