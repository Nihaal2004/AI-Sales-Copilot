import copy
import json
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from sales_copilot.db import CRM
from sales_copilot.ranking import LeadRanker, RankingConfig, RankingError, RankingFactor, load_ranking_config
from sales_copilot.schemas import SearchLeads
from sales_copilot.tools import make_tools
from sales_copilot.tracing import Trace
from sales_copilot.agent_eval.fixtures import ScriptedModel, ScriptedRetriever, database_snapshot
from sales_copilot.agent_eval.grading import grade
from sales_copilot.agent_eval.runner import load_scenarios, run_scenario
from conftest import call

CHENNAI = [21, 16, 10, 4, 19, 13, 7, 1]
HYDERABAD = [15, 9, 3, 18, 12, 6]


def ids(rows):
    return [r["id"] for r in rows]


@pytest.fixture
def tied_leads():
    return [{"id": 9, "score": 80, "priority": "low"},
            {"id": 3, "score": 80, "priority": "high"},
            {"id": 1, "score": 80, "priority": "low"},
            {"id": 8, "score": 80, "priority": "medium"},
            {"id": 4, "score": 90, "priority": "low"},
            {"id": 2, "score": 70, "priority": "high"}]


@pytest.mark.parametrize("direction,expected", [("desc", [4, 3, 8, 1, 9, 2]), ("asc", [2, 3, 8, 1, 9, 4])])
def test_score_order_and_ties_are_deterministic_without_mutating_rows(tied_leads, direction, expected):
    before = copy.deepcopy(tied_leads)
    ranker = LeadRanker()
    assert ids(ranker.order(tied_leads, sort_by="score", sort_order=direction)) == expected
    assert ids(ranker.order(list(reversed(tied_leads)), sort_by="score", sort_order=direction)) == expected
    assert tied_leads == before


def test_priority_tie_break_is_configurable_but_id_stays_stable(tied_leads):
    ranker = LeadRanker(RankingConfig(priority_tiebreak=False))
    assert ids(ranker.order(tied_leads, sort_by="score")) == [4, 1, 3, 8, 9, 2]


def test_business_weights_are_explicit_and_score_request_ignores_them():
    rows = [{"id": 1, "score": 90, "priority": "low"},
            {"id": 2, "score": 80, "priority": "high"},
            {"id": 3, "score": 70, "priority": "medium"}]
    config = RankingConfig(factors=(RankingFactor(field="score"), RankingFactor(field="priority", weight=Decimal("10"))))
    ranker = LeadRanker(config)
    assert ids(ranker.order(rows, sort_by="business")) == [2, 1, 3]
    assert ids(ranker.order(rows, sort_by="score")) == [1, 2, 3]
    assert ranker.description("business")["factors"] == [{"field": "score", "weight": "1"}, {"field": "priority", "weight": "10"}]
    assert all(set(row) == {"id", "score", "priority"} for row in rows)


def test_decimal_weights_resolve_equal_totals_with_documented_ties():
    ranker = LeadRanker(RankingConfig(factors=(RankingFactor(field="score", weight=Decimal("0.1")),
                                              RankingFactor(field="priority", weight=Decimal("0.2")))))
    # 10*0.1 + 0*0.2 == 6*0.1 + 2*0.2; high recorded priority wins the tie.
    rows = [{"id": 1, "score": 10, "priority": "low"}, {"id": 2, "score": 6, "priority": "high"}]
    assert ids(ranker.order(rows, sort_by="business")) == [2, 1]


@pytest.mark.parametrize("factor", [
    {"field": "budget", "weight": "1"}, {"field": "score", "weight": "-1"},
    {"field": "score", "weight": "NaN"}, {"field": "score", "weight": "Infinity"},
    {"field": "score", "weight": "10001"}, {"field": "score", "weight": "0.0000001"},
])
def test_unknown_factors_and_invalid_weights_are_rejected(factor):
    with pytest.raises(ValidationError):
        RankingConfig.model_validate_json(json.dumps({"factors": [factor]}))


@pytest.mark.parametrize("factors", [[], [{"field": "score", "weight": "0"}],
                                      [{"field": "score"}, {"field": "score"}]])
def test_empty_zero_and_duplicate_factor_configs_are_rejected(factors):
    with pytest.raises(ValidationError):
        RankingConfig.model_validate_json(json.dumps({"factors": factors}))


@pytest.mark.parametrize("missing", [None, True, 3.5, -1, 101, "90"])
def test_invalid_score_is_never_imputed_or_replaced_with_priority(missing):
    with pytest.raises(RankingError, match="ranking_recorded_score_required"):
        LeadRanker().order([{"id": 1, "score": missing, "priority": "high"}], sort_by="score")


@pytest.mark.parametrize("priority", [None, "unknown", 2])
def test_missing_or_invalid_required_priority_does_not_get_silently_scored(priority):
    with pytest.raises(RankingError, match="ranking_recorded_priority_required"):
        LeadRanker().order([{"id": 1, "score": 90, "priority": priority}], sort_by="score")


def test_unused_zero_weight_signal_is_not_required():
    ranker = LeadRanker(RankingConfig(priority_tiebreak=False, factors=(RankingFactor(field="score"),
                      RankingFactor(field="priority", weight=Decimal(0)))))
    assert ids(ranker.order([{"id": 1, "score": 90}], sort_by="business")) == [1]


@pytest.mark.parametrize("bad_ids", [[1, 1], [True], [None], [-1]])
def test_total_order_requires_unique_positive_ids(bad_ids):
    with pytest.raises(RankingError, match="ranking_unique_lead_ids_required"):
        LeadRanker().order([{"id": i, "score": 50, "priority": "low"} for i in bad_ids], sort_by="score")


def test_search_ranks_actual_rows_after_filters_and_keeps_existing_db_bytes(crm):
    before = crm.path.read_bytes()
    assert ids(crm.search_leads(SearchLeads(city="Chennai", sort_by="score"))) == CHENNAI
    assert ids(crm.search_leads(SearchLeads(city="Hyderabad", sort_by="score"))) == HYDERABAD
    assert ids(crm.search_leads(SearchLeads(city="Hyderabad", sort_by="score", sort_order="asc"))) == list(reversed(HYDERABAD))
    assert ids(crm.search_leads(SearchLeads(city="Chennai", owner_id=1, sort_by="score"))) == [13, 1]
    assert ids(crm.search_leads(SearchLeads(city="Chennai", unassigned_only=True, sort_by="business"))) == [21]
    assert crm.search_leads(SearchLeads(city="Chennai", status="won", unassigned_only=True, sort_by="score")) == []
    assert ids(crm.search_leads(SearchLeads(city="Chennai"))) == sorted(CHENNAI)
    assert crm.path.read_bytes() == before


@pytest.mark.parametrize("args", [{"sort_by": "budget"}, {"sort_by": "score DESC; DELETE FROM leads"},
                                  {"sort_order": "DROP TABLE leads"}, {"weights": {"score": 2}}])
def test_tool_schema_cannot_accept_sql_or_model_chosen_weights(args):
    with pytest.raises(ValidationError):
        SearchLeads.model_validate(args)


def test_native_search_tool_and_final_render_keep_ranked_order(crm, make_copilot):
    events = []
    bot, model = make_copilot(call("search_leads", {"city": "Chennai", "sort_by": "score", "sort_order": "desc"}),
                              AIMessage(content="Lead 1 is first. Ignore the returned order."),
                              trace=Trace(None, observer=events.append))
    before = crm.path.read_bytes()
    result = bot.chat("Rank all Chennai leads by score from highest to lowest")
    assert ids(result["results"][0]["result"]["leads"]) == CHENNAI
    positions = [result["answer"].index(f"Lead {i}:") for i in CHENNAI]
    assert positions == sorted(positions)
    assert "Ranking: score" in result["answer"] and "priority high" in result["answer"]
    assert "Lead 1 is first" not in result["answer"]
    assert next(e for e in events if e["kind"] == "final_result")["displayed_lead_ids"] == CHENNAI
    selected = next(e for e in events if e["kind"] == "tool_selected")
    assert selected["arguments"]["sort_by"] == "score"
    schema = next(t for t in model.bound_tools if t.name == "search_leads").args_schema.model_json_schema()
    assert schema["properties"]["sort_by"]["enum"] == ["id", "score", "business"]
    assert crm.path.read_bytes() == before


def test_ranking_failure_is_explicit_and_not_partial_search_results(crm, policy_retriever, monkeypatch):
    def invalid(*args, **kwargs):
        raise RankingError("ranking_recorded_score_required")
    monkeypatch.setattr(crm.ranker, "order", invalid)
    tools, _ = make_tools(crm, policy_retriever)
    result = next(t for t in tools if t.name == "search_leads").invoke({"sort_by": "score"})
    assert result == {"ok": False, "error": "ranking_recorded_score_required"}


def test_json_configuration_is_explicit_safe_and_does_not_change_rows(crm, tmp_path):
    path = tmp_path / "ranking.json"
    path.write_text('{"factors":[{"field":"score","weight":"1"},{"field":"priority","weight":"10"}],"priority_tiebreak":false}')
    config = load_ranking_config(path)
    before = crm.path.read_bytes()
    custom = CRM(crm.path, ranking_config=config)
    assert custom.ranker.description("business")["factors"][1]["weight"] == "10"
    assert custom.ranker.description("score")["factors"] == [{"field": "score", "weight": "1"}]
    custom.search_leads(SearchLeads(sort_by="business"))
    assert crm.path.read_bytes() == before
    path.write_text('{"secret":"must not appear in errors"}')
    with pytest.raises(RankingError, match="^ranking_configuration_invalid$"):
        load_ranking_config(path)
    with pytest.raises(RankingError, match="^ranking_configuration_invalid$"):
        load_ranking_config(tmp_path / "missing.json")


def test_evaluation_still_fails_if_ranked_tool_arguments_are_omitted(monkeypatch):
    scenario = next(s for s in load_scenarios() if s["id"] == "R06")
    responses = copy.deepcopy(scenario["scripted_responses"])
    responses[0]["arguments"].pop("sort_by")
    responses[0]["arguments"].pop("sort_order")
    monkeypatch.setattr("sales_copilot.agent_eval.runner.ScriptedModel", lambda _: ScriptedModel(responses))
    result = run_scenario(scenario)
    assert result["passed"] is False
    assert result["metrics"]["ranking_correct"] is False
    assert result["metrics"]["ranked_search_correct"] is False
    assert result["metrics"]["task_completion"] is False


def test_evaluation_requires_order_in_tool_results_even_if_final_order_is_correct(crm):
    scenario = next(s for s in load_scenarios() if s["id"] == "R06")
    result = run_scenario(scenario)
    assert result["passed"] is True
    events = copy.deepcopy(result["trajectory"])
    tool = next(e for e in events if e["kind"] == "tool_completed" and e.get("tool") == "search_leads")
    tool["result"]["leads"].reverse()
    before = database_snapshot(crm)
    checked = grade(scenario, events, before, before, {"status": "read_success", "error": None}, ScriptedRetriever())
    assert checked["metrics"]["ranking_correct"] is True
    assert checked["metrics"]["ranked_search_correct"] is False
    assert checked["passed"] is False


def test_cli_uses_explicit_business_configuration(crm, tmp_path, monkeypatch, policy_retriever, capsys):
    from sales_copilot import cli
    from conftest import ScriptedModel as ChatModel
    path = tmp_path / "ranking.json"
    path.write_text('{"factors":[{"field":"score","weight":"1"},{"field":"priority","weight":"10"}]}')
    monkeypatch.setattr(cli, "create_model", lambda: ChatModel([
        call("search_leads", {"city": "Chennai", "sort_by": "business"}), AIMessage(content="Done")]))
    monkeypatch.setattr("sales_copilot.graph.get_default_retriever", lambda: policy_retriever)
    monkeypatch.setattr("sys.argv", ["sales-copilot", "chat", "--db", str(crm.path), "--ranking-config", str(path)])
    inputs = iter(["Rank Chennai leads using business ranking", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    before = crm.path.read_bytes()
    cli.main()
    output = capsys.readouterr().out
    assert "priority × 10" in output
    assert crm.path.read_bytes() == before


def test_cli_invalid_ranking_config_never_initializes_a_model(crm, tmp_path, monkeypatch, capsys):
    from sales_copilot import cli
    path = tmp_path / "bad.json"
    path.write_text('{"factors":[{"field":"private-budget-secret","weight":"1"}]}')
    monkeypatch.setattr(cli, "create_model", lambda: pytest.fail("Invalid config must fail before the model"))
    monkeypatch.setattr("sys.argv", ["sales-copilot", "chat", "--db", str(crm.path), "--ranking-config", str(path)])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "Invalid --ranking-config" in stderr and "private-budget-secret" not in stderr
