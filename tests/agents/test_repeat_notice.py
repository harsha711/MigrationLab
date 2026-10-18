from migproto.agents import loop


def _h(attempt, names, errors=()):
    fails = [{"name": n, "error": e} for n, e in zip(names, list(errors) + [None] * len(names))]
    rec = {"attempt": attempt, "halted": None, "halted_step": None, "stages_run": ["s"],
           "failed_checks": list(names), "plan": {"plan_id": f"p{attempt}"}}
    return {"record": rec, "plan": rec["plan"], "result": {"ok": False}, "failures": fails}


def test_repeated_names_and_errors():
    prev = _h(1, ["case.a", "stage.x.executes"], ["boom", "trigger WHEN error"])
    latest = _h(2, ["case.a", "stage.y.executes"], [None, "trigger WHEN error"])
    out = loop._repeated(latest, prev)
    assert out["checks"] == ["case.a"]
    assert out["errors"] == ["trigger WHEN error"]
    assert "attempt 2 repeated failures of your attempt 1" in out["note"]


def test_nothing_repeated():
    assert loop._repeated(_h(2, ["case.b"]), _h(1, ["case.a"])) is None


def test_flag_gates_feedback(monkeypatch):
    hist = [_h(1, ["case.a"]), _h(2, ["case.a"])]
    monkeypatch.setattr(loop, "REPEAT_NOTICE", False)
    assert "repeated_failures" not in loop._revision_input(hist, None)[1]
    monkeypatch.setattr(loop, "REPEAT_NOTICE", True)
    assert loop._revision_input(hist, None)[1]["repeated_failures"]["checks"] == ["case.a"]
