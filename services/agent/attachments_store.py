"""Field-photo storage for corrections (design §4.9, Req 9).

The operator can attach ONE photo of the actual cause while correcting the
AI's verdict; it becomes multimodal evidence on the stored past case. Private
by construction: Cloud Storage bucket (never public URLs, Req 9.6) served
only through the dashboard proxy; local dev falls back to ATTACH_DIR files.
Validation (image/*, ≤10MB) lives here so every entry point shares it.
"""
from __future__ import annotations

import os
from pathlib import Path

from chokotei_shared import GCP

MAX_BYTES = 10 * 1024 * 1024
_ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

_BUCKET = os.environ.get("FRAMES_BUCKET", GCP.frames_bucket)  # same private bucket
_client = None


def _dir() -> Path:
    return Path(os.environ.get("ATTACH_DIR", "data/attachments"))


def _bucket():
    global _client
    if _client is None:
        from google.cloud import storage

        _client = storage.Client(project=GCP.project_id)
    return _client.bucket(_BUCKET)


def validate(content_type: str, size: int) -> str | None:
    """Return a refusal reason, or None when acceptable (Req 9.5)."""
    if content_type not in _ALLOWED:
        return "画像ファイル（JPEG/PNG/WebP）のみ添付できます"
    if size > MAX_BYTES:
        return "10MB以下の画像のみ添付できます"
    return None


def _ext(content_type: str) -> str:
    return _ALLOWED.get(content_type, ".jpg")


def save_pending(event_id: str, data: bytes, content_type: str) -> str:
    """Store the photo keyed by event; the correction commit links it (Req 9.2)."""
    if _BUCKET:
        name = f"attachments/{event_id}{_ext(content_type)}"
        blob = _bucket().blob(name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{_BUCKET}/{name}"
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{event_id}{_ext(content_type)}"
    path.write_bytes(data)
    return str(path)


def uri_for(event_id: str) -> str | None:
    """The pending/stored photo URI for an event, or None."""
    if _BUCKET:
        for ext in _ALLOWED.values():
            blob = _bucket().blob(f"attachments/{event_id}{ext}")
            if blob.exists():
                return f"gs://{_BUCKET}/attachments/{event_id}{ext}"
        return None
    for ext in _ALLOWED.values():
        path = _dir() / f"{event_id}{ext}"
        if path.exists():
            return str(path)
    return None


def get_bytes(uri: str) -> bytes | None:
    """Fetch photo bytes for the proxy / multimodal inference (Req 9.3/9.6)."""
    if not uri:
        return None
    if uri.startswith("gs://"):
        _, _, rest = uri.partition("gs://")
        bucket_name, _, name = rest.partition("/")
        blob = _bucket().blob(name) if bucket_name == _BUCKET else None
        if blob is None or not blob.exists():
            return None
        return blob.download_as_bytes()
    path = Path(uri)
    # local fallback: only serve files inside ATTACH_DIR (no path traversal)
    if not path.resolve().is_relative_to(_dir().resolve()) or not path.exists():
        return None
    return path.read_bytes()


def mime_of(uri: str) -> str:
    for ct, ext in _ALLOWED.items():
        if uri.endswith(ext):
            return ct
    return "image/jpeg"
