"""Escalation engine tests (andon-human-loop task 5, Req 6 / 10.2).

Pure-deterministic: fake clock, fake sink, local JSONL store — no LLM, no DB,
no Slack. Covers: tier2/tier3 firing, single-shot semantics, cancel on verdict,
restart restore, and the correction-dialogue timeout.
Run: PYTHONPATH=services/dashboard:services/agent:services/detector \
     python -m pytest -q services/dashboard/test_escalation.py
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="eandon-esc-test-")
os.environ["ESC_STORE"] = os.path.join(_TMP, "escalations.jsonl")
os.environ["NOTIF_STORE"] = os.path.join(_TMP, "notifications.jsonl")

import escalation  # noqa: E402
import notif_store  # noqa: E402
from chokotei_shared import (  # noqa: E402
    EscalationStep,
    NotificationRecord,
    RoutingDecision,
)


class FakeSink:
    def __init__(self) -> None:
        self.threads: list[tuple[str, str]] = []  # (event_id, text)

    def enabled(self) -> bool:
        return True

    async def post_thread(self, rec: NotificationRecord, text: str) -> None:
        self.threads.append((rec.event_id, text))


class Clock:
    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _decision(eid: str) -> RoutingDecision:
    return RoutingDecision(
        event_id=eid, category="positioning", rule_version=1,
        primary_mention="（保全担当）",
        escalation_plan=[
            EscalationStep(tier=2, delay_s=300, target_mention="（班長）"),
            EscalationStep(tier=3, delay_s=900, target_mention=None,
                           contact_note="ベンダー窓口 0120-000-000"),
        ])


@pytest.fixture()
def env():
    for key in ("ESC_STORE", "NOTIF_STORE"):
        if os.path.exists(os.environ[key]):
            os.remove(os.environ[key])
    clock = Clock()
    sink = FakeSink()
    verdicts: dict[str, dict] = {}
    notices: list[tuple[str, str]] = []
    eng = escalation.EscalationEngine(
        sink=sink, verdict_of=verdicts.get, now=clock,
        on_notice=lambda eid, text: notices.append((eid, text)))
    notif_store.save(NotificationRecord(event_id="evt-e1", channel_id="C1",
                                        message_ts="111.222", posted_at=clock.t))
    return eng, sink, clock, verdicts, notices


def test_fires_tier2_then_tier3_after_delays(env) -> None:
    """Req 6.2/6.3: 5min -> 班長, +15min -> vendor contact presented (not paged)."""
    eng, sink, clock, _, notices = env
    asyncio.run(eng.schedule(_decision("evt-e1")))
    asyncio.run(eng.tick())
    assert sink.threads == [], "nothing fires before the delay"
    clock.t += 301
    asyncio.run(eng.tick())
    assert len(sink.threads) == 1 and "（班長）" in sink.threads[0][1]
    clock.t += 901
    asyncio.run(eng.tick())
    assert len(sink.threads) == 2 and "0120-000-000" in sink.threads[1][1]
    assert notices and "0120-000-000" in notices[0][1], "tier3 also surfaces on dashboard"


def test_fires_once_even_across_many_ticks(env) -> None:
    eng, sink, clock, _, _ = env
    asyncio.run(eng.schedule(_decision("evt-e1")))
    clock.t += 301
    for _ in range(5):
        asyncio.run(eng.tick())
    assert len(sink.threads) == 1, "tier2 must fire exactly once"


def test_verdict_cancels_pending_tiers(env) -> None:
    """Req 6.4: adjudication at any point stops later escalations."""
    eng, sink, clock, verdicts, _ = env
    asyncio.run(eng.schedule(_decision("evt-e1")))
    verdicts["evt-e1"] = {"verdict": "correct"}
    clock.t += 2_000
    asyncio.run(eng.tick())
    assert sink.threads == [], "no escalation after a verdict"


def test_explicit_cancel_stops_everything(env) -> None:
    eng, sink, clock, _, _ = env
    asyncio.run(eng.schedule(_decision("evt-e1")))
    asyncio.run(eng.cancel("evt-e1"))
    clock.t += 2_000
    asyncio.run(eng.tick())
    assert sink.threads == []


def test_restart_restores_pending_timers(env) -> None:
    """Req 10.2: a new engine over the same store still fires the future tiers."""
    eng, sink, clock, verdicts, notices = env
    asyncio.run(eng.schedule(_decision("evt-e1")))
    reborn = escalation.EscalationEngine(
        sink=sink, verdict_of=verdicts.get, now=clock,
        on_notice=lambda eid, text: notices.append((eid, text)))
    clock.t += 301
    asyncio.run(reborn.tick())
    assert len(sink.threads) == 1, "pending rows survive the restart"


def test_correction_dialogue_times_out_after_30min(env) -> None:
    """Req 3.5: an unfinished correction thread is closed with guidance."""
    eng, sink, clock, _, _ = env
    eng.touch_correction("evt-e1")
    clock.t += 1_801
    asyncio.run(eng.tick())
    assert any("ダッシュボード" in t for _, t in sink.threads), \
        "timeout message points back to the dashboard"
    n = len(sink.threads)
    asyncio.run(eng.tick())
    assert len(sink.threads) == n, "timeout closes once, not repeatedly"
