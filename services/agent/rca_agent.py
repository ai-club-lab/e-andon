"""RCA orchestrator agent (design.md §8, Req 5).

Root LlmAgent (Gemini 3 Flash on Vertex via ADC) with FunctionTools for
IoT correlation and past-case retrieval (AgentTool pattern; no transfer_to_agent).
``infer`` runs the agent over an AnomalyEvent and returns a structured RcaResult.

Session persistence: DatabaseSessionService when SESSION_DB_URL is set
(postgresql+asyncpg://, de-risk #1), else InMemorySessionService for local dev.
Model-call failures are raised + logged, never silently swallowed (Req 5.6).
"""
from __future__ import annotations

import json
import logging
import os
import re

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

import situation
from chokotei_shared import GCP, AnomalyEvent, RcaResult, normalize_category
from tools import (
    _active_correction,
    bind_active_event,
    query_line_sensors,
    query_logs,
    record_correction,
    reset_active_event,
    search_past_cases,
    set_correction_recorder,  # re-exported so the dashboard wires it via rca_agent
)

logger = logging.getLogger("rca_agent")
_APP = "chokotei-rca"

# Route the ADK/google-genai client to Vertex AI using ADC (no API key).
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", GCP.project_id)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", GCP.model_region)

_INSTRUCTION = """あなたは工場ラインの整列異常の真因を特定するエンジニアです。
カメラ映像で部品の整列異常（横ズレ offset / 角度 rotation / 間隔 gap）が検知されました。
この位置決め・整列機構はセンサー非搭載で、映像が唯一の検知手段です。真因を推定してください。
手順:
1) query_line_sensors で異常時刻周辺の機械センサー（belt_speed / motor_current / vibration /
   motor_temp / air_pressure）を確認する。これらが正常範囲（速度≈12・電流≈3.0A・振動≈0.4mm/s・
   温度≈42℃・エア圧≈0.50MPa）なら、過負荷・異常振動・過熱・エア圧低下といった
   「センサーに現れる故障」ではないと判断する（＝映像でしか捉えられない機械的な位置決め異常）。
2) 検知された整列異常の種類とピーク量から真因を推定する。横ズレ(offset)＋角度(rotation)が同時なら
   位置決め治具・送り機構の精度低下や摩耗、間隔(gap)異常なら送りピッチ・インデックス機構の異常。
3) search_past_cases で類似事例を検索する。現在の異常の計測状況（種別・ピーク・センサー状況）は
   自動でキーに付与されるため、引数には観察された現象の記述だけを渡す（自分の推測した原因語で
   検索しない）。類似事例があれば correct_cause を最有力候補に採用し、action_taken（前回の処置）が
   あれば evidence に「前回の処置: …」として含める。
4) evidence には映像で検知した量（offset px・rotation deg 等）と、各センサーが正常だった値を必ず含める。
5) category は真因の分類を次の4値から必ず1つ選ぶ（この語彙以外は書かない）:
   positioning=位置決め治具・整列機構 / conveyance=搬送・ガイドレール・ベルト・送り機構 /
   sensor=センサー系の異常・誤検知 / other=上記以外・判断不能。
最後に必ず次のJSONのみを出力してください（前後に文章を付けない）:
{"cause_candidates": ["最有力の真因", "次点"], "confidence": 0.0〜1.0, "evidence": ["参照した数値やログ"], "category": "positioning|conveyance|sensor|other"}
confidence は断定の 1.0 を使わない。過去事例が一致しても現地確認前の推定である以上、最大 0.9 程度に留める。
"""


# Vertex 429 retry is OFF by default in google-genai — enable it explicitly
# (de-risk #4): exponential backoff, also covering transient 5xx.
_RETRY = types.HttpRetryOptions(attempts=4, initialDelay=1.0, maxDelay=8.0,
                                expBase=2.0, httpStatusCodes=[429, 500, 503])


def _model() -> Gemini:
    return Gemini(model=GCP.gemini_model, retry_options=_RETRY)


def build_agent() -> Agent:
    return Agent(
        name="rca_orchestrator",
        model=_model(),
        instruction=_INSTRUCTION,
        tools=[query_line_sensors, query_logs, search_past_cases],
    )


_CHAT_INSTRUCTION = """あなたは工場ライン監視のアシスタントです。
ユーザーの質問に答えるため、必要に応じてツールで機械センサー（belt_speed / motor_current /
vibration / motor_temp / air_pressure）や過去事例を照会し、参照した数値を根拠として簡潔に日本語で回答してください。
正常の目安: 速度≈12 m/min・電流≈3.0A・振動≈0.4mm/s・温度≈42℃・エア圧≈0.50MPa。
なお整列異常（横ズレ・角度・間隔）はカメラ映像で検知します。センサーは正常でも映像で異常を捉える点に留意してください。
利用可能なログは 0〜10秒 の範囲です。ユーザーが時間範囲を明示しない場合（「直近」「最近」等を含む）は、
必ず 0〜10秒 の全体を対象に query_logs を呼び出してください。安易に「データが無い」と答えないこと。
本当に対象チャネルのデータが無い場合のみ、その旨を明示してください。
重要: 異常検知でラインが停止すると、それ以降 belt_speed やモータ電流は 0 付近まで低下します。
これは停止の【結果】であり、整列異常の【原因】ではありません。因果を逆転させないこと。
原因を問われたら停止前の値と映像検知（整列ズレの種類・量）に基づいて答え、
必要なら search_past_cases の類似事例も参照してください。
回答は Markdown 記法（** や # など）を使わないプレーンテキストで書いてください（画面は装飾を解釈しません）。
"""


def build_chat_agent() -> Agent:
    return Agent(
        name="line_assistant",
        model=_model(),
        instruction=_CHAT_INSTRUCTION,
        tools=[query_line_sensors, query_logs, search_past_cases],
    )


# HITL correction elicitor (Req 8/9): when the operator rejects the AI's cause,
# this agent draws out the field's tacit knowledge through natural dialogue —
# not a form prompt — and records it as a reusable past case. The write is the
# one deterministic step (record_correction → server-side persistence + audit).
_CORRECTION_INSTRUCTION = """あなたは工場ライン監視AIの「学習・訂正」担当アシスタントです。
現場オペレーターが、AIの原因推定を「原因が違う」と裁定しました。あなたの目的は、
オペレーターが持つ現場の暗黙知を自然な対話で引き出し、次回の推定に活かせる形で記録することです。

進め方:
1) まず一言で受け止め（謝意・共感）、「現場では何が原因だと見ているか」を”1つの質問”で尋ねる。
   詰問・尋問にしない。フォームのように矢継ぎ早に聞かない。
2) オペレーターの回答が具体的な機械・部位・状態（例: ガイドレールのボルト緩み、治具の摩耗）を含むなら、
   要点を一度だけ短く復唱して確認する。その同じ復唱の一言に「どのように直したかも、差し支えなければ
   一言お願いします（任意）」を添えてよい。曖昧なら（「なんか変」等）、具体化する質問を”1つだけ”返す。
   掘り下げは合計で最大1〜2往復にとどめる。処置の回答がなくても記録を進めてよい（追加で催促しない）。
3) 真因が確認できたら record_correction(correct_cause, evidence_note, action_taken) を必ず呼び出して記録する。
   correct_cause はオペレーターの言葉を尊重した簡潔な真因。evidence_note は補足（見た兆候・確認した箇所）。
   action_taken は復旧のためにした処置（述べられた場合のみ。無ければ空のまま）。
4) 記録が成功したら、感謝と「次に同じ異常が出たら、この原因を最優先の候補として提示します」旨を一言添える。

厳守: 原因を勝手に創作しない。オペレーターが述べた内容だけを記録する。ツールが未記録を返したら、
その理由に従ってもう一度だけ確認の質問を返す。回答は常に簡潔な日本語で、
Markdown 記法（** など）を使わないプレーンテキストで書く。
"""


def build_correction_agent() -> Agent:
    return Agent(
        name="correction_elicitor",
        model=_model(),
        instruction=_CORRECTION_INSTRUCTION,
        tools=[record_correction, search_past_cases],
    )


_BUILDERS = {"rca": build_agent, "chat": build_chat_agent, "correction": build_correction_agent}
_runners: dict[str, Runner] = {}
_chat_sessions: dict[str, str] = {}  # user_id -> session_id (survives via SESSION_DB_URL)
_correction_sessions: dict[str, str] = {}  # f"{user_id}:{event_id}" -> session_id


def _runner(kind: str) -> Runner:
    """One Runner per agent kind — avoids re-creating the session service
    (and its DB pool) on every request."""
    if kind not in _runners:
        _runners[kind] = Runner(agent=_BUILDERS[kind](), app_name=_APP,
                                session_service=_session_service())
    return _runners[kind]


async def answer_query(question: str, user_id: str = "line-op") -> str:
    """Answer a free-form operator question over the logs (Req 6.2/6.4).

    Multi-turn: the session is reused per user, so follow-ups like
    「さっきの異常の件だけど」 keep their context (persisted in Cloud SQL
    through DatabaseSessionService when SESSION_DB_URL is set).
    """
    runner = _runner("chat")
    sid = _chat_sessions.get(user_id)
    if sid is not None and await runner.session_service.get_session(
            app_name=_APP, user_id=user_id, session_id=sid) is None:
        sid = None  # session evicted (e.g. DB reset) — start a new one
    if sid is None:
        session = await runner.session_service.create_session(app_name=_APP, user_id=user_id)
        sid = _chat_sessions[user_id] = session.id
    msg = types.Content(role="user", parts=[types.Part(text=question)])
    out = ""
    async for ev in runner.run_async(user_id=user_id, session_id=sid, new_message=msg):
        if ev.is_final_response() and ev.content and ev.content.parts:
            out = "".join(p.text or "" for p in ev.content.parts)
    return out or "（応答を生成できませんでした）"


async def elicit_correction(event_ctx: dict, message: str, user_id: str = "line-op") -> dict:
    """Drive one turn of the HITL correction dialogue (Req 8/9).

    ``event_ctx`` = {event_id, kind, ai_cause}. An empty ``message`` opens the
    dialogue (the agent asks the operator for their read); a non-empty one is the
    operator's reply. The session is per (user, event) so the exchange keeps its
    context. Returns {"reply", "recorded", "cause"} — ``recorded`` flips to True
    the turn the agent commits the correction via record_correction.
    """
    runner = _runner("correction")
    key = f"{user_id}:{event_ctx['event_id']}"
    sid = _correction_sessions.get(key)
    if sid is not None and await runner.session_service.get_session(
            app_name=_APP, user_id=user_id, session_id=sid) is None:
        sid = None  # session evicted (e.g. DB reset) — start a new one
    if sid is None:
        session = await runner.session_service.create_session(app_name=_APP, user_id=user_id)
        sid = _correction_sessions[key] = session.id
    text = (message or "").strip() or (
        f"（オペレーターがAIの推定「{event_ctx.get('ai_cause', '')}」を『原因が違う』と裁定しました。"
        f"検知された異常種別={event_ctx.get('kind', '')}。対話を始め、現場の見立てを一つ質問してください。）")

    holder = {"event": event_ctx, "recorded": False, "cause": None}
    token = _active_correction.set(holder)
    ev_token = bind_active_event(event_ctx)  # situation-keyed search here too
    try:
        msg = types.Content(role="user", parts=[types.Part(text=text)])
        out = ""
        async for ev in runner.run_async(user_id=user_id, session_id=sid, new_message=msg):
            if ev.is_final_response() and ev.content and ev.content.parts:
                out = "".join(p.text or "" for p in ev.content.parts)
    finally:
        reset_active_event(ev_token)
        _active_correction.reset(token)
    return {"reply": out or "…", "recorded": holder["recorded"], "cause": holder["cause"]}


def _session_service():
    url = GCP.session_db_url
    if url:
        from google.adk.sessions import DatabaseSessionService  # requires sqlalchemy+asyncpg

        logger.info("using DatabaseSessionService (persistent)")
        return DatabaseSessionService(db_url=url)
    logger.info("using InMemorySessionService (local dev)")
    return InMemorySessionService()


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _photo_evidence(event: AnomalyEvent) -> list[types.Part]:
    """Multimodal reflux (human-loop Req 9.3): among the closest past cases,
    attach the FIRST one that carries a field photo — always at most one image
    (context/cost cap). Never breaks inference: any failure returns no parts."""
    try:
        import attachments_store
        import past_cases as pc

        # wider net than the agent's own few-shot search (k=8): text-only
        # corrections cluster at the top, but we specifically want the nearest
        # case that carries a photo — still exactly one image attached.
        # Same situation textizer as store/search → same embedding space.
        hits = pc.search(situation.situation_text(
            event.kind, event.peak_magnitude, event.started_ts, event.ended_ts), k=8)
        top = next((c for c in hits if c.attachment_uri), None)
        if top is None or not top.attachment_uri:
            return []
        data = attachments_store.get_bytes(top.attachment_uri)
        if not data:
            return []
        logger.info("attaching past-case field photo",
                    extra={"ctx": {"case": top.source_event_id}})
        return [types.Part(text=f"参考: 類似の過去事例（確定原因: {top.correct_cause}）で"
                                f"現場が撮影した原因箇所の写真を添付します。"),
                types.Part.from_bytes(
                    data=data, mime_type=attachments_store.mime_of(top.attachment_uri))]
    except Exception:
        logger.warning("photo evidence lookup failed (continuing text-only)",
                       exc_info=True)
        return []


async def infer(event: AnomalyEvent, user_id: str = "line-op") -> RcaResult:
    """Run the RCA agent over an anomaly event and return a structured result.

    The tool calls the agent chose are appended to ``evidence`` — the trace is
    the proof of autonomy (which tools, in which order), not just the verdict.
    """
    runner = _runner("rca")
    session = await runner.session_service.create_session(app_name=_APP, user_id=user_id)
    prompt = (
        f"異常イベント: id={event.event_id} 種別={event.kind} "
        f"ピーク逸脱={event.peak_magnitude:.1f} 発生時刻={event.started_ts:.2f}s。"
        f"この異常の原因を推定してください。"
    )
    parts = [types.Part(text=prompt)]
    parts += _photo_evidence(event)  # top-1 past-case field photo (human-loop Req 9.3)
    msg = types.Content(role="user", parts=parts)
    final_text = ""
    tool_calls: list[str] = []
    # bind the event so search_past_cases keys on the measured situation,
    # not on whatever hypothesis text the model writes (server-built key)
    ev_token = bind_active_event(event.model_dump())
    try:
        async for ev in runner.run_async(user_id=user_id, session_id=session.id, new_message=msg):
            tool_calls += [fc.name for fc in (ev.get_function_calls() or []) if fc.name]
            if ev.is_final_response() and ev.content and ev.content.parts:
                final_text = "".join(p.text or "" for p in ev.content.parts)
    finally:
        reset_active_event(ev_token)

    trace = f"ツール呼び出し（エージェントが自律選択）: {' → '.join(tool_calls)}" if tool_calls else None
    data = _extract_json(final_text)
    if data is None:
        logger.warning("RCA output not parseable; returning low-confidence result")
        return RcaResult(event_id=event.event_id, cause_candidates=["推定不能"],
                         confidence=0.0, evidence=[final_text[:200]] + ([trace] if trace else []))
    evidence = list(data.get("evidence", []))[:6]
    if trace:
        evidence.append(trace)
    return RcaResult(
        event_id=event.event_id,
        cause_candidates=list(data.get("cause_candidates", []))[:3] or ["推定不能"],
        # deterministic cap outside the LLM: self-reported confidence is
        # calibration-free, so a flat 1.0 (「100%」) must never reach operators
        confidence=min(float(data.get("confidence", 0.0)), 0.95),
        evidence=evidence,
        # vocabulary guard outside the LLM: routing keys on this (Req 5.2/5.4)
        category=normalize_category(data.get("category")),
    )
