"""Behavior tests for detection + tracking against the real clip (Req 2, 3).

Runnable directly (`python services/detector/test_detection.py`) or via pytest.
Skips gracefully if the sample video is absent.
"""
from __future__ import annotations

import os

import cv2

from detection import detect_frame
from tracking import EventTracker

VIDEO = os.environ.get("SAMPLE_VIDEO", "video/factory_01.mov")


def _run() -> dict:
    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    tracker = EventTracker()
    fi, first_flag = -1, None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        fi += 1
        fr = detect_frame(frame, fi, fi / fps)
        if fr.flags and first_flag is None:
            first_flag = fi
        tracker.update(fr)
    tracker.flush(fi / fps)
    cap.release()
    return {"frames": fi + 1, "first_flag": first_flag, "events": tracker.closed}


def test_single_anomaly_event_no_false_positives() -> None:
    if not os.path.exists(VIDEO):
        print(f"skip: {VIDEO} not found")
        return
    r = _run()
    events = r["events"]
    # exactly one physical anomaly travels through the clip
    assert len(events) == 1, f"expected 1 event, got {len(events)}: {events}"
    # the clean first half must produce zero flags (no false positives)
    assert r["first_flag"] is not None and r["first_flag"] >= 130, (
        f"unexpected early flag at frame {r['first_flag']}"
    )
    ev = events[0]
    assert ev.peak_magnitude >= 12.0, f"peak too small: {ev.peak_magnitude}"
    print(
        f"OK frames={r['frames']} first_flag={r['first_flag']} "
        f"events=1 kind={ev.kind} peak={ev.peak_magnitude:.1f}"
    )


if __name__ == "__main__":
    test_single_anomaly_event_no_false_positives()
