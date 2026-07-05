"""RCA agent tools (design.md §4.3, Req 4.2/5.2/6.2/9).

Plain functions with type hints + docstrings; ADK wraps them as FunctionTools.
They return compact JSON-serializable summaries (not raw point clouds) so the
model gets clear signal at low token cost.
"""
from __future__ import annotations

import iot_store
import past_cases as pc

_CHANNEL_HELP = ("plc_actuator [%] (positioning-cylinder stroke completion) / "
                 "motor_current [A] / belt_speed [m/min] / temperature [C]")


def query_line_sensors(center_ts: float, half_width_s: float = 2.0) -> dict:
    """Summarize all line sensors in a window around an anomaly timestamp.

    Use this to trace the root cause: plc_actuator dipping below 100% just
    BEFORE the misalignment is the precursor (positioning-cylinder under-stroke),
    while motor_current and belt_speed staying constant rule out overload/jam.

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
        channel: one of plc_actuator / motor_current / belt_speed / temperature.
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
