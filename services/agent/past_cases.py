"""Past-case store for few-shot RAG (Req 9, design.md §4.3).

Human-confirmed cases prime the RCA agent. Backend: Cloud SQL ``past_cases``
when configured (survives Cloud Run cold starts), else local JSONL for dev.

Ranking: pgvector cosine similarity over Gemini embeddings (research #5,
gemini-embedding-001 @ 768d) when the DB is configured; keyword overlap is
the offline/dev fallback and the safety net when embedding calls fail.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import embeddings as emb
from chokotei_shared import FeedbackCase, db

logger = logging.getLogger("past_cases")
STORE = Path(os.environ.get("CASES_STORE", "data/cases/past_cases.jsonl"))

_SEED = [
    FeedbackCase(summary="映像で横ズレと角度ずれを同時検知、各センサーは正常。位置決め治具の精度低下",
                 correct_cause="位置決め治具の摩耗・ガタによる整列精度低下", source_event_id="seed-1"),
    FeedbackCase(summary="部品の間隔が不均一（ピッチ異常）、センサーは正常。送りインデックス機構の異常",
                 correct_cause="送りインデックス機構のピッチずれ", source_event_id="seed-2"),
]


def _tokens(s: str) -> set[str]:
    """Runs + CJK character bigrams — Japanese has no spaces, so run-level
    tokens rarely intersect; bigrams give the fallback real recall."""
    runs = re.findall(r"[\wぁ-んァ-ヶ一-龠]+", s.lower())
    toks = set(runs)
    for r in runs:
        toks.update(r[i:i + 2] for i in range(len(r) - 1))
    return toks


def _stored() -> list[FeedbackCase]:
    if db.enabled():
        rows = db.fetch(
            "SELECT summary, correct_cause, source_event_id, attachment_uri FROM past_cases")
        return [FeedbackCase(summary=r["summary"], correct_cause=r["correct_cause"],
                             source_event_id=r["source_event_id"] or "",
                             attachment_uri=r.get("attachment_uri")) for r in rows]
    if STORE.exists():
        with STORE.open() as fh:
            return [FeedbackCase(**json.loads(line)) for line in fh if line.strip()]
    return []


def ensure_schema() -> None:
    """Idempotent embedding column + backfill (mirrors infra/schema.sql).

    Run at service startup so schema.sql applied before research #5 keeps
    working without a manual migration; NULL embeddings are backfilled.
    """
    if not db.enabled():
        return
    db.execute(f"ALTER TABLE past_cases ADD COLUMN IF NOT EXISTS embedding vector({emb.DIM})")
    rows = db.fetch("SELECT id, summary, correct_cause FROM past_cases WHERE embedding IS NULL")
    for r in rows:
        v = emb.embed(f"{r['summary']} {r['correct_cause']}")
        if v is None:
            return  # embedding down; retried next startup
        db.execute("UPDATE past_cases SET embedding = %s::vector WHERE id = %s",
                   (emb.to_vector_literal(v), r["id"]))
    if rows:
        logger.info("backfilled %d past-case embeddings", len(rows))


def add(case: FeedbackCase) -> None:
    """Append a human-confirmed case (Req 9.2), embedded for vector search."""
    if db.enabled():
        v = emb.embed(f"{case.summary} {case.correct_cause}")
        db.execute(
            "INSERT INTO past_cases (summary, correct_cause, source_event_id, embedding, "
            "attachment_uri) VALUES (%s, %s, %s, %s::vector, %s)",
            (case.summary, case.correct_cause, case.source_event_id,
             emb.to_vector_literal(v) if v else None, case.attachment_uri),
        )
        return
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as fh:
        fh.write(case.model_dump_json() + "\n")


def _keyword_search(query: str, candidates: list[FeedbackCase], k: int) -> list[FeedbackCase]:
    q = _tokens(query)
    scored = [(len(q & _tokens(c.summary + " " + c.correct_cause)), c) for c in candidates]
    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for s, c in scored if s > 0][:k]


def _vector_search(query: str, k: int) -> list[FeedbackCase] | None:
    """pgvector cosine top-k, or None when unavailable (caller falls back)."""
    qv = emb.embed(query, for_query=True)
    if qv is None:
        return None
    try:
        rows = db.fetch(
            "SELECT summary, correct_cause, source_event_id, attachment_uri "
            "FROM past_cases WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector LIMIT %s",
            (emb.to_vector_literal(qv), k))
    except Exception:
        logger.warning("pgvector search failed; keyword fallback", exc_info=True)
        return None
    return [FeedbackCase(summary=r["summary"], correct_cause=r["correct_cause"],
                         source_event_id=r["source_event_id"] or "",
                         attachment_uri=r.get("attachment_uri")) for r in rows]


def search(query: str, k: int = 3) -> list[FeedbackCase]:
    """Top-k similar cases: vector search on Cloud SQL, keyword otherwise.

    Seed cases (in-code, not in the DB) are merged via keyword rank so a
    fresh deployment still has few-shot context before any human feedback.
    """
    hits: list[FeedbackCase] = []
    if db.enabled():
        hits = _vector_search(query, k) or []
    if len(hits) < k:
        pool = _SEED if hits else _SEED + _stored()
        seen = {c.source_event_id for c in hits}
        hits += [c for c in _keyword_search(query, pool, k)
                 if c.source_event_id not in seen]
    return hits[:k]
