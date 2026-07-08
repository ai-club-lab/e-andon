"""Deterministic cause→duty routing (design §4.3, Req 5).

The notification target is resolved by a table JOIN only — the LLM's output
never drives it directly (Req 5.2): the model suggests a category from a
closed enum, ``normalize_category`` guards the vocabulary at the server, and
this module maps category → duty roster. Rules live in Cloud SQL
``routing_rules`` (updatable via SQL, no redeploy — Req 5.5) with an in-code
fallback for DB-less environments.
"""
from __future__ import annotations

import logging

from chokotei_shared import CauseCategory, EscalationStep, RoutingDecision, db

logger = logging.getLogger("routing")

# Mirrors the schema.sql seed — used when no database is configured (local/CI).
_FALLBACK_RULES: dict[str, dict] = {
    "positioning": {"primary_mention": "（保全担当・位置決め）", "tier2_mention": "（班長）",
                    "tier2_delay_s": 300, "tier3_contact": "設備ベンダー保守窓口 0120-000-000（デモ値）",
                    "tier3_delay_s": 900, "version": 1},
    "conveyance": {"primary_mention": "（保全担当・搬送）", "tier2_mention": "（班長）",
                   "tier2_delay_s": 300, "tier3_contact": "設備ベンダー保守窓口 0120-000-000（デモ値）",
                   "tier3_delay_s": 900, "version": 1},
    "sensor": {"primary_mention": "（計装担当）", "tier2_mention": "（班長）",
               "tier2_delay_s": 300, "tier3_contact": "センサーベンダー窓口 0120-111-111（デモ値）",
               "tier3_delay_s": 900, "version": 1},
    "other": {"primary_mention": "（班長）", "tier2_mention": "（班長）",
              "tier2_delay_s": 300, "tier3_contact": "設備ベンダー保守窓口 0120-000-000（デモ値）",
              "tier3_delay_s": 900, "version": 1},
}


def _rules() -> dict[str, dict]:
    if not db.enabled():
        return _FALLBACK_RULES
    rows = db.fetch(
        "SELECT category, primary_mention, tier2_mention, tier2_delay_s, "
        "tier3_contact, tier3_delay_s, version FROM routing_rules")
    return {r["category"]: r for r in rows} or _FALLBACK_RULES


def resolve(event_id: str, category: CauseCategory) -> RoutingDecision:
    """Table JOIN only (Req 5.2). Unregistered category → default duty (班長)
    and the miss is recorded (Req 5.4). The full decision material is returned
    for the audit trail (Req 5.6)."""
    rules = _rules()
    rule = rules.get(category)
    if rule is None:
        logger.warning("unregistered routing category — using default",
                       extra={"ctx": {"event_id": event_id, "category": category}})
        rule = rules.get("other") or _FALLBACK_RULES["other"]
    decision = RoutingDecision(
        event_id=event_id, category=category, rule_version=int(rule["version"]),
        primary_mention=rule["primary_mention"],
        escalation_plan=[
            EscalationStep(tier=2, delay_s=int(rule["tier2_delay_s"]),
                           target_mention=rule["tier2_mention"]),
            EscalationStep(tier=3, delay_s=int(rule["tier3_delay_s"]),
                           target_mention=None, contact_note=rule["tier3_contact"]),
        ])
    logger.info("routing decision",
                extra={"ctx": decision.model_dump()})  # audit trail (Req 5.6)
    return decision
