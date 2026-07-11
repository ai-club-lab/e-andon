"""Offline UI tests for the dashboard API — chat / feedback / metrics (Req 6-9).

No GCP credentials or Cloud SQL required: agent calls are monkeypatched and
the JSONL stores are redirected to a temp dir before ``server`` is imported.
Run: PYTHONPATH=services/dashboard:services/agent:services/detector \
     python -m pytest -q services/dashboard/test_server.py
"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

_TMP = tempfile.mkdtemp(prefix="eandon-ui-test-")
for _key, _name in (("FEEDBACK_STORE", "feedback.jsonl"),
                    ("CASES_STORE", "cases.jsonl"),
                    ("IOT_STORE", "iot.jsonl")):
    os.environ[_key] = os.path.join(_TMP, _name)

import server  # noqa: E402  — stores resolve their paths at import time


def _seed_event(event_id: str = "evt-ui-1") -> None:
    rca = {"event_id": event_id, "cause_candidates": ["送り機構の速度低下"],
           "confidence": 0.9, "evidence": ["belt_speed 低下"]}
    server.state.events[event_id] = {
        "event": {"event_id": event_id, "kind": "offset", "peak_magnitude": 16.0,
                  "started_ts": 8.5, "ended_ts": 9.5, "rep_frame_uri": "",
                  "status": "closed"},
        "rca": rca,
    }
    server.state.rca_cache["offset:16"] = rca


@pytest.fixture()
def client():
    server.state.events.clear()
    server.state.rca_cache.clear()
    server._RATE.clear()
    if os.path.exists(os.environ["FEEDBACK_STORE"]):
        os.remove(os.environ["FEEDBACK_STORE"])
    with TestClient(server.app) as c:
        yield c


def test_should_reject_feedback_when_event_is_unknown(client) -> None:
    r = client.post("/feedback", json={"event_id": "nope", "verdict": "correct"})
    assert r.json() == {"ok": False, "error": "invalid event_id or verdict"}


def test_should_reject_feedback_when_verdict_is_invalid(client) -> None:
    _seed_event()
    r = client.post("/feedback", json={"event_id": "evt-ui-1", "verdict": "maybe"})
    assert r.json()["ok"] is False


def test_should_require_corrected_cause_when_verdict_is_wrong(client) -> None:
    _seed_event()
    r = client.post("/feedback", json={"event_id": "evt-ui-1", "verdict": "wrong"})
    assert r.json() == {"ok": False, "error": "human_cause required when wrong"}


def test_should_reflux_correction_and_invalidate_cache_when_wrong(client, monkeypatch) -> None:
    """The HITL loop: a correction becomes a searchable past case and the
    cached RCA for that anomaly signature is dropped (Req 8, 9)."""
    _seed_event()
    added = []
    monkeypatch.setattr(server.pc, "add", added.append)
    r = client.post("/feedback", json={
        "event_id": "evt-ui-1", "verdict": "wrong",
        "human_cause": "搬送ガイドレール固定ボルトの緩み"})
    body = r.json()
    assert body["ok"] is True
    assert body["metrics"]["wrong"] == 1
    assert len(added) == 1
    assert added[0].correct_cause == "搬送ガイドレール固定ボルトの緩み"
    assert "offset" in added[0].summary
    assert "offset:16" not in server.state.rca_cache, "recurrence must re-infer"


def test_should_track_correct_rate_when_verdicts_accumulate(client) -> None:
    _seed_event()
    client.post("/feedback", json={"event_id": "evt-ui-1", "verdict": "correct"})
    m = client.get("/metrics").json()
    assert (m["total"], m["correct"], m["wrong"]) == (1, 1, 0)
    assert m["correct_rate"] == 1.0


def test_should_prompt_for_input_when_chat_message_is_empty(client) -> None:
    r = client.post("/chat", json={"message": "  "})
    assert r.json()["reply"] == "質問を入力してください。"


def test_should_return_agent_reply_when_chat_message_is_given(client, monkeypatch) -> None:
    async def fake_answer(message: str, user_id: str = "line-op") -> str:
        return f"echo:{message}:{user_id}"
    monkeypatch.setattr(server, "answer_query", fake_answer)
    r = client.post("/chat", json={"message": "電流は正常?", "user_id": "op-abc123"})
    assert r.json()["reply"] == "echo:電流は正常?:op-abc123"


def test_should_rate_limit_chat_when_requests_flood(client, monkeypatch) -> None:
    async def fake_answer(message: str, user_id: str = "line-op") -> str:
        return "ok"
    monkeypatch.setattr(server, "answer_query", fake_answer)
    replies = [client.post("/chat", json={"message": "x"}).json()["reply"]
               for _ in range(21)]
    assert replies[0] == "ok"
    assert "リクエストが多すぎます" in replies[-1]


def test_should_cap_message_and_cause_lengths(client, monkeypatch) -> None:
    """Abuse guard for the public demo URL (docs/audit.md 脅威モデル)."""
    _seed_event()
    seen = {}
    async def fake_answer(message: str, user_id: str = "line-op") -> str:
        seen["len"] = len(message)
        return "ok"
    monkeypatch.setattr(server, "answer_query", fake_answer)
    client.post("/chat", json={"message": "あ" * 2000})
    assert seen["len"] == server.MAX_MESSAGE_LEN
    added = []
    monkeypatch.setattr(server.pc, "add", added.append)
    client.post("/feedback", json={"event_id": "evt-ui-1", "verdict": "wrong",
                                   "human_cause": "x" * 2000})
    assert len(added[0].correct_cause) == server.MAX_CAUSE_LEN


# --- conversational HITL correction (/correct + record_correction) ---

def test_should_open_correction_dialogue_when_event_exists(client, monkeypatch) -> None:
    _seed_event()
    async def fake_elicit(ctx, message, user_id="line-op"):
        return {"reply": f"現場では何が原因でしたか?（{ctx['kind']}）", "recorded": False, "cause": None}
    monkeypatch.setattr(server, "elicit_correction", fake_elicit)
    r = client.post("/correct", json={"event_id": "evt-ui-1", "message": "", "user_id": "op-1"}).json()
    assert r["reply"] == "現場では何が原因でしたか?（offset）"
    assert r["recorded"] is False
    assert r["suggestions"] == server._cause_suggestions("offset")   # contextual reply chips


def test_should_report_metrics_when_correction_is_recorded(client, monkeypatch) -> None:
    _seed_event()
    async def fake_elicit(ctx, message, user_id="line-op"):
        return {"reply": "記録しました。次回は最優先で提示します。", "recorded": True, "cause": "ボルト緩み"}
    monkeypatch.setattr(server, "elicit_correction", fake_elicit)
    r = client.post("/correct", json={"event_id": "evt-ui-1", "message": "ボルトが緩んでた"}).json()
    assert r["recorded"] is True
    assert "metrics" in r and r["cause"] == "ボルト緩み"


def test_should_reject_correction_when_event_is_unknown(client) -> None:
    r = client.post("/correct", json={"event_id": "nope", "message": "x"}).json()
    assert r["recorded"] is False and "見つかりません" in r["reply"]


def test_should_reflux_via_record_correction_tool(client, monkeypatch) -> None:
    """The agent's record_correction tool must persist through the same audited
    reflux path — event target is server-bound, LLM supplies only the cause."""
    import tools
    _seed_event()
    added = []
    monkeypatch.setattr(server.pc, "add", added.append)
    holder = {"event": {"event_id": "evt-ui-1"}, "recorded": False, "cause": None}
    tok = tools._active_correction.set(holder)
    try:
        res = tools.record_correction("ガイドレール固定ボルトの緩み", "カバーを開けたら緩んでいた")
    finally:
        tools._active_correction.reset(tok)
    assert res["recorded"] is True and holder["recorded"] is True
    assert added and added[0].correct_cause == "ガイドレール固定ボルトの緩み"
    assert "補足" in added[0].summary          # evidence_note folded into the case
    assert "offset:16" not in server.state.rca_cache, "recurrence must re-infer"


# --- single verdict path with actor attribution (andon-human-loop Req 2, 4) ---

def test_should_record_actor_when_dashboard_verdict_lands(client) -> None:
    """EARS 4.2: dashboard verdicts carry surface='dashboard' into the audit store."""
    import feedback_store
    _seed_event()
    r = client.post("/feedback", json={"event_id": "evt-ui-1", "verdict": "correct",
                                       "user_id": "op-7"}).json()
    assert r["ok"] is True
    rows = feedback_store.load()
    assert rows[-1]["actor_surface"] == "dashboard"
    assert rows[-1]["actor_id"] == "op-7"


def test_should_not_double_record_when_event_already_adjudicated(client) -> None:
    """EARS 2.4: a second verdict is not recorded; who/when/what is returned."""
    client_ip_reset = None  # noqa: F841 — readability only
    _seed_event()
    first = client.post("/feedback", json={"event_id": "evt-ui-1", "verdict": "correct",
                                           "user_id": "op-7"}).json()
    assert first["ok"] is True and first["metrics"]["total"] == 1
    second = client.post("/feedback", json={
        "event_id": "evt-ui-1", "verdict": "wrong", "human_cause": "ボルト緩み"}).json()
    assert second["already_adjudicated"] is True
    assert second["prior"]["verdict"] == "correct"
    assert second["prior"]["actor_id"] == "op-7"
    assert second["prior"]["at"]
    import feedback_store
    assert len(feedback_store.load()) == 1, "no duplicate record"


def test_should_share_one_write_path_across_surfaces(client) -> None:
    """EARS 2.2: a Slack-surface verdict lands in the same store, same shape."""
    import feedback_store
    from chokotei_shared import Actor
    _seed_event()
    out = server._record_verdict("evt-ui-1", "correct",
                                 Actor(surface="slack", user_id="U123",
                                       display_name="Suzuki"))
    assert out["ok"] is True
    row = feedback_store.load()[-1]
    assert (row["actor_surface"], row["actor_id"], row["actor_name"]) == \
        ("slack", "U123", "Suzuki")


def test_correct_dialogue_refuses_after_adjudication(client, monkeypatch) -> None:
    """Req 2.4 across surfaces: once adjudicated, /correct won't open a dialogue."""
    _seed_event()
    client.post("/feedback", json={"event_id": "evt-ui-1", "verdict": "correct",
                                   "user_id": "op-7"})
    called = []
    async def fake_elicit(ctx, message, user_id="line-op"):
        called.append(1)
        return {"reply": "x", "recorded": False, "cause": None}
    monkeypatch.setattr(server, "elicit_correction", fake_elicit)
    r = client.post("/correct", json={"event_id": "evt-ui-1", "message": ""}).json()
    assert r["recorded"] is False and "裁定済み" in r["reply"]
    assert not called


def test_notification_throttled_per_signature(client, monkeypatch) -> None:
    """Unique event IDs must not spam Slack: same anomaly signature posts at
    most once per window (deterministic alert-fatigue suppression)."""
    import asyncio as aio
    posted = []

    class CountingSink:
        def enabled(self) -> bool:
            return True

        async def post_card(self, ev, rca, routing, deep_link, frame_url=""):
            posted.append(ev.event_id)
            from chokotei_shared import NotificationRecord
            return NotificationRecord(event_id=ev.event_id, channel_id="C1",
                                      message_ts=f"{len(posted)}.0", posted_at=1.0)

    monkeypatch.setattr(server.state, "sink", CountingSink())
    scheduled = []
    async def fake_schedule(decision):
        scheduled.append(decision.event_id)
    monkeypatch.setattr(server.state.engine, "schedule", fake_schedule)

    def rca_d(eid):
        return {"event_id": eid, "cause_candidates": ["c"], "confidence": 0.8,
                "evidence": [], "category": "positioning"}

    def ev(eid):
        from chokotei_shared import AnomalyEvent
        return AnomalyEvent(event_id=eid, started_ts=5.8, kind="offset",
                            peak_magnitude=16.0, rep_frame_uri="", status="open")

    server.state.notif_sig_ts.clear()
    aio.run(server._post_card(ev("evt-1-a"), rca_d("evt-1-a")))
    aio.run(server._post_card(ev("evt-1-b"), rca_d("evt-1-b")))  # same signature
    assert posted == ["evt-1-a"], "second playthrough within the window is suppressed"
    assert scheduled == ["evt-1-a"], "no escalation timers for suppressed cards"
    server.state.notif_sig_ts["offset:16"] -= 10_000  # window elapsed
    aio.run(server._post_card(ev("evt-1-c"), rca_d("evt-1-c")))
    assert posted == ["evt-1-a", "evt-1-c"]


# --- mobile adjudication page (andon-human-loop task 8, Req 8) ---

def test_event_page_serves_mobile_first_html(client) -> None:
    """Req 8.1: the deep-link target renders standalone, mobile-first."""
    _seed_event()
    r = client.get("/e/evt-ui-1")
    assert r.status_code == 200
    html = r.text
    assert 'name="viewport"' in html            # mobile rendering
    assert "verdict" in html or "裁定" in html   # adjudication controls present


def test_event_api_returns_adjudication_material(client) -> None:
    """Req 8.3: one fetch carries cause/confidence/evidence/verdict state."""
    _seed_event()
    r = client.get("/api/event/evt-ui-1").json()
    assert r["event"]["event_id"] == "evt-ui-1"
    assert r["rca"]["cause_candidates"] == ["送り機構の速度低下"]
    assert r["verdict"] is None
    client.post("/feedback", json={"event_id": "evt-ui-1", "verdict": "correct",
                                   "user_id": "op-9"})
    r2 = client.get("/api/event/evt-ui-1").json()
    assert r2["verdict"]["verdict"] == "correct" and r2["verdict"]["actor_id"] == "op-9"


def test_event_api_404s_unknown_event(client) -> None:
    r = client.get("/api/event/nope")
    assert r.status_code == 404


def test_should_refuse_record_correction_without_active_session(client) -> None:
    import tools
    assert tools.record_correction("何か")["recorded"] is False        # no active event bound
    holder = {"event": {"event_id": "evt-ui-1"}, "recorded": False, "cause": None}
    tok = tools._active_correction.set(holder)
    try:
        assert tools.record_correction("x")["recorded"] is False       # cause too short
    finally:
        tools._active_correction.reset(tok)
