import json

import pytest
from langchain_core.messages import AIMessage

from sales_copilot import cli
from sales_copilot.tracing import Trace

from conftest import ScriptedModel, call
from test_stage1 import snapshot


@pytest.mark.parametrize("content", ["I found Kavya, an unassigned Chennai lead.",
                                    [{"type": "text", "text": "I found Kavya, an unassigned Chennai lead.",
                                      "extras": {"signature": "test-signature"}}]])
def test_final_gemini_message_is_preserved_and_verified_results_are_readable(make_copilot, content):
    lines = []
    final = AIMessage(content=content)
    bot, _ = make_copilot(call("search_leads", {"city": "Chennai", "unassigned_only": True}),
                          final, trace=Trace(lines.append))
    result = bot.chat("Show me all unassigned leads from Chennai")
    assert result["final_ai_message"].text == "I found Kavya, an unassigned Chennai lead."
    assert not result["final_ai_message"].tool_calls
    assert result["answer_source"] == "verified_results"
    assert "I found 1 unassigned lead in Chennai:" in result["answer"]
    assert "Lead 21: Kavya Iyer (Fictional)" in result["answer"]
    assert '"leads":' not in result["answer"] and "search_leads:" not in result["answer"]
    finish = next(json.loads(line[6:]) for line in lines if json.loads(line[6:])["node"] == "finish")
    assert finish["result"]["model_final_received"] is True
    assert finish["result"]["model_final_has_text"] is True
    assert "test-signature" not in "\n".join(lines)


def test_empty_final_ai_message_still_renders_only_verified_facts(make_copilot):
    bot, _ = make_copilot(call("search_leads", {"city": "Bangalore", "unassigned_only": True}),
                          AIMessage(content=[]))
    result = bot.chat("Show unassigned Bangalore leads")
    assert result["final_ai_message"] is not None and not result["final_ai_message"].text
    assert result["answer"] == "I found no unassigned leads in Bangalore."


def test_read_response_cannot_smuggle_a_model_fabricated_assignment_success(crm, make_copilot):
    before = snapshot(crm)
    bot, _ = make_copilot(call("get_lead_details", {"lead_id": 21}),
                          AIMessage(content="I assigned lead 21 to agent 2 successfully."))
    result = bot.chat("Show lead 21")
    assert "assigned lead 21" in result["final_ai_message"].text
    assert "I assigned" not in result["answer"] and "SQLite confirmed" not in result["answer"]
    assert "unassigned" in result["answer"]
    assert snapshot(crm) == before


def test_cli_prints_graph_answer_instead_of_falling_back_to_tool_messages(crm, monkeypatch, capsys):
    model = ScriptedModel([call("search_leads", {"city": "Chennai", "unassigned_only": True}),
                           AIMessage(content="A model-generated summary")])
    monkeypatch.setattr(cli, "create_model", lambda: model)
    monkeypatch.setattr("sys.argv", ["sales-copilot", "chat", "--db", str(crm.path)])
    inputs = iter(["Show unassigned Chennai leads", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    cli.main()
    output = capsys.readouterr().out
    reply = output.split("Copilot> ", 1)[1]
    assert reply.startswith("I found 1 unassigned lead in Chennai:")
    assert "Kavya Iyer (Fictional)" in reply and '"leads":' not in reply
    assert "A model-generated summary" not in reply
