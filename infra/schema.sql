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
    event_id        TEXT REFERENCES anomaly_events (event_id),
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
    -- embedding vector(N)  -- add once embedding model/dim is chosen (research #5)
);
