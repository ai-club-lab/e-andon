"""Notification sinks (design §4.2, Req 1/2.5).

``NotificationSink`` is the outbound abstraction: the notifier depends on this
Protocol only, so Slack is the first implementation — LINE WORKS / Teams /
a patrol light are drop-in replacements. ``NullSink`` keeps local dev and CI
Slack-free (Req 10.6). Delivery failures are loud: logged + surfaced via
``on_error`` (Req 1.4), never swallowed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Protocol

import notif_store
from chokotei_shared import SLACK, Actor, AnomalyEvent, NotificationRecord, RcaResult, RoutingDecision

logger = logging.getLogger("sinks")


class NotificationSink(Protocol):
    def enabled(self) -> bool: ...

    async def post_card(self, ev: AnomalyEvent, rca: RcaResult,
                        routing: RoutingDecision | None,
                        deep_link: str, frame_url: str = "",
                        similar: dict | None = None) -> NotificationRecord | None: ...

    async def update_card(self, rec: NotificationRecord, verdict: str,
                          actor: Actor) -> None: ...

    async def post_thread(self, rec: NotificationRecord, text: str) -> None: ...


class NullSink:
    """No-op sink for environments without Slack wiring (Req 10.6)."""

    def enabled(self) -> bool:
        return False

    async def post_card(self, ev, rca, routing, deep_link, frame_url="", similar=None):
        return None

    async def update_card(self, rec, verdict, actor):
        return None

    async def post_thread(self, rec, text):
        return None


def _card_blocks(ev: AnomalyEvent, rca: RcaResult,
                 routing: RoutingDecision | None, deep_link: str,
                 frame_url: str = "", similar: dict | None = None) -> list[dict]:
    conf = min(rca.confidence, 0.95)  # same deterministic cap as the dashboard
    cause = "、".join(rca.cause_candidates[:2])
    evidence = "\n".join(f"• {e}" for e in rca.evidence[:3])
    # 事案 → 推測 → 「この設備の担当は誰々 → 通知しました」の語り。役割名や
    # 内部語（エスカレーション等）を出さず、次に何が起きるかを名前で言う。
    mention = ""
    if routing:
        tier2 = next((s for s in routing.escalation_plan
                      if s.tier == 2 and s.target_mention), None)
        # 一次担当は実メンション（SLACK_PERSONASで実IDに紐づく場合）— 通知が
        # その人に「鳴る」。tier2はこの時点では名前のみ（鳴らすのは5分後の
        # エスカレーション時。カードで先に鳴らすと二段構えの意味が消える）
        uid = SLACK.slack_id_for(routing.primary_mention)
        primary = (f"<@{uid}>（{routing.primary_mention}）" if uid
                   else f"*{routing.primary_mention}*")
        mention = f"\n👥 この設備の担当: {primary} に通知しました。"
        if tier2:
            mention += (f"応答がなければ{tier2.delay_s // 60}分後に "
                        f"{tier2.target_mention} へ連絡します。")
    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": "⚠ ライン停止（チョコ停）— 整列異常"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"*真因候補*: {cause}\n*確信度*: {conf:.0%}\n*根拠*:\n{evidence}{mention}")}},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": "ack",
             "text": {"type": "plain_text", "text": "👋 私が対応します"}, "value": ev.event_id},
            {"type": "button", "action_id": "verdict_correct", "style": "primary",
             "text": {"type": "plain_text", "text": "✅ 正しい"}, "value": ev.event_id},
            {"type": "button", "action_id": "verdict_wrong", "style": "danger",
             "text": {"type": "plain_text", "text": "❌ 違う（対話で訂正）"},
             "value": ev.event_id},
        ]},
    ]
    # NB: no "open detail" button — the operator never leaves Slack. The frame
    # is embedded below, adjudication is the two buttons, and a photo can be
    # dropped straight into the correction thread (deep_link kept for callers).
    if frame_url:  # image-based detection: show the annotated frame in-card
        blocks.insert(2, {"type": "image", "image_url": frame_url,
                          "alt_text": "検知フレーム（赤枠=整列異常）"})
    if similar:  # 初動短縮: how the SAME situation was fixed last time
        line = f"📚 *類似の過去停止*: 真因「{similar['cause']}」"
        if similar.get("action_taken"):
            line += f" ／ 処置: {similar['action_taken']}"
        blocks.insert(len(blocks) - 1,
                      {"type": "context", "elements": [{"type": "mrkdwn", "text": line}]})
        if similar.get("photo") and deep_link:
            # 検知フレーム（いま）と過去事例の現場写真（前回の真因の実物）を並べて
            # 届ける — 初動で見るべき2枚。context の極小サムネイルでは伝わらない
            base = deep_link.split("/e/")[0]
            blocks.insert(len(blocks) - 1, {
                "type": "image",
                "title": {"type": "plain_text", "text": "📷 過去事例の現場写真（前回の真因の実物）"},
                "image_url": f"{base}/attachment/{similar['source_event_id']}",
                "alt_text": "過去事例の現場写真"})
    return blocks


class SlackSink:
    """Slack implementation (Req 1.1–1.5). ``client`` is injectable for tests."""

    def __init__(self, client=None, channel_id: str = "",
                 on_error: Callable[[str], None] | None = None) -> None:
        if client is None and SLACK.send_enabled:
            from slack_sdk import WebClient
            client = WebClient(token=SLACK.bot_token)
        self._client = client
        self._channel = channel_id or SLACK.channel_id
        self._on_error = on_error or (lambda msg: None)

    def enabled(self) -> bool:
        return self._client is not None and bool(self._channel)

    async def post_card(self, ev: AnomalyEvent, rca: RcaResult,
                        routing: RoutingDecision | None,
                        deep_link: str, frame_url: str = "",
                        similar: dict | None = None) -> NotificationRecord | None:
        if not self.enabled():
            return None
        existing = await asyncio.to_thread(notif_store.get, ev.event_id)
        if existing:  # Req 1.5 — restarts/retries reuse the posted card
            return existing
        summary = f"⚠ ライン停止: {'、'.join(rca.cause_candidates[:1])}（{ev.kind}）"
        try:
            resp = await self._post_joining(
                channel=self._channel, text=summary,
                blocks=_card_blocks(ev, rca, routing, deep_link, frame_url, similar))
        except Exception as e:  # Req 1.4 — loud, never silent
            logger.exception("Slack post failed", extra={"ctx": {"event_id": ev.event_id}})
            self._on_error(f"Slack通知の送信に失敗しました（{type(e).__name__}）")
            return None
        rec = NotificationRecord(event_id=ev.event_id, channel_id=self._channel,
                                 message_ts=resp["ts"], posted_at=time.time())
        await asyncio.to_thread(notif_store.save, rec)
        return rec

    async def _post_joining(self, **kwargs):
        """chat.postMessage; on not_in_channel, conversations.join once and retry
        (channels:join scope — no manual /invite needed for public channels)."""
        from slack_sdk.errors import SlackApiError
        try:
            return await asyncio.to_thread(self._client.chat_postMessage, **kwargs)
        except SlackApiError as e:
            if (e.response or {}).get("error") != "not_in_channel":
                raise
            await asyncio.to_thread(self._client.conversations_join,
                                    channel=kwargs["channel"])
            return await asyncio.to_thread(self._client.chat_postMessage, **kwargs)

    def _is_stale(self, rec: NotificationRecord) -> bool:
        """A record whose channel isn't our configured one belongs to a
        decommissioned workspace (e.g. after a migration) — its card can't be
        threaded/updated here. Skip quietly rather than alarm the operator."""
        if rec.channel_id != self._channel:
            logger.info("skipping notification from another channel/workspace",
                        extra={"ctx": {"event_id": rec.event_id,
                                       "rec_channel": rec.channel_id,
                                       "cur_channel": self._channel}})
            return True
        return False

    async def update_card(self, rec: NotificationRecord, verdict: str,
                          actor: Actor) -> None:
        if not self.enabled() or self._is_stale(rec):
            return
        who = actor.display_name or actor.user_id
        label = "✅ 正しい" if verdict == "correct" else "❌ 違う（訂正あり）"
        when = time.strftime("%H:%M", time.localtime())
        try:
            await asyncio.to_thread(
                self._client.chat_update, channel=rec.channel_id, ts=rec.message_ts,
                text=f"裁定済み: {label}",
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": (
                    f"*裁定済み* {label} — {who}（{when}）\n"
                    f"経緯はこのスレッドを参照。")}}])
        except Exception as e:
            if self._stale_channel_error(e):
                return
            logger.exception("Slack card update failed",
                             extra={"ctx": {"event_id": rec.event_id}})
            self._on_error("Slackカードの更新に失敗しました")

    async def post_thread(self, rec: NotificationRecord, text: str) -> None:
        if not self.enabled() or self._is_stale(rec):
            return
        try:
            await asyncio.to_thread(
                self._client.chat_postMessage, channel=rec.channel_id,
                thread_ts=rec.message_ts, text=text)
        except Exception as e:
            if self._stale_channel_error(e):
                return  # decommissioned channel — stale record, not a live failure
            logger.exception("Slack thread post failed",
                             extra={"ctx": {"event_id": rec.event_id}})
            self._on_error("Slackスレッドへの投稿に失敗しました")

    @staticmethod
    def _stale_channel_error(e: Exception) -> bool:
        """channel_not_found = the target channel doesn't exist for this token
        (a migrated/decommissioned workspace). Log, but never a user banner."""
        err = getattr(getattr(e, "response", None), "get", lambda _k: None)("error")
        if err == "channel_not_found":
            logger.warning("thread target channel not found (stale record); skipping")
            return True
        return False


def default_sink(on_error: Callable[[str], None] | None = None) -> NotificationSink:
    """SlackSink when configured, else NullSink — chosen once at startup."""
    if SLACK.send_enabled:
        return SlackSink(on_error=on_error)
    logger.info("Slack sink disabled (no SLACK_BOT_TOKEN/SLACK_CHANNEL_ID)")
    return NullSink()
