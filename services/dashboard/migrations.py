"""Startup self-migration for the human loop (design.md §5, tasks 1.3/1.4).

Mirrors the andon-human-loop section of infra/schema.sql so a database created
before this feature needs no manual migration — same pattern as
past_cases.ensure_schema. Idempotent: IF NOT EXISTS / ON CONFLICT DO NOTHING.
"""
from __future__ import annotations

import logging

from chokotei_shared import db

logger = logging.getLogger("migrations")

_DDL = [
    """CREATE TABLE IF NOT EXISTS notifications (
        event_id   TEXT PRIMARY KEY REFERENCES anomaly_events (event_id),
        channel_id TEXT NOT NULL,
        message_ts TEXT NOT NULL,
        posted_at  TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS routing_rules (
        category        TEXT PRIMARY KEY,
        primary_mention TEXT NOT NULL,
        tier2_mention   TEXT NOT NULL,
        tier2_delay_s   INT  NOT NULL DEFAULT 300,
        tier3_contact   TEXT NOT NULL,
        tier3_delay_s   INT  NOT NULL DEFAULT 900,
        version         INT  NOT NULL DEFAULT 1,
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS escalations (
        id             BIGSERIAL PRIMARY KEY,
        event_id       TEXT NOT NULL REFERENCES anomaly_events (event_id),
        tier           INT  NOT NULL,
        fire_at        TIMESTAMPTZ NOT NULL,
        target_mention TEXT,
        contact_note   TEXT,
        state          TEXT NOT NULL DEFAULT 'pending',
        fired_at       TIMESTAMPTZ)""",
    "CREATE INDEX IF NOT EXISTS idx_escalations_pending ON escalations (state, fire_at)",
    "ALTER TABLE rca_results ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'other'",
    "ALTER TABLE feedback    ADD COLUMN IF NOT EXISTS actor_surface TEXT",
    "ALTER TABLE feedback    ADD COLUMN IF NOT EXISTS actor_id TEXT",
    "ALTER TABLE feedback    ADD COLUMN IF NOT EXISTS actor_name TEXT",
    "ALTER TABLE past_cases  ADD COLUMN IF NOT EXISTS attachment_uri TEXT",
    """CREATE TABLE IF NOT EXISTS acks (
        event_id      TEXT PRIMARY KEY REFERENCES anomaly_events (event_id),
        actor_surface TEXT NOT NULL,
        actor_id      TEXT NOT NULL,
        actor_name    TEXT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now())""",
    # 復旧クローズ（flow phase 5）: 停止時間の実測を対応記録の第2フェーズとして持つ
    "ALTER TABLE acks ADD COLUMN IF NOT EXISTS recovered_at TIMESTAMPTZ",
    "ALTER TABLE acks ADD COLUMN IF NOT EXISTS recovered_by TEXT",
    "ALTER TABLE acks ADD COLUMN IF NOT EXISTS stop_seconds DOUBLE PRECISION",
    """INSERT INTO routing_rules (category, primary_mention, tier2_mention, tier3_contact) VALUES
        ('positioning', '保全・高橋さん（位置決め担当）', '班長・鈴木さん', '設備ベンダー保守窓口 0120-000-000（デモ値）'),
        ('conveyance',  '保全・安藤さん（搬送担当）', '班長・鈴木さん', '設備ベンダー保守窓口 0120-000-000（デモ値）'),
        ('sensor',      '計装・田中さん', '班長・鈴木さん', 'センサーベンダー窓口 0120-111-111（デモ値）'),
        ('other',       '班長・鈴木さん', '製造課長・伊藤さん', '設備ベンダー保守窓口 0120-000-000（デモ値）')
        ON CONFLICT (category) DO NOTHING""",
]


def ensure_human_loop_schema() -> None:
    """Apply the human-loop schema increment; no-op without a database."""
    if not db.enabled():
        return
    for stmt in _DDL:
        db.execute(stmt)
    logger.info("human-loop schema ensured (%d statements)", len(_DDL))
