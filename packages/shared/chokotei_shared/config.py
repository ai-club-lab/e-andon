"""Runtime configuration (design.md §3, Req 2/10).

Values are injected via environment variables so thresholds and regions can be
tuned per site without code changes. Defaults come from the PoC calibration
(docs/poc/findings.md): baseline noise <2px, anomaly ~18px -> safe margin at 10px.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class DetectionConfig:
    """CV detection thresholds and ROI (Req 2)."""

    roi_y0: int = _i("DET_ROI_Y0", 250)
    roi_y1: int = _i("DET_ROI_Y1", 430)
    area_min: int = _i("DET_AREA_MIN", 1800)
    area_max: int = _i("DET_AREA_MAX", 8000)
    aspect_max: float = _f("DET_ASPECT_MAX", 1.6)
    offset_px: float = _f("DET_OFFSET_PX", 10.0)
    angle_deg: float = _f("DET_ANGLE_DEG", 10.0)
    gap_ratio: float = _f("DET_GAP_RATIO", 1.5)
    band_low: float = _f("DET_BAND_LOW", 8.0)   # gemini-confirm band lower bound
    band_high: float = _f("DET_BAND_HIGH", 12.0)
    min_parts: int = _i("DET_MIN_PARTS", 4)
    sample_fps: float = _f("DET_SAMPLE_FPS", 5.0)


def _session_db_url() -> str:
    """Build the ADK session URL (de-risk #1). async driver is mandatory.

    Priority: explicit SESSION_DB_URL (e.g. local via Cloud SQL Auth Proxy),
    else compose the Cloud Run unix-socket URL from components.
    """
    direct = os.environ.get("SESSION_DB_URL")
    if direct:
        return direct
    conn = os.environ.get("INSTANCE_CONNECTION_NAME")
    pw = os.environ.get("DB_PASSWORD")
    if conn and pw:
        db = os.environ.get("DB_NAME", "chokotei")
        user = os.environ.get("DB_USER", "postgres")
        return f"postgresql+asyncpg://{user}:{pw}@/{db}?host=/cloudsql/{conn}"
    return ""


@dataclass(frozen=True)
class GcpConfig:
    """GCP wiring (Req 10). Regions per gcp-integration.md."""

    # Gemini 3 family is served on the "global" endpoint (regional 404s);
    # gemini-embedding-001 works there too, so all model calls share one location.
    project_id: str = os.environ.get("GCP_PROJECT", "fhack26-aiclub")
    model_region: str = os.environ.get("MODEL_REGION", "global")
    runtime_region: str = os.environ.get("RUNTIME_REGION", "asia-northeast1")
    # gemini-2.5-flash is scheduled for shutdown on 2026-10-16; we follow the
    # ADK 2.3 default onto gemini-3-flash-preview (rollback: set GEMINI_MODEL).
    gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
    session_db_url: str = _session_db_url()
    frames_bucket: str = os.environ.get("FRAMES_BUCKET", "")


DETECTION = DetectionConfig()
GCP = GcpConfig()
