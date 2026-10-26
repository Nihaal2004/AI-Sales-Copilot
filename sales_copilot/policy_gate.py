"""Action-bound retrieval evidence checks, deliberately not business authorization."""

import json

from .corpus import PolicyError, digest
from .schemas import AssignLead, GetLeadDetails


def action_fingerprint(action: dict) -> str:
    validated = AssignLead.model_validate(action).model_dump()
    return digest(json.dumps(validated, sort_keys=True, separators=(",", ":")))


def retrieve_for_action(crm, retriever, action: dict) -> dict:
    lead = crm.get_lead_details(GetLeadDetails(lead_id=action["lead_id"]))
    if lead is None:
        raise PolicyError("lead_not_found")
    agent = next((a for a in crm.list_agents() if a["id"] == action["assignee_id"]), None)
    if agent is None:
        raise PolicyError("assignee_not_found")
    # Use verified, non-contact CRM fields. Do not infer an industry, certificate
    # or availability from names; this schema has no such business facts.
    kind = "initial assignment of unassigned lead" if lead["owner_id"] is None else "reassignment ownership handover"
    query = (f"Lead assignment ownership review {kind}. Geographic territory {lead['city']} "
             f"receiving agent territory {agent['city']}. {lead['priority']} priority "
             + ("VIP score 90 or above. " if lead["score"] >= 90 else "")
             + "Human confirmation and management review requirements.")
    result = retriever.compact(retriever.search(query, top_k=5))
    if result.get("ok") is not True:
        raise PolicyError(result.get("error", "policy_evidence_not_found"))
    # Minimal topical coverage, not an eligibility/compliance decision.
    if not any(r["policy_id"] in {"CRM-OWN-010", "CRM-TERR-001", "CRM-REASSIGN-005"}
               for r in result["policy_refs"]):
        raise PolicyError("policy_evidence_not_applicable")
    return {**result, "action_fingerprint": action_fingerprint(action)}


def validate_action_evidence(retriever, action: dict, evidence: dict | None):
    if not evidence or evidence.get("action_fingerprint") != action_fingerprint(action):
        raise PolicyError("policy_action_binding_invalid")
    retriever.validate_references(evidence.get("policy_refs", []), evidence.get("corpus_fingerprint"))
    if not any(r["policy_id"] in {"CRM-OWN-010", "CRM-TERR-001", "CRM-REASSIGN-005"}
               for r in evidence["policy_refs"]):
        raise PolicyError("policy_evidence_not_applicable")
