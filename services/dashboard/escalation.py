"""Escalation engine (design §4.4, Req 6 / 3.5 / 10.2).

Deterministic by construction: firing = (clock × verdict state × stored rows).
No LLM anywhere in this module (Req 6.6). Rows persist in Cloud SQL
``escalations`` (or local JSONL) and every tick re-reads them, so a process
restart *is* the restore path (Req 10.2). Every fire/cancel is audit-logged.

Also owns the correction-dialogue timeout (Req 3.5): the same tick closes
threads idle past ESCALATION.correction_timeout_s with guidance back to the
dashboard.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

import notif_store
from chokotei_shared import ESCALATION, RoutingDecision, db

logger = logging.getLogger("escalation")


def _store() -> Path:
    return Path(os.environ.get("ESC_STORE", "data/escalations/escalations.jsonl"))


def _load_local() -> list[dict]:
    store = _store()
    if not store.exists():
        return []
    return [json.loads(x) for x in store.read_text().splitlines() if x.strip()]


def _save_local(rows: list[dict]) -> None:
    store = _store()
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def _insert(row: dict) -> None:
    if db.enabled():
        db.execute(
            "INSERT INTO escalations (event_id, tier, fire_at, target_mention, contact_note) "
            "VALUES (%s, %s, to_timestamp(%s), %s, %s)",
            (row["event_id"], row["tier"], row["fire_at"],
             row.get("target_mention"), row.get("contact_note")))
        return
    rows = _load_local()
    rows.append(row)
    _save_local(rows)


def _pending() -> list[dict]:
    if db.enabled():
        return db.fetch(
            "SELECT id, event_id, tier, EXTRACT(EPOCH FROM fire_at) AS fire_at, "
            "target_mention, contact_note FROM escalations WHERE state = 'pending'")
    return [r for r in _load_local() if r.get("state", "pending") == "pending"]


def _mark(event_id: str, tier: int | None, state: str, fired_at: float | None) -> None:
    if db.enabled():
        cond, params = ("AND tier = %s", [tier]) if tier is not None else ("", [])
        db.execute(
            f"UPDATE escalations SET state = %s, fired_at = to_timestamp(%s) "
            f"WHERE event_id = %s AND state = 'pending' {cond}",
            tuple([state, fired_at or time.time(), event_id] + params))
        return
    rows = _load_local()
    for r in rows:
        if (r["event_id"] == event_id and r.get("state", "pending") == "pending"
                and (tier is None or r["tier"] == tier)):
            r["state"] = state
            r["fired_at"] = fired_at
    _save_local(rows)


class EscalationEngine:
    """Tick-driven, store-backed, dependency-injected (sink/clock/verdicts)."""

    def __init__(self, sink, verdict_of: Callable[[str], dict | None],
                 now: Callable[[], float] = time.time,
                 on_notice: Callable[[str, str], None] | None = None) -> None:
        self._sink = sink
        self._verdict_of = verdict_of
        self._now = now
        self._on_notice = on_notice or (lambda eid, text: None)
        self._corrections: dict[str, float] = {}  # event_id -> last activity ts

    async def schedule(self, decision: RoutingDecision) -> None:
        """Register tier2/3 timers after a successful card post (Req 6.1).
        Delays are relative to the previous tier."""
        base = self._now()
        fire_at = base
        for step in decision.escalation_plan:
            fire_at += step.delay_s
            await asyncio.to_thread(_insert, {
                "event_id": decision.event_id, "tier": step.tier,
                "fire_at": fire_at, "target_mention": step.target_mention,
                "contact_note": step.contact_note, "state": "pending",
                "fired_at": None})
        logger.info("escalation scheduled",
                    extra={"ctx": {"event_id": decision.event_id,
                                   "tiers": [s.tier for s in decision.escalation_plan]}})

    async def cancel(self, event_id: str) -> None:
        """Verdict/response stops all later tiers (Req 6.4). Audited."""
        await asyncio.to_thread(_mark, event_id, None, "cancelled", self._now())
        logger.info("escalation cancelled", extra={"ctx": {"event_id": event_id}})

    def touch_correction(self, event_id: str) -> None:
        """Record correction-dialogue activity for the timeout watch (Req 3.5)."""
        self._corrections[event_id] = self._now()

    def close_correction(self, event_id: str) -> None:
        self._corrections.pop(event_id, None)

    def correction_open(self, event_id: str) -> bool:
        """スレッド発言を訂正として扱ってよいか（✗で明示的に開かれた対話のみ）."""
        return event_id in self._corrections

    async def tick(self) -> None:
        now = self._now()
        for row in await asyncio.to_thread(_pending):
            if row["fire_at"] > now:
                continue
            if self._verdict_of(row["event_id"]):
                await self.cancel(row["event_id"])  # adjudicated meanwhile (Req 6.4)
                continue
            await self._fire(row, now)
        await self._expire_corrections(now)

    async def _fire(self, row: dict, now: float) -> None:
        eid, tier = row["event_id"], row["tier"]
        # mark first — a sink error must not re-fire the tier forever
        await asyncio.to_thread(_mark, eid, tier, "fired", now)
        if tier == 2:
            text = (f"⏱ {ESCALATION.tier2_delay_s // 60}分たっても応答がないため、"
                    f"{row.get('target_mention') or ''} に連絡します。"
                    f"対応できる方はカードの「👋 私が対応します」を押してください。")
        else:  # tier 3: present the outside contact, never auto-page (Req 6.3)
            text = (f"⏱ まだ応答がないため、外部保守窓口の連絡先をご案内します: "
                    f"{row.get('contact_note') or ''}（自動では発報しません）")
            self._on_notice(eid, text)
        rec = await asyncio.to_thread(notif_store.get, eid)
        if rec is not None:
            await self._sink.post_thread(rec, text)
        logger.warning("escalation fired",
                       extra={"ctx": {"event_id": eid, "tier": tier, "at": now}})

    async def _expire_corrections(self, now: float) -> None:
        timeout = ESCALATION.correction_timeout_s
        for eid, last in list(self._corrections.items()):
            if now - last <= timeout:
                continue
            self._corrections.pop(eid, None)
            rec = await asyncio.to_thread(notif_store.get, eid)
            if rec is not None:
                await self._sink.post_thread(
                    rec, "訂正対話は未確定のままクローズしました。"
                         "ダッシュボードの該当イベントからいつでも訂正を再開できます。")
            logger.info("correction dialogue timed out",
                        extra={"ctx": {"event_id": eid}})

    async def run(self) -> None:
        """Background loop for the singleton process (started at app startup)."""
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("escalation tick failed")
            await asyncio.sleep(ESCALATION.tick_s)
