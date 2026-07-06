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
    async def fake_answer(message: str) -> str:
        return f"echo:{message}"
    monkeypatch.setattr(server, "answer_query", fake_answer)
    r = client.post("/chat", json={"message": "電流は正常?"})
    assert r.json()["reply"] == "echo:電流は正常?"
