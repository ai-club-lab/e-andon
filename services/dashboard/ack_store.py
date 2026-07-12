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


def save_recovery(event_id: str, actor: Actor, stop_seconds: float | None) -> None:
    """復旧確認（flow phase 5）— first wins. ack が無ければ対応者確定も兼ねる
    （復旧を宣言した人が対応した人）。停止時間は検知から復旧までの実測。"""
    who = actor.display_name
    if db.enabled():
        db.execute(
            "INSERT INTO acks (event_id, actor_surface, actor_id, actor_name, "
            "                  recovered_at, recovered_by, stop_seconds) "
            "VALUES (%s, %s, %s, %s, now(), %s, %s) "
            "ON CONFLICT (event_id) DO UPDATE SET "
            "  recovered_at = now(), recovered_by = EXCLUDED.recovered_by, "
            "  stop_seconds = EXCLUDED.stop_seconds "
            "WHERE acks.recovered_at IS NULL",
            (event_id, actor.surface, actor.user_id, who, who, stop_seconds))
        return
    prior = get(event_id)
    if prior and prior.get("recovered_at"):
        return
    store = _store()
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a") as fh:
        fh.write(json.dumps({"event_id": event_id, "type": "recovery",
                             "recovered_by": who, "recovered_at": time.time(),
                             "stop_seconds": stop_seconds,
                             "actor_surface": actor.surface,
                             "actor_id": actor.user_id}, ensure_ascii=False) + "\n")


def get(event_id: str) -> dict | None:
    """The response record: ack fields + recovery fields (None until each phase)."""
    if db.enabled():
        rows = db.fetch(
            "SELECT event_id, actor_surface, actor_id, actor_name, "
            "EXTRACT(EPOCH FROM created_at)::float8 AS ts, "
            "EXTRACT(EPOCH FROM recovered_at)::float8 AS recovered_at, "
            "recovered_by, stop_seconds "
            "FROM acks WHERE event_id = %s", (event_id,))
        return rows[0] if rows else None
    store = _store()
    if not store.exists():
        return None
    out: dict | None = None
    for line in store.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("event_id") != event_id:
            continue
        if row.get("type") == "recovery":
            base = out or {"event_id": event_id,
                           "actor_surface": row.get("actor_surface"),
                           "actor_id": row.get("actor_id"),
                           "actor_name": row.get("recovered_by")}
            if not base.get("recovered_at"):     # first recovery wins
                base.update({k: row.get(k) for k in
                             ("recovered_at", "recovered_by", "stop_seconds")})
            out = base
        elif out is None:                        # first ack wins
            out = row
    return out
