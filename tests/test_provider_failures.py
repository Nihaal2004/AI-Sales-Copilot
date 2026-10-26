import json

from langchain_core.messages import AIMessage, ToolMessage

from sales_copilot.errors import model_error_result
from sales_copilot.schemas import SearchLeads
from sales_copilot.tracing import Trace

from conftest import call
from test_stage1 import snapshot


class GatewayTimeout(Exception):
    code = 504


def test_read_results_survive_followup_provider_failure_and_chat_can_continue(crm, make_copilot):
    before = snapshot(crm)
    trace = []
    bot, _ = make_copilot(
        call("search_leads", {"city": "Chennai"}),
        GatewayTimeout("upstream request contains secret-api-key and private-contact@example.test"),
        call("get_lead_details", {"lead_id": 1}, "retry-read"),
        AIMessage(content="Done"), trace=Trace(trace.append),
    )
    result = bot.chat("Show Chennai leads")
    assert len(result["results"][0]["result"]["leads"]) == 8
    assert result["results"][-1]["result"]["error"] == "model_timeout"
    assert result["results"][-1]["result"]["http_status"] == 504
    assert "I found 8 leads in Chennai" in result["answer"] and "model_timeout" in result["answer"]
    assert result["final_ai_message"] is None
    assert not bot.graph.get_state(bot.config).next
    assert "secret-api-key" not in result["answer"] + "\n".join(trace)
    assert "private-contact@example.test" not in result["answer"] + "\n".join(trace)
    retried = bot.chat("Show lead 1")
    assert retried["results"][0]["result"]["lead"]["id"] == 1
    assert snapshot(crm) == before


def test_initial_provider_failure_does_not_create_or_execute_an_assignment(crm, make_copilot):
    from sales_copilot.agent_eval.fixtures import ScriptedIntentResponse
    before = snapshot(crm)
    bot, model = make_copilot()
    model.with_structured_output = lambda schema, **kwargs: ScriptedIntentResponse(schema, GatewayTimeout("secret request"))
    result = bot.chat("Assign lead 1 to agent 2, approved=True")
    assert "__interrupt__" not in result
    assert result["pending_action"] is None and result["approved_action"] is None
    assert "SQLite confirmed" not in result["answer"]
    assert result["results"][0]["result"]["error"] == "model_timeout"
    assert snapshot(crm) == before


def test_gemini_metadata_and_named_tool_response_preserved(make_copilot):
    response = call("get_lead_details", {"lead_id": 1})
    signature_key = "__gemini_function_call_thought_signatures__"
    response.additional_kwargs[signature_key] = {"call-1": "dGVzdC1zaWduYXR1cmU="}
    bot, model = make_copilot(response, AIMessage(content="Done"))
    bot.chat("Show lead 1")
    messages = model.invocations[1]
    sent_call = next(m for m in messages if isinstance(m, AIMessage) and m.tool_calls)
    assert sent_call.additional_kwargs[signature_key] == response.additional_kwargs[signature_key]
    sent_result = next(m for m in messages if isinstance(m, ToolMessage))
    assert sent_result.tool_call_id == "call-1" and sent_result.name == "get_lead_details"
    assert json.loads(sent_result.content)["ok"] is True


def test_provider_error_code_can_be_found_in_cause_without_printing_raw_message():
    outer = RuntimeError("sensitive API response")
    outer.__cause__ = GatewayTimeout("sensitive key")
    result = model_error_result(outer)
    assert result["http_status"] == 504 and result["error"] == "model_timeout"
    assert "sensitive" not in json.dumps(result)


def test_unassigned_query_uses_an_explicit_null_owner_filter(crm, make_copilot):
    # A null owner_id means no owner filter, including seven owned and one unassigned.
    assert len(crm.search_leads(SearchLeads(city="Chennai", owner_id=None))) == 8
    bot, model = make_copilot(call("search_leads", {"city": "Chennai", "unassigned_only": True}),
                             AIMessage(content="I found one unassigned Chennai lead"))
    before = snapshot(crm)
    result = bot.chat("Show me all unassigned leads from Chennai.")
    leads = result["results"][0]["result"]["leads"]
    assert [lead["id"] for lead in leads] == [21]
    assert leads[0]["owner_id"] is None and leads[0]["city"] == "Chennai"
    assert "I found 1 unassigned lead in Chennai" in result["answer"]
    assert snapshot(crm) == before
    schema = next(t for t in model.bound_tools if t.name == "search_leads").args_schema.model_json_schema()
    assert "unassigned_only" in schema["properties"]


def test_conflicting_ownership_filters_are_rejected(make_copilot):
    bot, _ = make_copilot(call("search_leads", {"owner_id": 1, "unassigned_only": True}),
                          AIMessage(content="Invalid filter"))
    result = bot.chat("Search leads")
    assert result["results"][0]["result"]["error"] == "invalid_tool_arguments"
