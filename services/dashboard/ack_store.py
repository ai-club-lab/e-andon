"""対応中 (ack) store — who is on it, first-wins (business-flow phase 4).

A stop's real urgency is "someone is responding", not "paperwork is filed":
the ack is what stops the escalation tiers, while the verdict can follow
after the fix. One row per event; same dual-backend pattern as notif_store.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from chokotei_shared import Actor, db


def _store() -> Path:
    return Path(os.environ.get("ACK_STORE", "data/acks/acks.jsonl"))


def save(event_id: str, actor: Actor) -> None:
    """First responder wins; later acks are no-ops (mirror of verdict 2.4)."""
    if db.enabled():
        db.execute(
            "INSERT INTO acks (event_id, actor_surface, actor_id, actor_name) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
            (event_id, actor.surface, actor.user_id, actor.display_name))
        return
    if get(event_id):
        return
    store = _store()
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a") as fh:
        fh.write(json.dumps({"event_id": event_id, "actor_surface": actor.surface,
                             "actor_id": actor.user_id,
                             "actor_name": actor.display_name,
                             "ts": time.time()}, ensure_ascii=False) + "\n")


def get(event_id: str) -> dict | None:
    if db.enabled():
        rows = db.fetch(
            "SELECT event_id, actor_surface, actor_id, actor_name, "
            "EXTRACT(EPOCH FROM created_at)::float8 AS ts "
            "FROM acks WHERE event_id = %s", (event_id,))
        return rows[0] if rows else None
    store = _store()
    if not store.exists():
        return None
    for line in store.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("event_id") == event_id:
                return row
    return None
