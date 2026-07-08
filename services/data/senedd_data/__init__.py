"""Top-level package surface.

Only lightweight, leaf modules (settings, session helpers) are imported eagerly.
The pipeline classes are exposed lazily via ``__getattr__`` so that running a
stage as a module (``python -m senedd_data.acquisition`` / ``senedd_data.transformation``)
does not trigger a transitive import of that same stage through this package
init — which otherwise emits a spurious runpy double-import warning.
"""
from senedd_data.settings import settings, setup_logging
from senedd_data.session import get_engine, get_session, get_sessionmaker

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
]


def __getattr__(name):
    """Lazily resolve the pipeline classes on first access."""
    if name == "AcquisitionPipeline":
        from senedd_data.acquisition import AcquisitionPipeline
        return AcquisitionPipeline
    if name == "TransformationPipeline":
        from senedd_data.transformation import TransformationPipeline
        return TransformationPipeline
    if name == "Provisioner":
        from senedd_data.provisioning import Provisioner
        return Provisioner
    if name == "SeneddPipeline":
        from senedd_data.pipeline import SeneddPipeline
        return SeneddPipeline
    raise AttributeError(f"module 'senedd_data' has no attribute {name!r}")
