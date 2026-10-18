"""NeMo Gym resources server for MigrationLab migration-review tasks (nemo-gym 0.7.0).

Single-step: the policy answers a task prompt with one migration plan (JSON text); /verify
scores it with the MigrationLab deterministic runner. No tools are exposed.

The runner lives in the MigrationLab checkout (Python 3.12 venv, `migproto`), while nemo-gym
0.7.0 needs Python >= 3.13.14, so /verify runs `gym/verify_cli.py` in that checkout through
`uv run` as a subprocess. The runner remains the only source of pass/fail; this server only
forwards the answer and returns the reward and the runner's info.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, ClassVar, Optional

from pydantic import ConfigDict

from nemo_gym.base_resources_server import (
    BaseResourcesServerConfig,
    BaseVerifyRequest,
    BaseVerifyResponse,
    ReverifyMode,
    SimpleResourcesServer,
)

INFO_FIELDS = ("status", "policy_recommendation", "plan_id", "run_id", "attempt_id",
               "runner_verdict", "runner_recommendation", "failed_checks", "halted", "error")


class MigprotoReviewResourcesServerConfig(BaseResourcesServerConfig):
    # Re-verifying runs the plan again on a fresh database; the result is the runner's.
    REVERIFY_MODE: ClassVar[ReverifyMode] = ReverifyMode.STATELESS
    migproto_root: str               # MigrationLab checkout (with gym/verify_cli.py)
    uv_bin: str = "uv"
    verify_timeout_s: float = 900.0
    # The runner holds one repository-wide execution lock; more is only queueing.
    max_concurrency: int = 1


class MigprotoReviewVerifyRequest(BaseVerifyRequest):
    model_config = ConfigDict(extra="allow")

    scenario_id: str
    task_id: Optional[str] = None
    task_version: Optional[int] = None
    prompt_sha256: Optional[str] = None


class MigprotoReviewVerifyResponse(BaseVerifyResponse):
    model_config = ConfigDict(extra="allow")

    scenario_id: str
    status: Optional[str] = None
    policy_recommendation: Optional[str] = None
    plan_id: Optional[str] = None
    run_id: Optional[str] = None
    attempt_id: Optional[str] = None
    runner_verdict: Optional[str] = None
    runner_recommendation: Optional[str] = None
    failed_checks: list[str] = []
    halted: Optional[str] = None
    error: Optional[str] = None


def _subprocess_env() -> dict[str, str]:
    # This process's venv must not leak into the MigrationLab `uv run`.
    drop = {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PYTHONHOME"}
    return {k: v for k, v in os.environ.items() if k not in drop}


async def run_verifier(config: MigprotoReviewResourcesServerConfig, payload: dict) -> dict:
    """Run gym/verify_cli.py in the MigrationLab checkout; its last stdout line is the result."""
    root = Path(config.migproto_root)
    proc = await asyncio.create_subprocess_exec(
        config.uv_bin, "run", "--project", str(root), "python", str(root / "gym" / "verify_cli.py"),
        cwd=str(root), env=_subprocess_env(), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(json.dumps(payload).encode()), config.verify_timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"verify_cli timed out after {config.verify_timeout_s}s")
    lines = stdout.decode(errors="replace").strip().splitlines()
    if proc.returncode != 0 or not lines:
        tail = stderr.decode(errors="replace")[-500:]
        raise RuntimeError(f"verify_cli exited {proc.returncode}: {tail}")
    return json.loads(lines[-1])


class MigprotoReviewResourcesServer(SimpleResourcesServer):
    ray_enabled = False
    config: MigprotoReviewResourcesServerConfig

    def model_post_init(self, context: Any) -> None:
        super().model_post_init(context)
        self._semaphore = asyncio.Semaphore(self.config.max_concurrency)

    async def verify(self, body: MigprotoReviewVerifyRequest) -> MigprotoReviewVerifyResponse:
        base = body.model_dump()
        for f in ("scenario_id", "task_id", "task_version", "prompt_sha256"):
            base.pop(f, None)
        payload = {"scenario_id": body.scenario_id,
                   "output_text": body.response.output_text or "",
                   "model": body.response.model}
        try:
            async with self._semaphore:
                result = await run_verifier(self.config, payload)
        except Exception as exc:   # verifier infrastructure, not the policy
            return MigprotoReviewVerifyResponse(
                **base, reward=0.0, scenario_id=body.scenario_id, status="verifier_error",
                mask_sample=True, failure_kind="verifier_error",
                failure_reason=f"{type(exc).__name__}: {exc}"[:500])
        mask = bool(result.get("mask_sample"))
        return MigprotoReviewVerifyResponse(
            **base, reward=float(result.get("reward", 0.0)), scenario_id=body.scenario_id,
            mask_sample=mask, failure_kind="verifier_error" if mask else None,
            failure_reason=result.get("error") if mask else None,
            **{k: result.get(k) for k in INFO_FIELDS if result.get(k) is not None})


if __name__ == "__main__":
    MigprotoReviewResourcesServer.run_webserver()
