"""Posted-notification store — the per-event idempotency key (Req 1.5).

One row per anomaly event: which channel, which message_ts (thread anchor).
Backend: Cloud SQL ``notifications`` when configured, else local JSONL —
same dual-backend pattern as feedback_store.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from chokotei_shared import NotificationRecord, db

def _store() -> Path:
    # resolved per call (not at import) so test env vars always win regardless
    # of module import order across the suite
    return Path(os.environ.get("NOTIF_STORE", "data/notifications/notifications.jsonl"))


def save(rec: NotificationRecord) -> None:
    if db.enabled():
        db.execute(
            "INSERT INTO notifications (event_id, channel_id, message_ts) "
            "VALUES (%s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
            (rec.event_id, rec.channel_id, rec.message_ts))
        return
    store = _store()
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a") as fh:
        fh.write(rec.model_dump_json() + "\n")


def get(event_id: str) -> NotificationRecord | None:
    if db.enabled():
        rows = db.fetch(
            "SELECT event_id, channel_id, message_ts, "
            "EXTRACT(EPOCH FROM posted_at)::float8 AS posted_at "
            "FROM notifications WHERE event_id = %s", (event_id,))
        return NotificationRecord(**rows[0]) if rows else None
    store = _store()
    if not store.exists():
        return None
    for line in store.read_text().splitlines():
        if line.strip():
            rec = NotificationRecord(**json.loads(line))
            if rec.event_id == event_id:
                return rec
    return None


def by_message_ts(message_ts: str) -> NotificationRecord | None:
    """Correlate a Slack thread reply back to its event (Req 3.1)."""
    if db.enabled():
        rows = db.fetch(
            "SELECT event_id, channel_id, message_ts, "
            "EXTRACT(EPOCH FROM posted_at)::float8 AS posted_at "
            "FROM notifications WHERE message_ts = %s", (message_ts,))
        return NotificationRecord(**rows[0]) if rows else None
    store = _store()
    if not store.exists():
        return None
    for line in store.read_text().splitlines():
        if line.strip():
            rec = NotificationRecord(**json.loads(line))
            if rec.message_ts == message_ts:
                return rec
    return None
