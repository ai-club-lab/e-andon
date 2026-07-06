"""Detector service: pseudo-stream + SSE (Req 1, design.md §4.1).

Loops the sample video at the configured fps, runs detection + event tracking
on each sampled frame, and streams annotated frames plus flags/events to the
dashboard over Server-Sent Events. Detection is deterministic and offline;
Cloud dependencies (Storage/Gemini) are not required to run this service.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os

import cv2
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from chokotei_shared import DETECTION
from detection import detect_frame
from render import annotate, to_jpeg
from tracking import EventTracker

VIDEO = os.environ.get("SAMPLE_VIDEO", "video/factory_01.mov")

app = FastAPI(title="e-Andon Detector")


async def _frame_events():
    """Yield SSE payloads: annotated frame (base64 jpeg) + flags + new events."""
    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    step = max(1, round(fps / DETECTION.sample_fps))
    period = step / fps
    tracker = EventTracker()
    fi = -1
    try:
        while True:
            ok, frame = cap.read()
            if not ok:  # loop the clip (Req 1.2)
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                fi = -1
                continue
            fi += 1
            if fi % step != 0:
                continue
            fr = detect_frame(frame, fi, fi / fps)
            new_events = tracker.update(fr)
            img = to_jpeg(annotate(frame, fr))
            yield {
                "event": "frame",
                "data": json.dumps({
                    "frame_index": fr.frame_index,
                    "ts": round(fr.ts, 3),
                    "baseline_y": round(fr.baseline_y, 1),
                    "n_parts": len(fr.parts),
                    "flags": [f.model_dump() for f in fr.flags],
                    "new_events": [e.model_dump() for e in new_events],
                    "image": base64.b64encode(img).decode("ascii"),
                }),
            }
            await asyncio.sleep(period)
    finally:
        cap.release()


@app.get("/stream")
async def stream() -> EventSourceResponse:
    return EventSourceResponse(_frame_events())


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "video": VIDEO, "video_present": os.path.exists(VIDEO)}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _PAGE


_PAGE = """<!doctype html><meta charset=utf-8><title>e-Andon detector</title>
<style>body{font-family:system-ui;margin:16px;background:#0f1115;color:#e6e6e6}
#f{max-width:100%;border:1px solid #333}#log{margin-top:8px;font-size:13px}
.ev{color:#ff6b6b}</style>
<h3>チョコ停 検知ストリーム</h3><img id=f><div id=log></div>
<script>
const img=document.getElementById('f'),log=document.getElementById('log');
const es=new EventSource('/stream');
es.addEventListener('frame',e=>{const d=JSON.parse(e.data);
 img.src='data:image/jpeg;base64,'+d.image;
 if(d.new_events.length){for(const ev of d.new_events){
  const p=document.createElement('div');p.className='ev';
  p.textContent='⚠ 異常 '+ev.event_id+' kind='+ev.kind+' peak='+ev.peak_magnitude.toFixed(1);
  log.prepend(p);}}});
</script>"""
