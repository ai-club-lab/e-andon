"""Synthetic IoT generation + query store (Req 4, design.md §4.5).

Predictive-maintenance model for the choko-tei scenario: the positioning
actuator (PLC-controlled) briefly under-strokes — its PLC output drops from
100% to ~74% during [4.0, 5.6]s — which mis-positions the part. The visual
misalignment appears ~1.8s later (~5.8s); the line is then stopped (~9.5s).

Key point (physical realism): the belt motor current stays CONSTANT — a single
mis-positioned part does not load the belt. The root cause is upstream in the
PLC actuator output, not the motor. Temperature is a normal decoy.

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
STOP_TS = 9.5                 # line stops here (choko-tei); aligns with the video end-stop
MISALIGN_TS = 5.8            # visual misalignment appears here
PLC_PRECURSOR = (4.0, 5.6)  # actuator PLC output dips BEFORE the misalignment (root cause)
_CHANNELS: list[IoTChannel] = ["plc_actuator", "motor_current", "belt_speed", "temperature"]


def generate(duration_s: float = 10.0, stop_ts: float = STOP_TS, seed: int = 42) -> list[IoTReading]:
    """Generate readings for the whole timeline. Deterministic given ``seed``.

    Causal chain (Req 4.3): plc_actuator dip (4.0-5.6s) -> part misalignment
    (~5.8s) -> line stop (9.5s). motor_current/belt_speed stay flat while
    running, so the agent can rule out overload and trace the PLC precursor.
    """
    rng = np.random.default_rng(seed)
    n = int(duration_s * _SAMPLE_HZ)
    p0, p1 = PLC_PRECURSOR
    out: list[IoTReading] = []
    for i in range(n):
        ts = i / _SAMPLE_HZ
        running = ts < stop_ts
        # positioning-actuator PLC output (stroke completion %) — the precursor
        if not running:
            plc = 0.0
        elif p0 <= ts <= p1:
            plc = 74.0 + rng.normal(0.0, 1.2)   # under-stroke: root cause
        else:
            plc = min(100.0, 99.4 + rng.normal(0.0, 0.4))
        vals = {
            "plc_actuator": plc,
            "motor_current": (3.0 + rng.normal(0.0, 0.05)) if running else 0.2 + rng.normal(0.0, 0.02),
            "belt_speed": (12.0 + rng.normal(0.0, 0.08)) if running else 0.0,
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
