"""Unified dashboard service (design.md §2 co-located P1, Req 6/7).

Streams annotated frames, tracks anomaly events, auto-runs RCA inference on
each new event and pushes a chat notification, and answers free-form operator
queries over the logs. Reuses detector + agent modules (PYTHONPATH includes
both service dirs). Local stores only — no Cloud SQL required to run.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os

import cv2
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

import event_store
import feedback_store
import frames_store
import iot_store
import past_cases as pc
from fastapi.responses import Response
from chokotei_shared import DETECTION, AnomalyEvent, FeedbackCase, RcaResult, db
from detection import detect_frame
from rca_agent import answer_query, infer
from render import annotate, to_jpeg
from tracking import EventTracker
from vision_confirm import confirm_with_gemini

VIDEO = os.environ.get("SAMPLE_VIDEO", "video/factory_01.mov")
app = FastAPI(title="chokotei-dashboard")


class _State:
    def __init__(self) -> None:
        self.tracker = EventTracker()
        self.events: dict[str, dict] = {}          # event_id -> {event, rca}
        self.notifs: asyncio.Queue[dict] = asyncio.Queue()
        self.feedback: list[dict] = []
        self.rca_cache: dict[str, dict] = {}       # signature -> rca dict
        self.infer_lock = asyncio.Lock()           # serialize Vertex calls (avoid 429)


state = _State()


def _sig(ev: AnomalyEvent) -> str:
    """Signature so a recurring physical anomaly reuses its RCA (dedupe)."""
    return f"{ev.kind}:{round(ev.peak_magnitude)}"


async def _infer_and_notify(ev: AnomalyEvent) -> None:
    """Run (or reuse) RCA for an event and push a chat notification (Req 5, 6.1).

    Inferences are serialized and de-duplicated by signature: the looping demo
    re-detects the same anomaly each pass, so we infer once and reuse — this
    both matches reality and avoids Vertex rate limits (de-risk #4).
    """
    sig = _sig(ev)
    try:
        async with state.infer_lock:
            if sig in state.rca_cache:
                rca_d = state.rca_cache[sig]
            else:
                rca_d = (await infer(ev)).model_dump()
                state.rca_cache[sig] = rca_d
        state.events[ev.event_id]["rca"] = rca_d
        event_store.save_rca(RcaResult(
            event_id=ev.event_id, cause_candidates=rca_d["cause_candidates"],
            confidence=rca_d["confidence"], evidence=rca_d["evidence"]))
        text = (
            f"⚠ 異常 {ev.event_id}（{ev.kind}, ピーク{ev.peak_magnitude:.1f}）"
            f" 推定原因: {', '.join(rca_d['cause_candidates'])}"
            f"（確信度 {rca_d['confidence']:.0%}）根拠: {'; '.join(rca_d['evidence'][:2])}"
        )
    except Exception as exc:  # no silent fallback (Req 5.6)
        text = f"⚠ 異常 {ev.event_id}: 原因推定に失敗しました（{type(exc).__name__}）"
    await state.notifs.put({"event_id": ev.event_id, "text": text})


async def _store_frame(event_id: str, jpg: bytes) -> None:
    """Upload the representative frame to Cloud Storage and record its URI (Req 3.4)."""
    uri = await asyncio.to_thread(frames_store.upload_frame, event_id, jpg)
    if uri and db.enabled():
        await asyncio.to_thread(
            db.execute, "UPDATE anomaly_events SET rep_frame_uri=%s WHERE event_id=%s",
            (uri, event_id))


async def _frames():
    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    step = max(1, round(fps / DETECTION.sample_fps))
    period = step / fps
    fi = -1
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                fi = -1
                continue
            fi += 1
            if fi % step != 0:
                continue
            fr = detect_frame(frame, fi, fi / fps)
            annotated_jpg = to_jpeg(annotate(frame, fr))
            for ev in state.tracker.update(fr):
                # Stage 2 (Req 2.5): borderline-band offsets get a Gemini yes/no
                # confirmation before being treated as anomalies. Clear anomalies
                # (peak above the band) skip it. Rare -> off the hot path.
                if ev.kind == "offset" and DETECTION.band_low <= ev.peak_magnitude <= DETECTION.band_high:
                    cand = next((f for f in fr.flags if f.kind == "offset"
                                 and abs(f.magnitude - ev.peak_magnitude) < 0.6), None)
                    if cand is not None and not await asyncio.to_thread(
                            confirm_with_gemini, frame, cand.cx, cand.cy):
                        continue  # Gemini judged it aligned -> suppress
                state.events[ev.event_id] = {"event": ev.model_dump(), "rca": None}
                event_store.save_event(ev)
                if frames_store.enabled():
                    asyncio.create_task(_store_frame(ev.event_id, annotated_jpg))
                asyncio.create_task(_infer_and_notify(ev))
            notes = []
            while not state.notifs.empty():
                notes.append(state.notifs.get_nowait())
            yield {"event": "frame", "data": json.dumps({
                "frame_index": fr.frame_index, "ts": round(fr.ts, 3),
                "n_parts": len(fr.parts),
                "flags": [f.model_dump() for f in fr.flags],
                "notifications": notes,
                "image": base64.b64encode(annotated_jpg).decode("ascii"),
            })}
            await asyncio.sleep(period)
    finally:
        cap.release()


@app.get("/stream")
async def stream() -> EventSourceResponse:
    return EventSourceResponse(_frames())


@app.get("/events")
async def events() -> list[dict]:
    if db.enabled():
        return event_store.list_events()
    return list(state.events.values())


@app.get("/iot")
async def iot(channel: str, t0: float = 0.0, t1: float = 10.0) -> dict:
    rows = iot_store.query(channel, t0, t1)  # type: ignore[arg-type]
    if not rows:
        return {"channel": channel, "found": False, "points": []}
    pts = [[round(r.ts, 3), r.value] for r in rows[::5]]  # downsample
    return {"channel": channel, "found": True, "points": pts}


@app.post("/chat")
async def chat(req: Request) -> dict:
    body = await req.json()
    message = (body or {}).get("message", "").strip()
    if not message:
        return {"reply": "質問を入力してください。"}
    reply = await answer_query(message)
    return {"reply": reply}


@app.post("/feedback")
async def feedback(req: Request) -> dict:
    """Record a human verdict; reflux corrections into past cases (Req 8, 9)."""
    body = await req.json() or {}
    event_id = body.get("event_id", "")
    verdict = body.get("verdict")
    human_cause = (body.get("human_cause") or "").strip()
    rec = event_store.get_event(event_id) if db.enabled() else state.events.get(event_id)
    if not rec or verdict not in ("correct", "wrong"):
        return {"ok": False, "error": "invalid event_id or verdict"}
    if verdict == "wrong" and not human_cause:
        return {"ok": False, "error": "human_cause required when wrong"}

    ai = rec.get("rca") or {}
    feedback_store.save({
        "event_id": event_id, "verdict": verdict,
        "ai_cause": ai.get("cause_candidates", []), "human_cause": human_cause or None,
        "kind": rec["event"]["kind"], "peak": rec["event"]["peak_magnitude"],
    })
    if verdict == "wrong":
        ev = rec["event"]
        # reflux: make the corrected case searchable + let next occurrence re-infer
        summary = f"{ev['kind']}異常 vibration_x逸脱 ピーク{ev['peak_magnitude']:.0f} {human_cause}"
        pc.add(FeedbackCase(summary=summary, correct_cause=human_cause, source_event_id=event_id))
        state.rca_cache.pop(f"{ev['kind']}:{round(ev['peak_magnitude'])}", None)
    return {"ok": True, "metrics": feedback_store.metrics()}


@app.get("/metrics")
async def metrics() -> dict:
    return feedback_store.metrics()


@app.get("/frame/{event_id}")
async def frame_img(event_id: str) -> Response:
    """Proxy the stored representative frame (bucket stays private, Req 3.4)."""
    data = await asyncio.to_thread(frames_store.get_frame_bytes, event_id)
    if data is None:
        return Response(status_code=404)
    return Response(content=data, media_type="image/jpeg")


@app.on_event("startup")
async def _seed_iot() -> None:
    """Ensure deterministic IoT seed exists per instance (ephemeral fs)."""
    if not iot_store.STORE.exists():
        iot_store.persist(iot_store.generate())


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "video_present": os.path.exists(VIDEO),
            "session_db": bool(__import__("chokotei_shared").GCP.session_db_url),
            "events": len(state.events)}


_STATIC = os.path.join(os.path.dirname(__file__), "static", "index.html")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    with open(_STATIC, encoding="utf-8") as fh:
        return fh.read()
