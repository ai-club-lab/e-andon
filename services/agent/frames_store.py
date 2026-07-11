"""Representative anomaly frame storage in Cloud Storage (Req 3.4).

Stores the annotated frame that triggered an event in a private bucket and
serves it back by proxy (bucket stays private; no public objects). No-ops when
FRAMES_BUCKET is unset, so local dev works without GCS.
"""
from __future__ import annotations

import os

from chokotei_shared import GCP

_BUCKET = os.environ.get("FRAMES_BUCKET", GCP.frames_bucket)
_client = None


def enabled() -> bool:
    return bool(_BUCKET)


def _bucket():
    global _client
    if _client is None:
        from google.cloud import storage

        _client = storage.Client(project=GCP.project_id)
    return _client.bucket(_BUCKET)


def upload_frame(event_id: str, jpg: bytes) -> str:
    """Upload the representative frame; return its gs:// URI (or "")."""
    if not enabled():
        return ""
    blob = _bucket().blob(f"frames/{event_id}.jpg")
    blob.upload_from_string(jpg, content_type="image/jpeg")
    return f"gs://{_BUCKET}/frames/{event_id}.jpg"


def get_frame_bytes(event_id: str) -> bytes | None:
    """Fetch a stored frame's bytes for proxying (Req 3.4)."""
    if not enabled():
        return None
    blob = _bucket().blob(f"frames/{event_id}.jpg")
    if not blob.exists():
        return None
    return blob.download_as_bytes()


def exists(event_id: str) -> bool:
    """Cheap presence check (metadata only) — gate the Slack image block so
    Slack never fetches a not-yet-uploaded frame (would render broken)."""
    if not enabled():
        return False
    return _bucket().blob(f"frames/{event_id}.jpg").exists()
