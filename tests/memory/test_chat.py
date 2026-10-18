import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from migproto.memory import store
from migproto.ui import chat, server

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def conn():
    db = store.connect(":memory:")
    store.ingest_all(db, sources=(ROOT / "examples",))
    return db


def test_id_extraction(conn):
    oid = conn.execute("SELECT observation_id FROM observations WHERE check_name = ? LIMIT 1",
                       ("case.old_write_new_read",)).fetchone()[0]
    refs = chat.extract_ids(
        f"why {oid} in 20261017-230415-agent-loop-llm and "
        "brev-batch-20261018-000918 on rename-note parallel case.old_write_new_read?", conn)
    assert refs["runs"] == ["20261017-230415-agent-loop-llm"]
    assert refs["batches"] == ["brev-batch-20261018-000918"]
    assert refs["observations"] == [oid]
    assert refs["scenarios"] == ["rename-note"]
    assert refs["topologies"] == ["parallel"]
    assert refs["checks"] == ["case.old_write_new_read"]


def test_facts_cap_and_id_index(conn, tmp_path):
    facts = chat.build_facts("why did live-ordering-revision fail?", conn, tmp_path, limit=2200)
    assert len(json.dumps(facts, default=str)) <= 2200
    indexed = set(facts["record_ids"]["run_ids"] + facts["record_ids"]["observation_ids"])
    text = json.dumps({k: v for k, v in facts.items() if k != "record_ids"})
    assert all(identifier in text for identifier in indexed)


def test_validator_rejects_unknown_citation():
    with pytest.raises(ValueError, match="not present"):
        chat.validate_reply({"answer": "x", "citations": ["invented"]}, {"known"})


def test_parallel_decision_uses_registered_rule_and_finished_batch():
    rows = ([{"topology": "parallel", "matches_expected": True}] * 9
            + [{"topology": "parallel", "matches_expected": False}]
            + [{"topology": "single", "matches_expected": False}] * 10)
    running = chat._parallel_assessment("running", rows)
    finished = chat._parallel_assessment("finished", rows)
    assert running["fisher_exact_two_sided_p"] < .05 and running["parallel_better"] is False
    assert finished["parallel_better"] is True


def test_facts_only_fixture_answer(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("MIGPROTO_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("MIGPROTO_LLM_MODEL", raising=False)
    result = chat.answer("why did scripted-sync-failure fail?", conn, tmp_path)
    assert result["mode"] == "facts"
    assert "verdict FAIL" in result["answer"]
    assert "9" in result["answer"]
    assert "scripted-sync-failure" in result["citations"]


def test_scenario_failure_frequency_is_from_observations(conn, tmp_path):
    for row in conn.execute("SELECT run_id, summary_json FROM runs").fetchall():
        summary = json.loads(row["summary_json"])
        summary["scenario"] = "rename-note"
        conn.execute("UPDATE runs SET summary_json = ? WHERE run_id = ?",
                     (json.dumps(summary), row["run_id"]))
    result = chat.answer("which check failed most on rename-note?", conn, tmp_path)
    assert result["mode"] == "facts"
    assert "most frequently failed recorded check" in result["answer"]
    assert result["facts"]["scenario_failed_checks"][0]["failed"] > 0


@pytest.mark.parametrize("question", [
    "why did the recent run fail?",
    "why was the latest review unsuccessful?",
    "explain the most recent execution in plain English",
])
def test_recent_run_resolves_from_latest_batch(conn, tmp_path, question):
    batch_root = Path(__file__).parent / "fixtures" / "batches"
    result = chat.answer(question, conn, batch_root)
    assert result["mode"] == "facts"
    assert result["facts"]["question_refs"]["runs"]
    assert "Batch " not in result["answer"]


def test_facts_answer_explains_cause_not_just_counts(conn, tmp_path):
    result = chat.answer("why did scripted-sync-failure fail?", conn, tmp_path)
    assert "underlying problem" in result["answer"]
    assert "cross-version" in result["answer"]


def test_prompt_level_answers_are_direct_and_bounded(conn, tmp_path):
    passed = chat.answer("did run live-ordering-revision pass?", conn, tmp_path)
    assert passed["answer"].startswith("Yes. The runner recorded PASS")
    assert "persistent" not in passed["answer"]

    attempts = chat.answer("how many attempts did live-ordering-revision make?", conn, tmp_path)
    assert "made 2 attempts" in attempts["answer"]

    limited = chat.answer("did this prove zero downtime?", conn, tmp_path)
    assert limited["answer"].startswith("No.")
    assert "do not establish zero downtime" in limited["answer"]

    for row in conn.execute("SELECT run_id, summary_json FROM runs").fetchall():
        summary = json.loads(row["summary_json"])
        summary["scenario"] = "rename-note"
        conn.execute("UPDATE runs SET summary_json = ? WHERE run_id = ?",
                     (json.dumps(summary), row["run_id"]))
    scenario = chat.answer("which check failed most on rename-note?", conn, tmp_path)
    assert "most frequently failed" in scenario["answer"]
    assert "Batch " not in scenario["answer"]
    assert len(json.dumps(scenario["facts"], default=str)) <= chat.FACTS_LIMIT


def test_missing_observation_is_explicit_and_history_is_capped(conn, tmp_path):
    result = chat.answer("show observation live-ordering-revision/a1/retirement_gate#999",
                         conn, tmp_path)
    assert result["answer"].startswith("The exact observation")
    assert "other observations" in result["answer"]
    assert len(json.dumps(result["facts"], default=str)) <= chat.FACTS_LIMIT


class BrokenLLM:
    model = "broken"
    base_url = "http://broken"
    budget = None

    def chat_json(self, *args, **kwargs):
        raise RuntimeError("offline")


class FakeLLM:
    model = "nemotron-test"
    base_url = "http://test/v1"
    budget = None

    def __init__(self):
        self.user = None

    def chat_json(self, system, user, **kwargs):
        self.user = user
        rid = json.loads(user)["facts"]["record_ids"]["run_ids"][0]
        return kwargs["validator"]({"answer": "Recorded answer.", "citations": [rid]})


def test_llm_error_falls_back_and_prompt_isolated(conn, tmp_path):
    fallback = chat.answer("live-ordering-revision", conn, tmp_path, llm=BrokenLLM())
    assert fallback["mode"] == "facts"
    fake = FakeLLM()
    result = chat.answer("live-ordering-revision", conn, tmp_path, llm=fake)
    assert result["mode"] == "model"
    rendered = chat.SYSTEM + fake.user
    for forbidden in chat.RESTRICTED:
        assert forbidden not in rendered


def test_chat_endpoint_with_fake_llm(conn, tmp_path):
    fake = FakeLLM()

    def responder(question, **kwargs):
        return chat.answer(question, llm=fake, **kwargs)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(
        conn, threading.Lock(), runs_root=tmp_path, chat_answer=responder))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/api/chat",
            data=json.dumps({"question": "live-ordering-revision"}).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as response:
            body = json.loads(response.read())
        assert body["mode"] == "model" and body["citations"] == ["live-ordering-revision"]
    finally:
        httpd.shutdown()
