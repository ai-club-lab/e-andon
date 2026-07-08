"""RCA agent tools (design.md §4.3, Req 4.2/5.2/6.2/9).

Plain functions with type hints + docstrings; ADK wraps them as FunctionTools.
They return compact JSON-serializable summaries (not raw point clouds) so the
model gets clear signal at low token cost.
"""
from __future__ import annotations

import contextvars
from typing import Callable

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


# --- HITL correction capture (Req 8/9) ---
# The correction agent elicits the operator's tacit knowledge in natural dialogue
# and calls record_correction to persist it. Guardrail: the LLM supplies only the
# *cause text* (the operator's words); the *target event* and the actual write are
# server-controlled — the event is bound per-request via a ContextVar the model
# cannot see, and persistence goes through an injected recorder (audit + dedupe).
_active_correction: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "active_correction", default=None)
_recorder: Callable[[dict, str, str], dict] | None = None


def set_correction_recorder(fn: Callable[[dict, str, str], dict]) -> None:
    """Inject the persistence side-effect (dashboard wires past_cases + audit)."""
    global _recorder
    _recorder = fn


def record_correction(correct_cause: str, evidence_note: str = "") -> dict:
    """Record the operator's corrected root cause for the anomaly under review.

    Call this ONLY after the operator has named a concrete cause (a machine part
    or mechanical state) AND you have repeated it back for confirmation. Never
    invent a cause — record only what the operator stated.

    Args:
        correct_cause: the operator's confirmed root cause, in their words.
        evidence_note: optional supporting detail (what they saw / checked).

    Returns:
        {"recorded": bool, ...}. If not recorded, the reason says what to ask next.
    """
    holder = _active_correction.get()
    if holder is None or _recorder is None:
        return {"recorded": False, "reason": "訂正セッションが有効ではありません"}
    cause = (correct_cause or "").strip()
    if len(cause) < 2:
        return {"recorded": False,
                "reason": "原因が具体的でありません。オペレーターにもう一度確認してください"}
    result = _recorder(holder["event"], cause[:200], (evidence_note or "").strip()[:200])
    if result.get("recorded"):
        holder["recorded"] = True
        holder["cause"] = cause[:200]
    return result
