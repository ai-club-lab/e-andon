"""Deterministic routing tests (andon-human-loop tasks 4.1–4.4, Req 5).

The LLM's only contribution is picking one value from a closed enum; everything
after that — normalization, table lookup, escalation plan — is deterministic
and covered here without any model or DB.
Run: PYTHONPATH=services/dashboard:services/agent:services/detector \
     python -m pytest -q services/dashboard/test_routing.py
"""
from __future__ import annotations

import routing
from chokotei_shared import normalize_category


def test_normalize_accepts_enum_and_rejects_free_text():
    """Req 5.1/5.4: anything outside the closed vocabulary becomes 'other'."""
    assert normalize_category("positioning") == "positioning"
    assert normalize_category("conveyance") == "conveyance"
    assert normalize_category("sensor") == "sensor"
    assert normalize_category("位置決め治具の摩耗") == "other"   # free text
    assert normalize_category("") == "other"
    assert normalize_category(None) == "other"
    assert normalize_category(" Positioning ") == "positioning"  # tolerate case/space


def test_resolve_maps_each_category_to_its_duty(monkeypatch):
    """Req 5.2/5.3: mention comes from the table JOIN only."""
    d = routing.resolve("evt-1", "positioning")
    assert d.category == "positioning"
    assert d.primary_mention  # seeded duty roster
    assert d.event_id == "evt-1" and d.rule_version >= 1
    assert [s.tier for s in d.escalation_plan] == [2, 3]
    assert d.escalation_plan[0].target_mention           # tier2 = 班長 mention
    assert d.escalation_plan[1].contact_note             # tier3 = vendor contact
    assert d.escalation_plan[1].target_mention is None   # Req 6.3: no auto-page


def test_resolve_falls_back_to_default_for_unregistered(monkeypatch):
    """Req 5.4: unregistered category routes to the default (班長) and is logged."""
    monkeypatch.setattr(routing, "_rules", lambda: {
        k: v for k, v in routing._FALLBACK_RULES.items() if k != "sensor"})
    d = routing.resolve("evt-2", "sensor")
    assert d.category == "sensor"                        # category preserved for audit
    assert d.primary_mention == routing._FALLBACK_RULES["other"]["primary_mention"]


def test_resolve_decision_carries_audit_material():
    """Req 5.6: the decision object holds category, rule version, full plan."""
    d = routing.resolve("evt-3", "conveyance")
    dumped = d.model_dump()
    assert {"event_id", "category", "rule_version", "primary_mention",
            "escalation_plan"} <= set(dumped)
