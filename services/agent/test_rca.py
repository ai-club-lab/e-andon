"""Integration test for the RCA agent (Req 5, 6.6).

Makes a real Vertex AI Gemini call via ADC — run manually (not in CI, which
stays offline). Scenario: an alignment anomaly on the sensor-less positioning
mechanism. The agent must (1) consult the machine sensors it chose itself,
(2) reason by elimination toward the positioning/feed side, and (3) cite the
sensor values it checked as evidence.
Run: PYTHONPATH=services/agent python services/agent/test_rca.py
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


def test_infer_reasons_by_elimination_toward_positioning_side() -> None:
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
    causes = " ".join(result.cause_candidates)
    # elimination must land on the sensor-less positioning/feed/jig side
    assert any(k in causes for k in ["位置決め", "治具", "整列", "送り", "ガイドレール", "機構"]), (
        f"expected a positioning-side cause: {causes}"
    )
    joined = " ".join(result.evidence)
    # the agent must cite the sensor values it checked
    assert any(k in joined for k in ["belt_speed", "電流", "motor_current", "センサー"]), (
        f"expected checked sensor values in evidence: {joined}"
    )
    # autonomy proof: the appended trace lists the tools the agent chose
    assert "query_line_sensors" in joined, f"expected the tool-call trace in evidence: {joined}"
    print("OK: elimination reasoning with sensor evidence and tool trace")


if __name__ == "__main__":
    test_infer_reasons_by_elimination_toward_positioning_side()
