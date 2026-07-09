"""Analytics aggregations (design §4.7, Req 7).

Pure functions over event/feedback records — deterministic, injectable,
testable without a DB. The endpoints in server.py feed them whatever store
is active (Cloud SQL rows or in-memory/JSONL).

Time base: wall-clock ``created_at``/``ts`` epochs. Demo events restored
without one are treated as current (included in every window) — the demo
line replays the same recording, so age is not meaningful there.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict

DEFAULT_STOP_S = float(os.environ.get("ANALYTICS_DEFAULT_STOP_S", 300.0))
_DAY = 86_400.0

# TPM の現場語彙に合わせた表示名
CATEGORY_LABELS = {
    "positioning": "位置決め・整列機構",
    "conveyance": "搬送・ガイド・送り",
    "sensor": "センサー系",
    "other": "その他",
}


def _in_window(epoch: object, days: int, now: float) -> bool:
    # float() — DB epochs may arrive as decimal.Decimal despite ::float8 casts
    # upstream; never let a numeric type crash the analytics surface
    return epoch is None or (now - float(epoch)) <= days * _DAY  # type: ignore[arg-type]


def _loss_minutes(ev: dict) -> float:
    started, ended = ev.get("started_ts") or 0.0, ev.get("ended_ts")
    dur = (ended - started) if ended is not None else DEFAULT_STOP_S
    return round(max(dur, 0.0) / 60.0, 2)


def _category(rec: dict) -> str:
    return (rec.get("rca") or {}).get("category") or "other"


def pareto(events: list[dict], days: int, now: float | None = None) -> dict:
    """チョコ停パレート: category × (count, loss_minutes), count desc + 累積比率."""
    now = now or time.time()
    counts: dict[str, int] = defaultdict(int)
    loss: dict[str, float] = defaultdict(float)
    for rec in events:
        ev = rec["event"]
        if not _in_window(ev.get("created_at"), days, now):
            continue
        cat = _category(rec)
        counts[cat] += 1
        loss[cat] += _loss_minutes(ev)
    total = sum(counts.values())
    if total == 0:
        return {"buckets": [], "total": 0, "empty": True, "days": days}
    order = sorted(counts, key=lambda c: counts[c], reverse=True)
    buckets, cum = [], 0
    for cat in order:
        cum += counts[cat]
        buckets.append({"category": cat, "label": CATEGORY_LABELS.get(cat, cat),
                        "count": counts[cat], "loss_minutes": round(loss[cat], 2),
                        "cum_ratio": round(cum / total, 4)})
    return {"buckets": buckets, "total": total, "empty": False, "days": days}


def recurrence(events: list[dict], feedback: list[dict], days: int,
               now: float | None = None, threshold: int = 3) -> dict:
    """再発検知 (Req 7.4): 同一 category が窓内 threshold 回以上 → 能動指摘。
    指摘文には当該カテゴリの直近の人手訂正原因を引用する（現場の言葉で促す）。
    閾値判定は決定論 — LLM は関与しない。"""
    now = now or time.time()
    par = pareto(events, days, now)
    corrections: dict[str, str] = {}
    ev_cat = {rec["event"]["event_id"]: _category(rec) for rec in events}
    for row in sorted(feedback, key=lambda r: r.get("ts") or 0.0):
        if row.get("verdict") == "wrong" and row.get("human_cause"):
            cat = ev_cat.get(row.get("event_id", ""))
            if cat:
                corrections[cat] = row["human_cause"]  # keep the latest
    alerts = []
    for b in par["buckets"]:
        if b["count"] < threshold:
            continue
        cited = corrections.get(b["category"])
        suggestion = (f"直近{days}日で「{b['label']}」起因の停止が{b['count']}回。"
                      + (f"現場の訂正では「{cited}」が挙がっています。" if cited else "")
                      + "恒久対策（点検基準・部品交換周期の見直し）の検討を推奨します。")
        alerts.append({"category": b["category"], "label": b["label"],
                       "count": b["count"], "threshold": threshold,
                       "loss_minutes": b["loss_minutes"], "suggestion": suggestion})
    return {"alerts": alerts, "days": days, "threshold": threshold}


def accuracy(feedback: list[dict], days: int, now: float | None = None) -> dict:
    """AI 正答率の日次推移 (Req 7.5) — /metrics の時系列化."""
    now = now or time.time()
    by_day: dict[str, list[str]] = defaultdict(list)
    for row in feedback:
        ts = row.get("ts")
        if not _in_window(ts, days, now):
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(float(ts) if ts is not None else now))
        by_day[day].append(row.get("verdict") or "")
    if not by_day:
        return {"points": [], "empty": True, "days": days}
    points = []
    for day in sorted(by_day):
        verdicts = by_day[day]
        n = len(verdicts)
        points.append({"date": day, "n": n,
                       "correct_rate": round(verdicts.count("correct") / n, 4)})
    return {"points": points, "empty": False, "days": days}
