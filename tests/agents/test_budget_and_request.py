import pytest

from migproto.agents.budget import BudgetExceeded, ReviewBudget
from migproto.agents.request import load_review_request


def test_default_request_is_backward_compatible():
    request = load_review_request(None, "scripted")
    assert request["change"]["old_column"] == "note"
    assert request["change"]["new_column"] == "customer_note"
    assert request["agent_topology"] == "roles"
    assert request["budget"] == {
        "max_revisions": 1,
        "max_model_calls": 10,
        "max_total_tokens": 40_000,
        "max_wall_seconds": 600,
    }


def test_topology_override_does_not_mutate_input():
    raw = {
        "change": {"description": "rename a column"},
        "agent_topology": "roles",
        "budget": {},
    }
    loaded = load_review_request(raw, "llm", "single")
    assert loaded["agent_topology"] == "single"
    assert loaded["mode"] == "llm"
    assert raw["agent_topology"] == "roles"


def test_combined_call_and_token_budget():
    budget = ReviewBudget(max_model_calls=1, max_total_tokens=3)
    budget.begin_call()
    budget.finish_call({"prompt_tokens": 1, "completion_tokens": 2}, 0.5, valid=True)
    with pytest.raises(BudgetExceeded, match="model-call budget"):
        budget.begin_call()
    assert budget.snapshot()["valid_proposal_rate"] == 1.0


def test_token_overrun_is_incomplete_condition():
    budget = ReviewBudget(max_total_tokens=2)
    budget.begin_call()
    with pytest.raises(BudgetExceeded, match="token budget"):
        budget.finish_call({"total_tokens": 3}, 0.1)


def test_scenario_request_uses_the_catalogue_change():
    request = load_review_request(None, "llm", scenario="add-required-column")
    assert request["scenario"] == "add-required-column"
    assert request["change"]["new_column"] == "marketing_opt_in"
    assert load_review_request(None, "llm")["scenario"] == "rename-note"


def test_request_may_not_contradict_its_scenario():
    raw = {"scenario": "drop-column", "change": {"description": "x", "old_column": "email"}}
    with pytest.raises(ValueError, match="contradicts"):
        load_review_request(raw, "llm")
    with pytest.raises(ValueError, match="unknown scenario"):
        load_review_request(None, "llm", scenario="no-such-scenario")


def test_rename_scenario_text_is_unchanged():
    from migproto.agents.request import scenario_text
    text = scenario_text(load_review_request(None, "llm"))
    assert "Table: account_user; old column: note; new column: customer_note.\n" in text
    assert "Application versions: old=old, new=new-staged.\n" in text
