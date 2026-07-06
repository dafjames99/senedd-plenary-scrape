"""Experiment configuration: the single declaration of one embedding recipe.

An :class:`ExperimentConfig` captures every knob that changes what gets
embedded — model, chunking strategy and sizes, prefixing — plus a name and
free-text hypothesis. Two properties make experiments reproducible and safe:

* ``config_hash`` — SHA-256 over the *embedding-affecting* fields only (name
  and notes excluded), so the same recipe always resolves to the same identity
  even if renamed.
* ``namespace`` — ``exp:<name>-<hash8>``, the ``model_name`` under which the
  run's vectors are stored in ``speech_embeddings``. Registry model names never
  start with ``exp:``, so experiment vectors can never collide with (or leak
  into) production search, and purging an experiment is a single delete.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

import yaml

from src.embeddings.config import MODEL_METADATA_REGISTRY

CHUNK_STRATEGIES = ("sentence-window", "fixed-window", "whole-item")

# speech_embeddings.model_name is VARCHAR(100); "exp:" + name + "-" + 8 hex
# chars must fit with room to spare.
_MAX_NAME_LENGTH = 60


@dataclass(frozen=True)
class ExperimentConfig:
    """One embedding recipe to evaluate.

    Attributes:
        name: Short kebab-case identifier; part of the vector namespace.
        provider: Key in ``PROVIDER_REGISTER`` (``sentence-transformer`` |
            ``ollama`` | ``openai``).
        model: Bare model name as the provider expects it
            (e.g. ``embeddinggemma:300m``, ``text-embedding-3-small``).
        chunk_strategy: ``sentence-window`` (sentence-packed windows with
            overlap — current production behaviour), ``fixed-window``
            (sentence-packed, no overlap), or ``whole-item`` (one chunk per
            speech, still split at the model's context cap since that limit is
            physical).
        chunk_max_words: Window size in words. ``None`` uses the model's
            registry ``max_chunk_words``. Ignored by ``whole-item`` (which
            always uses the model cap).
        chunk_overlap_words: Carried-forward context between windows.
            Only meaningful for ``sentence-window``.
        chunk_min_words: Trailing chunks below this merge into the previous one.
        min_item_words: Speeches shorter than this are not embedded at all
            (production gate is 10).
        speaker_prefix: Prepend ``"<speaker>: "`` to each chunk before
            embedding (production behaviour is True).
        doc_prefix: Model instruction prefix for documents. ``None`` uses the
            registry value; ``""`` forces none.
        query_prefix: Model instruction prefix for queries — applied at eval
            time so retrieval stays symmetric with the document side. ``None``
            uses the registry value; ``""`` forces none.
        notes: Free-text hypothesis / rationale. Not part of the hash.
    """

    name: str
    provider: str
    model: str
    chunk_strategy: str = "sentence-window"
    chunk_max_words: Optional[int] = None
    chunk_overlap_words: int = 50
    chunk_min_words: int = 20
    min_item_words: int = 10
    speaker_prefix: bool = True
    doc_prefix: Optional[str] = None
    query_prefix: Optional[str] = None
    notes: str = field(default="", compare=False)

    # Fields excluded from the config hash: identity/commentary, not recipe.
    _HASH_EXCLUDED = ("name", "notes")

    def __post_init__(self):
        if not self.name or len(self.name) > _MAX_NAME_LENGTH:
            raise ValueError(
                f"Experiment name must be 1-{_MAX_NAME_LENGTH} chars, got {self.name!r}."
            )
        if any(c.isspace() or c == ":" for c in self.name):
            raise ValueError(
                f"Experiment name must not contain whitespace or ':' ({self.name!r})."
            )
        if self.chunk_strategy not in CHUNK_STRATEGIES:
            raise ValueError(
                f"Unknown chunk_strategy {self.chunk_strategy!r}. "
                f"Use one of: {', '.join(CHUNK_STRATEGIES)}."
            )

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def config_hash(self) -> str:
        """SHA-256 hex digest over the embedding-affecting fields."""
        payload = {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name not in self._HASH_EXCLUDED
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def run_id(self) -> str:
        """Human-readable, hash-qualified run identity."""
        return f"{self.name}-{self.config_hash[:8]}"

    @property
    def namespace(self) -> str:
        """``model_name`` value under which this run's vectors are stored."""
        return f"exp:{self.run_id}"

    # ------------------------------------------------------------------
    # Resolution against the model registry
    # ------------------------------------------------------------------

    @property
    def registry_key(self) -> str:
        provider = (
            "sentence-transformers"
            if self.provider == "sentence-transformer"
            else self.provider
        )
        return f"{provider}/{self.model}"

    def resolve(self) -> "ResolvedConfig":
        """Fill registry defaults in; validate that every needed value exists.

        A model in ``MODEL_METADATA_REGISTRY`` supplies ``max_chunk_words`` and
        prefixes. A model *not* in the registry may still be experimented with,
        but the config must then set ``chunk_max_words``, ``doc_prefix`` and
        ``query_prefix`` explicitly — there is nothing to default from.
        """
        meta = MODEL_METADATA_REGISTRY.get(self.registry_key)
        if meta is None:
            missing = [
                label
                for label, value in (
                    ("chunk_max_words", self.chunk_max_words),
                    ("doc_prefix", self.doc_prefix),
                    ("query_prefix", self.query_prefix),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"Model '{self.registry_key}' is not in MODEL_METADATA_REGISTRY; "
                    f"the config must set {', '.join(missing)} explicitly."
                )
            model_cap = self.chunk_max_words
        else:
            model_cap = meta["max_chunk_words"]

        if self.chunk_strategy == "whole-item":
            max_words = model_cap
            overlap = 0
        elif self.chunk_strategy == "fixed-window":
            max_words = self.chunk_max_words or model_cap
            overlap = 0
        else:  # sentence-window
            max_words = self.chunk_max_words or model_cap
            overlap = self.chunk_overlap_words

        if max_words > model_cap:
            raise ValueError(
                f"chunk_max_words={max_words} exceeds the model's context cap "
                f"({model_cap} words)."
            )

        doc_prefix = self.doc_prefix
        if doc_prefix is None:
            doc_prefix = meta["doc_prefix"] if meta else ""
        query_prefix = self.query_prefix
        if query_prefix is None:
            query_prefix = meta["query_prefix"] if meta else ""

        return ResolvedConfig(
            config=self,
            max_words=max_words,
            overlap_words=overlap,
            min_words=self.chunk_min_words,
            doc_prefix=doc_prefix,
            query_prefix=query_prefix,
        )


@dataclass(frozen=True)
class ResolvedConfig:
    """An :class:`ExperimentConfig` with every default made concrete."""

    config: ExperimentConfig
    max_words: int
    overlap_words: int
    min_words: int
    doc_prefix: str
    query_prefix: str


def load_config(path: Path | str) -> ExperimentConfig:
    """Load an experiment config from a YAML file.

    The file is a flat mapping of :class:`ExperimentConfig` fields; ``name``
    defaults to the file stem.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a mapping of config fields.")
    raw.setdefault("name", path.stem)

    known = {f.name for f in fields(ExperimentConfig)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(
            f"{path} has unknown config fields: {', '.join(sorted(unknown))}. "
            f"Known fields: {', '.join(sorted(known))}."
        )
    return ExperimentConfig(**raw)
