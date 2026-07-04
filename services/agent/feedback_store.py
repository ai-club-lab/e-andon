"""HITL feedback store + quality metrics (Req 8, 9.3).

Persists operator verdicts (AI result / human verdict / correct cause / ts) and
computes the running correct-rate. P1 uses a local JSONL; swap for Cloud SQL
(feedback table) once provisioned.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

STORE = Path(os.environ.get("FEEDBACK_STORE", "data/feedback/feedback.jsonl"))


def save(record: dict) -> None:
    """Append one feedback record (Req 8.3)."""
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load() -> list[dict]:
    if not STORE.exists():
        return []
    with STORE.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def metrics() -> dict:
    """Return correct-rate metrics for the dashboard (Req 9.3)."""
    rows = load()
    total = len(rows)
    correct = sum(1 for r in rows if r.get("verdict") == "correct")
    return {
        "total": total,
        "correct": correct,
        "wrong": total - correct,
        "correct_rate": round(correct / total, 3) if total else None,
    }
