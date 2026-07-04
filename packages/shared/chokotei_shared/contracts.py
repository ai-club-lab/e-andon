"""Shared data contracts for chokotei-anomaly-rca.

These pydantic models are the typed boundaries between the detector, agent,
and dashboard services (design.md §4). They are contracts, not implementations.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- detection (Req 2) ---------------------------------------------------

FlagKind = Literal["offset", "rotation", "gap"]


class PartObservation(BaseModel):
    """A single detected part: centroid and orientation."""

    cx: float
    cy: float
    angle: float = Field(description="rotation in degrees, normalized to [-45,45]")


class FlagDetail(BaseModel):
    """One anomaly signal raised on a frame."""

    kind: FlagKind
    cx: float
    cy: float
    magnitude: float = Field(description="deviation size (px or deg or gap ratio)")
    reason: str


class FrameResult(BaseModel):
    """Per-frame detection output (Req 2)."""

    frame_index: int
    ts: float
    baseline_y: float
    median_gap: float
    median_angle: float
    parts: list[PartObservation]
    flags: list[FlagDetail]


# --- event aggregation (Req 3) -------------------------------------------


class AnomalyEvent(BaseModel):
    """A single anomaly aggregated across frames (Req 3)."""

    event_id: str
    started_ts: float
    ended_ts: float | None = None
    kind: FlagKind
    peak_magnitude: float
    rep_frame_uri: str = Field(description="Cloud Storage URI of representative frame")
    status: Literal["open", "closed"] = "open"


# --- IoT (Req 4) ---------------------------------------------------------

IoTChannel = Literal[
    "vibration_x", "vibration_y", "vibration_z", "temperature", "motor_current"
]


class IoTReading(BaseModel):
    """One synthetic IoT sample (Req 4)."""

    ts: float
    channel: IoTChannel
    value: float


# --- RCA agent (Req 5) ---------------------------------------------------


class RcaResult(BaseModel):
    """Root-cause inference output (Req 5)."""

    event_id: str
    cause_candidates: list[str] = Field(description="ranked cause candidates")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(description="referenced values/logs backing the guess")


# --- HITL feedback (Req 8) ----------------------------------------------


class Feedback(BaseModel):
    """Human verdict on an RCA result (Req 8). human_cause required when wrong."""

    event_id: str
    ai_result: RcaResult
    verdict: Literal["correct", "wrong"]
    human_cause: str | None = None
    ts: float


class FeedbackCase(BaseModel):
    """A stored, human-confirmed case reused as few-shot context (Req 9)."""

    summary: str
    correct_cause: str
    source_event_id: str
