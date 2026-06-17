"""Load and validate the labelled evaluation case set (``cases.yaml``)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

CASES_PATH = Path(__file__).resolve().parent / "cases.yaml"


@dataclass
class EvalCase:
    """A single known-item retrieval case.

    Attributes:
        id: Stable, unique kebab-case identifier.
        query: Natural-language query a user might ask.
        relevant_speech_ids: Speech ids judged relevant (>= 1).
        speaker: Optional speaker filter to pass through to retrieval.
        note: Optional human note on why these speeches are relevant.
    """

    id: str
    query: str
    relevant_speech_ids: List[int]
    speaker: Optional[str] = None
    note: Optional[str] = None


def load_cases(path: Path = CASES_PATH) -> List[EvalCase]:
    """Parse ``cases.yaml`` into validated :class:`EvalCase` objects.

    Raises:
        ValueError: If the file is malformed, an id is duplicated, or a case has
            no relevant speech ids.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "cases" not in raw:
        raise ValueError(f"{path} must contain a top-level 'cases' list.")

    cases: List[EvalCase] = []
    seen_ids: set[str] = set()
    for entry in raw["cases"]:
        case_id = entry.get("id")
        query = entry.get("query")
        relevant = entry.get("relevant_speech_ids")

        if not case_id or not query:
            raise ValueError(f"Case missing 'id' or 'query': {entry!r}")
        if case_id in seen_ids:
            raise ValueError(f"Duplicate case id: {case_id!r}")
        if not relevant:
            raise ValueError(f"Case {case_id!r} has no relevant_speech_ids.")

        seen_ids.add(case_id)
        cases.append(
            EvalCase(
                id=case_id,
                query=query,
                relevant_speech_ids=[int(x) for x in relevant],
                speaker=entry.get("speaker"),
                note=entry.get("note"),
            )
        )
    return cases
