"""Frame overlay rendering (Req 1.3).

Draws the row baseline, part centroids, and anomaly markers onto a frame so the
dashboard can show what the detector sees.
"""
from __future__ import annotations

import cv2
import numpy as np

from chokotei_shared import FrameResult

_REF = (150, 150, 150)    # neutral gray — a reference guide, not a "detection"
_OK = (170, 170, 170)     # subtle marker on in-tolerance parts
_RED = (0, 0, 255)


def annotate(frame: np.ndarray, fr: FrameResult) -> np.ndarray:
    """Return a copy of ``frame`` with a clean detection overlay.

    The baseline is a subtle dashed *reference* guide (not a solid green line
    that reads as "detection on"); in-tolerance parts get a faint hollow marker;
    only out-of-tolerance parts are emphasized in red with the measured metric
    and a tick visualizing the deviation from the reference row.
    """
    img = frame.copy()
    w = img.shape[1]
    flagged = {(round(f.cx), round(f.cy)) for f in fr.flags}
    if fr.baseline_y > 0:
        y = int(fr.baseline_y)
        for x in range(0, w, 24):  # dashed reference line
            cv2.line(img, (x, y), (min(x + 12, w), y), _REF, 1)
        cv2.putText(img, "reference", (8, max(12, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, _REF, 1)
    for p in fr.parts:
        if (round(p.cx), round(p.cy)) in flagged:
            continue  # emphasized below
        cv2.circle(img, (int(p.cx), int(p.cy)), 5, _OK, 1)  # faint hollow marker
    for f in fr.flags:
        cx, cy = int(f.cx), int(f.cy)
        if fr.baseline_y > 0:  # tick showing the deviation from the row
            cv2.line(img, (cx, int(fr.baseline_y)), (cx, cy), _RED, 1)
        cv2.rectangle(img, (cx - 16, cy - 16), (cx + 16, cy + 16), _RED, 2)
        cv2.putText(img, f.reason, (cx - 26, cy - 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, _RED, 2)
    return img


def to_jpeg(img: np.ndarray, quality: int = 70) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return buf.tobytes()
