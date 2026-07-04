"""Synthetic IoT generation + query store (Req 4, design.md §4.5).

Generates vibration/temperature/motor-current readings aligned to the video
timeline and injects a correlated X-axis acceleration spike + harmonics during
the anomaly window, so the RCA agent has real physical signal to reason over.

Local-first: persists to a JSONL file so query works without Cloud SQL. Swap
``_backend`` for asyncpg/Cloud SQL once the DB is provisioned (task 5.3).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np

from chokotei_shared import IoTChannel, IoTReading

STORE = Path(os.environ.get("IOT_STORE", "data/iot/readings.jsonl"))
_SAMPLE_HZ = 50.0
_CHANNELS: list[IoTChannel] = [
    "vibration_x", "vibration_y", "vibration_z", "temperature", "motor_current",
]


def generate(
    duration_s: float = 10.0,
    anomaly_windows: list[tuple[float, float]] | None = None,
    seed: int = 42,
) -> list[IoTReading]:
    """Generate readings for the whole timeline. Deterministic given ``seed``.

    ``anomaly_windows`` are (start_ts, end_ts) ranges where an X-axis vibration
    spike + harmonics is injected (correlated with the visual anomaly, Req 4.3).
    """
    windows = anomaly_windows or [(5.8, 10.0)]
    rng = np.random.default_rng(seed)
    n = int(duration_s * _SAMPLE_HZ)
    out: list[IoTReading] = []
    for i in range(n):
        ts = i / _SAMPLE_HZ
        anom = any(a <= ts <= b for a, b in windows)
        base = {
            "vibration_x": rng.normal(0.0, 0.15),
            "vibration_y": rng.normal(0.0, 0.15),
            "vibration_z": rng.normal(0.0, 0.15),
            "temperature": 42.0 + rng.normal(0.0, 0.2),
            "motor_current": 3.0 + rng.normal(0.0, 0.05),
        }
        if anom:
            # X-axis spike + second/third harmonics; slight current draw rise
            f = 18.0
            spike = 3.2 * math.sin(2 * math.pi * f * ts)
            harm = 0.8 * math.sin(2 * math.pi * 2 * f * ts) + 0.4 * math.sin(2 * math.pi * 3 * f * ts)
            base["vibration_x"] += spike + harm
            base["motor_current"] += 0.35
        for ch in _CHANNELS:
            out.append(IoTReading(ts=round(ts, 4), channel=ch, value=round(float(base[ch]), 4)))
    return out


def persist(readings: list[IoTReading], path: Path = STORE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for r in readings:
            fh.write(r.model_dump_json() + "\n")


def _load(path: Path = STORE) -> list[IoTReading]:
    if not path.exists():
        return []
    with path.open() as fh:
        return [IoTReading(**json.loads(line)) for line in fh if line.strip()]


def query(channel: IoTChannel, t0: float, t1: float, path: Path = STORE) -> list[IoTReading]:
    """Return readings for ``channel`` within [t0, t1] (Req 4.2/6.2)."""
    return [r for r in _load(path) if r.channel == channel and t0 <= r.ts <= t1]


def query_window(center_ts: float, half_width_s: float = 1.0, path: Path = STORE) -> list[IoTReading]:
    """Return all channels within a window centered on an anomaly (Req 4.2)."""
    t0, t1 = center_ts - half_width_s, center_ts + half_width_s
    return [r for r in _load(path) if t0 <= r.ts <= t1]


if __name__ == "__main__":
    rs = generate()
    persist(rs)
    print(f"generated {len(rs)} readings -> {STORE}")
