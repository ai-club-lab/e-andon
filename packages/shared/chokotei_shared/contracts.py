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
    "belt_speed", "motor_current", "vibration", "motor_temp", "air_pressure"
]


class IoTReading(BaseModel):
    """One synthetic IoT sample (Req 4)."""

    ts: float
    channel: IoTChannel
    value: float


# --- RCA agent (Req 5) ---------------------------------------------------


CauseCategory = Literal["positioning", "conveyance", "sensor", "other"]

CAUSE_CATEGORIES: tuple[str, ...] = ("positioning", "conveyance", "sensor", "other")


def normalize_category(raw: object) -> CauseCategory:
    """Server-side vocabulary guard (human-loop Req 5.1/5.4): the model's
    category suggestion only passes if it is exactly one of the closed enum;
    anything else — free text, None, casing drift — becomes "other"."""
    s = str(raw).strip().lower() if raw is not None else ""
    return s if s in CAUSE_CATEGORIES else "other"  # type: ignore[return-value]


# Deterministic keyword→category map, mirroring the RCA instruction's own
# definition of the enum (positioning=位置決め治具・整列機構 / conveyance=搬送・
# ガイドレール・ベルト・送り機構 / sensor=センサー系). Order matters: the first
# matching bucket wins, and positioning terms are checked before conveyance so
# 「位置決め」「治具」 beat a co-occurring 「ズレ」.
_CATEGORY_KEYWORDS: tuple[tuple[CauseCategory, tuple[str, ...]], ...] = (
    ("positioning", ("位置決め", "治具", "整列機構", "ワーク受け", "ストッパ")),
    ("conveyance", ("搬送", "ガイドレール", "ガイド", "レール", "ベルト", "チェーン",
                    "コンベア", "送り", "インデックス", "ピッチ", "グリップ")),
    ("sensor", ("センサ", "誤検知", "カメラ", "計装")),
)


def categorize(raw: object, *texts: str) -> CauseCategory:
    """Category with a keyword fallback: Gemini often omits the enum, which
    used to collapse everything to "other" (a one-bar Pareto and 班長-only
    routing). When the guarded enum is "other", map deterministically from the
    cause text — same closed vocabulary, still zero LLM involvement."""
    cat = normalize_category(raw)
    if cat != "other":
        return cat
    joined = " ".join(t for t in texts if t)
    for bucket, words in _CATEGORY_KEYWORDS:
        if any(w in joined for w in words):
            return bucket
    return "other"


class RcaResult(BaseModel):
    """Root-cause inference output (Req 5)."""

    event_id: str
    cause_candidates: list[str] = Field(description="ranked cause candidates")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(description="referenced values/logs backing the guess")
    # closed vocabulary — the deterministic routing key; the server normalizes
    # anything the model emits outside this enum to "other" (human-loop Req 5)
    category: CauseCategory = "other"


# --- HITL feedback (Req 8) ----------------------------------------------


class Feedback(BaseModel):
    """Human verdict on an RCA result (Req 8). human_cause required when wrong."""

    event_id: str
    ai_result: RcaResult
    verdict: Literal["correct", "wrong"]
    human_cause: str | None = None
    ts: float


class FeedbackCase(BaseModel):
    """A stored, human-confirmed case reused as few-shot context (Req 9).

    Key/value split: ``summary`` is the situation key (measured facts only,
    embedded for retrieval — see agent ``situation.py``); everything else is
    the value payload (conclusions) and must stay out of the embedding.
    """

    summary: str
    correct_cause: str
    source_event_id: str
    # confirmed = the AI's cause was adjudicated right (reinforcement);
    # corrected = a human replaced it (default keeps old stored rows valid)
    verdict: Literal["corrected", "confirmed"] = "corrected"
    # what the operator saw at the cause site ("ボルトが手で回った")
    evidence_note: str | None = None
    # what was done to restore the line ("増し締めして再稼働")
    action_taken: str | None = None
    # optional field photo taken at correction time — multimodal few-shot
    # evidence for the next inference (human-loop Req 9)
    attachment_uri: str | None = None


# --- human loop: notification / routing / escalation (andon-human-loop) ---


class Actor(BaseModel):
    """Who adjudicated or corrected, and from which surface (human-loop Req 4)."""

    surface: Literal["dashboard", "slack"]
    user_id: str
    display_name: str | None = None


class EscalationStep(BaseModel):
    """One deferred notification tier; tier 1 is the card itself (human-loop Req 6)."""

    tier: Literal[2, 3]
    delay_s: int = Field(gt=0, description="delay from the previous tier, seconds")
    target_mention: str | None = Field(
        description="Slack mention for tier 2; None for tier 3 (contact info only)")
    contact_note: str | None = None


class RoutingDecision(BaseModel):
    """Deterministic routing outcome, recorded in full for audit (human-loop Req 5.6)."""

    event_id: str
    category: CauseCategory
    rule_version: int
    primary_mention: str
    escalation_plan: list[EscalationStep]


class NotificationRecord(BaseModel):
    """One posted card per event — the idempotency key (human-loop Req 1.5)."""

    event_id: str
    channel_id: str
    message_ts: str = Field(description="Slack ts; correlates thread replies")
    posted_at: float
