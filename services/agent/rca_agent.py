"""RCA orchestrator agent (design.md §8, Req 5).

Root LlmAgent (Gemini 2.5 Flash on Vertex via ADC) with FunctionTools for
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
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from chokotei_shared import GCP, AnomalyEvent, RcaResult
from tools import get_frame, query_line_sensors, query_logs, search_past_cases

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
3) search_past_cases で類似事例を検索し、あれば correct_cause を最有力候補に採用する。
4) evidence には映像で検知した量（offset px・rotation deg 等）と、各センサーが正常だった値を必ず含める。
最後に必ず次のJSONのみを出力してください（前後に文章を付けない）:
{"cause_candidates": ["最有力の真因", "次点"], "confidence": 0.0〜1.0, "evidence": ["参照した数値やログ"]}
"""


def build_agent() -> Agent:
    return Agent(
        name="rca_orchestrator",
        model=GCP.gemini_model,
        instruction=_INSTRUCTION,
        tools=[query_line_sensors, query_logs, search_past_cases, get_frame],
    )


_CHAT_INSTRUCTION = """あなたは工場ライン監視のアシスタントです。
ユーザーの質問に答えるため、必要に応じてツールで機械センサー（belt_speed / motor_current /
vibration / motor_temp / air_pressure）や過去事例を照会し、参照した数値を根拠として簡潔に日本語で回答してください。
正常の目安: 速度≈12 m/min・電流≈3.0A・振動≈0.4mm/s・温度≈42℃・エア圧≈0.50MPa。
なお整列異常（横ズレ・角度・間隔）はカメラ映像で検知します。センサーは正常でも映像で異常を捉える点に留意してください。
利用可能なログは 0〜10秒 の範囲です。ユーザーが時間範囲を明示しない場合（「直近」「最近」等を含む）は、
必ず 0〜10秒 の全体を対象に query_logs を呼び出してください。安易に「データが無い」と答えないこと。
本当に対象チャネルのデータが無い場合のみ、その旨を明示してください。
"""


def build_chat_agent() -> Agent:
    return Agent(
        name="line_assistant",
        model=GCP.gemini_model,
        instruction=_CHAT_INSTRUCTION,
        tools=[query_line_sensors, query_logs, search_past_cases],
    )


_runners: dict[str, Runner] = {}
_chat_sessions: dict[str, str] = {}  # user_id -> session_id (survives via SESSION_DB_URL)


def _runner(kind: str) -> Runner:
    """One Runner per agent kind — avoids re-creating the session service
    (and its DB pool) on every request."""
    if kind not in _runners:
        agent = build_agent() if kind == "rca" else build_chat_agent()
        _runners[kind] = Runner(agent=agent, app_name=_APP, session_service=_session_service())
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
    msg = types.Content(role="user", parts=[types.Part(text=prompt)])
    final_text = ""
    tool_calls: list[str] = []
    async for ev in runner.run_async(user_id=user_id, session_id=session.id, new_message=msg):
        tool_calls += [fc.name for fc in (ev.get_function_calls() or []) if fc.name]
        if ev.is_final_response() and ev.content and ev.content.parts:
            final_text = "".join(p.text or "" for p in ev.content.parts)

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
        confidence=float(data.get("confidence", 0.0)),
        evidence=evidence,
    )
