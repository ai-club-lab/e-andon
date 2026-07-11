"""Situation-key tests — the deterministic textizer behind past-case
storage and retrieval (key/value split: measured facts only, never causes).

Run: PYTHONPATH=services/dashboard:services/agent:services/detector \
     python -m pytest -q services/dashboard/test_situation.py
"""
from __future__ import annotations

import situation
import tools
from chokotei_shared import IoTReading


def _readings(**channel_values: float) -> list[IoTReading]:
    return [IoTReading(ts=8.5, channel=ch, value=v)  # type: ignore[arg-type]
            for ch, v in channel_values.items()]


def test_should_quantize_key_so_recurrences_produce_identical_text(monkeypatch) -> None:
    """Same signature (kind + rounded peak + window) → same string, which is
    what makes confirmed-case dedupe work by exact match."""
    monkeypatch.setattr(situation.iot_store, "query_window", lambda *a, **k: [])
    a = situation.situation_text("offset", 16.2, 8.5, 9.5)
    b = situation.situation_text("offset", 15.8, 8.5, 9.5)
    assert a == b
    assert "offset" in a and "16px" in a and "継続1.0s" in a


def test_should_report_unknown_when_no_sensor_data(monkeypatch) -> None:
    monkeypatch.setattr(situation.iot_store, "query_window", lambda *a, **k: [])
    assert "センサー状況不明" in situation.situation_text("gap", 0.4, 3.0, 4.0)


def test_should_report_all_normal_when_sensors_in_band(monkeypatch) -> None:
    monkeypatch.setattr(situation.iot_store, "query_window",
                        lambda *a, **k: _readings(belt_speed=12.0, motor_current=3.0,
                                                  vibration=0.38, motor_temp=42.0,
                                                  air_pressure=0.50))
    assert "センサー全チャネル正常" in situation.situation_text("offset", 16, 8.5)


def test_should_name_deviating_channels_when_out_of_band(monkeypatch) -> None:
    monkeypatch.setattr(situation.iot_store, "query_window",
                        lambda *a, **k: _readings(belt_speed=0.03, motor_temp=42.0))
    text = situation.situation_text("offset", 16, 8.5)
    assert "センサー逸脱" in text and "belt_speed=0.03" in text
    assert "motor_temp" not in text


def test_should_key_search_on_measured_situation_not_hypothesis(monkeypatch) -> None:
    """Hybrid retrieval: the agent decides WHEN to search; the server builds
    WHAT keys it — the bound event's situation, with the model's free text
    demoted to a symptom suffix."""
    monkeypatch.setattr(situation.iot_store, "query_window", lambda *a, **k: [])
    queries: list[str] = []
    monkeypatch.setattr(tools.pc, "search",
                        lambda q, k=3: queries.append(q) or [])
    token = tools.bind_active_event(
        {"kind": "offset", "peak_magnitude": 16.2, "started_ts": 8.5, "ended_ts": 9.5})
    try:
        tools.search_past_cases("部品が右に寄って流れる")
    finally:
        tools.reset_active_event(token)
    assert queries[0].startswith("映像検知")
    assert "16px" in queries[0] and queries[0].endswith("現象: 部品が右に寄って流れる")
    # unbound (e.g. free chat) → the raw free text passes through unchanged
    tools.search_past_cases("ベルトの様子がおかしい")
    assert queries[1] == "ベルトの様子がおかしい"
