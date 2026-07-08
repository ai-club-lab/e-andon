"""Before/After experiment: one HITL correction changes the next RCA run.

Reproduces the learning-loop table in README (§学習ループの実測). Runs the real
RCA agent twice against Vertex AI — once before and once after a correction is
refluxed through the exact same code path as the dashboard's /feedback handler.

Isolation: all stores are JSONL files in a throwaway temp directory (override
with EXP_DIR). No Cloud SQL / production data is read or written.

Usage (needs ADC with Vertex access; 2 real model calls):
    pip install -e packages/shared && pip install -r services/agent/requirements.txt
    PYTHONPATH=services/agent python scripts/loop_experiment.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

WORKDIR = os.environ.get("EXP_DIR") or tempfile.mkdtemp(prefix="eandon-loop-exp-")
os.environ["CASES_STORE"] = os.path.join(WORKDIR, "exp_past_cases.jsonl")
os.environ["FEEDBACK_STORE"] = os.path.join(WORKDIR, "exp_feedback.jsonl")
os.environ["IOT_STORE"] = os.path.join(WORKDIR, "exp_iot.jsonl")
os.environ.pop("SESSION_DB_URL", None)  # force InMemorySessionService
os.environ.pop("INSTANCE_CONNECTION_NAME", None)

import iot_store  # noqa: E402
import past_cases as pc  # noqa: E402
from chokotei_shared import AnomalyEvent, FeedbackCase  # noqa: E402
from rca_agent import infer  # noqa: E402


def show(tag: str, r) -> None:
    print(f"\n===== {tag} =====")
    print("cause_candidates:", json.dumps(r.cause_candidates, ensure_ascii=False))
    print("confidence:", r.confidence)
    print("evidence:", json.dumps(r.evidence, ensure_ascii=False))


async def main() -> int:
    print("workdir:", WORKDIR)
    if not os.path.exists(iot_store.STORE):
        iot_store.persist(iot_store.generate())

    ev1 = AnomalyEvent(event_id="exp-before", started_ts=8.5, kind="offset",
                       peak_magnitude=16.0, rep_frame_uri="", status="closed")
    before = await infer(ev1)
    show("BEFORE (訂正前のRCA)", before)

    # operator verdict: wrong + corrected cause (same reflux logic as /feedback)
    human_cause = "搬送ガイドレール固定ボルトの緩みによる横ズレ"
    summary = f"映像でoffset整列異常を検知・センサー正常・ライン停止 {human_cause}"
    pc.add(FeedbackCase(summary=summary, correct_cause=human_cause,
                        source_event_id="exp-before"))
    print("\n>>> HITL: 「違う」+ 訂正真因を登録:", human_cause)

    ev2 = AnomalyEvent(event_id="exp-after", started_ts=8.5, kind="offset",
                       peak_magnitude=16.5, rep_frame_uri="", status="closed")
    after = await infer(ev2)
    show("AFTER (類似異常の再発時)", after)

    joined = " ".join(after.cause_candidates + after.evidence)
    hit = any(k in joined for k in ["ガイドレール", "ボルト", "緩み"])
    print("\n訂正が次回推論に反映されたか:", "YES" if hit else "NO")
    return 0 if hit else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
