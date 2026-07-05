"""RCA agent tools (design.md §4.3, Req 4.2/5.2/6.2/9).

Plain functions with type hints + docstrings; ADK wraps them as FunctionTools.
They return compact JSON-serializable summaries (not raw point clouds) so the
model gets clear signal at low token cost.
"""
from __future__ import annotations

import iot_store
import past_cases as pc

_CHANNEL_HELP = ("belt_speed [m/min] / motor_current [A] / vibration [mm/s] / "
                 "motor_temp [C] / air_pressure [MPa]")


def query_line_sensors(center_ts: float, half_width_s: float = 2.0) -> dict:
    """Summarize the machine sensors in a window around the anomaly timestamp.

    The alignment anomaly is camera-detected; use this to CONFIRM the machine
    sensors are all in their normal band (no overload / vibration / overheat /
    air-pressure drop), which points the root cause to the un-instrumented
    mechanical positioner rather than a sensor-detectable fault.

    Args:
        center_ts: anomaly time in seconds (event.started_ts).
        half_width_s: half-width of the window in seconds.

    Returns:
        Per-channel mean/min/max over the window. Channels: """ + _CHANNEL_HELP
    rows = iot_store.query_window(center_ts, half_width_s)
    chans: dict[str, list[float]] = {}
    for r in rows:
        chans.setdefault(r.channel, []).append(r.value)
    stats = {
        ch: {"mean": round(sum(v) / len(v), 3), "min": round(min(v), 3),
             "max": round(max(v), 3), "n": len(v)}
        for ch, v in chans.items()
    }
    return {"window": [round(center_ts - half_width_s, 2), round(center_ts + half_width_s, 2)],
            "channels": stats}


def query_logs(channel: str, t0: float, t1: float) -> dict:
    """Fetch summary stats for one line sensor over [t0, t1] (Req 6.2).

    Args:
        channel: one of belt_speed / motor_current / vibration / motor_temp / air_pressure.
        t0: window start seconds. t1: window end seconds.
    """
    rows = iot_store.query(channel, t0, t1)  # type: ignore[arg-type]
    if not rows:
        return {"channel": channel, "t0": t0, "t1": t1, "found": False}
    vals = [r.value for r in rows]
    return {"channel": channel, "t0": t0, "t1": t1, "found": True,
            "mean": round(sum(vals) / len(vals), 3),
            "min": round(min(vals), 3), "max": round(max(vals), 3), "n": len(vals)}


def search_past_cases(query: str) -> list[dict]:
    """Search human-confirmed past cases for few-shot context (Req 9.1/9.2).

    Args:
        query: free-text describing the current anomaly.
    """
    return [c.model_dump() for c in pc.search(query)]


def get_frame(event_id: str) -> dict:
    """Return the representative frame reference for an anomaly event (Req 5.2).

    Args:
        event_id: the anomaly event id.
    """
    return {"event_id": event_id, "note": "representative frame reference (P1: local)"}
