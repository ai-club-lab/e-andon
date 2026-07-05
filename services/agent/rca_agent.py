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

_INSTRUCTION = """あなたは工場ラインの停止（チョコ停）の原因を推定するエンジニアです。
映像で部品の整列ズレが検知され、その後ベルトコンベアが停止しました。原因を推定してください。
手順:
1) 必ず query_line_sensors で異常時刻周辺のライン信号を確認する。次の因果を読み解く:
   ズレた部品が搬送機構で噛み込む → motor_current が定格(約3.0A)から急上昇 →
   belt_speed が 0 に低下し plc_status が 1→0（停止）。temperature 42℃前後は正常（無関係）。
2) 必ず search_past_cases で類似事例を検索する。類似事例があれば、その correct_cause を最有力候補に採用する。
3) evidence には実際に参照した数値（motor_current の max、belt_speed/plc_status の min 等）を必ず含める。
最後に必ず次のJSONのみを出力してください（前後に文章を付けない）:
{"cause_candidates": ["最有力の原因", "次点"], "confidence": 0.0〜1.0, "evidence": ["参照した数値やログ"]}
"""


def build_agent() -> Agent:
    return Agent(
        name="rca_orchestrator",
        model=GCP.gemini_model,
        instruction=_INSTRUCTION,
        tools=[query_line_sensors, query_logs, search_past_cases, get_frame],
    )


_CHAT_INSTRUCTION = """あなたは工場ライン監視のアシスタントです。
ユーザーの質問に答えるため、必要に応じてツールでライン信号（motor_current / belt_speed /
plc_status / temperature）や過去事例を照会し、参照した数値を根拠として簡潔に日本語で回答してください。
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


async def answer_query(question: str, user_id: str = "line-op") -> str:
    """Answer a free-form operator question over the logs (Req 6.2/6.4)."""
    runner = Runner(agent=build_chat_agent(), app_name=_APP, session_service=_session_service())
    session = await runner.session_service.create_session(app_name=_APP, user_id=user_id)
    msg = types.Content(role="user", parts=[types.Part(text=question)])
    out = ""
    async for ev in runner.run_async(user_id=user_id, session_id=session.id, new_message=msg):
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
    """Run the RCA agent over an anomaly event and return a structured result."""
    runner = Runner(agent=build_agent(), app_name=_APP, session_service=_session_service())
    session = await runner.session_service.create_session(app_name=_APP, user_id=user_id)
    prompt = (
        f"異常イベント: id={event.event_id} 種別={event.kind} "
        f"ピーク逸脱={event.peak_magnitude:.1f} 発生時刻={event.started_ts:.2f}s。"
        f"この異常の原因を推定してください。"
    )
    msg = types.Content(role="user", parts=[types.Part(text=prompt)])
    final_text = ""
    async for ev in runner.run_async(user_id=user_id, session_id=session.id, new_message=msg):
        if ev.is_final_response() and ev.content and ev.content.parts:
            final_text = "".join(p.text or "" for p in ev.content.parts)

    data = _extract_json(final_text)
    if data is None:
        logger.warning("RCA output not parseable; returning low-confidence result")
        return RcaResult(event_id=event.event_id, cause_candidates=["推定不能"],
                         confidence=0.0, evidence=[final_text[:200]])
    return RcaResult(
        event_id=event.event_id,
        cause_candidates=list(data.get("cause_candidates", []))[:3] or ["推定不能"],
        confidence=float(data.get("confidence", 0.0)),
        evidence=list(data.get("evidence", []))[:6],
    )
