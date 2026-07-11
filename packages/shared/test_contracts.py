"""Contract tests for andon-human-loop additions (tasks.md 1.1, Req 4.1/5.1).

Behavior under test: the typed boundaries new surfaces (Slack, routing,
escalation) rely on — defaults, closed vocabularies, and required fields.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from chokotei_shared import (
    Actor,
    EscalationStep,
    NotificationRecord,
    RcaResult,
    RoutingDecision,
)


def test_rca_result_defaults_category_to_other_for_backward_compat():
    """Req 5.1: rows/callers predating the category field keep working."""
    r = RcaResult(event_id="ev-1", cause_candidates=["c"], confidence=0.5, evidence=[])
    assert r.category == "other"


def test_rca_result_accepts_closed_vocabulary_only():
    """Req 5.2: category is a closed enum — the deterministic routing key."""
    ok = RcaResult(event_id="ev-1", cause_candidates=["c"], confidence=0.5,
                   evidence=[], category="positioning")
    assert ok.category == "positioning"
    with pytest.raises(ValidationError):
        RcaResult(event_id="ev-1", cause_candidates=["c"], confidence=0.5,
                  evidence=[], category="位置決め")  # free text must not pass the boundary


def test_actor_surface_is_dashboard_or_slack_only():
    """Req 4.1/4.2: every verdict is attributed to a known surface."""
    a = Actor(surface="slack", user_id="U123", display_name="Suzuki")
    assert a.display_name == "Suzuki"
    assert Actor(surface="dashboard", user_id="line-op").display_name is None
    with pytest.raises(ValidationError):
        Actor(surface="email", user_id="x")


def test_routing_decision_carries_full_audit_material():
    """Req 5.6: category, rule version, and the escalation plan are recorded."""
    d = RoutingDecision(
        event_id="ev-1", category="positioning", rule_version=1,
        primary_mention="<@U_MAINT>",
        escalation_plan=[
            EscalationStep(tier=2, delay_s=300, target_mention="<@U_LEAD>"),
            EscalationStep(tier=3, delay_s=900, target_mention=None,
                           contact_note="ベンダー窓口 0120-000-000"),
        ])
    assert [s.tier for s in d.escalation_plan] == [2, 3]
    with pytest.raises(ValidationError):
        EscalationStep(tier=1, delay_s=300, target_mention=None)  # tier1 = the card itself


def test_notification_record_is_the_idempotency_key():
    """Req 1.5: one card per event — event_id is the primary key material."""
    rec = NotificationRecord(event_id="ev-1", channel_id="C1",
                             message_ts="1720000000.000100", posted_at=1720000000.0)
    assert rec.event_id == "ev-1" and rec.message_ts


def test_categorize_passes_explicit_enum_through():
    from chokotei_shared import categorize
    assert categorize("positioning", "ガイドレールの緩み") == "positioning"


def test_categorize_falls_back_to_cause_keywords_when_enum_missing():
    """Gemini omitting the enum must not collapse the Pareto/routing to
    "other" — the deterministic keyword map recovers the bucket."""
    from chokotei_shared import categorize
    assert categorize(None, "搬送ガイドレール固定ボルトの緩みによる横ズレ") == "conveyance"
    assert categorize("", "位置決め治具の摩耗・ガタによる整列精度低下") == "positioning"
    assert categorize("other", "カメラの誤検知の可能性") == "sensor"
    # positioning wins over a co-occurring conveyance word (order of buckets)
    assert categorize(None, "位置決め治具のズレでベルトに干渉") == "positioning"


def test_categorize_stays_other_when_nothing_matches():
    from chokotei_shared import categorize
    assert categorize(None, "原因不明") == "other"
    assert categorize(None) == "other"
