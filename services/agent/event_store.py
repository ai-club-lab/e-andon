"""Anomaly event + RCA persistence (Req 3/5, design.md §5).

Persists events and their RCA so the HITL flow works across Cloud Run
instances (in-memory state would strand feedback on the instance that saw
the event). No-ops when the DB is not configured; the dashboard keeps its
in-memory view as the local-dev fallback.
"""
from __future__ import annotations

import json

from chokotei_shared import AnomalyEvent, RcaResult, db


def save_event(ev: AnomalyEvent) -> None:
    if not db.enabled():
        return
    db.execute(
        "INSERT INTO anomaly_events "
        "(event_id, started_ts, ended_ts, kind, peak_magnitude, rep_frame_uri, status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING",
        (ev.event_id, ev.started_ts, ev.ended_ts, ev.kind, ev.peak_magnitude,
         ev.rep_frame_uri, ev.status),
    )


def save_rca(r: RcaResult) -> None:
    if not db.enabled():
        return
    db.execute(
        "INSERT INTO rca_results (event_id, cause_candidates, confidence, evidence) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (event_id) DO UPDATE SET "
        "cause_candidates = EXCLUDED.cause_candidates, confidence = EXCLUDED.confidence, "
        "evidence = EXCLUDED.evidence",
        (r.event_id, json.dumps(r.cause_candidates), r.confidence, json.dumps(r.evidence)),
    )


def _shape(row: dict) -> dict:
    rca = None
    if row.get("cause_candidates") is not None:
        rca = {"cause_candidates": row["cause_candidates"],
               "confidence": row["confidence"], "evidence": row["evidence"]}
    return {"event": {"event_id": row["event_id"], "kind": row["kind"],
                      "peak_magnitude": row["peak_magnitude"],
                      "started_ts": row.get("started_ts", 0.0)}, "rca": rca}


def get_event(event_id: str) -> dict | None:
    if not db.enabled():
        return None
    rows = db.fetch(
        "SELECT e.event_id, e.kind, e.peak_magnitude, e.started_ts, "
        "r.cause_candidates, r.confidence, r.evidence "
        "FROM anomaly_events e LEFT JOIN rca_results r ON r.event_id = e.event_id "
        "WHERE e.event_id = %s", (event_id,))
    return _shape(rows[0]) if rows else None


def list_events(limit: int = 12) -> list[dict]:
    if not db.enabled():
        return []
    rows = db.fetch(
        "SELECT e.event_id, e.kind, e.peak_magnitude, e.started_ts, "
        "r.cause_candidates, r.confidence, r.evidence "
        "FROM anomaly_events e LEFT JOIN rca_results r ON r.event_id = e.event_id "
        "ORDER BY e.created_at DESC LIMIT %s", (limit,))
    return [_shape(r) for r in rows]
