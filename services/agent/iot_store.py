"""Synthetic IoT generation + query store (Req 4, design.md §4.5).

Physically-coherent model for the choko-tei (minor line stop) scenario: a
misaligned part travels down the belt, jams in the transfer mechanism, the
motor current climbs then spikes, and the conveyor stops (belt speed -> 0,
PLC RUN -> STOP). Temperature stays normal as a decoy. This gives the RCA
agent real, correlated signal to reason over.

Local-first: persists to a JSONL file so query works without Cloud SQL.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from chokotei_shared import IoTChannel, IoTReading

STORE = Path(os.environ.get("IOT_STORE", "data/iot/readings.jsonl"))
_SAMPLE_HZ = 50.0
STOP_TS = 9.5  # belt stops here (choko-tei); aligns with the video's end-stop
_CHANNELS: list[IoTChannel] = ["motor_current", "belt_speed", "plc_status", "temperature"]


def generate(duration_s: float = 10.0, stop_ts: float = STOP_TS, seed: int = 42) -> list[IoTReading]:
    """Generate readings for the whole timeline. Deterministic given ``seed``.

    Causal chain (Req 4.3): misaligned part -> jam -> motor current ramp+spike
    -> belt stop. All channels are correlated with the visual anomaly window.
    """
    rng = np.random.default_rng(seed)
    n = int(duration_s * _SAMPLE_HZ)
    out: list[IoTReading] = []
    for i in range(n):
        ts = i / _SAMPLE_HZ
        running = ts < stop_ts
        # motor current [A]: 3.0 idle -> load ramp (6.0-9.3s) -> jam spike -> ~0 on stop
        if ts >= stop_ts:
            current = 0.2 + rng.normal(0.0, 0.02)
        elif ts >= 9.3:
            current = 4.6 + (ts - 9.3) / (stop_ts - 9.3) * 1.2 + rng.normal(0.0, 0.05)  # spike -> ~5.8
        elif ts >= 6.0:
            current = 3.0 + (ts - 6.0) / (9.3 - 6.0) * 1.6 + rng.normal(0.0, 0.04)       # ramp -> ~4.6
        else:
            current = 3.0 + rng.normal(0.0, 0.04)
        vals = {
            "motor_current": current,
            "belt_speed": (12.0 + rng.normal(0.0, 0.08)) if running else 0.0,
            "plc_status": 1.0 if running else 0.0,   # 1=RUN, 0=STOP
            "temperature": 42.0 + rng.normal(0.0, 0.2),
        }
        for ch in _CHANNELS:
            out.append(IoTReading(ts=round(ts, 4), channel=ch, value=round(float(vals[ch]), 4)))
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


def query_window(center_ts: float, half_width_s: float = 2.0, path: Path = STORE) -> list[IoTReading]:
    """Return all channels within a window centered on an anomaly (Req 4.2)."""
    t0, t1 = center_ts - half_width_s, center_ts + half_width_s
    return [r for r in _load(path) if t0 <= r.ts <= t1]


if __name__ == "__main__":
    rs = generate()
    persist(rs)
    print(f"generated {len(rs)} readings -> {STORE}")
