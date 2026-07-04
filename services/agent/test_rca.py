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
        event_id="evt-0139-1", started_ts=5.8, kind="offset",
        peak_magnitude=17.5, rep_frame_uri="", status="closed",
    )
    result = asyncio.run(infer(event))
    print("cause_candidates:", result.cause_candidates)
    print("confidence:", result.confidence)
    print("evidence:", result.evidence)
    assert result.event_id == event.event_id
    assert result.confidence > 0.0, "expected non-zero confidence"
    joined = " ".join(result.cause_candidates + result.evidence)
    # the agent must have correlated the injected X-axis vibration spike
    assert any(k in joined for k in ["vibration_x", "X軸", "振動", "スパイク", "治具", "緩み"]), (
        f"expected the X-axis vibration correlation in result: {joined}"
    )
    print("OK: RCA produced a correlated, structured result")


if __name__ == "__main__":
    test_infer_identifies_correlated_cause()
