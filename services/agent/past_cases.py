"""Past-case store for few-shot RAG (Req 9, design.md §4.3).

Human-confirmed cases are appended here and searched to prime the RCA agent.
P1 uses a local JSONL + keyword overlap as a stand-in for pgvector similarity;
swap ``search`` for a vector query once Cloud SQL + pgvector is provisioned.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from chokotei_shared import FeedbackCase

STORE = Path(os.environ.get("CASES_STORE", "data/cases/past_cases.jsonl"))

_SEED = [
    FeedbackCase(summary="部品が上方へ変位しX軸振動スパイク", correct_cause="搬送治具の固定緩み",
                 source_event_id="seed-1"),
    FeedbackCase(summary="部品が回転しモータ電流上昇", correct_cause="ガイド摩耗による噛み込み",
                 source_event_id="seed-2"),
]


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[\wぁ-んァ-ヶ一-龠]+", s.lower()))


def _load() -> list[FeedbackCase]:
    cases = list(_SEED)
    if STORE.exists():
        with STORE.open() as fh:
            cases += [FeedbackCase(**json.loads(line)) for line in fh if line.strip()]
    return cases


def add(case: FeedbackCase) -> None:
    """Append a human-confirmed case (Req 9.2)."""
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as fh:
        fh.write(case.model_dump_json() + "\n")


def search(query: str, k: int = 3) -> list[FeedbackCase]:
    """Return up to ``k`` cases ranked by keyword overlap with ``query``."""
    q = _tokens(query)
    scored = [(len(q & _tokens(c.summary + " " + c.correct_cause)), c) for c in _load()]
    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for s, c in scored if s > 0][:k]
