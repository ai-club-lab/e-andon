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

import feedback_store
import iot_store
import past_cases as pc
from chokotei_shared import DETECTION, AnomalyEvent, FeedbackCase
from detection import detect_frame
from rca_agent import answer_query, infer
from render import annotate, to_jpeg
from tracking import EventTracker

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
        text = (
            f"⚠ 異常 {ev.event_id}（{ev.kind}, ピーク{ev.peak_magnitude:.1f}）"
            f" 推定原因: {', '.join(rca_d['cause_candidates'])}"
            f"（確信度 {rca_d['confidence']:.0%}）根拠: {'; '.join(rca_d['evidence'][:2])}"
        )
    except Exception as exc:  # no silent fallback (Req 5.6)
        text = f"⚠ 異常 {ev.event_id}: 原因推定に失敗しました（{type(exc).__name__}）"
    await state.notifs.put({"event_id": ev.event_id, "text": text})


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
            for ev in state.tracker.update(fr):
                state.events[ev.event_id] = {"event": ev.model_dump(), "rca": None}
                asyncio.create_task(_infer_and_notify(ev))
            notes = []
            while not state.notifs.empty():
                notes.append(state.notifs.get_nowait())
            yield {"event": "frame", "data": json.dumps({
                "frame_index": fr.frame_index, "ts": round(fr.ts, 3),
                "n_parts": len(fr.parts),
                "flags": [f.model_dump() for f in fr.flags],
                "notifications": notes,
                "image": base64.b64encode(to_jpeg(annotate(frame, fr))).decode("ascii"),
            })}
            await asyncio.sleep(period)
    finally:
        cap.release()


@app.get("/stream")
async def stream() -> EventSourceResponse:
    return EventSourceResponse(_frames())


@app.get("/events")
async def events() -> list[dict]:
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
    rec = state.events.get(event_id)
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


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _PAGE


_PAGE = """<!doctype html><meta charset=utf-8><title>チョコ停 監視ダッシュボード</title>
<style>
body{font-family:system-ui;margin:0;background:#0f1115;color:#e6e6e6}
header{padding:10px 16px;font-weight:600;border-bottom:1px solid #222}
.wrap{display:grid;grid-template-columns:2fr 1fr;gap:12px;padding:12px}
img{max-width:100%;border:1px solid #333;border-radius:6px}
canvas{width:100%;height:80px;background:#161922;border-radius:6px}
.panel{background:#161922;border:1px solid #222;border-radius:8px;padding:10px;margin-bottom:12px}
.ev{border-left:3px solid #ff6b6b;padding:6px 8px;margin:6px 0;font-size:13px;background:#1b1e28}
.rca{color:#9ad;margin-top:4px}
#chatlog{max-height:220px;overflow:auto;font-size:13px}
.me{color:#8fd18f}.bot{color:#cdd}
input{width:72%;padding:6px;background:#0f1115;color:#eee;border:1px solid #333;border-radius:4px}
button{padding:6px 10px;background:#2a3350;color:#fff;border:0;border-radius:4px;cursor:pointer}
</style>
<header>チョコ停 監視ダッシュボード <span id=st style=color:#888></span></header>
<div class=wrap>
 <div>
  <img id=f>
  <div class=panel>振動 X 軸 <canvas id=vib width=800 height=80></canvas></div>
 </div>
 <div>
  <div class=panel><b>異常イベント</b> <span id=metrics style=color:#8fd18f;font-size:12px></span><div id=events></div></div>
  <div class=panel><b>チャット</b><div id=chatlog></div>
   <div style=margin-top:6px><input id=q placeholder="例: 直近の温度は？"><button onclick=send()>送信</button></div>
  </div>
 </div>
</div>
<script>
const img=document.getElementById('f'),evc=document.getElementById('events'),
 st=document.getElementById('st'),log=document.getElementById('chatlog');
const seen={};
const es=new EventSource('/stream');
es.addEventListener('frame',e=>{const d=JSON.parse(e.data);
 img.src='data:image/jpeg;base64,'+d.image;
 st.textContent='t='+d.ts+'s parts='+d.n_parts;
 for(const n of d.notifications){addChat('bot',n.text);refreshEvents();}
});
async function refreshEvents(){const r=await fetch('/events');const evs=await r.json();
 evc.innerHTML='';for(const it of evs.slice().reverse().slice(0,6)){const e=it.event;
  const div=document.createElement('div');div.className='ev';
  div.innerHTML='<b>'+e.event_id+'</b> '+e.kind+' peak='+e.peak_magnitude.toFixed(1)+
   (it.rca?'<div class=rca>推定: '+it.rca.cause_candidates.join(', ')+
    ' ('+Math.round(it.rca.confidence*100)+'%)</div>'+
    '<div style=margin-top:4px><button onclick="fb(\\''+e.event_id+'\\',\\'correct\\')">正しい</button> '+
    '<button onclick="fb(\\''+e.event_id+'\\',\\'wrong\\')">誤り</button></div>'
    :'<div class=rca>推論中…</div>');
  evc.appendChild(div);}}
async function fb(id,verdict){let hc=null;
 if(verdict==='wrong'){hc=prompt('正しい原因を入力してください');if(!hc)return;}
 const r=await fetch('/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({event_id:id,verdict:verdict,human_cause:hc})});
 const d=await r.json();refreshMetrics();refreshEvents();
 addChat('bot',d.ok?('✓ フィードバック記録: '+verdict+(hc?(' → '+hc):'')):('記録失敗: '+d.error));}
async function refreshMetrics(){const r=await fetch('/metrics');const m=await r.json();
 document.getElementById('metrics').textContent=m.total?
  ('正答率 '+Math.round((m.correct_rate||0)*100)+'% ('+m.correct+'/'+m.total+')'):'';}
function addChat(cls,txt){const p=document.createElement('div');p.className=cls;
 p.textContent=(cls==='me'?'> ':'🤖 ')+txt;log.appendChild(p);log.scrollTop=log.scrollHeight;}
async function send(){const q=document.getElementById('q');const m=q.value.trim();if(!m)return;
 addChat('me',m);q.value='';const r=await fetch('/chat',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m})});
 const d=await r.json();addChat('bot',d.reply);}
async function drawVib(){const r=await fetch('/iot?channel=vibration_x&t0=0&t1=10');
 const d=await r.json();if(!d.found)return;const c=document.getElementById('vib'),x=c.getContext('2d');
 const w=c.width,h=c.height,pts=d.points;x.clearRect(0,0,w,h);x.strokeStyle='#6b9bff';x.beginPath();
 const mx=Math.max(...pts.map(p=>Math.abs(p[1])))||1;
 pts.forEach((p,i)=>{const px=i/(pts.length-1)*w,py=h/2-(p[1]/mx)*(h/2-4);
  i?x.lineTo(px,py):x.moveTo(px,py);});x.stroke();}
drawVib();refreshMetrics();setInterval(refreshEvents,3000);setInterval(refreshMetrics,3000);
</script>"""
