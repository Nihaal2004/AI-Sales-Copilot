"""Explicit first-assignment rules. Neither prose nor approval grants permission."""

from datetime import datetime, timezone

from .corpus import Corpus, PolicyError

RULE_VERSION = "stage3-first-assignment-v1"
APPROVED_CORPUS = "64a7f402e0be29c2c4b389fd1e0bc9d5f42a57ea31af9ade702e34b42f629c7e"
ACTIVE = ("new", "contacted", "qualified")
CAPACITY_LIMIT = 25  # Existing CRM-CAP-006, explicitly retained by the user.
ABSOLUTE_MAXIMUM = 50
ORDINARY = {"retail", "manufacturing", "general_software"}
RESTRICTED = {"healthcare", "financial_services", "insurance", "public_sector"}
CITIES = {"Chennai", "Bangalore", "Hyderabad"}


def outcome(status, error=None, **fields):
    return {"ok": status in {"success", "already_completed"}, "status": status,
            **({"error": error} if error else {}), **fields}


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def future(value, now):
    parsed = timestamp(value)
    return parsed is not None and parsed > now


def actor_allowed(conn, actor_id):
    if not actor_id:
        return outcome("forbidden", "actor_identity_required")
    actor = conn.execute("SELECT * FROM crm_actors WHERE actor_id=?", (actor_id,)).fetchone()
    if actor is None or actor["enabled"] != 1 or actor["can_assign"] != 1 or not actor["source"].strip():
        return outcome("forbidden", "actor_not_authorized")
    return None


def authorize(conn, actor_id, action, corpus_dir):
    """Called on a fresh snapshot and again under BEGIN IMMEDIATE before the update."""
    denied = actor_allowed(conn, actor_id)
    if denied:
        return denied
    try:
        if Corpus(corpus_dir).fingerprint != APPROVED_CORPUS:
            return outcome("policy_blocked", "policy_rules_require_review")
    except PolicyError:
        return outcome("policy_blocked", "policy_rules_unavailable")
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (action.lead_id,)).fetchone()
    if lead is None:
        return outcome("conflict", "lead_not_found")
    agent = conn.execute("SELECT * FROM agents WHERE id=?", (action.assignee_id,)).fetchone()
    if agent is None:
        return outcome("conflict", "assignee_not_found")
    if lead["owner_id"] is not None:
        return outcome("conflict", "lead_already_assigned")
    if lead["status"] not in ACTIVE:
        return outcome("policy_blocked", "closed_lead_not_assignable")
    intake = conn.execute("SELECT * FROM lead_intake WHERE lead_id=?", (action.lead_id,)).fetchone()
    now = datetime.now(timezone.utc)
    if (intake is None or not intake["source"].strip() or timestamp(intake["verified_at"]) is None
            or timestamp(intake["verified_at"]) > now or intake["industry"] not in ORDINARY | RESTRICTED):
        return outcome("policy_blocked", "verified_intake_required")
    if intake["territory_confirmed"] != 1 or lead["city"] not in CITIES or lead["city"] != agent["city"]:
        return outcome("policy_blocked", "territory_ineligible")
    if lead["score"] >= 90 or intake["strategic"] != 0:
        return outcome("policy_blocked", "vip_management_workflow_out_of_scope")
    if lead["priority"] not in {"low", "medium", "high"}:
        return outcome("policy_blocked", "priority_unknown")
    roster = conn.execute("SELECT * FROM agent_roster WHERE agent_id=?", (action.assignee_id,)).fetchone()
    if (roster is None or not roster["source"].strip() or roster["available"] != 1
            or roster["response_window_confirmed"] != 1 or not future(roster["valid_until"], now)):
        return outcome("policy_blocked", "agent_availability_unverified")
    if intake["industry"] in RESTRICTED:
        if not future(intake["compliance_review_until"], now):
            return outcome("policy_blocked", "compliance_intake_review_required")
        cert = conn.execute("SELECT * FROM agent_certifications WHERE agent_id=? AND industry=?",
                            (action.assignee_id, intake["industry"])).fetchone()
        if (cert is None or cert["suspended"] != 0 or not all(cert[k].strip() for k in ("source", "issuer", "scheme"))
                or timestamp(cert["valid_from"]) is None or timestamp(cert["valid_from"]) > now
                or not future(cert["valid_until"], now)):
            return outcome("policy_blocked", "current_industry_certification_required")
        if timestamp(cert["valid_until"]) < timestamp(roster["valid_until"]):
            return outcome("policy_blocked", "certification_does_not_cover_contact_window")
    count = conn.execute("SELECT count(*) FROM leads WHERE owner_id=? AND status IN ('new','contacted','qualified')",
                         (action.assignee_id,)).fetchone()[0]
    if count >= min(CAPACITY_LIMIT, ABSOLUTE_MAXIMUM):
        return outcome("capacity_exceeded", "active_lead_capacity_exceeded", active_count=count, capacity_limit=CAPACITY_LIMIT)
    return None
