"""Runtime configuration (design.md §3, Req 2/10).

Values are injected via environment variables so thresholds and regions can be
tuned per site without code changes. Defaults come from the PoC calibration
(docs/poc/findings.md): baseline noise <2px, anomaly ~18px -> safe margin at 10px.
"""
from __future__ import annotations

import os
import re
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


@dataclass(frozen=True)
class SlackConfig:
    """Slack sink/inbound wiring (human-loop Req 1/10).

    All values come from Secret Manager via env on Cloud Run; when unset the
    sink is a no-op and inbound routes reject, so local dev/CI run Slack-free
    (human-loop Req 10.6).
    """

    bot_token: str = os.environ.get("SLACK_BOT_TOKEN", "")
    signing_secret: str = os.environ.get("SLACK_SIGNING_SECRET", "")
    channel_id: str = os.environ.get("SLACK_CHANNEL_ID", "")
    base_url: str = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")  # deep links (Req 1.3)
    # alert-fatigue suppression: with per-playthrough unique event ids, the same
    # anomaly signature posts at most one card per window (deterministic)
    notif_throttle_s: float = float(os.environ.get("NOTIF_THROTTLE_S", 600.0))
    # デモ用ペルソナ割当: Slackの操作者を当番表の人物として表示する。
    # "U123:保全・佐藤さん（搬送担当）;U456:班長・鈴木さん" 形式＋既定名。
    # 区切りは ; または ,（gcloud --set-env-vars が , を変数区切りに使うため）。
    # 未設定なら Slack プロフィール名をそのまま使う（実運用の姿）。
    personas_raw: str = os.environ.get("SLACK_PERSONAS", "")
    persona_default: str = os.environ.get("SLACK_PERSONA_DEFAULT", "")

    def persona_of(self, user_id: str, fallback: str | None = None) -> str | None:
        for pair in re.split(r"[;,]", self.personas_raw):
            uid, _, name = pair.partition(":")
            if uid.strip() and uid.strip() == user_id and name.strip():
                return name.strip()
        return self.persona_default or fallback

    def slack_id_for(self, persona_name: str) -> str | None:
        """当番表の人物名 → 実SlackユーザーID（カードで実メンションを飛ばす用）."""
        for pair in re.split(r"[;,]", self.personas_raw):
            uid, _, name = pair.partition(":")
            if name.strip() and name.strip() == (persona_name or "").strip():
                return uid.strip() or None
        return None

    @property
    def send_enabled(self) -> bool:
        return bool(self.bot_token and self.channel_id)

    @property
    def inbound_enabled(self) -> bool:
        return bool(self.signing_secret)


@dataclass(frozen=True)
class EscalationConfig:
    """Escalation timing (human-loop Req 6). Deterministic timer × verdict state."""

    tier2_delay_s: int = _i("ESC_TIER2_DELAY_S", 300)
    tier3_delay_s: int = _i("ESC_TIER3_DELAY_S", 900)
    tick_s: float = _f("ESC_TICK_S", 10.0)
    correction_timeout_s: int = _i("CORRECTION_TIMEOUT_S", 1800)  # Req 3.5


DETECTION = DetectionConfig()
GCP = GcpConfig()
SLACK = SlackConfig()
ESCALATION = EscalationConfig()
