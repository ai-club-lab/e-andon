"""Deterministic CV detection (design.md §7, Req 2).

Segments parts on the belt ROI, measures centroid + rotation, and flags
deviation from the regular row via three signals: vertical offset (primary),
rotation, and lattice gap (backup when a shifted part merges with the rail).
Ported and hardened from docs/poc/detect_v2.py.
"""
from __future__ import annotations

import cv2
import numpy as np

from chokotei_shared import DETECTION, DetectionConfig, FlagDetail, FrameResult, PartObservation


def _norm_angle(w: float, h: float, ang: float) -> float:
    """minAreaRect angle -> deviation from axis-aligned in [-45, 45] degrees."""
    a = ang + 90 if w < h else ang
    while a > 45:
        a -= 90
    while a < -45:
        a += 90
    return a


def _find_parts(frame: np.ndarray, cfg: DetectionConfig) -> list[PartObservation]:
    roi = frame[cfg.roi_y0 : cfg.roi_y1]
    gray = cv2.GaussianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=1)
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    parts: list[PartObservation] = []
    for c in cnts:
        area = cv2.contourArea(c)
        if area < cfg.area_min or area > cfg.area_max:
            continue
        (cx, cy), (w, h), ang = cv2.minAreaRect(c)
        if min(w, h) == 0 or max(w, h) / min(w, h) > cfg.aspect_max:
            continue
        parts.append(
            PartObservation(cx=float(cx), cy=float(cy + cfg.roi_y0), angle=_norm_angle(w, h, ang))
        )
    parts.sort(key=lambda p: p.cx)
    return parts


def detect_frame(
    frame: np.ndarray, frame_index: int, ts: float, cfg: DetectionConfig = DETECTION
) -> FrameResult:
    """Detect parts and flag anomalies for a single frame (Req 2).

    Returns a FrameResult with parts, per-frame baselines, and flag details.
    Frames with fewer than ``cfg.min_parts`` parts yield an empty flag list and
    are treated as non-judgeable upstream (Req 2.6).
    """
    parts = _find_parts(frame, cfg)
    if len(parts) < cfg.min_parts:
        return FrameResult(
            frame_index=frame_index, ts=ts, baseline_y=0.0, median_gap=0.0,
            median_angle=0.0, parts=parts, flags=[],
        )
    ys = np.array([p.cy for p in parts])
    angs = np.array([p.angle for p in parts])
    cxs = np.array([p.cx for p in parts])
    base_y, base_ang = float(np.median(ys)), float(np.median(angs))
    gaps = np.diff(cxs)
    med_gap = float(np.median(gaps)) if len(gaps) else 0.0

    flags: list[FlagDetail] = []
    for p in parts:
        off = p.cy - base_y
        if abs(off) > cfg.offset_px:
            flags.append(FlagDetail(kind="offset", cx=p.cx, cy=p.cy,
                                    magnitude=abs(off), reason=f"offset {off:+.0f}px"))
        dang = p.angle - base_ang
        if abs(dang) > cfg.angle_deg:
            flags.append(FlagDetail(kind="rotation", cx=p.cx, cy=p.cy,
                                    magnitude=abs(dang), reason=f"rot {dang:+.0f}deg"))
    for i, g in enumerate(gaps):
        if med_gap > 0 and g > cfg.gap_ratio * med_gap:
            xmid = float((cxs[i] + cxs[i + 1]) / 2)
            flags.append(FlagDetail(kind="gap", cx=xmid, cy=base_y,
                                    magnitude=g / med_gap, reason=f"gap {g/med_gap:.1f}x"))
    return FrameResult(
        frame_index=frame_index, ts=ts, baseline_y=base_y, median_gap=med_gap,
        median_angle=base_ang, parts=parts, flags=flags,
    )


def in_confirm_band(flag: FlagDetail, cfg: DetectionConfig = DETECTION) -> bool:
    """Whether an offset flag sits in the borderline band needing Gemini confirm (Req 2.5)."""
    return flag.kind == "offset" and cfg.band_low <= flag.magnitude <= cfg.band_high
