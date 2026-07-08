"""Field-photo attachment tests (andon-human-loop task 9, Req 9).

Local backend only (tmp dir) — no GCS, no Slack, no model. Covers validation
(type/size), store roundtrip, the upload endpoint, and the link into
past_cases at correction commit.
Run: PYTHONPATH=services/dashboard:services/agent:services/detector \
     python -m pytest -q services/dashboard/test_attachments.py
"""
from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="eandon-attach-test-")
os.environ["ATTACH_DIR"] = os.path.join(_TMP, "attachments")
for _key, _name in (("FEEDBACK_STORE", "feedback.jsonl"),
                    ("CASES_STORE", "cases.jsonl"),
                    ("IOT_STORE", "iot.jsonl"),
                    ("NOTIF_STORE", "notifications.jsonl"),
                    ("ESC_STORE", "escalations.jsonl")):
    os.environ.setdefault(_key, os.path.join(_TMP, _name))

import attachments_store  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64          # minimal png-magic payload
JPG = b"\xff\xd8\xff\xe0" + b"0" * 64


def _seed_event(event_id: str = "evt-at-1") -> None:
    server.state.events[event_id] = {
        "event": {"event_id": event_id, "kind": "offset", "peak_magnitude": 16.0,
                  "started_ts": 8.5, "ended_ts": 9.5, "rep_frame_uri": "",
                  "status": "closed"},
        "rca": {"event_id": event_id, "cause_candidates": ["c"], "confidence": 0.8,
                "evidence": [], "category": "positioning"},
    }


@pytest.fixture()
def client():
    server.state.events.clear()
    server._RATE.clear()
    for key in ("FEEDBACK_STORE",):
        if os.path.exists(os.environ[key]):
            os.remove(os.environ[key])
    import shutil
    shutil.rmtree(os.environ["ATTACH_DIR"], ignore_errors=True)
    with TestClient(server.app) as c:
        yield c


def test_validate_rejects_non_image_and_oversize() -> None:
    """Req 9.5: image/* only, 10MB cap — refused with a reason."""
    assert attachments_store.validate("text/plain", 10) is not None
    assert attachments_store.validate("image/jpeg", 11 * 1024 * 1024) is not None
    assert attachments_store.validate("image/png", 1024) is None


def test_store_roundtrip_local() -> None:
    uri = attachments_store.save_pending("evt-x", JPG, "image/jpeg")
    assert uri
    assert attachments_store.uri_for("evt-x") == uri
    assert attachments_store.get_bytes(uri) == JPG
    assert attachments_store.uri_for("evt-none") is None


def test_upload_endpoint_accepts_photo_and_serves_proxy(client) -> None:
    """Req 9.1/9.6: upload lands in the private store; served only by proxy."""
    _seed_event()
    r = client.post("/correct/attachment?event_id=evt-at-1",
                    files={"file": ("genba.png", PNG, "image/png")})
    body = r.json()
    assert body["ok"] is True
    p = client.get("/attachment/evt-at-1")
    assert p.status_code == 200 and p.content == PNG


def test_upload_endpoint_refuses_bad_type(client) -> None:
    _seed_event()
    r = client.post("/correct/attachment?event_id=evt-at-1",
                    files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.json()["ok"] is False
    assert client.get("/attachment/evt-at-1").status_code == 404


def test_correction_commit_links_attachment_to_past_case(client, monkeypatch) -> None:
    """Req 9.2: the pending photo rides the correction into past_cases."""
    _seed_event()
    client.post("/correct/attachment?event_id=evt-at-1",
                files={"file": ("genba.jpg", JPG, "image/jpeg")})
    added = []
    monkeypatch.setattr(server.pc, "add", added.append)
    r = client.post("/feedback", json={"event_id": "evt-at-1", "verdict": "wrong",
                                       "human_cause": "ボルト緩み"}).json()
    assert r["ok"] is True
    assert added and added[0].attachment_uri
    assert attachments_store.get_bytes(added[0].attachment_uri) == JPG


def test_correction_without_photo_still_completes(client, monkeypatch) -> None:
    """Req 9.4: the photo is optional — no attachment, same flow."""
    _seed_event()
    added = []
    monkeypatch.setattr(server.pc, "add", added.append)
    r = client.post("/feedback", json={"event_id": "evt-at-1", "verdict": "wrong",
                                       "human_cause": "ボルト緩み"}).json()
    assert r["ok"] is True
    assert added and added[0].attachment_uri is None
