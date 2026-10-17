"""Shared model-call budget for one migration review."""

from dataclasses import asdict, dataclass, field
import time


class BudgetExceeded(RuntimeError):
    """Raised before an over-budget model result can influence a review."""


@dataclass
class ReviewBudget:
    max_revisions: int = 1
    max_model_calls: int = 10
    max_total_tokens: int = 40_000
    max_wall_seconds: float = 600
    started: float = field(default_factory=time.monotonic, repr=False)
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_seconds: float = 0.0
    valid_proposals: int = 0
    exhausted_reason: str | None = None

    @classmethod
    def from_dict(cls, raw: dict | None) -> "ReviewBudget":
        raw = raw or {}
        defaults = cls()
        values = {}
        for name in ("max_revisions", "max_model_calls", "max_total_tokens",
                     "max_wall_seconds"):
            value = raw.get(name)
            values[name] = getattr(defaults, name) if value is None else value
        budget = cls(**values)
        if any(not isinstance(getattr(budget, name), int) or getattr(budget, name) < 0
               for name in ("max_revisions", "max_model_calls", "max_total_tokens")):
            raise ValueError("revision, call and token budgets must be non-negative integers")
        if not isinstance(budget.max_wall_seconds, (int, float)) \
                or budget.max_wall_seconds <= 0:
            raise ValueError("max_wall_seconds must be positive")
        return budget

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def _exhaust(self, reason: str) -> None:
        self.exhausted_reason = reason
        raise BudgetExceeded(reason)

    def remaining_wall(self) -> float:
        return max(0.0, self.max_wall_seconds - self.elapsed)

    def check_wall(self) -> None:
        if self.elapsed >= self.max_wall_seconds:
            self._exhaust(f"wall-time budget exhausted ({self.max_wall_seconds}s)")

    def begin_call(self) -> None:
        self.check_wall()
        if self.model_calls >= self.max_model_calls:
            self._exhaust(f"model-call budget exhausted ({self.max_model_calls})")
        self.model_calls += 1

    def finish_call(self, usage: dict | None, seconds: float, valid: bool = False) -> None:
        self.model_seconds += seconds
        usage = usage or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or prompt + completion)
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        if valid:
            self.valid_proposals += 1
        if self.total_tokens > self.max_total_tokens:
            self._exhaust(
                f"token budget exhausted ({self.total_tokens}/{self.max_total_tokens})"
            )
        self.check_wall()

    def snapshot(self) -> dict:
        data = asdict(self)
        data.pop("started", None)
        data["wall_seconds"] = round(self.elapsed, 3)
        data["model_seconds"] = round(self.model_seconds, 3)
        data["valid_proposal_rate"] = (
            round(self.valid_proposals / self.model_calls, 3) if self.model_calls else None
        )
        data["remaining"] = {
            "model_calls": max(0, self.max_model_calls - self.model_calls),
            "tokens": max(0, self.max_total_tokens - self.total_tokens),
            "wall_seconds": max(0, round(self.max_wall_seconds - self.elapsed, 3)),
        }
        return data
