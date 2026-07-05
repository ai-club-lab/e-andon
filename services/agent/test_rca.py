"""Integration test for the RCA agent (Req 5, 6.6).

Makes a real Vertex AI Gemini call via ADC. Skips if IoT seed data is missing.
Run: `python services/agent/test_rca.py`
"""
from __future__ import annotations

import asyncio
import os

import iot_store
from chokotei_shared import AnomalyEvent
from rca_agent import infer


def _ensure_iot() -> None:
    if not os.path.exists(iot_store.STORE):
        iot_store.persist(iot_store.generate())


def test_infer_identifies_correlated_cause() -> None:
    _ensure_iot()
    event = AnomalyEvent(
        event_id="evt-0139-1", started_ts=8.5, kind="offset",
        peak_magnitude=17.5, rep_frame_uri="", status="closed",
    )
    result = asyncio.run(infer(event))
    print("cause_candidates:", result.cause_candidates)
    print("confidence:", result.confidence)
    print("evidence:", result.evidence)
    assert result.event_id == event.event_id
    assert result.confidence > 0.0, "expected non-zero confidence"
    joined = " ".join(result.cause_candidates + result.evidence)
    # the agent must have correlated the jam/stop signals (current up, belt stop)
    assert any(k in joined for k in ["motor_current", "電流", "belt", "停止", "噛み", "治具", "緩み"]), (
        f"expected the jam/stop correlation in result: {joined}"
    )
    print("OK: RCA produced a correlated, structured result")


if __name__ == "__main__":
    test_infer_identifies_correlated_cause()
