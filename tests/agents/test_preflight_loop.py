"""Configuration K: dry run before each runner execution, one planner fix per attempt."""

import json

import pytest

from migproto import runner_api, staged
from migproto.agents import loop, roles
from migproto.agents.budget import ReviewBudget
from migproto.runlog import RunLog

from test_config_i import FakeLLM, _runner  # noqa: E402  (same directory, rootdir import)

PROBE = {"point": "before d", "writes": [{"write": "UPDATE ...", "after": {"note": "x"}}]}


@pytest.mark.parametrize("topology", ["roles", "single"])
def test_preflight_report_reaches_planner_before_runner(monkeypatch, tmp_path, topology):
    monkeypatch.setattr(roles, "gather_hits", lambda *a, **k: [])
    monkeypatch.setattr(loop, "PREFLIGHT", True)
    dry = []
    monkeypatch.setattr(runner_api, "dry_run", lambda sid, plan: dry.append(plan["plan_id"])
                        or {"stages": [], "probes": [PROBE], "first_error": None})
    runs, fake = _runner([1, 0])
    monkeypatch.setattr(staged, "run", fake)
    llm = FakeLLM(ReviewBudget(max_revisions=2, max_model_calls=40, max_total_tokens=10**6))
    log = RunLog("test", root=tmp_path)
    result = loop.run_review(log, "llm", llm=llm,
                             request={"change": {"description": "rename note"},
                                      "agent_topology": topology})
    assert result["verdict"] == "PASS"
    assert len(dry) == 2                       # one dry run per executed attempt
    fixes = [u for _, u in llm.prompts if "DRY-RUN" in u]
    assert len(fixes) == 2 and "preflight probe" not in fixes[0] and "UPDATE ..." in fixes[0]
    assert runs == ["a1-p-pf", "a2-p-pf"]      # the runner executes the fixed plan
    assert (log.dir / "agents" / "preflight-1.json").is_file()
    config = next(json.loads(line) for line in (log.dir / "events.jsonl").open()
                  if '"agent_config"' in line)
    assert config["preflight"] is True


def test_no_fix_call_when_nothing_to_report(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "gather_hits", lambda *a, **k: [])
    monkeypatch.setattr(loop, "PREFLIGHT", True)
    monkeypatch.setattr(runner_api, "dry_run", lambda sid, plan:
                        {"stages": [], "probes": [{"skipped": "x", "writes": []}],
                         "first_error": None})
    runs, fake = _runner([0])
    monkeypatch.setattr(staged, "run", fake)
    llm = FakeLLM(ReviewBudget(max_revisions=2, max_model_calls=40, max_total_tokens=10**6))
    log = RunLog("test", root=tmp_path)
    loop.run_review(log, "llm", llm=llm, request={"change": {"description": "rename note"},
                                                  "agent_topology": "roles"})
    assert not [u for _, u in llm.prompts if "DRY-RUN" in u] and runs == ["a1-p"]


def test_preflight_off_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "gather_hits", lambda *a, **k: [])
    monkeypatch.setattr(runner_api, "dry_run", lambda *a: pytest.fail("dry run called"))
    runs, fake = _runner([0])
    monkeypatch.setattr(staged, "run", fake)
    llm = FakeLLM(ReviewBudget(max_revisions=2, max_model_calls=40, max_total_tokens=10**6))
    loop.run_review(RunLog("test", root=tmp_path), "llm", llm=llm,
                    request={"change": {"description": "rename note"},
                             "agent_topology": "roles"})
    assert runs == ["a1-p"]
