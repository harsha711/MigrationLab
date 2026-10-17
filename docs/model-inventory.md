# Model Inventory and Parameter Budget

Budget (event page, verified): total parameters ≤ 33B. Team rule: count total parameters including inactive MoE experts, and include every draft, embedding, critic, or judge model in the submitted system. Update this file **before** adding or swapping any model. Owner of this file: Owner 2 (models). Harsha reviews.

## Submitted system (planned)

| Model | Checkpoint and revision | Role | Parameters | Status |
|---|---|---|---|---|
| NVIDIA Nemotron 3.5 Lightning | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` @ `bee7596271d1495f6992ae224aefde4410e816b8` | all agent roles (investigator, planner, tester, single agent), one shared vLLM deployment | Model card: **30B total, 3B active**. Hugging Face metadata for the BF16 reference checkpoint (`...-BF16` @ `a9904d24bcc1d289a1950fa9d2b978c47cf903b9`): `safetensors.total` = 31,577,937,344, per-dtype sum = 32,913,263,168. The NVFP4 metadata (17.8B stored elements) counts packed 4-bit values, so it is not a parameter count | Agent client/controller implemented; deployment and live run still **planned** |
| Embedding, critic, judge models | none | none | 0 | none in use |

## Excluded unless organizers confirm accounting

| Model | Why considered | Parameters | Decision |
|---|---|---|---|
| DSpark draft `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark` | speculative decoding (latency) | 763,713,984 stored elements (BF16 plus packed U8). The true count may be higher | **excluded**. With the 32.91B figure the total would exceed 33B |
| DFlash draft | speculative decoding | not checked | **excluded** |
| MTP layers | built into the Lightning checkpoint | presumably included in the checkpoint counts above, but this is not confirmed | served only if accounting allows. The recommended H100 max-throughput recipe uses no speculative decoding |

## Development-only (never part of the submitted workflow)

| Model | Where used | Parameters | Notes |
|---|---|---|---|
| `gpt-oss:120b-cloud` via local Ollama (cloud-backed) | live run `20261017-123652-agent-loop-llm` (`examples/live-ordering-revision`) | about 120B class (far above budget) | development smoke test only, run through a personal Ollama cloud account. Do not use it again. It is no longer the client default; live runs require an explicit model id |

## Budget calculation

| Accounting assumption | Lightning | Drafts | Total | Within 33B? |
|---|---|---|---|---|
| Model card nominal, no drafts | 30B | 0 | 30B | yes |
| BF16 `safetensors.total`, no drafts | 31.58B | 0 | 31.58B | yes |
| BF16 per-dtype sum, no drafts | 32.91B | 0 | 32.91B | yes, 0.09B headroom |
| BF16 per-dtype sum + DSpark | 32.91B | ≥ 0.76B | ≥ 33.67B | **no** |

Open task (Owner 2): count parameters directly from the downloaded checkpoint's safetensors headers (sum of tensor element counts, unpacking FP4), explain the 31.58B versus 32.91B difference, and record the method here. Raw sources: `https://huggingface.co/api/models/<checkpoint>` fields `sha` and `safetensors`, fetched 2026-10-17.
