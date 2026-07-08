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
import logging
import os
import re
import time

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
from chokotei_shared import DETECTION, AnomalyEvent, FeedbackCase, RcaResult, db, obs
from detection import detect_frame
from rca_agent import answer_query, elicit_correction, infer, set_correction_recorder
from render import annotate, to_jpeg
from tracking import EventTracker
from vision_confirm import confirm_with_gemini

VIDEO = os.environ.get("SAMPLE_VIDEO", "video/factory_01.mov")
obs.setup_logging()
logger = logging.getLogger("dashboard")
app = FastAPI(title="e-Andon — AIアンドン")


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


async def _infer(ev: AnomalyEvent) -> None:
    """Run (or reuse) RCA for an event and store it — no notification yet.

    Started when the misalignment is first detected; the notification is held
    until the belt actually stops (see _notify_stop). Inferences are serialized
    and de-duplicated by signature (de-risk #4).
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
    except Exception:  # no silent fallback (Req 5.6)
        logger.exception("RCA failed", extra={"ctx": {"event_id": ev.event_id}})


async def _notify_stop(ev: AnomalyEvent, notifs: "asyncio.Queue") -> None:
    """When the belt stops, post the agent's story message (Req 5, 6.1).

    Reads: misalignment detected -> belt stopped -> here is the likely cause.
    """
    rca_d = None
    for _ in range(30):  # RCA started at detection; wait a little if still running
        rca_d = state.events.get(ev.event_id, {}).get("rca")
        if rca_d:
            break
        await asyncio.sleep(0.3)
    if rca_d:
        cause = "、".join(rca_d["cause_candidates"][:2])
        text = (f"⚠ カメラで部品の整列異常を検知し、ラインを停止しました。確認をお願いします。"
                f"各機械センサー（速度・電流・振動・温度・エア圧）は正常のため、"
                f"センサーに現れない位置決め機構側の問題と考えられます。"
                f"真因は「{cause}」と推定されます（確信度 {rca_d['confidence']:.0%}）。"
                f"根拠: {'; '.join(rca_d['evidence'][:2])}")
    else:
        text = "⚠ カメラで部品の整列異常を検知し、ラインを停止しました。確認をお願いします。（真因を推定中です）"
    await notifs.put({"event_id": ev.event_id, "text": text})


async def _store_frame(event_id: str, jpg: bytes) -> None:
    """Upload the representative frame to Cloud Storage and record its URI (Req 3.4)."""
    uri = await asyncio.to_thread(frames_store.upload_frame, event_id, jpg)
    if uri and db.enabled():
        await asyncio.to_thread(
            db.execute, "UPDATE anomaly_events SET rep_frame_uri=%s WHERE event_id=%s",
            (uri, event_id))


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return 0.0 if n == 0 else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def _vision_metrics(fr) -> dict:
    """Per-frame geometric quality from the camera: alignment offset (px),
    angle deviation (deg), pitch uniformity (max gap ratio deviation)."""
    parts = fr.parts
    if not parts:
        return {"offset": 0.0, "angle": 0.0, "gap": 0.0}
    base_y = fr.baseline_y or _median([p.cy for p in parts])
    base_a = fr.median_angle
    offset = max(abs(p.cy - base_y) for p in parts)
    angle = max(abs(p.angle - base_a) for p in parts)
    cxs = sorted(p.cx for p in parts)
    gaps = [cxs[i + 1] - cxs[i] for i in range(len(cxs) - 1)]
    med = _median(gaps)
    gap = max(abs(g / med - 1.0) for g in gaps) if gaps and med else 0.0
    return {"offset": round(offset, 1), "angle": round(angle, 1), "gap": round(gap, 2)}


async def _frames():
    # per-connection tracker + notifications: each viewing starts fresh (parts
    # flow, then an anomaly happens ~6s in) and only THIS stream's anomalies
    # notify — no global-queue leakage from other sessions/async inferences.
    tracker = EventTracker()
    notifs: asyncio.Queue = asyncio.Queue()
    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    step = max(1, round(fps / DETECTION.sample_fps))
    period = step / fps
    fi = -1
    pending_ev = None      # the misalignment event seen this playthrough
    stop_notified = False  # story notification fired for the stop
    last_jpg = last_n = None
    last_metrics = {"offset": 0.0, "angle": 0.0, "gap": 0.0}
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                # play once, then hold on the stopped line indefinitely — the
                # story ends stopped (choko-tei). The line restarts physically
                # (an operator button on the machine), not from this dashboard;
                # a fresh page load is naturally a new real-time recording.
                while last_jpg is not None:
                    held = []
                    while not notifs.empty():
                        held.append(notifs.get_nowait())
                    yield {"event": "frame", "data": json.dumps({
                        "frame_index": -1, "ts": round(iot_store.STOP_TS, 3),
                        "n_parts": last_n or 0, "line_status": "stopped",
                        "flags": [], "notifications": held, "image": last_jpg,
                        "metrics": last_metrics})}
                    await asyncio.sleep(1.0)
                break
            fi += 1
            if fi % step != 0:
                continue
            ts = fi / fps
            fr = detect_frame(frame, fi, ts)
            annotated_jpg = to_jpeg(annotate(frame, fr))
            img_b64 = base64.b64encode(annotated_jpg).decode("ascii")
            last_jpg, last_n = img_b64, len(fr.parts)
            last_metrics = _vision_metrics(fr)
            for ev in tracker.update(fr):
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
                asyncio.create_task(_infer(ev))   # infer now, notify at the stop
                pending_ev = ev
            # line state machine: running -> warning (misalignment) -> stopped
            if ts >= iot_store.STOP_TS:
                line_status = "stopped"
                if pending_ev is not None and not stop_notified:
                    stop_notified = True
                    asyncio.create_task(_notify_stop(pending_ev, notifs))
            elif pending_ev is not None:
                line_status = "warning"
            else:
                line_status = "running"
            notes = []
            while not notifs.empty():
                notes.append(notifs.get_nowait())
            yield {"event": "frame", "data": json.dumps({
                "frame_index": fr.frame_index, "ts": round(fr.ts, 3),
                "n_parts": len(fr.parts), "line_status": line_status,
                "flags": [f.model_dump() for f in fr.flags],
                "notifications": notes, "image": img_b64,
                "metrics": last_metrics,
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


# Abuse guard for the public demo URL: unauthenticated /chat and /feedback
# feed an LLM and the RAG store, so cap sizes and rate-limit per client IP
# (single-instance in-memory window; enough for a demo deployment).
_RATE: dict[str, list[float]] = {}
MAX_MESSAGE_LEN = 500
MAX_CAUSE_LEN = 200


def _rate_ok(req: Request, limit: int = 20, window_s: float = 60.0) -> bool:
    fwd = req.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() or (req.client.host if req.client else "?")
    now = time.monotonic()
    q = _RATE.setdefault(ip, [])
    while q and now - q[0] > window_s:
        q.pop(0)
    if len(q) >= limit:
        return False
    q.append(now)
    return True


@app.post("/chat")
async def chat(req: Request) -> dict:
    body = await req.json()
    message = (body or {}).get("message", "").strip()[:MAX_MESSAGE_LEN]
    user_id = re.sub(r"[^\w-]", "", str((body or {}).get("user_id") or ""))[:40] or "line-op"
    if not message:
        return {"reply": "質問を入力してください。"}
    if not _rate_ok(req):
        return {"reply": "リクエストが多すぎます。1分ほど待ってから再度お試しください。"}
    try:
        reply = await answer_query(message, user_id=user_id)
    except Exception:  # surfaced to the user + logged — never a raw 500 (Req 5.6)
        logger.exception("chat failed", extra={"ctx": {"user_id": user_id}})
        return {"reply": "モデル呼び出しが混み合っています。数秒おいてもう一度お試しください。"}
    return {"reply": reply}


def _persist_correction(event_id: str, cause: str, evidence_note: str = "") -> dict:
    """Reflux a human correction into the asset store — the shared write behind
    both the /feedback 'wrong' path and the conversational /correct agent.

    Records the wrong verdict (audit + metrics), makes the corrected case
    searchable (past_cases), and drops the cached RCA for that anomaly signature
    so the next occurrence re-infers with the new knowledge (Req 8, 9).
    """
    rec = event_store.get_event(event_id) if db.enabled() else state.events.get(event_id)
    if not rec:
        return {"recorded": False, "reason": "unknown event"}
    ev = rec["event"]
    ai = rec.get("rca") or {}
    cause = cause.strip()[:MAX_CAUSE_LEN]
    note = (evidence_note or "").strip()[:MAX_CAUSE_LEN]
    feedback_store.save({
        "event_id": event_id, "verdict": "wrong",
        "ai_cause": ai.get("cause_candidates", []), "human_cause": cause,
        "kind": ev["kind"], "peak": ev["peak_magnitude"],
    })
    detail = f"（現場の補足: {note}）" if note else ""
    summary = f"映像で{ev['kind']}整列異常を検知・センサー正常・ライン停止 {cause}{detail}"
    pc.add(FeedbackCase(summary=summary, correct_cause=cause, source_event_id=event_id))
    state.rca_cache.pop(f"{ev['kind']}:{round(ev['peak_magnitude'])}", None)
    return {"recorded": True, "metrics": feedback_store.metrics()}


@app.post("/feedback")
async def feedback(req: Request) -> dict:
    """Record a human verdict; reflux corrections into past cases (Req 8, 9).

    The UI routes 'wrong' through the conversational /correct agent; this endpoint
    stays the deterministic path for 'correct' and keeps working for 'wrong' too.
    """
    if not _rate_ok(req):
        return {"ok": False, "error": "rate limited"}
    body = await req.json() or {}
    event_id = body.get("event_id", "")
    verdict = body.get("verdict")
    human_cause = (body.get("human_cause") or "").strip()[:MAX_CAUSE_LEN]
    rec = event_store.get_event(event_id) if db.enabled() else state.events.get(event_id)
    if not rec or verdict not in ("correct", "wrong"):
        return {"ok": False, "error": "invalid event_id or verdict"}
    if verdict == "wrong" and not human_cause:
        return {"ok": False, "error": "human_cause required when wrong"}

    if verdict == "wrong":
        _persist_correction(event_id, human_cause)
    else:
        ai = rec.get("rca") or {}
        feedback_store.save({
            "event_id": event_id, "verdict": "correct",
            "ai_cause": ai.get("cause_candidates", []), "human_cause": None,
            "kind": rec["event"]["kind"], "peak": rec["event"]["peak_magnitude"],
        })
    return {"ok": True, "metrics": feedback_store.metrics()}


# Plausible answers the operator might give, by anomaly kind — shown as reply
# chips during the correction dialogue (contextual, not the fixed log-query chips).
_CAUSE_SUGGESTIONS = {
    "offset": ["位置決め治具のガタ・摩耗", "ガイドレール固定ボルトの緩み", "送り機構の精度低下"],
    "rotation": ["位置決め治具のガタ・摩耗", "ワーク受けの傾き", "送り機構の精度低下"],
    "gap": ["送りインデックス機構のずれ", "搬送ベルト／チェーンの伸び", "ストッパ位置のずれ"],
}


def _cause_suggestions(kind: str) -> list[str]:
    return _CAUSE_SUGGESTIONS.get(kind, _CAUSE_SUGGESTIONS["offset"])


@app.post("/correct")
async def correct(req: Request) -> dict:
    """Conversational HITL correction: the agent elicits the operator's tacit
    knowledge and commits it via record_correction (Req 8, 9). One turn per call;
    ``recorded`` flips True on the turn the correction lands."""
    if not _rate_ok(req):
        return {"reply": "リクエストが多すぎます。少し待ってから再度お試しください。", "recorded": False}
    body = await req.json() or {}
    event_id = body.get("event_id", "")
    message = (body.get("message") or "").strip()[:MAX_MESSAGE_LEN]
    user_id = re.sub(r"[^\w-]", "", str(body.get("user_id") or ""))[:40] or "line-op"
    rec = event_store.get_event(event_id) if db.enabled() else state.events.get(event_id)
    if not rec:
        return {"reply": "対象の異常が見つかりません。", "recorded": False}
    ev = rec["event"]
    ai = rec.get("rca") or {}
    ctx = {"event_id": event_id, "kind": ev["kind"],
           "ai_cause": "、".join(ai.get("cause_candidates", [])[:2])}
    try:
        result = await elicit_correction(ctx, message, user_id=user_id)
    except Exception:  # surfaced + logged, never a raw 500 (Req 5.6)
        logger.exception("correction failed", extra={"ctx": {"event_id": event_id}})
        return {"reply": "モデル呼び出しが混み合っています。数秒おいて、もう一度お書きください。",
                "recorded": False}
    out = {"reply": result["reply"], "recorded": result["recorded"]}
    if result["recorded"]:
        out["metrics"] = feedback_store.metrics()
        out["cause"] = result.get("cause")
    else:
        out["suggestions"] = _cause_suggestions(ev["kind"])  # contextual reply chips
    return out


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
async def _wire_correction() -> None:
    """Let the correction agent's record_correction tool persist through the
    dashboard's shared reflux path (past_cases + audit + cache invalidation)."""
    set_correction_recorder(
        lambda ev_ctx, cause, note: _persist_correction(ev_ctx["event_id"], cause, note))


@app.on_event("startup")
async def _seed_iot() -> None:
    """Ensure deterministic IoT seed exists per instance (ephemeral fs)."""
    if not iot_store.STORE.exists():
        iot_store.persist(iot_store.generate())


@app.on_event("startup")
async def _restore_state() -> None:
    """Rehydrate events + RCA cache from Cloud SQL after a cold start.

    Keeps the story continuous across scale-to-zero restarts: past events
    stay feedback-able and a recurring anomaly reuses its stored RCA instead
    of a cold re-inference (faster first notification, fewer model calls).
    """
    if not db.enabled():
        return
    try:
        recs = await asyncio.to_thread(event_store.list_events, 50)
        for rec in recs:
            state.events.setdefault(rec["event"]["event_id"], rec)
            if rec["rca"]:
                sig = f"{rec['event']['kind']}:{round(rec['event']['peak_magnitude'])}"
                state.rca_cache.setdefault(sig, rec["rca"])
        await asyncio.to_thread(pc.ensure_schema)  # embedding column + backfill
        logger.info("cold-start restore done",
                    extra={"ctx": {"restored_events": len(recs),
                                   "rca_cache": len(state.rca_cache)}})
    except Exception:
        logger.exception("cold-start restore failed (continuing with empty state)")


@app.get("/health")  # GFE reserves /healthz on run.app URLs and 404s it upstream
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
