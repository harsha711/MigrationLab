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
