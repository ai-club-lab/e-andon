"""Gemini embeddings for past-case retrieval (Req 9, research #5).

gemini-embedding-001 on Vertex (ADC, MODEL_REGION) truncated via MRL to 768
dims — well inside pgvector index limits and cheap to store. Any failure
returns None and callers fall back to keyword ranking: retrieval quality
degrades, RCA never breaks.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("embeddings")

MODEL = "gemini-embedding-001"
DIM = 768

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai  # lazy: offline tests never import the SDK

        from chokotei_shared import GCP

        _client = genai.Client(vertexai=True, project=GCP.project_id,
                               location=GCP.model_region)
    return _client


def embed(text: str, *, for_query: bool = False) -> list[float] | None:
    """Return a DIM-dim embedding, or None on any failure (fail-soft)."""
    try:
        from google.genai import types

        res = _get_client().models.embed_content(
            model=MODEL,
            contents=text,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY" if for_query else "RETRIEVAL_DOCUMENT",
                output_dimensionality=DIM,
            ),
        )
        return list(res.embeddings[0].values)
    except Exception:
        logger.warning("embedding failed; keyword fallback will be used",
                       exc_info=True)
        return None


def to_vector_literal(v: list[float]) -> str:
    """pgvector text literal for a psycopg parameter (cast with ::vector)."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
