"""Single-agent topology: one agent, one compacted memory.

Every request the controller sends already restates the scenario and the plan contract, so
resending the whole conversation mostly repeats itself (on Lightning about 250k of 300k
tokens were prompt tokens). `Memory` keeps the system prompt, the investigator exchange, the
most recent plan exchange and the most recent runner-evidence exchange in full, and replaces
every other earlier exchange with a short deterministic note built from runner results by
the controller (never written by a model). See docs/decisions/single-history.md.
"""

SYSTEM = """You are the sole agent in a database-migration review. Across a sequence of
requests you investigate affected code, propose a staged migration, propose compatibility
tests, and diagnose execution evidence. Preserve context between requests, but follow the
JSON shape requested by each user message. You propose; the deterministic runner decides
pass/fail. Reply with one JSON object only, with no prose outside JSON."""

POLICY = "latest-plan-evidence/v1"
HALT_CHARS = 300


def _kind(label: str) -> str:
    for prefix in ("investigator", "planner", "tester-propose", "tester-report"):
        if label.startswith(prefix):
            return prefix
    raise ValueError(f"unknown single-agent turn label {label!r}")


class Memory:
    """The single agent's conversation, compacted before each request.

    `begin()` returns the history list for the next chat_json call (chat_json appends the new
    user turn and, on success, the assistant reply); `end(label)` stores that exchange.
    """

    def __init__(self, system: str = SYSTEM):
        self.system = system
        self.turns: list[dict] = []      # {"label", "kind", "user", "assistant"}
        self.attempts: list[dict] = []   # runner facts, in attempt order
        self._history: list[dict] | None = None
        self._sent = 0

    def note_attempt(self, attempt: int, plan_id: str | None, ok: bool, halted: str | None,
                     failed_checks: list[str]) -> None:
        self.attempts.append({"attempt": attempt, "plan_id": plan_id, "ok": ok,
                              "halted": halted, "failed_checks": list(failed_checks)})

    def _kept(self) -> set[int]:
        keep = {0} if self.turns else set()   # the investigator exchange
        for kind in ("planner", "tester-report"):
            idx = [i for i, t in enumerate(self.turns) if t["kind"] == kind]
            if idx:
                keep.add(idx[-1])
        return keep

    def note(self, replaced: list[str]) -> str:
        lines = ["[Controller memory note. Earlier turns were replaced by this deterministic "
                 "summary to bound the context; it is built from runner results by the "
                 "controller, not by a model.]",
                 f"Replaced turns: {', '.join(replaced)}."]
        if self.attempts:
            lines.append("Runner results so far:")
            for a in self.attempts:
                halted = (a["halted"] or "none")[:HALT_CHARS]
                failed = ", ".join(a["failed_checks"]) or "none"
                lines.append(f"- attempt {a['attempt']}: plan {a['plan_id']}, "
                             f"{'PASS' if a['ok'] else 'FAIL'}, halted: {halted}; "
                             f"failed checks: {failed}")
        lines.append("[End of memory note.]")
        return "\n".join(lines)

    def begin(self) -> tuple[list[dict], dict]:
        """History to send next, and the compaction record for the transcript."""
        keep = self._kept()
        replaced = [t["label"] for i, t in enumerate(self.turns) if i not in keep]
        kept = [t["label"] for i, t in enumerate(self.turns) if i in keep]
        note = self.note(replaced) if replaced else None
        history = [{"role": "system", "content": self.system}]
        for i, turn in enumerate(self.turns):
            if i not in keep:
                continue
            user = turn["user"]
            if note and i != 0:
                # Prefixed to the first kept turn after the investigator exchange, so the
                # messages keep alternating user/assistant.
                user, note = f"{note}\n\n{user}", None
            history += [{"role": "user", "content": user},
                        {"role": "assistant", "content": turn["assistant"]}]
        if note:  # nothing kept after the investigator exchange
            history.append({"role": "user", "content": note})
        self._history, self._sent = history, len(history)
        return history, {"policy": POLICY, "kept_turns": kept, "replaced_turns": replaced,
                         "messages_sent": len(history) + 1}

    def end(self, label: str) -> None:
        """Store the exchange chat_json just appended; a failed call stores nothing."""
        h, self._history = self._history, None
        if h and len(h) == self._sent + 2 and h[-1]["role"] == "assistant" \
                and h[-2]["role"] == "user":
            self.turns.append({"label": label, "kind": _kind(label),
                               "user": h[-2]["content"], "assistant": h[-1]["content"]})
