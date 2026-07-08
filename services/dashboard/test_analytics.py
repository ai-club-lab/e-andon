"""Analytics aggregation tests (andon-human-loop task 10, Req 7).

Pure functions over injected fixtures — no DB, no model. Covers pareto
ordering/cumulative ratio, empty-window signalling, recurrence threshold,
and the day-bucketed accuracy trend.
Run: PYTHONPATH=services/dashboard:services/agent:services/detector \
     python -m pytest -q services/dashboard/test_analytics.py
"""
from __future__ import annotations

import analytics

NOW = 1_700_000_000.0
DAY = 86_400.0


def _ev(eid: str, category: str, dur_s: float = 120.0, age_days: float = 1.0) -> dict:
    return {"event": {"event_id": eid, "kind": "offset", "peak_magnitude": 16.0,
                      "started_ts": 5.0, "ended_ts": 5.0 + dur_s,
                      "created_at": NOW - age_days * DAY,
                      "rep_frame_uri": "", "status": "closed"},
            "rca": {"event_id": eid, "cause_candidates": ["c"], "confidence": 0.8,
                    "evidence": [], "category": category}}


def _fb(eid: str, verdict: str, age_days: float = 1.0, cause: str | None = None) -> dict:
    return {"event_id": eid, "verdict": verdict, "human_cause": cause,
            "ts": NOW - age_days * DAY}


def test_pareto_orders_by_count_with_cumulative_ratio():
    events = ([_ev(f"p{i}", "positioning") for i in range(3)]
              + [_ev(f"c{i}", "conveyance") for i in range(2)]
              + [_ev("o0", "other")])
    out = analytics.pareto(events, days=7, now=NOW)
    cats = [b["category"] for b in out["buckets"]]
    assert cats == ["positioning", "conveyance", "other"]
    assert out["buckets"][0]["count"] == 3
    assert abs(out["buckets"][-1]["cum_ratio"] - 1.0) < 1e-9
    assert out["buckets"][0]["loss_minutes"] == 6.0   # 3 × 120s
    assert out["empty"] is False


def test_pareto_clips_open_events_to_default_stop():
    ev = _ev("x", "positioning")
    ev["event"]["ended_ts"] = None                    # still open
    out = analytics.pareto([ev], days=7, now=NOW)
    assert out["buckets"][0]["loss_minutes"] == analytics.DEFAULT_STOP_S / 60


def test_pareto_flags_empty_window():
    """Req 7.7: no data -> explicit empty, not a silent blank chart."""
    events = [_ev("old", "positioning", age_days=40.0)]
    out = analytics.pareto(events, days=7, now=NOW)
    assert out["empty"] is True and out["buckets"] == []


def test_recurrence_alerts_at_threshold():
    """Req 7.4: >=3 same-category stops in 7 days -> proactive alert."""
    events = [_ev(f"p{i}", "positioning") for i in range(3)] + [_ev("c0", "conveyance")]
    fb = [_fb("p1", "wrong", cause="ガイドレール固定ボルトの緩み")]
    out = analytics.recurrence(events, fb, days=7, now=NOW, threshold=3)
    assert len(out["alerts"]) == 1
    alert = out["alerts"][0]
    assert alert["category"] == "positioning" and alert["count"] == 3
    assert "ボルト" in alert["suggestion"], "cites the field's own correction"
    out2 = analytics.recurrence(events[:2], fb, days=7, now=NOW, threshold=3)
    assert out2["alerts"] == []


def test_accuracy_trend_buckets_by_day():
    fb = [_fb("a", "correct", age_days=2.2), _fb("b", "wrong", age_days=2.5),
          _fb("c", "correct", age_days=0.5)]
    out = analytics.accuracy(fb, days=30, now=NOW)
    assert len(out["points"]) == 2                    # two distinct days
    first, last = out["points"][0], out["points"][-1]
    assert first["n"] == 2 and abs(first["correct_rate"] - 0.5) < 1e-9
    assert last["n"] == 1 and last["correct_rate"] == 1.0
    assert analytics.accuracy([], days=7, now=NOW)["empty"] is True
