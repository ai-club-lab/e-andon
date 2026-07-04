"""Frame overlay rendering (Req 1.3).

Draws the row baseline, part centroids, and anomaly markers onto a frame so the
dashboard can show what the detector sees.
"""
from __future__ import annotations

import cv2
import numpy as np

from chokotei_shared import FrameResult

_GREEN = (0, 180, 0)
_RED = (0, 0, 255)


def annotate(frame: np.ndarray, fr: FrameResult) -> np.ndarray:
    """Return a copy of ``frame`` with detection overlay drawn."""
    img = frame.copy()
    if fr.baseline_y > 0:
        y = int(fr.baseline_y)
        cv2.line(img, (0, y), (img.shape[1], y), _GREEN, 1)
    for p in fr.parts:
        cv2.circle(img, (int(p.cx), int(p.cy)), 4, _GREEN, -1)
    for f in fr.flags:
        cv2.circle(img, (int(f.cx), int(f.cy)), 12, _RED, 2)
        cv2.putText(img, f.reason, (int(f.cx) - 60, int(f.cy) + 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, _RED, 1)
    return img


def to_jpeg(img: np.ndarray, quality: int = 70) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return buf.tobytes()
