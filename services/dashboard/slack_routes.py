"""Slack inbound adapter (design §4.5, Req 2 / 10.4).

HTTP mode: signature-verified routes on the existing public dashboard.
Slack-specific interpretation ends here — handlers are injected by the server
(``configure``), and everything lands on the same code paths the dashboard
uses (single source of truth, Req 2.2). ACK is immediate; real work runs as
asyncio tasks (Slack's 3-second rule).

Without SLACK_SIGNING_SECRET the surface refuses with 503 (Req 10.6) — the
secret is read per request so environments (and tests) can toggle it freely.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Request, Response
from slack_sdk.signature import SignatureVerifier

from chokotei_shared import SLACK, Actor

logger = logging.getLogger("slack_routes")
router = APIRouter()

_handlers: dict[str, Callable[..., Any]] = {}


def configure(record_verdict: Callable[[str, str, Actor], dict],
              on_wrong: Callable[[str, Actor], Awaitable[None]] | None = None,
              on_message: Callable[[dict], Awaitable[None]] | None = None,
              record_ack: Callable[[str, Actor], dict] | None = None) -> None:
    """Inject the server-side handlers (avoids a circular import)."""
    _handlers["record_verdict"] = record_verdict
    if on_wrong:
        _handlers["on_wrong"] = on_wrong
    if on_message:
        _handlers["on_message"] = on_message
    if record_ack:
        _handlers["record_ack"] = record_ack


def _guard(request: Request, body: bytes) -> Response | None:
    """Signature check, fail-closed (Req 10.4). Returns a refusal or None."""
    secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    if not secret:
        return Response(status_code=503, content="slack inbound disabled")
    try:
        ok = SignatureVerifier(secret).is_valid(
            body=body,
            timestamp=request.headers.get("x-slack-request-timestamp", ""),
            signature=request.headers.get("x-slack-signature", ""))
    except Exception:  # e.g. missing/garbage timestamp header — fail closed
        ok = False
    if not ok:  # covers bad secret AND stale timestamps (5-minute window)
        logger.warning("slack signature verification failed",
                       extra={"ctx": {"path": str(request.url.path)}})
        return Response(status_code=401, content="bad signature")
    return None


@router.post("/slack/events")
async def slack_events(request: Request) -> Any:
    body = await request.body()
    refusal = _guard(request, body)
    if refusal is not None:
        return refusal
    data = json.loads(body or b"{}")
    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge", "")}
    event = data.get("event") or {}
    handler = _handlers.get("on_message")
    if handler and event.get("type") == "message" and not event.get("bot_id"):
        asyncio.create_task(handler(event))  # ack now, work later (3s rule)
    return {"ok": True}


@router.post("/slack/interactivity")
async def slack_interactivity(request: Request) -> Any:
    body = await request.body()
    refusal = _guard(request, body)
    if refusal is not None:
        return refusal
    form = urllib.parse.parse_qs(body.decode())
    payload = json.loads(form.get("payload", ["{}"])[0])
    if payload.get("type") != "block_actions":
        return {"ok": True}
    user = payload.get("user") or {}
    # デモではSlackの操作者を当番表のペルソナ（例: 保全・佐藤さん）として表示。
    # SLACK_PERSONAS 未設定時は Slack プロフィール名のまま（実運用の姿）。
    actor = Actor(surface="slack", user_id=user.get("id", "?"),
                  display_name=SLACK.persona_of(
                      user.get("id", ""), user.get("username") or user.get("name")))
    for action in payload.get("actions") or []:
        event_id = action.get("value", "")
        if action.get("action_id") == "verdict_correct":
            out = _handlers["record_verdict"](event_id, "correct", actor)
            logger.info("slack verdict", extra={"ctx": {
                "event_id": event_id, "actor": actor.user_id,
                "already": out.get("already_adjudicated", False)}})
        elif action.get("action_id") == "verdict_wrong":
            on_wrong = _handlers.get("on_wrong")
            if on_wrong:
                asyncio.create_task(on_wrong(event_id, actor))
        elif action.get("action_id") == "ack":
            record_ack = _handlers.get("record_ack")
            if record_ack:
                out = record_ack(event_id, actor)
                logger.info("slack ack", extra={"ctx": {
                    "event_id": event_id, "actor": actor.user_id,
                    "already": out.get("already_acked", False)}})
    return {"ok": True}
