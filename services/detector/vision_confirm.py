"""Gemini Vision second-stage confirmation for borderline anomalies (Req 2.5).

The deterministic CV stage decides clear anomalies on its own; only flags in the
borderline band (design §2, DETECTION.band_low..band_high) are sent here. We crop
around the suspect part and ask Gemini 2.5 Flash a yes/no alignment question, so
the expensive model call is rare (de-risk #4: cost/429).
"""
from __future__ import annotations

import logging
import os

import cv2
import numpy as np

from chokotei_shared import GCP

logger = logging.getLogger("vision_confirm")
_CROP = 90  # half-size of the square crop around the part

_PROMPT = (
    "これは製造ラインを上から見た切り出し画像です。中央の金属部品が、"
    "周囲の部品の整列（等間隔・同じ向き）から外れて『ズレている/傾いている』なら YES、"
    "正常に整列しているなら NO のみを1語で答えてください。"
)

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai  # lazy import; Vertex via ADC

        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
        _client = genai.Client(vertexai=True, project=GCP.project_id, location=GCP.model_region)
    return _client


def confirm_with_gemini(frame: np.ndarray, cx: float, cy: float) -> bool:
    """Return True if Gemini judges the cropped part as misaligned (Req 2.5).

    Fails open (returns True) on model error so a borderline anomaly is not
    silently dropped — surfacing over suppressing (aligns with Req 5.6 spirit).
    """
    from google.genai import types

    h, w = frame.shape[:2]
    x0, x1 = max(0, int(cx) - _CROP), min(w, int(cx) + _CROP)
    y0, y1 = max(0, int(cy) - _CROP), min(h, int(cy) + _CROP)
    crop = frame[y0:y1, x0:x1]
    ok, buf = cv2.imencode(".jpg", crop)
    if not ok:
        return True
    try:
        resp = _get_client().models.generate_content(
            model=GCP.gemini_model,
            contents=[
                types.Part.from_bytes(data=buf.tobytes(), mime_type="image/jpeg"),
                _PROMPT,
            ],
        )
        verdict = (resp.text or "").strip().upper()
        logger.info("gemini confirm verdict=%s", verdict)
        return verdict.startswith("YES")
    except Exception as exc:  # do not silently drop a borderline anomaly
        logger.warning("gemini confirm failed, keeping anomaly: %s", exc)
        return True
