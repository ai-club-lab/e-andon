"""Deterministic situation key for past-case storage and retrieval.

A stored case is key/value: the KEY describes the situation (what the data
looked like — kind, magnitude, duration, sensor context), the VALUE holds the
conclusions (cause, action, photo). The key must never contain cause words:
embedding causes into the key makes retrieval confirm whatever hypothesis
wrote the query. Store side (case summary) and query side (search key) both
go through this one textizer so they live in the same embedding space.
"""
from __future__ import annotations

import logging

import iot_store

logger = logging.getLogger("situation")

KIND_JA = {"offset": "横ズレ", "rotation": "角度ずれ", "gap": "間隔異常"}
_KIND_UNIT = {"offset": "px", "rotation": "deg", "gap": ""}

# Deterministic normal bands per channel (mirrors the RCA instruction's guide
# values: speed≈12, current≈3.0A, vibration≈0.4mm/s, temp≈42℃, air≈0.50MPa).
_BANDS: dict[str, tuple[float, float]] = {
    "belt_speed": (10.0, 14.0),
    "motor_current": (2.4, 3.6),
    "vibration": (0.0, 0.6),
    "motor_temp": (36.0, 48.0),
    "air_pressure": (0.45, 0.55),
}


def sensor_summary(center_ts: float | None, half_width_s: float = 2.0) -> str:
    """One clause describing the machine sensors around the anomaly instant:
    all-normal, the deviating channels with values, or unknown (no data)."""
    if center_ts is None:
        return "センサー状況不明"
    try:
        rows = iot_store.query_window(center_ts, half_width_s)
    except Exception:
        logger.warning("sensor window lookup failed", exc_info=True)
        return "センサー状況不明"
    if not rows:
        return "センサー状況不明"
    chans: dict[str, list[float]] = {}
    for r in rows:
        chans.setdefault(r.channel, []).append(r.value)
    deviations = []
    for ch, vals in sorted(chans.items()):
        mean = sum(vals) / len(vals)
        lo, hi = _BANDS.get(ch, (float("-inf"), float("inf")))
        if not lo <= mean <= hi:
            deviations.append(f"{ch}={mean:.2f}(正常{lo}〜{hi})")
    return "センサー逸脱: " + " ".join(deviations) if deviations else "センサー全チャネル正常"


def situation_text(kind: str, peak_magnitude: float,
                   started_ts: float | None = None,
                   ended_ts: float | None = None) -> str:
    """Textize the measured situation of an anomaly (the retrieval key).

    Values are quantized (int peak, 0.1s duration) so recurrences of the same
    signature produce the identical string — which is what lets confirmed-case
    dedupe work by exact match.
    """
    parts = [f"映像検知 {KIND_JA.get(kind, kind)}({kind})",
             f"ピーク{round(peak_magnitude)}{_KIND_UNIT.get(kind, '')}"]
    if started_ts is not None and ended_ts is not None and ended_ts >= started_ts:
        parts.append(f"継続{ended_ts - started_ts:.1f}s")
    parts.append(sensor_summary(started_ts))
    return " ".join(parts)
