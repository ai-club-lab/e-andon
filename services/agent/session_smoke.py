"""ADK session-persistence smoke (task 2.3, de-risk #1).

Proves that ADK's DatabaseSessionService round-trips a session through Cloud SQL
(postgresql+asyncpg://) and that a *fresh* service instance can read it back —
i.e. history survives across Cloud Run instances instead of vanishing in-memory.

Run with SESSION_DB_URL set, e.g. (via local Cloud SQL Auth Proxy):
  SESSION_DB_URL="postgresql+asyncpg://postgres:PW@127.0.0.1:5432/chokotei" \
    python services/agent/session_smoke.py
"""
from __future__ import annotations

import asyncio
import os

from google.adk.sessions import DatabaseSessionService

URL = os.environ["SESSION_DB_URL"]
APP, USER = "smoke", "u1"


async def main() -> None:
    svc = DatabaseSessionService(db_url=URL)
    created = await svc.create_session(app_name=APP, user_id=USER, state={"hello": "world"})
    print(f"created session id={created.id} state={created.state}")

    # a brand-new service instance simulates a different Cloud Run instance
    svc2 = DatabaseSessionService(db_url=URL)
    got = await svc2.get_session(app_name=APP, user_id=USER, session_id=created.id)
    assert got is not None, "session not found via fresh service — NOT persistent!"
    assert got.state.get("hello") == "world", f"state lost: {got.state}"
    print(f"re-read via fresh service OK: id={got.id} state={got.state}")
    print("SESSION PERSISTENCE OK (de-risk #1 cleared)")


if __name__ == "__main__":
    asyncio.run(main())
