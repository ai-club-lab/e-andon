-- chokotei-anomaly-rca app schema (design.md §5, task 2.2)
-- ADK manages its own sessions/events tables via DatabaseSessionService.
-- pgvector is enabled for future embedding-based RAG; P1 past_cases uses text search.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS anomaly_events (
    event_id       TEXT PRIMARY KEY,
    started_ts     DOUBLE PRECISION NOT NULL,
    ended_ts       DOUBLE PRECISION,
    kind           TEXT NOT NULL,
    peak_magnitude DOUBLE PRECISION NOT NULL,
    rep_frame_uri  TEXT,
    status         TEXT NOT NULL DEFAULT 'open',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS iot_readings (
    id      BIGSERIAL PRIMARY KEY,
    ts      DOUBLE PRECISION NOT NULL,
    channel TEXT NOT NULL,
    value   DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_iot_ts_channel ON iot_readings (channel, ts);

CREATE TABLE IF NOT EXISTS rca_results (
    event_id        TEXT REFERENCES anomaly_events (event_id) UNIQUE,
    cause_candidates JSONB NOT NULL,
    confidence      DOUBLE PRECISION NOT NULL,
    evidence        JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feedback (
    id         BIGSERIAL PRIMARY KEY,
    event_id   TEXT NOT NULL,
    verdict    TEXT NOT NULL,
    ai_cause   JSONB,
    human_cause TEXT,
    kind       TEXT,
    peak       DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS past_cases (
    id             BIGSERIAL PRIMARY KEY,
    summary        TEXT NOT NULL,
    correct_cause  TEXT NOT NULL,
    source_event_id TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- research #5: gemini-embedding-001, MRL-truncated to 768d (pgvector index-safe).
-- The service also runs this at startup (past_cases.ensure_schema) and
-- backfills NULL embeddings, so existing databases need no manual migration.
ALTER TABLE past_cases ADD COLUMN IF NOT EXISTS embedding vector(768);
-- case = key/value: summary is the measured situation key (the only embedded
-- text); verdict / evidence_note / action_taken are the conclusion payload.
ALTER TABLE past_cases ADD COLUMN IF NOT EXISTS verdict TEXT DEFAULT 'corrected';
ALTER TABLE past_cases ADD COLUMN IF NOT EXISTS evidence_note TEXT;
ALTER TABLE past_cases ADD COLUMN IF NOT EXISTS action_taken TEXT;

-- 対応中 (ack): first responder per event — stops the escalation tiers
-- before the verdict paperwork (business-flow phase 4).
CREATE TABLE IF NOT EXISTS acks (
    event_id      TEXT PRIMARY KEY REFERENCES anomaly_events (event_id),
    actor_surface TEXT NOT NULL,
    actor_id      TEXT NOT NULL,
    actor_name    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 復旧クローズ（flow phase 5）: 検知から復旧までの停止時間を実測で残す
    recovered_at  TIMESTAMPTZ,
    recovered_by  TEXT,
    stop_seconds  DOUBLE PRECISION
);

-- ---------------------------------------------------------------------------
-- andon-human-loop (design.md §5): notification idempotency, deterministic
-- routing, escalation timers, verdict attribution, photo attachments.
-- The service also applies these at startup (dashboard migrations.ensure_
-- human_loop_schema), so existing databases need no manual migration.

CREATE TABLE IF NOT EXISTS notifications (
    event_id   TEXT PRIMARY KEY REFERENCES anomaly_events (event_id),
    channel_id TEXT NOT NULL,
    message_ts TEXT NOT NULL,
    posted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS routing_rules (
    category        TEXT PRIMARY KEY,  -- positioning/conveyance/sensor/other
    primary_mention TEXT NOT NULL,
    tier2_mention   TEXT NOT NULL,
    tier2_delay_s   INT  NOT NULL DEFAULT 300,
    tier3_contact   TEXT NOT NULL,
    tier3_delay_s   INT  NOT NULL DEFAULT 900,
    version         INT  NOT NULL DEFAULT 1,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS escalations (
    id             BIGSERIAL PRIMARY KEY,
    event_id       TEXT NOT NULL REFERENCES anomaly_events (event_id),
    tier           INT  NOT NULL,
    fire_at        TIMESTAMPTZ NOT NULL,
    target_mention TEXT,
    contact_note   TEXT,
    state          TEXT NOT NULL DEFAULT 'pending',  -- pending|fired|cancelled
    fired_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_escalations_pending ON escalations (state, fire_at);

ALTER TABLE rca_results ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'other';
ALTER TABLE feedback    ADD COLUMN IF NOT EXISTS actor_surface TEXT;
ALTER TABLE feedback    ADD COLUMN IF NOT EXISTS actor_id TEXT;
ALTER TABLE feedback    ADD COLUMN IF NOT EXISTS actor_name TEXT;
ALTER TABLE past_cases  ADD COLUMN IF NOT EXISTS attachment_uri TEXT;

-- Demo duty roster (human-loop Req 5.4/5.5): mentions are placeholders the
-- owner replaces with real Slack IDs via UPDATE (no redeploy needed).
INSERT INTO routing_rules (category, primary_mention, tier2_mention, tier3_contact) VALUES
    ('positioning', '保全・高橋さん（位置決め担当）', '班長・鈴木さん', '設備ベンダー保守窓口 0120-000-000（デモ値）'),
    ('conveyance',  '保全・安藤さん（搬送担当）', '班長・鈴木さん', '設備ベンダー保守窓口 0120-000-000（デモ値）'),
    ('sensor',      '計装・田中さん', '班長・鈴木さん', 'センサーベンダー窓口 0120-111-111（デモ値）'),
    ('other',       '班長・鈴木さん', '製造課長・伊藤さん', '設備ベンダー保守窓口 0120-000-000（デモ値）')
ON CONFLICT (category) DO NOTHING;
