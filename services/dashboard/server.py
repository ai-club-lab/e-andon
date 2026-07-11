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
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

import analytics
import attachments_store
import escalation
import event_store
import feedback_store
import frames_store
import iot_store
import ack_store
import migrations
import notif_store
import routing
import sinks
import slack_routes
import past_cases as pc
import situation
from fastapi.responses import Response
from chokotei_shared import (
    DETECTION,
    SLACK,
    Actor,
    AnomalyEvent,
    FeedbackCase,
    RcaResult,
    categorize,
    db,
    obs,
)
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
        self.sink_error: str | None = None         # loud sink failures (Req 1.4)
        self.notif_sig_ts: dict[str, float] = {}   # signature -> last card time (throttle)
        self.sink = sinks.default_sink(
            on_error=lambda msg: setattr(self, "sink_error", msg))
        self.engine = escalation.EscalationEngine(
            sink=self.sink, verdict_of=feedback_store.get_verdict,
            on_notice=self._escalation_notice)

    def _escalation_notice(self, event_id: str, text: str) -> None:
        """Tier-3 contact info also surfaces on the dashboard (Req 6.3)."""
        rec = self.events.get(event_id)
        if rec is not None:
            rec.setdefault("escalation_notes", []).append(text)


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
                # min(): rows restored from DB may predate the 0.95 cap in infer()
                f"真因は「{cause}」と推定されます（確信度 {min(rca_d['confidence'], 0.95):.0%}）。"
                f"根拠: {'; '.join(rca_d['evidence'][:2])}")
    else:
        text = "⚠ カメラで部品の整列異常を検知し、ラインを停止しました。確認をお願いします。（真因を推定中です）"
    # 前回の対処を人にも見せる（初動短縮）— the same store the agent reads
    rec = state.events.get(ev.event_id) or {"event": ev.model_dump()}
    similar = await asyncio.to_thread(_similar_case, rec)
    await notifs.put({"event_id": ev.event_id, "text": text, "similar_case": similar})
    # Push the same material to the notification sink (Slack card, Req 1.2).
    # SSE always goes first; the card needs the RCA (fires only when present).
    if rca_d and state.sink.enabled():
        asyncio.create_task(_post_card(ev, rca_d, similar))


async def _post_card(ev: AnomalyEvent, rca_d: dict, similar: dict | None = None) -> None:
    # alert-fatigue suppression: event ids are unique per playthrough, so the
    # throttle keys on the anomaly signature — one card per signature per
    # window, no Slack spam from every viewer of the public demo (deterministic)
    sig, now = _sig(ev), time.time()
    last = state.notif_sig_ts.get(sig)
    if last is not None and now - last < SLACK.notif_throttle_s:
        logger.info("notification throttled",
                    extra={"ctx": {"event_id": ev.event_id, "signature": sig}})
        return
    rca = RcaResult(**{**rca_d, "event_id": ev.event_id})
    # keyword rescue here too: DB-restored rca_cache rows predate the fallback
    cat = categorize(rca.category, *rca.cause_candidates)
    decision = await asyncio.to_thread(routing.resolve, ev.event_id, cat)
    deep_link = f"{SLACK.base_url}/e/{ev.event_id}" if SLACK.base_url else ""
    # embed the annotated anomaly frame (red box) in the card — but only once
    # it's actually in GCS, so Slack never fetches a missing image (Req 1: image
    # detection -> show the evidence in Slack, not just behind the detail link)
    frame_url = ""
    if SLACK.base_url and await asyncio.to_thread(frames_store.exists, ev.event_id):
        frame_url = f"{SLACK.base_url}/frame/{ev.event_id}"
    rec = await state.sink.post_card(ev, rca, decision, deep_link, frame_url,
                                     similar=similar)
    if rec is not None:  # timers only when the card actually went out (Req 6.1)
        state.notif_sig_ts[sig] = now
        await state.engine.schedule(decision)


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
                        "metrics": last_metrics, "sink_error": state.sink_error})}
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
                "metrics": last_metrics, "sink_error": state.sink_error,
            })}
            await asyncio.sleep(period)
    finally:
        cap.release()


@app.get("/stream")
async def stream() -> EventSourceResponse:
    return EventSourceResponse(_frames())


@app.get("/events")
async def events() -> list[dict]:
    recs = event_store.list_events() if db.enabled() else list(state.events.values())
    # both-surface verdict sync (Req 2.3): one load, latest verdict per event
    latest: dict[str, dict] = {}
    for row in feedback_store.load():
        latest[row.get("event_id", "")] = row
    for rec in recs:
        v = latest.get(rec["event"]["event_id"])
        rec["verdict"] = ({"verdict": v.get("verdict"),
                           "actor_surface": v.get("actor_surface"),
                           "actor_id": v.get("actor_id"),
                           "actor_name": v.get("actor_name"),
                           "at": v.get("ts")} if v else None)
        # read-time category rescue (matches analytics._category) — stored rows
        # from before the keyword fallback would otherwise drill down as "other"
        rca = rec.get("rca")
        if rca:
            rca["category"] = categorize(rca.get("category"),
                                         *(rca.get("cause_candidates") or []))
    return recs


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


_DEFAULT_ACTOR = Actor(surface="dashboard", user_id="line-op")


def _persist_correction(event_id: str, cause: str, evidence_note: str = "",
                        actor: Actor | None = None, action_taken: str = "") -> dict:
    """Reflux a human correction into the asset store — the shared write behind
    both the /feedback 'wrong' path and the conversational /correct agent.

    Records the wrong verdict (audit + metrics), makes the corrected case
    searchable (past_cases), and drops the cached RCA for that anomaly signature
    so the next occurrence re-infers with the new knowledge (Req 8, 9).
    """
    rec = event_store.get_event(event_id) if db.enabled() else state.events.get(event_id)
    if not rec:
        return {"recorded": False, "reason": "unknown event"}
    actor = actor or _DEFAULT_ACTOR
    ev = rec["event"]
    ai = rec.get("rca") or {}
    cause = cause.strip()[:MAX_CAUSE_LEN]
    note = (evidence_note or "").strip()[:MAX_CAUSE_LEN]
    feedback_store.save({
        "event_id": event_id, "verdict": "wrong",
        "ai_cause": ai.get("cause_candidates", []), "human_cause": cause,
        "kind": ev["kind"], "peak": ev["peak_magnitude"],
        "actor_surface": actor.surface, "actor_id": actor.user_id,
        "actor_name": actor.display_name,
    })
    # situation key from measured values (kind/peak/duration/sensor context) —
    # conclusions (cause, note, action) stay on the value side, out of the key
    summary = _situation_key(ev)
    # a pending field photo rides the correction into the case (Req 9.2)
    attachment = attachments_store.uri_for(event_id)
    pc.add(FeedbackCase(summary=summary, correct_cause=cause, source_event_id=event_id,
                        verdict="corrected", evidence_note=note or None,
                        action_taken=(action_taken or "").strip()[:MAX_CAUSE_LEN] or None,
                        attachment_uri=attachment))
    state.rca_cache.pop(f"{ev['kind']}:{round(ev['peak_magnitude'])}", None)
    return {"recorded": True, "metrics": feedback_store.metrics()}


def _situation_key(ev: dict) -> str:
    return situation.situation_text(ev["kind"], ev["peak_magnitude"],
                                    ev.get("started_ts"), ev.get("ended_ts"))


def _similar_case(rec: dict) -> dict | None:
    """Nearest past case, surfaced to the HUMAN (not just the agent): the
    maintenance staff's first question on arrival is 「前はどう直した？」.
    Searched with the same situation key the agent uses; the event's own case
    is excluded so a fresh stop never cites itself. Best-effort — never blocks
    a notification."""
    try:
        ev = rec["event"]
        hits = [c for c in pc.search(_situation_key(ev), k=5)
                if c.source_event_id != ev["event_id"] and c.correct_cause]
        # cosine ties are common (recurrences share the quantized key) —
        # among equals, a field photo then a recorded action helps most
        hits.sort(key=lambda c: (bool(c.attachment_uri), bool(c.action_taken)),
                  reverse=True)
        if hits:
            c = hits[0]
            return {"cause": c.correct_cause,
                    "action_taken": c.action_taken,
                    "verdict": c.verdict,
                    "photo": bool(c.attachment_uri),
                    "source_event_id": c.source_event_id}
    except Exception:
        logger.warning("similar-case lookup failed", exc_info=True)
    return None


def _persist_confirmation(rec: dict) -> None:
    """✅正しい も事例化する — 当たった推論を状況キーごと強化する（外れからしか
    学ばないストアにしない）。同一シグネチャの再裁定（公開デモのリプレイ）は
    summary の量子化により同文になるため、完全一致で重複を弾く。Best-effort:
    失敗しても裁定の記録自体は既に永続済み。"""
    try:
        ai = rec.get("rca") or {}
        cause = (ai.get("cause_candidates") or [""])[0].strip()
        if not cause or cause == "推定不能":
            return
        ev = rec["event"]
        summary = _situation_key(ev)
        if pc.has_case(summary, cause):
            return
        pc.add(FeedbackCase(summary=summary, correct_cause=cause, verdict="confirmed",
                            source_event_id=ev["event_id"]))
    except Exception:
        logger.warning("confirmed-case reflux failed (verdict already durable)",
                       exc_info=True)


def _record_verdict(event_id: str, verdict: str, actor: Actor,
                    human_cause: str = "") -> dict:
    """The single verdict write path — dashboard, Slack, and the mobile page all
    land here (human-loop Req 2.2). Re-adjudication is not recorded; the prior
    verdict (who / when / what) is returned instead (Req 2.4)."""
    rec = event_store.get_event(event_id) if db.enabled() else state.events.get(event_id)
    if not rec or verdict not in ("correct", "wrong"):
        return {"ok": False, "error": "invalid event_id or verdict"}
    prior = feedback_store.get_verdict(event_id)
    if prior:
        return {"ok": True, "already_adjudicated": True,
                "prior": {"verdict": prior.get("verdict"),
                          "actor_id": prior.get("actor_id"),
                          "actor_name": prior.get("actor_name"),
                          "at": prior.get("ts")}}
    if verdict == "wrong":
        if not human_cause:
            return {"ok": False, "error": "human_cause required when wrong"}
        _persist_correction(event_id, human_cause, actor=actor)
    else:
        ai = rec.get("rca") or {}
        feedback_store.save({
            "event_id": event_id, "verdict": "correct",
            "ai_cause": ai.get("cause_candidates", []), "human_cause": None,
            "kind": rec["event"]["kind"], "peak": rec["event"]["peak_magnitude"],
            "actor_surface": actor.surface, "actor_id": actor.user_id,
            "actor_name": actor.display_name,
        })
        _persist_confirmation(rec)
    _after_verdict(event_id, verdict, actor)
    return {"ok": True, "metrics": feedback_store.metrics()}


def _record_ack(event_id: str, actor: Actor) -> dict:
    """対応中 — the single ack write path (dashboard / Slack / mobile).

    First responder wins. The ack is the REAL urgency signal of a stop:
    it cancels the escalation tiers immediately (Req 6.4 semantics), while
    the verdict/correction can follow after the fix. Audited via logs."""
    rec = _event_rec(event_id)
    if not rec:
        return {"ok": False, "error": "invalid event_id"}
    if feedback_store.get_verdict(event_id):
        return {"ok": True, "already_adjudicated": True}
    prior = ack_store.get(event_id)
    if prior:
        return {"ok": True, "already_acked": True, "ack": prior}
    ack_store.save(event_id, actor)
    logger.info("ack recorded", extra={"ctx": {
        "event_id": event_id, "actor_surface": actor.surface,
        "actor_id": actor.user_id}})
    who = actor.display_name or actor.user_id

    async def _side() -> None:
        await state.engine.cancel(event_id)   # response stops later tiers
        nrec = await asyncio.to_thread(notif_store.get, event_id)
        if nrec is not None:
            await state.sink.post_thread(
                nrec, f"🔧 {who} が対応中です（エスカレーションを停止しました）")
    try:
        asyncio.get_running_loop().create_task(_side())
    except RuntimeError:  # sync caller (tests)
        asyncio.run(_side())
    return {"ok": True, "ack": {"actor_id": actor.user_id,
                                "actor_name": actor.display_name,
                                "actor_surface": actor.surface}}


@app.post("/ack")
async def ack(req: Request) -> dict:
    """「対応中」— acknowledge a stop from the dashboard or the mobile page."""
    if not _rate_ok(req):
        return {"ok": False, "error": "rate limited"}
    body = await req.json() or {}
    user_id = re.sub(r"[^\w-]", "", str(body.get("user_id") or ""))[:40] or "line-op"
    return _record_ack(body.get("event_id", ""),
                       Actor(surface="dashboard", user_id=user_id))


def _after_verdict(event_id: str, verdict: str, actor: Actor) -> None:
    """Verdict side effects: stop escalations (Req 6.4) and reflect the result
    on the Slack card (Req 2.5). Best-effort — the verdict record is already
    durable; sink errors surface through the sink's own on_error path."""
    async def _side() -> None:
        await state.engine.cancel(event_id)
        state.engine.close_correction(event_id)
        rec = await asyncio.to_thread(notif_store.get, event_id)
        if rec is not None:
            await state.sink.update_card(rec, verdict, actor)
    try:
        asyncio.get_running_loop().create_task(_side())
    except RuntimeError:  # sync caller (tests) — run to completion inline
        asyncio.run(_side())


@app.post("/feedback")
async def feedback(req: Request) -> dict:
    """Record a human verdict; reflux corrections into past cases (Req 8, 9).

    The UI routes 'wrong' through the conversational /correct agent; this endpoint
    stays the deterministic path for 'correct' and keeps working for 'wrong' too.
    """
    if not _rate_ok(req):
        return {"ok": False, "error": "rate limited"}
    body = await req.json() or {}
    user_id = re.sub(r"[^\w-]", "", str(body.get("user_id") or ""))[:40] or "line-op"
    return _record_verdict(
        body.get("event_id", ""), body.get("verdict"),
        Actor(surface="dashboard", user_id=user_id),
        (body.get("human_cause") or "").strip()[:MAX_CAUSE_LEN])


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
    prior = feedback_store.get_verdict(event_id)
    if prior:  # same guard as buttons/Slack — one verdict per event (Req 2.4)
        who = prior.get("actor_name") or prior.get("actor_id")
        label = "正しい" if prior.get("verdict") == "correct" else "違う（訂正記録あり）"
        return {"reply": f"このイベントは裁定済みです（{label}"
                         f"{'・' + who if who else ''}）。", "recorded": False}
    ctx = _correction_ctx(rec, Actor(surface="dashboard", user_id=user_id))
    ev = rec["event"]
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
        state.engine.close_correction(event_id)
    else:
        out["suggestions"] = _cause_suggestions(ev["kind"])  # contextual reply chips
        state.engine.touch_correction(event_id)              # timeout watch (Req 3.5)
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


@app.post("/correct/attachment")
async def correct_attachment(event_id: str, file: UploadFile, req: Request) -> dict:
    """Attach ONE field photo to a pending correction (Req 9.1/9.5). Optional —
    the correction completes without it. Stored privately, served by proxy only."""
    if not _rate_ok(req):
        return {"ok": False, "error": "rate limited"}
    if not _event_rec(event_id):
        return {"ok": False, "error": "unknown event"}
    data = await file.read()
    reason = attachments_store.validate(file.content_type or "", len(data))
    if reason:
        return {"ok": False, "error": reason}
    uri = await asyncio.to_thread(
        attachments_store.save_pending, event_id, data, file.content_type)
    logger.info("field photo attached",
                extra={"ctx": {"event_id": event_id, "bytes": len(data)}})
    return {"ok": True, "uri": uri}


@app.get("/attachment/{event_id}")
async def attachment_img(event_id: str) -> Response:
    """Proxy the field photo (store stays private, Req 9.6)."""
    uri = await asyncio.to_thread(attachments_store.uri_for, event_id)
    data = await asyncio.to_thread(attachments_store.get_bytes, uri) if uri else None
    if data is None:
        return Response(status_code=404)
    return Response(content=data, media_type=attachments_store.mime_of(uri))


@app.on_event("startup")
async def _wire_correction() -> None:
    """Let the correction agent's record_correction tool persist through the
    dashboard's shared reflux path (past_cases + audit + cache invalidation)."""
    set_correction_recorder(
        lambda ev_ctx, cause, note, action="": _persist_correction(
            ev_ctx["event_id"], cause, note,
            actor=Actor(**ev_ctx["actor"]) if ev_ctx.get("actor") else None,
            action_taken=action))


@app.on_event("startup")
async def _start_escalation_loop() -> None:
    """Singleton background tick — store-backed scans mean a restart restores
    pending timers by construction (Req 10.2)."""
    asyncio.create_task(state.engine.run())


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
        await asyncio.to_thread(migrations.ensure_human_loop_schema)
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


def _event_rec(event_id: str) -> dict | None:
    return event_store.get_event(event_id) if db.enabled() else state.events.get(event_id)


def _correction_ctx(rec: dict, actor: Actor) -> dict:
    ev, ai = rec["event"], rec.get("rca") or {}
    return {"event_id": ev["event_id"], "kind": ev["kind"],
            # measured fields feed the situation-keyed past-case search
            "peak_magnitude": ev.get("peak_magnitude", 0.0),
            "started_ts": ev.get("started_ts"), "ended_ts": ev.get("ended_ts"),
            "ai_cause": "、".join(ai.get("cause_candidates", [])[:2]),
            "actor": actor.model_dump()}


def _download_slack_file(url_private: str) -> bytes | None:
    """Fetch a Slack-hosted file with the bot token (files:read, Req 9.3)."""
    import urllib.request
    req = urllib.request.Request(
        url_private, headers={"Authorization": f"Bearer {SLACK.bot_token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read(attachments_store.MAX_BYTES + 1)[:attachments_store.MAX_BYTES]
    except Exception:
        logger.exception("slack file download failed")
        return None


async def _slack_on_wrong(event_id: str, actor: Actor) -> None:
    """「違う」ボタン → open the correction dialogue in the card's thread (Req 3.1)."""
    rec = _event_rec(event_id)
    nrec = await asyncio.to_thread(notif_store.get, event_id)
    if not rec or nrec is None:
        return
    prior = feedback_store.get_verdict(event_id)
    if prior:  # both-surface guard (Req 2.4)
        who = prior.get("actor_name") or prior.get("actor_id") or "?"
        await state.sink.post_thread(
            nrec, f"このイベントは裁定済みです（{prior.get('verdict')} / {who}）。")
        return
    try:
        result = await elicit_correction(_correction_ctx(rec, actor), "",
                                         user_id=actor.user_id)
    except Exception:  # surfaced, never silent (Req 5.6 posture)
        logger.exception("slack correction open failed",
                         extra={"ctx": {"event_id": event_id}})
        await state.sink.post_thread(nrec, "訂正対話を開始できませんでした。少し待って再度お試しください。")
        return
    state.engine.touch_correction(event_id)  # 30-min timeout watch (Req 3.5)
    # everything happens in Slack: reply with the cause, and drop a photo of the
    # actual spot right into this thread if you have one (Req 9 via thread intake)
    hint = "\n（原因が分かる写真があれば、この返信にそのまま添付してください）"
    await state.sink.post_thread(nrec, result["reply"] + hint)


async def _slack_on_message(event: dict) -> None:
    """Thread replies to our cards drive the correction agent (Req 3.1–3.4).
    Correlation: thread_ts == our card's message_ts; bots are filtered upstream."""
    thread_ts = event.get("thread_ts")
    if not thread_ts:
        return
    nrec = await asyncio.to_thread(notif_store.by_message_ts, thread_ts)
    if nrec is None:
        return  # unrelated thread
    rec = _event_rec(nrec.event_id)
    if not rec:
        return
    actor = Actor(surface="slack", user_id=event.get("user", "?"))
    text = (event.get("text") or "").strip()[:MAX_MESSAGE_LEN]
    # one field photo per correction (Req 9.1/9.3): validate, store privately
    for f in (event.get("files") or [])[:1]:
        ct, size = f.get("mimetype", ""), int(f.get("size") or 0)
        if f.get("url_private") and attachments_store.validate(ct, size) is None:
            data = await asyncio.to_thread(_download_slack_file, f["url_private"])
            if data:
                await asyncio.to_thread(
                    attachments_store.save_pending, nrec.event_id, data, ct)
                await state.sink.post_thread(nrec, "📷 写真を受け取りました。訂正確定時に事例へ添付します。")
                text = text or "（原因箇所の写真を添付しました）"
    try:
        result = await elicit_correction(_correction_ctx(rec, actor), text,
                                         user_id=actor.user_id)
    except Exception:
        logger.exception("slack correction turn failed",
                         extra={"ctx": {"event_id": nrec.event_id}})
        await state.sink.post_thread(nrec, "応答の生成に失敗しました。もう一度お書きください。")
        return
    if result["recorded"]:
        state.engine.close_correction(nrec.event_id)
        await state.engine.cancel(nrec.event_id)
        cause = result.get("cause") or ""
        await state.sink.post_thread(
            nrec, f"✅ 訂正を記録しました: {cause}\n"
                  f"次回同種の異常では、この原因を最優先の候補として提示します。")
        await state.sink.update_card(nrec, "wrong", actor)
    else:
        state.engine.touch_correction(nrec.event_id)
        await state.sink.post_thread(nrec, result["reply"])


app.include_router(slack_routes.router)
slack_routes.configure(record_verdict=_record_verdict, record_ack=_record_ack,
                       on_wrong=_slack_on_wrong, on_message=_slack_on_message)

_STATIC = os.path.join(os.path.dirname(__file__), "static", "index.html")
_EVENT_PAGE = os.path.join(os.path.dirname(__file__), "static", "event.html")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    with open(_STATIC, encoding="utf-8") as fh:
        return fh.read()


def _all_events() -> list[dict]:
    return event_store.list_events() if db.enabled() else list(state.events.values())


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page() -> str:
    with open(os.path.join(os.path.dirname(__file__), "static", "analytics.html"),
              encoding="utf-8") as fh:
        return fh.read()


@app.get("/analytics/pareto")
async def analytics_pareto(days: int = 7) -> dict:
    return analytics.pareto(_all_events(), days)


@app.get("/analytics/accuracy")
async def analytics_accuracy(days: int = 30) -> dict:
    return analytics.accuracy(feedback_store.load(), days)


@app.get("/analytics/recurrence")
async def analytics_recurrence(days: int = 7) -> dict:
    return analytics.recurrence(_all_events(), feedback_store.load(), days)


@app.get("/api/event/{event_id}")
async def api_event(event_id: str) -> dict:
    """Everything the mobile adjudication page needs in one fetch (Req 8.3)."""
    rec = _event_rec(event_id)
    if not rec:
        raise HTTPException(status_code=404, detail="unknown event")
    v = feedback_store.get_verdict(event_id)
    return {
        "event": rec["event"], "rca": rec.get("rca"),
        "verdict": ({"verdict": v.get("verdict"), "actor_id": v.get("actor_id"),
                     "actor_name": v.get("actor_name"), "at": v.get("ts")} if v else None),
        "ack": await asyncio.to_thread(ack_store.get, event_id),
        "similar_case": await asyncio.to_thread(_similar_case, rec),
        "escalation_notes": rec.get("escalation_notes", []),
        "suggestions": _cause_suggestions(rec["event"]["kind"]),
    }


@app.get("/e/{event_id}", response_class=HTMLResponse)
async def event_page(event_id: str) -> str:
    """Deep-link target from the Slack card — mobile-first (Req 8.1)."""
    with open(_EVENT_PAGE, encoding="utf-8") as fh:
        return fh.read()
