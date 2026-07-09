"""Notification sink tests (andon-human-loop tasks 3.1–3.6, Req 1/2.5).

Given a stopped line with an RCA result, when the notifier posts to Slack,
then the card carries the adjudication material, posts exactly once per event,
and failures are loud (never silent). WebClient is faked — no Slack, no GCP.
Run: PYTHONPATH=services/dashboard:services/agent:services/detector \
     python -m pytest -q services/dashboard/test_sinks.py
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="eandon-sink-test-")
os.environ["NOTIF_STORE"] = os.path.join(_TMP, "notifications.jsonl")

import notif_store  # noqa: E402
import sinks  # noqa: E402
from chokotei_shared import (  # noqa: E402
    Actor,
    AnomalyEvent,
    EscalationStep,
    RcaResult,
    RoutingDecision,
)


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self.fail = fail

    def chat_postMessage(self, **kw):
        if self.fail:
            raise RuntimeError("slack down")
        self.posts.append(kw)
        return {"ts": f"1720000000.{len(self.posts):06d}"}

    def chat_update(self, **kw):
        self.updates.append(kw)
        return {"ok": True}


def _event(eid: str = "evt-s1") -> AnomalyEvent:
    return AnomalyEvent(event_id=eid, started_ts=8.5, ended_ts=9.5, kind="offset",
                        peak_magnitude=16.0, rep_frame_uri="", status="closed")


def _rca(eid: str = "evt-s1") -> RcaResult:
    return RcaResult(event_id=eid, cause_candidates=["搬送ガイドレール固定ボルトの緩み", "治具摩耗"],
                     confidence=0.85, evidence=["offset 16px", "全センサー正常"],
                     category="positioning")


def _routing(eid: str = "evt-s1") -> RoutingDecision:
    return RoutingDecision(event_id=eid, category="positioning", rule_version=1,
                           primary_mention="<@U_MAINT>",
                           escalation_plan=[EscalationStep(tier=2, delay_s=300,
                                                           target_mention="<@U_LEAD>")])


@pytest.fixture()
def clean_store():
    if os.path.exists(os.environ["NOTIF_STORE"]):
        os.remove(os.environ["NOTIF_STORE"])
    yield


def test_null_sink_is_disabled_and_silent(clean_store) -> None:
    s = sinks.NullSink()
    assert s.enabled() is False
    assert asyncio.run(s.post_card(_event(), _rca(), _routing(), "")) is None


def test_post_card_carries_adjudication_material(clean_store) -> None:
    """Req 1.2/1.3/2.1/5.3: cause+confidence+evidence+mention+deep link+buttons."""
    fake = FakeClient()
    s = sinks.SlackSink(client=fake, channel_id="C1")
    rec = asyncio.run(s.post_card(_event(), _rca(), _routing(),
                                  "https://example.run.app/e/evt-s1"))
    assert rec is not None and rec.message_ts and rec.channel_id == "C1"
    blob = json.dumps(fake.posts[0], ensure_ascii=False)
    assert "搬送ガイドレール固定ボルトの緩み" in blob
    assert "85%" in blob
    assert "<@U_MAINT>" in blob
    assert "https://example.run.app/e/evt-s1" in blob
    assert "verdict_correct" in blob and "verdict_wrong" in blob
    assert "evt-s1" in blob  # button value = event_id


def test_post_card_is_idempotent_per_event(clean_store) -> None:
    """Req 1.5: at most one card per event, across retries and restarts."""
    fake = FakeClient()
    s = sinks.SlackSink(client=fake, channel_id="C1")
    r1 = asyncio.run(s.post_card(_event(), _rca(), _routing(), ""))
    r2 = asyncio.run(s.post_card(_event(), _rca(), _routing(), ""))
    assert len(fake.posts) == 1
    assert r1.message_ts == r2.message_ts
    fresh = sinks.SlackSink(client=fake, channel_id="C1")  # "restart"
    r3 = asyncio.run(fresh.post_card(_event(), _rca(), _routing(), ""))
    assert len(fake.posts) == 1 and r3.message_ts == r1.message_ts


def test_post_card_failure_is_loud_not_silent(clean_store) -> None:
    """Req 1.4: delivery failure surfaces via on_error, returns None."""
    errors: list[str] = []
    s = sinks.SlackSink(client=FakeClient(fail=True), channel_id="C1",
                        on_error=errors.append)
    assert asyncio.run(s.post_card(_event(), _rca(), _routing(), "")) is None
    assert errors and "Slack" in errors[0]
    assert notif_store.get("evt-s1") is None, "failed post must not claim the idempotency key"


def test_post_card_joins_channel_on_first_post(clean_store) -> None:
    """channels:join: not_in_channel -> conversations_join -> retry once."""
    from slack_sdk.errors import SlackApiError

    class JoinClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.joined: list[str] = []

        def chat_postMessage(self, **kw):
            if not self.joined and "thread_ts" not in kw:
                raise SlackApiError("not_in_channel", {"error": "not_in_channel"})
            return super().chat_postMessage(**kw)

        def conversations_join(self, channel: str):
            self.joined.append(channel)
            return {"ok": True}

    fake = JoinClient()
    s = sinks.SlackSink(client=fake, channel_id="C1")
    rec = asyncio.run(s.post_card(_event(), _rca(), _routing(), ""))
    assert fake.joined == ["C1"]
    assert rec is not None and len(fake.posts) == 1


def test_update_card_reflects_verdict_and_actor(clean_store) -> None:
    """Req 2.5: the card is updated with verdict, actor, and time."""
    fake = FakeClient()
    s = sinks.SlackSink(client=fake, channel_id="C1")
    rec = asyncio.run(s.post_card(_event(), _rca(), _routing(), ""))
    asyncio.run(s.update_card(rec, "correct",
                              Actor(surface="slack", user_id="U123", display_name="鈴木")))
    assert fake.updates and fake.updates[0]["ts"] == rec.message_ts
    blob = json.dumps(fake.updates[0], ensure_ascii=False)
    assert "鈴木" in blob and "正しい" in blob
