"""Guardrail checks (Req 10.4, 10.6): deterministic decision + regions.

Confirms the heavy decision (anomaly confirmation) is deterministic CV, not an
LLM (guardrail: heavy decisions outside the model + audit log), and that region
config matches the standard (model=global endpoint, runtime=asia-northeast1).
Run: `python services/detector/test_guardrails.py`
"""
from __future__ import annotations

import cv2

from chokotei_shared import GCP
from detection import detect_frame


def test_detection_is_deterministic() -> None:
    cap = cv2.VideoCapture("video/factory_01.mov")
    cap.set(cv2.CAP_PROP_POS_FRAMES, 168)
    ok, frame = cap.read()
    cap.release()
    assert ok, "could not read frame"
    a = detect_frame(frame, 168, 7.0)
    b = detect_frame(frame, 168, 7.0)
    # identical output across runs -> no randomness, no LLM in the decision
    assert [round(f.magnitude, 3) for f in a.flags] == [round(f.magnitude, 3) for f in b.flags]
    assert a.flags, "expected the anomaly flag on frame 168"


def test_regions_match_standard() -> None:
    # Gemini 3 family is global-endpoint only; runtime stays in Tokyo.
    assert GCP.model_region == "global", GCP.model_region
    assert GCP.runtime_region == "asia-northeast1", GCP.runtime_region


if __name__ == "__main__":
    test_detection_is_deterministic()
    test_regions_match_standard()
    print("guardrails OK: deterministic detection + correct regions")
