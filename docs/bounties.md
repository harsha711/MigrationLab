# Bounty Integration

Status date: 2026-10-17. Nothing in this file claims qualification. Each bounty below separates what organizers have published (verified) from what we plan or have built. Bounty integrations that are not implemented are marked **planned**.

## 1. Sources and verification status

| Source | Status | What it establishes |
|---|---|---|
| Public event page, [luma.com/nvidia-redhat-small-models-hack](https://luma.com/nvidia-redhat-small-models-hack), fetched 2026-10-17 | **verified** | Event "NVIDIA x Red Hat Small Models Hack", Raleigh, 2026-10-17/18. "small, open models (total parameters must be ≤ 33 billion)". Teams of at most 3. Bounty track names: "Best Use of NVIDIA Nemotron 3.5 Lightning", "Best Use of NVIDIA NeMo Gym", "Best Use of Red Hat vLLM". Prizes go to "qualifying submissions, depending on the unique track requirements" |
| Track-specific bounty requirements | **not available to us** | The public page does not list them. They are presumably given at kickoff or by the bounty sponsors |
| Teammate proposal citing kickoff requirements | **not seen** | Not in the repository or on this machine. Its requirement claims stay unverified until checked against organizer material |
| Model card, [nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4) (and the BF16 card), fetched 2026-10-17 | **verified** (vendor documentation, not organizer rules) | 30B total / 3B active hybrid Mamba-2 + MoE + Attention. vLLM `v0.27.1` or newer. Supported on Blackwell, Hopper, and Ampere via W4A16. Optional draft models (DSpark, DFlash) and built-in MTP |
| NeMo Gym docs, [docs.nvidia.com/nemo/gym](https://docs.nvidia.com/nemo/gym/latest/environment-tutorials/single-step-environment.html), fetched 2026-10-17 | **verified** (vendor documentation) | Agent server, model server (`vllm_model`, `openai_model`), and resources server. Resources servers expose `POST /seed_session`, tool endpoints keyed by `session_id`, and `POST /verify` returning `reward` (optionally `reward_components`) |

**Action before any qualification claim:** obtain the written track requirements from the organizers or kickoff slides, record them under each bounty's "Verified organizer requirements" with source and date, and resolve the open questions in section 6.

## 2. Event-wide rule: parameter budget

Verified: the submitted system must use models with ≤ 33B total parameters. Our team rules, which are stricter until organizers clarify:

- Count total parameters, including inactive MoE experts and any draft, embedding, critic, or judge model.
- One shared Lightning deployment serves every agent role.
- The `gpt-oss:120b-cloud` runs are development experiments only and stay outside the submitted workflow.
- Do not add a model without updating [model-inventory.md](model-inventory.md) and checking the remaining budget.

The inventory shows why accounting needs organizer confirmation. Lightning is "30B" on its model card, but the Hugging Face metadata for its BF16 checkpoint reports 31.58B (total) or 32.91B (per-dtype sum) tensor elements. Adding the DSpark draft model (0.76B stored elements) could exceed 33B.

## 2a. Compute and accounts

All model serving and live model calls for bounty work run on GPU instances paid for with the **event-provided NVIDIA Brev credits**. Do not use personal machines, personal cloud accounts, or personal credits, including the personal Ollama cloud account used for the earlier `gpt-oss:120b-cloud` development runs. Record the Brev instance type, GPU, driver, and region with every serving run. Scripted mode (`--proposals scripted`) needs no GPU and remains the default for development and CI.

## 3. Bounties

### 3.1 Best Use of NVIDIA Nemotron 3.5 Lightning

| | |
|---|---|
| Owner | Owner 2 (agents and orchestration) |
| Verified organizer requirements | Track name only (event page). Track requirements: **unknown** |
| Planned integration | Lightning as the single model for the investigator, planner, and tester roles, plus the single-agent topology, through one shared vLLM endpoint. Separate role contexts. Structured outputs where supported, validated in code by `migproto/plans.py` |
| Current status | **planned, not started.** No Lightning run exists. The only live runs used `gpt-oss:120b-cloud` (development only). The default model in `migproto/agents/llm.py` is still `gpt-oss:120b-cloud` and must change before submission (Owner 2 task 1) |
| Implemented features usable for it | OpenAI-compatible client with configurable endpoint (`MIGPROTO_LLM_*`), JSON-mode requests, schema validation, scripted fallback, per-call usage and latency in `agents/transcript-*.json` |
| Evidence locations (planned) | `runs/<id>-agent-loop-llm/` locally, promoted to `examples/lightning-*` with `scripts/export_example.py`, plus a measurements table in `docs/results.md` (to be created by Owner 2) |
| Measurements (planned) | valid-proposal rate (schema-valid plans and tester proposals / model calls), per-call latency, prompt and completion tokens, successful final plans, attempts used. Per topology (single versus roles), same model, tools, evidence, budgets |

### 3.2 Best Use of Red Hat vLLM

| | |
|---|---|
| Owner | Owner 2 |
| Verified organizer requirements | Track name only. Whether "Red Hat vLLM" means upstream vLLM, Red Hat AI Inference Server, or another Red Hat distribution: **unknown** |
| Planned integration | Serve Lightning with vLLM (≥ 0.27.1) as the one shared deployment for all roles. Record image or package version, exact flags, checkpoint revision, GPU, and driver. Optional prefix-caching and concurrency experiments only after the core loop works (priority 4) |
| Current status | **planned, not started.** No vLLM deployment exists. Serve on an H100-class GPU provisioned with the **event-provided NVIDIA Brev credits** (NVIDIA cloud). Do not use Harsha's local GPU (RTX 3070 Ti Laptop, 8 GB, too small anyway: NVFP4 weights are about 18 GB) or any teammate's personal accounts or credits |
| Evidence locations (planned) | `docs/serving.md` (command, config, hardware, `vllm --version`, server log excerpt), `agent_config` events in runs |

### 3.3 Best Use of NVIDIA NeMo Gym

| | |
|---|---|
| Owner | Owner 1 (Harsha) |
| Verified organizer requirements | Track name only. Whether the bounty requires model training (RL or SFT) or accepts an environment plus evaluation: **unknown** |
| Planned integration | Wrap the existing migration task and deterministic verifier as a NeMo Gym resources server that calls the existing runner (no second execution path). Tools: investigation search, plan submission, test execution. `verify()` scores final-plan completion and mandatory compatibility and data-preservation checks, and reports setup errors separately. Evaluate baseline and final systems on the same frozen tasks |
| Current status | **implemented, not run against a NeMo Gym trainer.** `gym/`: resources server for nemo-gym 0.7.0, 14 frozen single-step tasks, `/verify` through `runner_api.execute_plan`. Verified only with scripted plans and server unit tests (`gym/README.md`). One verification at a time: per-session port allocation is still open (`gym/HANDOFF.md`) |
| Claim limits | An evaluation environment does not by itself show training or weight improvement. Report measured system or prompt improvements as such |
| Evidence locations (planned) | `gym/` (resources server, config, frozen task JSONL), `gym/results/` (trajectories, scores, config), reproduction steps in `gym/README.md` |

## 4. Priority

1. Working small-model loop: Lightning served through vLLM (Owner 2).
2. Integrated evidence and UI demonstration (Owner 3, with Harsha's v1 events).
3. NeMo Gym adapter and reproducible evaluation (Owner 1).
4. Optional serving-performance experiments (Owner 2).

## 5. Reproduction commands

Implemented today, no model required:

```bash
uv run migproto setup && uv run migproto baseline --skip-pytest
uv run migproto agent-loop --proposals scripted
```

**Planned, not yet executed by us.** Serving recipe adapted from the model card's "1x H100 (max throughput)" configuration, which uses no speculative decoding and therefore no draft model. Pin the revision and re-check flags against the card when provisioning:

```bash
pip install "vllm==0.27.1"     # or the vllm/vllm-openai:v0.27.1 image; record the exact version used
VLLM_HUMMING_MOE_GEMM_TYPE=indexed \
vllm serve --model nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 \
    --revision bee7596271d1495f6992ae224aefde4410e816b8 \
    --served-model-name nemotron-3.5-lightning \
    --max-num-seqs 256 --max-num-batched-tokens 16384 \
    --enable-prefix-caching --async-scheduling \
    --mamba-backend flashinfer --moe-backend humming --linear-backend humming \
    --mamba-ssu-algorithm horizontal --mamba-cache-mode align \
    --mamba-ssm-cache-dtype float16 --enable-mamba-cache-stochastic-rounding \
    --mamba-cache-philox-rounds 5 \
    --reasoning-parser nemotron_v3 --tool-call-parser qwen3_coder --enable-auto-tool-choice \
    --host 0.0.0.0 --port 8000

export MIGPROTO_LLM_BASE_URL=http://<gpu-host>:8000/v1
export MIGPROTO_LLM_MODEL=nemotron-3.5-lightning
uv run migproto agent-loop --proposals llm
```

Notes from the model card that affect our client: reasoning is on by default and is split out by `--reasoning-parser nemotron_v3`, and the card recommends `chat_template_kwargs: {"force_nonempty_content": true}` for coding agents. `llm.py` reads only `message.content`, so Owner 2 must confirm content is non-empty and decide the thinking setting. The card recommends temperature 1.0 and top_p 0.95, while `llm.py` sends temperature 0, which is a choice to record.

## 6. Unresolved eligibility questions

1. What are the written requirements for each bounty track, and do the teammate proposal's "kickoff requirements" match them?
2. Parameter accounting:
   - Does the 33B limit use the model card's nominal 30B or counted checkpoint tensors (31.58B to 32.91B for the BF16 checkpoint)?
   - Do MTP layers count?
   - Do draft models (DSpark 0.76B stored elements, DFlash) count?
   - Is one shared deployment serving several roles counted once?
3. Does "Red Hat vLLM" require Red Hat AI Inference Server or another Red Hat build, or does upstream vLLM qualify?
4. Does the NeMo Gym bounty require training (weight updates), or does it accept an environment plus evaluation of an agent system?
5. Can one submission be considered for several bounty tracks and a main track at the same time?
6. Are development-time models outside the budget (our `gpt-oss:120b-cloud` runs) acceptable if they are excluded from the submitted system and labeled as such?
