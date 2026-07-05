"""HITL feedback store + quality metrics (Req 8, 9.3).

Backend: Cloud SQL ``feedback`` when configured (survives Cloud Run cold
starts), else local JSONL for dev. Persists operator verdicts (AI result /
human verdict / correct cause) and computes the running correct-rate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from chokotei_shared import db

STORE = Path(os.environ.get("FEEDBACK_STORE", "data/feedback/feedback.jsonl"))


def save(record: dict) -> None:
    """Append one feedback record (Req 8.3)."""
    if db.enabled():
        db.execute(
            "INSERT INTO feedback (event_id, verdict, ai_cause, human_cause, kind, peak) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (record["event_id"], record["verdict"], json.dumps(record.get("ai_cause")),
             record.get("human_cause"), record.get("kind"), record.get("peak")),
        )
        return
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load() -> list[dict]:
    if db.enabled():
        return db.fetch("SELECT event_id, verdict, human_cause, kind, peak FROM feedback")
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
