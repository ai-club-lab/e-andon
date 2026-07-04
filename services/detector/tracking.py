"""Temporal aggregation of frame flags into single anomaly events (Req 3).

The line's flagged part travels across frames; without tracking, one physical
anomaly fires on ~100 frames. This tracker matches flags across frames by
centroid continuity and emits ONE AnomalyEvent per physical anomaly, opening on
first confirmation and closing after a run of clean frames.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from chokotei_shared import AnomalyEvent, DETECTION, DetectionConfig, FlagDetail, FrameResult


@dataclass
class _Track:
    event: AnomalyEvent
    last_cx: float
    last_seen_index: int
    misses: int = 0


@dataclass
class EventTracker:
    """Aggregates per-frame flags into open/closed AnomalyEvents.

    Call :meth:`update` for each FrameResult in order. Returns events that were
    newly opened on this frame (Req 3.3 — trigger downstream exactly once).
    Closed events are exposed via :attr:`closed`.
    """

    cfg: DetectionConfig = DETECTION
    miss_limit: int = 3
    _tracks: list[_Track] = field(default_factory=list)
    closed: list[AnomalyEvent] = field(default_factory=list)
    _seq: int = 0

    def update(self, fr: FrameResult) -> list[AnomalyEvent]:
        newly_open: list[AnomalyEvent] = []
        tol = fr.median_gap if fr.median_gap > 0 else 120.0
        unmatched = list(fr.flags)

        # match each active track to the nearest flag within tolerance
        for tr in self._tracks:
            best, best_d = None, tol
            for f in unmatched:
                d = abs(f.cx - tr.last_cx)
                if d < best_d:
                    best, best_d = f, d
            if best is not None:
                unmatched.remove(best)
                tr.last_cx = best.cx
                tr.last_seen_index = fr.frame_index
                tr.misses = 0
                if best.magnitude > tr.event.peak_magnitude:
                    tr.event.peak_magnitude = best.magnitude
                    tr.event.kind = best.kind
            else:
                tr.misses += 1

        # remaining flags start new events
        for f in unmatched:
            self._seq += 1
            ev = AnomalyEvent(
                event_id=f"evt-{fr.frame_index:04d}-{self._seq}",
                started_ts=fr.ts, kind=f.kind, peak_magnitude=f.magnitude,
                rep_frame_uri="", status="open",
            )
            self._tracks.append(_Track(event=ev, last_cx=f.cx, last_seen_index=fr.frame_index))
            newly_open.append(ev)

        # close tracks that have missed too long
        still: list[_Track] = []
        for tr in self._tracks:
            if tr.misses >= self.miss_limit:
                tr.event.status = "closed"
                tr.event.ended_ts = fr.ts
                self.closed.append(tr.event)
            else:
                still.append(tr)
        self._tracks = still
        return newly_open

    def flush(self, ts: float) -> None:
        """Close any still-open tracks (e.g. end of stream)."""
        for tr in self._tracks:
            tr.event.status = "closed"
            tr.event.ended_ts = ts
            self.closed.append(tr.event)
        self._tracks = []
