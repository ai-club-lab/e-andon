"""HITL feedback store + quality metrics (Req 8, 9.3).

Backend: Cloud SQL ``feedback`` when configured (survives Cloud Run cold
starts), else local JSONL for dev. Persists operator verdicts (AI result /
human verdict / correct cause) and computes the running correct-rate.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from chokotei_shared import db

def _store() -> Path:
    # resolved per call (not at import) so test env vars win regardless of
    # module import order across the suite
    return Path(os.environ.get("FEEDBACK_STORE", "data/feedback/feedback.jsonl"))


def save(record: dict) -> None:
    """Append one feedback record with actor attribution (Req 8.3, human-loop Req 4)."""
    record.setdefault("ts", time.time())
    if db.enabled():
        db.execute(
            "INSERT INTO feedback (event_id, verdict, ai_cause, human_cause, kind, peak, "
            "actor_surface, actor_id, actor_name) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (record["event_id"], record["verdict"], json.dumps(record.get("ai_cause")),
             record.get("human_cause"), record.get("kind"), record.get("peak"),
             record.get("actor_surface"), record.get("actor_id"), record.get("actor_name")),
        )
        return
    store = _store()
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load() -> list[dict]:
    if db.enabled():
        return db.fetch(
            "SELECT event_id, verdict, human_cause, kind, peak, actor_surface, actor_id, "
            # ::float8 — EXTRACT returns numeric (Decimal in psycopg), which
            # breaks float arithmetic in analytics windows
            "actor_name, EXTRACT(EPOCH FROM created_at)::float8 AS ts FROM feedback ORDER BY id")
    store = _store()
    if not store.exists():
        return []
    with store.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def get_verdict(event_id: str) -> dict | None:
    """Latest verdict for an event, or None — the double-adjudication guard
    (human-loop Req 2.4). Returns verdict + actor + timestamp for display."""
    rows = [r for r in load() if r.get("event_id") == event_id]
    return rows[-1] if rows else None


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
