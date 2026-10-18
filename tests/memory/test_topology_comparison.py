"""Evidence-only roles/single comparison from synthetic batch folders."""

import json

from migproto.memory import query, store


def write_run(root, run_id, topology, verdict, *, tokens, seconds, calls, failed):
    path = root / run_id
    path.mkdir()
    (path / "events.jsonl").write_text("")
    (path / "summary.json").write_text(json.dumps({
        "run_id": run_id, "scenario": "rename-note", "topology": topology,
        "verdict": verdict,
        "agent_totals": {"total_tokens": tokens, "wall_seconds": seconds,
                         "model_calls": calls,
                         "exhausted_reason": "token budget" if verdict == "INCOMPLETE" else None},
        "attempts": [{"failed_checks": list(range(value))} for value in failed],
    }))


def batch(root, name, config, rows, infra=None):
    path = root / name
    path.mkdir()
    (path / "CONFIG-NOTE").write_text(f"CONFIGURATION {config} synthetic settings")
    (path / "runs.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    if infra:
        (path / "INFRA-NOTE").write_text(infra)


def test_topology_comparison_groups_only_same_configuration_and_excludes_infra(tmp_path):
    roles = "20260101-100001-agent-loop-llm"
    single = "20260101-100002-agent-loop-llm"
    infra = "20260101-100003-agent-loop-llm"
    write_run(tmp_path, roles, "roles", "FAIL", tokens=100, seconds=20, calls=3, failed=[2, 1])
    write_run(tmp_path, single, "single", "INCOMPLETE", tokens=300, seconds=40, calls=5, failed=[4, 2, 1])
    write_run(tmp_path, infra, "roles", "ERROR", tokens=1, seconds=1, calls=1, failed=[])
    batch(tmp_path, "brev-batch-20260101-100000", "B", [
        {"run_id": roles, "scenario": "rename-note", "topology": "roles", "verdict": "FAIL"},
        {"run_id": single, "scenario": "rename-note", "topology": "single", "verdict": "INCOMPLETE"},
        {"run_id": infra, "scenario": "rename-note", "topology": "roles", "verdict": "ERROR"},
    ], infra=f"Infrastructure failure: {infra[:15]}")
    # This batch is intentionally ignored: no configuration declaration.
    batch(tmp_path, "brev-batch-20260101-110000", "B", [], infra=None)
    (tmp_path / "brev-batch-20260101-110000" / "CONFIG-NOTE").write_text("earlier settings")

    result = query.topology_comparison(tmp_path)

    assert [c["configuration"] for c in result["configurations"]] == ["B"]
    setups = result["configurations"][0]["setups"]
    assert setups["roles"]["reviews"] == 1
    assert setups["roles"]["verdicts"]["FAIL"] == 1
    assert setups["roles"]["median_tokens"] == 100
    assert setups["single"]["incomplete_stops"]["token_budget"] == 1
    assert setups["single"]["runs"][0]["failed_checks"] == [4, 2, 1]


def test_configuration_letter_requires_a_supported_configuration():
    assert query._configuration_letter("CONFIGURATION F full feedback") == "F"
    assert query._configuration_letter("CONFIGURATION A older settings") is None
    assert query._configuration_letter("no configuration") is None


def test_activity_overview_keeps_models_and_unknown_scenarios_visible(tmp_path):
    known = "20260102-100001-agent-loop-llm"
    unknown = "20260102-100002-agent-loop-llm"
    write_run(tmp_path, known, "roles", "PASS", tokens=120, seconds=20, calls=3, failed=[1, 0])
    write_run(tmp_path, unknown, "single", "FAIL", tokens=240, seconds=30, calls=4, failed=[2, 1])
    for run_id, scenario, model in ((known, "rename-note", "model-a"),
                                    (unknown, "future-migration", "model-b")):
        path = tmp_path / run_id / "summary.json"
        summary = json.loads(path.read_text())
        summary["scenario"] = scenario
        summary["model"] = model
        path.write_text(json.dumps(summary))
    conn = store.connect(":memory:")
    store.ingest_all(conn, sources=(tmp_path,))
    activities = {item["scenario"]: item for item in query.scenario_activity_overview(conn)["activities"]}
    assert activities["rename-note"]["performance"][0]["model"] == "model-a"
    assert activities["future-migration"]["title"] == "Future Migration"
    assert activities["future-migration"]["runs"][0]["topology"] == "single"
