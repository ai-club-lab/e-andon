"""Synthetic IoT generation + query store (Req 4, design.md §4.5).

The anomaly in this system is GEOMETRIC (part alignment / angle / pitch) and is
detected by the camera — the positioning mechanism itself is not instrumented.
So the machine sensors here are a normal-running context bank: belt speed, motor
current, vibration, motor temperature, air pressure. They stay in-range during
operation (dropping only when the line stops), which lets the RCA agent confirm
"no sensor-detectable fault" and point upstream to the mechanical positioner.

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
STOP_TS = 9.5   # line stops here (choko-tei); aligns with the video end-stop
_CHANNELS: list[IoTChannel] = [
    "belt_speed", "motor_current", "vibration", "motor_temp", "air_pressure",
]


def generate(duration_s: float = 10.0, stop_ts: float = STOP_TS, seed: int = 42) -> list[IoTReading]:
    """Generate a normal-running sensor bank (Req 4.3).

    All channels stay in their normal band while running; belt_speed and
    motor_current fall to ~0 when the line stops. No anomaly is injected here —
    the alignment anomaly lives in the vision metrics, not the sensors.
    """
    rng = np.random.default_rng(seed)
    n = int(duration_s * _SAMPLE_HZ)
    out: list[IoTReading] = []
    for i in range(n):
        ts = i / _SAMPLE_HZ
        running = ts < stop_ts
        vals = {
            "belt_speed": (12.0 + rng.normal(0.0, 0.08)) if running else 0.0,      # m/min
            "motor_current": (3.0 + rng.normal(0.0, 0.05)) if running else 0.2,    # A
            "vibration": 0.38 + rng.normal(0.0, 0.03),                             # mm/s (ISO, low)
            "motor_temp": 42.0 + rng.normal(0.0, 0.2),                             # C
            "air_pressure": 0.50 + rng.normal(0.0, 0.004),                         # MPa
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
