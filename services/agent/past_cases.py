"""Past-case store for few-shot RAG (Req 9, design.md §4.3).

Human-confirmed cases prime the RCA agent. Backend: Cloud SQL ``past_cases``
when configured (survives Cloud Run cold starts), else local JSONL for dev.
Ranking is keyword overlap in Python (stand-in for pgvector similarity until an
embedding model/dim is chosen — research #5).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from chokotei_shared import FeedbackCase, db

STORE = Path(os.environ.get("CASES_STORE", "data/cases/past_cases.jsonl"))

_SEED = [
    FeedbackCase(summary="部品が位置ずれし搬送機構で噛み込み、モータ電流上昇後にライン停止",
                 correct_cause="搬送治具の固定緩みによる部品の位置ずれ", source_event_id="seed-1"),
    FeedbackCase(summary="ガイド摩耗で部品が引っ掛かりモータ電流上昇・ベルト停止",
                 correct_cause="ガイドレール摩耗による噛み込み", source_event_id="seed-2"),
]


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[\wぁ-んァ-ヶ一-龠]+", s.lower()))


def _stored() -> list[FeedbackCase]:
    if db.enabled():
        rows = db.fetch("SELECT summary, correct_cause, source_event_id FROM past_cases")
        return [FeedbackCase(summary=r["summary"], correct_cause=r["correct_cause"],
                             source_event_id=r["source_event_id"] or "") for r in rows]
    if STORE.exists():
        with STORE.open() as fh:
            return [FeedbackCase(**json.loads(line)) for line in fh if line.strip()]
    return []


def add(case: FeedbackCase) -> None:
    """Append a human-confirmed case (Req 9.2)."""
    if db.enabled():
        db.execute(
            "INSERT INTO past_cases (summary, correct_cause, source_event_id) VALUES (%s, %s, %s)",
            (case.summary, case.correct_cause, case.source_event_id),
        )
        return
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as fh:
        fh.write(case.model_dump_json() + "\n")


def search(query: str, k: int = 3) -> list[FeedbackCase]:
    """Return up to ``k`` cases ranked by keyword overlap with ``query``."""
    q = _tokens(query)
    candidates = _SEED + _stored()
    scored = [(len(q & _tokens(c.summary + " " + c.correct_cause)), c) for c in candidates]
    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for s, c in scored if s > 0][:k]
