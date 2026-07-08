"""Slack inbound tests (andon-human-loop task 6, Req 2 / 10.4 / 10.6).

Recorded-payload fixtures with real HMAC signatures — no Slack, no GCP.
Covers: signature verification (valid/invalid/stale), url_verification,
button verdict → the single write path, and retry idempotency.
Run: PYTHONPATH=services/dashboard:services/agent:services/detector \
     python -m pytest -q services/dashboard/test_slack_routes.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

_TMP = tempfile.mkdtemp(prefix="eandon-slackrt-test-")
for _key, _name in (("FEEDBACK_STORE", "feedback.jsonl"),
                    ("CASES_STORE", "cases.jsonl"),
                    ("IOT_STORE", "iot.jsonl"),
                    ("NOTIF_STORE", "notifications.jsonl"),
                    ("ESC_STORE", "escalations.jsonl")):
    os.environ[_key] = os.path.join(_TMP, _name)
os.environ.setdefault("ATTACH_DIR", os.path.join(_TMP, "attachments"))
_SECRET = "test-signing-secret"
os.environ["SLACK_SIGNING_SECRET"] = _SECRET

import server  # noqa: E402


def _sign(body: str, ts: str | None = None, secret: str = _SECRET) -> dict:
    ts = ts or str(int(time.time()))
    digest = hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(),
                      hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": f"v0={digest}"}


def _interactivity_body(action_id: str, event_id: str, user_id: str = "U777",
                        user_name: str = "suzuki") -> str:
    payload = {
        "type": "block_actions",
        "user": {"id": user_id, "username": user_name, "name": user_name},
        "container": {"channel_id": "C1", "message_ts": "111.222"},
        "actions": [{"action_id": action_id, "value": event_id}],
    }
    return "payload=" + urllib.parse.quote(json.dumps(payload))


def _seed_event(event_id: str = "evt-sl-1") -> None:
    rca = {"event_id": event_id, "cause_candidates": ["位置決め治具の摩耗"],
           "confidence": 0.8, "evidence": ["offset 16px"], "category": "positioning"}
    server.state.events[event_id] = {
        "event": {"event_id": event_id, "kind": "offset", "peak_magnitude": 16.0,
                  "started_ts": 8.5, "ended_ts": 9.5, "rep_frame_uri": "",
                  "status": "closed"},
        "rca": rca,
    }


@pytest.fixture()
def client():
    server.state.events.clear()
    server.state.rca_cache.clear()
    server._RATE.clear()
    for key in ("FEEDBACK_STORE", "NOTIF_STORE", "ESC_STORE"):
        if os.path.exists(os.environ[key]):
            os.remove(os.environ[key])
    with TestClient(server.app) as c:
        yield c


CT_FORM = {"content-type": "application/x-www-form-urlencoded"}


def test_rejects_invalid_signature_with_401(client) -> None:
    """Req 10.4: authenticity check fails closed and is recorded."""
    body = _interactivity_body("verdict_correct", "evt-sl-1")
    bad = _sign(body, secret="wrong-secret")
    r = client.post("/slack/interactivity", content=body, headers={**bad, **CT_FORM})
    assert r.status_code == 401


def test_rejects_stale_timestamp(client) -> None:
    """Req 10.4: 5-minute window — replayed requests are refused."""
    body = _interactivity_body("verdict_correct", "evt-sl-1")
    old = _sign(body, ts=str(int(time.time()) - 600))
    r = client.post("/slack/interactivity", content=body, headers={**old, **CT_FORM})
    assert r.status_code == 401


def test_answers_url_verification_challenge(client) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "chal-123"})
    r = client.post("/slack/events", content=body,
                    headers={**_sign(body), "content-type": "application/json"})
    assert r.status_code == 200 and r.json()["challenge"] == "chal-123"


def test_button_verdict_lands_in_single_path_with_slack_actor(client) -> None:
    """Req 2.1/2.2/4.1: the card button writes the same record, actor=slack."""
    import feedback_store
    _seed_event()
    body = _interactivity_body("verdict_correct", "evt-sl-1")
    r = client.post("/slack/interactivity", content=body,
                    headers={**_sign(body), **CT_FORM})
    assert r.status_code == 200
    rows = feedback_store.load()
    assert len(rows) == 1
    assert (rows[0]["actor_surface"], rows[0]["actor_id"]) == ("slack", "U777")
    assert rows[0]["verdict"] == "correct"


def test_retry_does_not_double_record(client) -> None:
    """Req 2.4 + Slack retries: same button event twice -> one record."""
    import feedback_store
    _seed_event()
    body = _interactivity_body("verdict_correct", "evt-sl-1")
    for _ in range(2):
        r = client.post("/slack/interactivity", content=body,
                        headers={**_sign(body), **CT_FORM})
        assert r.status_code == 200
    assert len(feedback_store.load()) == 1


def test_dashboard_events_expose_verdict_state(client) -> None:
    """Req 2.3: a Slack verdict shows up as adjudicated on the dashboard side."""
    _seed_event()
    body = _interactivity_body("verdict_correct", "evt-sl-1")
    client.post("/slack/interactivity", content=body,
                headers={**_sign(body), **CT_FORM})
    events = client.get("/events").json()
    ev = next(e for e in events if e["event"]["event_id"] == "evt-sl-1")
    assert ev["verdict"]["verdict"] == "correct"
    assert ev["verdict"]["actor_surface"] == "slack"


# --- thread correction dialogue (task 7, Req 3) ---

class _FakeSink:
    def __init__(self) -> None:
        self.threads: list[str] = []
        self.updates: list[tuple[str, str]] = []

    def enabled(self) -> bool:
        return True

    async def post_thread(self, rec, text: str) -> None:
        self.threads.append(text)

    async def update_card(self, rec, verdict: str, actor) -> None:
        self.updates.append((verdict, actor.user_id))


def _seed_notification(event_id: str = "evt-sl-1", ts: str = "111.222") -> None:
    import notif_store
    from chokotei_shared import NotificationRecord
    notif_store.save(NotificationRecord(event_id=event_id, channel_id="C1",
                                        message_ts=ts, posted_at=1.0))


def test_wrong_button_opens_thread_dialogue(client, monkeypatch) -> None:
    """Req 3.1: 「違う」 opens the correction dialogue in the card's thread."""
    import asyncio as aio
    from chokotei_shared import Actor
    _seed_event()
    _seed_notification()
    sink = _FakeSink()
    monkeypatch.setattr(server.state, "sink", sink)
    calls = []
    async def fake_elicit(ctx, message, user_id="line-op"):
        calls.append((ctx, message, user_id))
        return {"reply": "現場では何が原因と見ていますか？", "recorded": False, "cause": None}
    monkeypatch.setattr(server, "elicit_correction", fake_elicit)
    aio.run(server._slack_on_wrong("evt-sl-1", Actor(surface="slack", user_id="U777")))
    assert calls and calls[0][1] == "" and calls[0][2] == "U777"
    assert sink.threads and "原因" in sink.threads[0]


def test_thread_reply_reaches_agent_and_summary_on_record(client, monkeypatch) -> None:
    """Req 3.2/3.3/3.4: a reply drives one turn; recording posts the summary."""
    import asyncio as aio
    _seed_event()
    _seed_notification()
    sink = _FakeSink()
    monkeypatch.setattr(server.state, "sink", sink)
    async def fake_elicit(ctx, message, user_id="line-op"):
        assert ctx.get("actor", {}).get("user_id") == "U777"   # attribution flows
        return {"reply": "記録しました", "recorded": True,
                "cause": "ガイドレール固定ボルトの緩み"}
    monkeypatch.setattr(server, "elicit_correction", fake_elicit)
    aio.run(server._slack_on_message({
        "type": "message", "thread_ts": "111.222", "user": "U777",
        "text": "ボルトが緩んでいた"}))
    assert any("ガイドレール固定ボルトの緩み" in t for t in sink.threads), \
        "confirmed cause is echoed into the thread"
    assert sink.updates and sink.updates[0][0] == "wrong", "card reflects the correction"


def test_unrelated_thread_replies_are_ignored(client, monkeypatch) -> None:
    """Req 3.1: only replies to our notification cards reach the agent."""
    import asyncio as aio
    _seed_event()
    _seed_notification(ts="111.222")
    called = []
    async def fake_elicit(ctx, message, user_id="line-op"):
        called.append(1)
        return {"reply": "x", "recorded": False, "cause": None}
    monkeypatch.setattr(server, "elicit_correction", fake_elicit)
    aio.run(server._slack_on_message({
        "type": "message", "thread_ts": "999.999", "user": "U777", "text": "hi"}))
    aio.run(server._slack_on_message({
        "type": "message", "user": "U777", "text": "not in a thread"}))
    assert not called


def test_thread_photo_is_stored_for_the_correction(client, monkeypatch) -> None:
    """Req 9.1/9.3: an image in the thread reply becomes the pending photo."""
    import asyncio as aio
    import attachments_store
    _seed_event()
    _seed_notification()
    sink = _FakeSink()
    monkeypatch.setattr(server.state, "sink", sink)
    monkeypatch.setattr(server, "_download_slack_file",
                        lambda url: b"\xff\xd8\xff\xe0fakejpg")
    async def fake_elicit(ctx, message, user_id="line-op"):
        assert "写真" in message  # empty text + file -> synthesized message
        return {"reply": "どの部品の写真ですか？", "recorded": False, "cause": None}
    monkeypatch.setattr(server, "elicit_correction", fake_elicit)
    aio.run(server._slack_on_message({
        "type": "message", "thread_ts": "111.222", "user": "U777", "text": "",
        "files": [{"url_private": "https://files.slack/x.jpg",
                   "mimetype": "image/jpeg", "size": 1234}]}))
    uri = attachments_store.uri_for("evt-sl-1")
    assert uri and attachments_store.get_bytes(uri) == b"\xff\xd8\xff\xe0fakejpg"
    assert any("写真を受け取りました" in t for _, t in
               [(None, x) for x in sink.threads]) or any("写真" in t for t in sink.threads)


def test_inbound_disabled_without_secret(client, monkeypatch) -> None:
    """Req 10.6: without SLACK_SIGNING_SECRET the inbound surface refuses."""
    monkeypatch.delenv("SLACK_SIGNING_SECRET")
    body = _interactivity_body("verdict_correct", "evt-sl-1")
    r = client.post("/slack/interactivity", content=body,
                    headers={**_sign(body), **CT_FORM})
    assert r.status_code == 503
