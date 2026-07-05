"""Sync Postgres helper for app stores (design.md §5).

Connects to Cloud SQL via unix socket on Cloud Run (/cloudsql/CONN) or via the
Cloud SQL Auth Proxy locally (DB_HOST=127.0.0.1). Kept sync (psycopg) so the
store interfaces stay sync; ADK sessions use the async engine separately.

When the DB is not configured, ``enabled()`` is False and callers fall back to
local JSONL — so local dev/tests work without a database.
"""
from __future__ import annotations

import os
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _conn_kwargs() -> dict[str, Any]:
    host = os.environ.get("DB_HOST")
    if not host:
        conn = os.environ.get("INSTANCE_CONNECTION_NAME")
        host = f"/cloudsql/{conn}" if conn else None
    return {
        "host": host,
        "port": os.environ.get("DB_PORT", "5432"),
        "dbname": os.environ.get("DB_NAME", "chokotei"),
        "user": os.environ.get("DB_USER", "postgres"),
        "password": os.environ.get("DB_PASSWORD"),
    }


def enabled() -> bool:
    """True when a Postgres backend is configured (password + host)."""
    kw = _conn_kwargs()
    return bool(kw["password"] and kw["host"])


def connect() -> psycopg.Connection:
    return psycopg.connect(row_factory=dict_row, **_conn_kwargs())


def fetch(sql: str, params: tuple = ()) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def execute(sql: str, params: tuple = ()) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
    # psycopg commits on successful context exit
