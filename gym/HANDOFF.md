# Gym integration: handoff notes

Interface requests and open decisions from the NeMo Gym work (branch `feat/nemo-gym`). Nothing outside `gym/` and `tests/gym/` was changed. In particular, `migproto/agents/*`, `staged.py`, `checks.py`, `plans.py` and `scripts/brev/*` were not touched.

## For Harsha (runner)

1. **Per-call runner settings instead of import-time env.** `migproto.config` reads `MIGPROTO_PORT_*`, `MIGPROTO_CACHE_URL` at import, so the Gym bridge has to set the environment before importing the runner and run one subprocess per verification. A `runner_api.RunContext(ports=..., cache_url=..., lock_path=...)` (or a `RunnerSlot`) would let one Gym server process verify several sessions in parallel, one slot each (like `supervise.py --workers`). Until then the server keeps `max_concurrency: 1`.
2. **Decide whether `gym-verify` runs belong in `runs/`.** Every verification writes `runs/<ts>-gym-verify/` (git-ignored). Training-scale rollouts would create thousands. Options: a `--runs-root` for Gym, or pruning after export to `gym/results/`.
3. **`migproto.lock` at the worktree root is untracked but not git-ignored** (the path was given for this worktree). Consider adding `migproto.lock` to `.gitignore`.

## For Owner 2 (agents)

1. **Shared target rule.** `gym/migration_review/scoring.target()` reimplements `target()` from `scripts/brev/score_scenarios.py` (only on local `brev/lightning-serving`, commit `1b477c4`, not pushed when this was written). Two copies can drift. Proposal: move it to one evaluation-side module (for example `migproto/evaluation.py`, which never feeds prompts) that both import, after `brev/lightning-serving` merges.
2. **Full-controller evaluation in NeMo Gym.** These tasks are single-step (one planner-style answer, no investigator model, no tester cases, no revision), so scores are not comparable to `agent-loop` reviews. Evaluating the multi-role controller under NeMo Gym would need a `responses_api_agents` server that calls `loop.run_review` and uses the policy model endpoint for every role. That is agent-layer work, not done here.
3. **Structured output.** The Gym tasks do not send `roles.plan_schema(variants)` as a response format, because whether NeMo Gym's `responses_create_params` accepts `text.format` json_schema was not verified. If it does, adding it would match the controller's guided decoding.
4. **Recommendation is required for reward.** `facts.plan_validator` accepts a plan without `recommendation`. Gym verification runs such a plan but cannot reward it (no recommendation to match). No change requested, but note it if the planner schema changes.

## For Owner 3 (memory and UI)

1. **`gym-verify` runs and `memory.retrieval.prior_evidence`.** Gym runs have provenance `agent:<policy model>` and a `summary.json`, so the adapter can ingest them. Their summaries hold runner facts and the policy's recommendation only (no reward, no expected verdict), but they are produced by policies under evaluation or training. Decide whether retrieval should exclude `command == "gym-verify"` (recommended, together with the same-model filter already noted in AGENTS.md).

## Open questions (organizers)

- Does the NeMo Gym bounty require training (weight updates) or accept an environment plus evaluation? (`docs/bounties.md` section 6, question 4.) This server supports evaluation now. Training additionally needs a trainer integration and many more tasks than 14.
