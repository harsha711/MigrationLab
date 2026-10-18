"""Stable entry point for executing a migration plan. The CLI, the agent loop, and the
planned UI and Gym adapter call `execute_plan`; nothing else starts servers or runs plan SQL.

    from migproto.runlog import RunLog
    from migproto.runner_api import RunContext, execute_plan

    log = RunLog("my-caller")
    report = execute_plan(plan, run_context=RunContext(log, provenance="agent:<model>"),
                          extra_tests=tester_proposal)
    report.verdict          # "pass" | "fail" | "refused" | "error"
    report.observations     # this attempt's runner observations (v1 check records)

Raises lock.ExecutionBusy if another MigrationLab execution holds the lock.
"""

from dataclasses import dataclass, field
from typing import Callable

from . import lock, staged
from .profile import render as render_profile, scenario_profile  # noqa: F401  (read-only S0 profile)
from .preflight import dry_run  # noqa: F401  (pre-execution dry run and write probe, no verdict)
from .config import SEED_DB
from .runlog import CONTRACT_VERSION, OUTCOMES, RunLog

VERDICTS = ("pass", "fail", "refused", "error")


@dataclass
class RunContext:
    log: RunLog
    provenance: str = "scripted"
    attempt: int | None = None  # None: the log's current attempt (bumped by `attempt` events)
    keep_db: bool = False
    progress: Callable[[dict], None] | None = None


@dataclass
class ExecutionReport:
    verdict: str
    ok: bool
    plan_id: str | None
    run_id: str
    attempt_id: str
    halted: str | None
    halted_step: str | None
    completed_all_stages: bool
    stages: list[dict]
    failed_checks: list[dict]
    outcome_counts: dict[str, int]
    database: dict
    artifacts: dict
    observations: list[dict] = field(repr=False)
    error: str | None = None
    contract_version: str = CONTRACT_VERSION
    scenario: str | None = None
    recommendation: str | None = None   # accept_as_is | staged_rollout | reject

    def as_dict(self, include_observations: bool = False) -> dict:
        """The v0 result keys first (agents' prompts truncate this), then v1 additions."""
        d = {
            "ok": self.ok, "plan_id": self.plan_id,
            "completed_all_stages": self.completed_all_stages, "halted": self.halted,
            "stages": self.stages, "failed_checks": self.failed_checks,
            "run_db": self.database.get("run_db"),
        }
        if self.error is not None:
            d["error"] = self.error
        d.update(contract_version=self.contract_version, verdict=self.verdict,
                 run_id=self.run_id, attempt_id=self.attempt_id, halted_step=self.halted_step,
                 outcome_counts=self.outcome_counts, database=self.database,
                 artifacts=self.artifacts, scenario=self.scenario,
                 recommendation=self.recommendation)
        if include_observations:
            d["observations"] = self.observations
        return d


def _verdict(ok: bool, counts: dict[str, int], completed: bool) -> str:
    if ok:
        return "pass"
    for v in ("error", "fail", "refused"):
        if counts.get(v):
            return v
    return "fail" if completed else "error"


def execute_plan(plan: dict, *, run_context: RunContext,
                 extra_tests: dict | None = None, scenario: str | None = None) -> ExecutionReport:
    log = run_context.log
    if run_context.attempt is not None:
        log.set_attempt(run_context.attempt)
    with lock.execution_lock(f"execute_plan:{log.command}", run_id=log.run_id):
        if run_context.progress:
            log.listeners.append(run_context.progress)
        start = len(log.checks)
        try:
            extra = {"scenario": scenario} if scenario is not None else {}
            result = staged.run(log, plan, extra_tests, provenance=run_context.provenance,
                                keep_db=run_context.keep_db, **extra)
        finally:
            if run_context.progress:
                log.listeners.remove(run_context.progress)
    observations = log.checks[start:]
    counts = {o: sum(c["outcome"] == o for c in observations) for o in OUTCOMES}
    plan_id = result.get("plan_id")
    artifacts = {"run_dir": f"runs/{log.run_id}", "events": "events.jsonl"}
    if result.get("run_db"):
        artifacts.update(plan=f"plan-{plan_id}.json", sql=f"sql/plan-{plan_id}.sql",
                         server_logs=[f"server-old-{plan_id}.log", f"server-new-{plan_id}.log"],
                         migrate_log=f"migrate-{result.get('new_version', 'new-staged').replace('/', '-')}"
                                     f"-{plan_id}.log")
    return ExecutionReport(
        verdict=_verdict(result["ok"], counts, result.get("completed_all_stages", False)),
        ok=result["ok"], plan_id=plan_id, run_id=log.run_id, attempt_id=log.attempt_id,
        halted=result.get("halted"), halted_step=result.get("halted_step"),
        completed_all_stages=result.get("completed_all_stages", False),
        stages=result.get("stages", []), failed_checks=result.get("failed_checks", []),
        outcome_counts=counts,
        database={"run_db": result.get("run_db"), "dropped": result.get("db_dropped"),
                  "template_db": SEED_DB,
                  "database_snapshot_id": log.start_states.get(log.attempt_id),
                  "final_schema_state": log.scope["schema_state"]},
        artifacts=artifacts, observations=observations, error=result.get("error"),
        scenario=result.get("scenario"), recommendation=result.get("recommendation"),
    )
