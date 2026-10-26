"""Conversational output grounded only in verified structured tool results."""


def _lead_line(lead: dict) -> str:
    owner = "unassigned" if lead["owner_id"] is None else f"owner agent {lead['owner_id']}"
    return (f"Lead {lead['id']}: {lead['name']} at {lead['company']}; {lead['city']}; "
            f"status {lead['status']}; score {lead['score']}; {lead['priority']} priority; {owner}.")


def render_results(results: list[dict]) -> str:
    if not results:
        return ("No CRM tool was executed. Please provide a lead query or an assignment with lead and agent IDs. "
                "Example: show Chennai leads; show lead 1; assign lead 1 to agent 2.")
    lines = []
    for item in results:
        tool, result = item["tool"], item["result"]
        if tool == "completion":
            prefix = "Assignment needs clarification" if result.get("status") == "clarification" else "Assignment incomplete"
            lines.append(f"{prefix} ({result['error']}). {result['hint']}")
        elif result.get("ok") is not True:
            code = result.get("error", "operation_failed")
            status = f", HTTP {result['http_status']}" if result.get("http_status") is not None else ""
            hint = result.get("hint", "")
            if code == "approval_denied":
                hint = "Assignment rejected; CRM data was not changed."
            elif code == "invalid_approval_decision":
                hint = "The approval decision was invalid; CRM data was not changed."
            elif result.get("status") == "temporary_failure":
                hint = ("The outcome is not confirmed. Retry the same approved operation with /retry, or look up "
                        "its original operation ID after restarting. Do not create a new key to retry it.")
            elif code == "lead_already_assigned":
                hint = "Only first-time assignment is supported. The existing owner was preserved."
            elif result.get("status") == "capacity_exceeded":
                hint = "The receiving agent has reached the 25 active-lead ceiling. No override is supported."
            elif code in {"actor_identity_required", "actor_not_authorized"}:
                hint = "An explicitly provisioned host identity with first-assignment permission is required."
            lines.append(f"{tool} failed ({code}{status})." + (f" {hint}" if hint else ""))
        elif tool == "assign_lead":
            if result.get("status") == "already_completed":
                lines.append(f"Previously committed operation: SQLite recorded lead {result['lead_id']} "
                             f"assigned to agent {result['owner_id']}. No new assignment was made.")
            else:
                lines.append(f"SQLite confirmed: lead {result['lead_id']} assigned to agent {result['owner_id']}.")
        elif tool == "search_leads":
            args = item.get("arguments", {})
            noun = "unassigned lead" if args.get("unassigned_only") else "lead"
            context = f" in {args['city']}" if args.get("city") else ""
            if args.get("status"):
                context += f" with status {args['status']}"
            if args.get("owner_id") is not None:
                context += f" owned by agent {args['owner_id']}"
            leads = result["leads"]
            if not leads:
                lines.append(f"I found no {noun}s{context}.")
            else:
                lines.append(f"I found {len(leads)} {noun}{'' if len(leads) == 1 else 's'}{context}:")
                if result.get("ranking"):
                    ranking = result["ranking"]
                    factors = ", ".join(f"{f['field']} × {f['weight']}" for f in ranking["factors"])
                    ties = "priority high → medium → low, then ID ascending" if "priority_desc" in ranking["tie_breakers"] else "ID ascending"
                    lines.append(f"Ranking: {factors}; {ranking['direction']}ending; ties: {ties}.")
                # The read service already ordered the rows. Do not sort again here.
                lines.extend("- " + _lead_line(lead) for lead in leads)
        elif tool == "get_lead_details":
            lead = result["lead"]
            lines.extend([_lead_line(lead), f"Email: {lead['email']}.",
                          f"Created: {lead['created_at']}. Updated: {lead['updated_at']}."])
        elif tool == "retrieve_policy":
            lines.append("Relevant fictional policy evidence (not authorization):")
            for passage in result.get("passages", []):
                excerpt = passage["text"][:360]
                if len(passage["text"]) > 360:
                    excerpt += "… [excerpt]"
                lines.append(f"{passage['citation']} {passage['title']}, section {passage['section_id']}, "
                             f"version {passage['version']} (dense={passage['dense_score']:.4f}, "
                             f"BM25={passage['bm25_score']:.4f}, RRF={passage['rrf_score']:.6f}, "
                             f"cross={passage['cross_score']:.4f})\nQuoted policy data: {excerpt}")
            lines.append(result.get("notice", "Retrieval is not authorization."))
    return "\n".join(lines)
