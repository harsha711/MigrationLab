# Model Inventory and Parameter Budget

Budget (event page, verified): total parameters ≤ 33B. Team rule: count total parameters including inactive MoE experts, and include every draft, embedding, critic, or judge model in the submitted system. Update this file **before** adding or swapping any model. Owner of this file: Owner 2 (models). Harsha reviews.

## Submitted system (planned)

| Model | Checkpoint and revision | Role | Parameters | Status |
|---|---|---|---|---|
| NVIDIA Nemotron 3.5 Lightning | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` @ `bee7596271d1495f6992ae224aefde4410e816b8` | all agent roles (investigator, planner, tester, single agent), one shared vLLM deployment | **Counted from safetensors headers: 32,913,266,240** (both checkpoints), of which 1,335,325,952 are the MTP layers; 31,577,940,288 without MTP. Model card: 30B total, 3B active. See "Parameter count from safetensors headers" | Agent client/controller implemented; deployment and live run still **planned** |
| Embedding, critic, judge models | none | none | 0 | none in use |

## Excluded unless organizers confirm accounting

| Model | Why considered | Parameters | Decision |
|---|---|---|---|
| DSpark draft `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark` | speculative decoding (latency) | 763,713,984 stored elements (BF16 plus packed U8). The true count may be higher | **excluded**. With the 32.91B figure the total would exceed 33B |
| DFlash draft | speculative decoding | not checked | **excluded** |
| MTP layers | built into the Lightning checkpoint (`mtp.*` tensors) | **1,335,325,952**, counted from the headers; they are part of the 32.91B total and **not** part of Hugging Face's 31.58B | served only if accounting allows. The recommended H100 max-throughput recipe uses no speculative decoding |

## Development-only (never part of the submitted workflow)

| Model | Where used | Parameters | Notes |
|---|---|---|---|
| `gpt-oss:120b-cloud` via local Ollama (cloud-backed) | live run `20261017-123652-agent-loop-llm` (`examples/live-ordering-revision`) | about 120B class (far above budget) | development smoke test only, run through a personal Ollama cloud account. Do not use it again. It is no longer the client default; live runs require an explicit model id |

## Budget calculation

| Accounting assumption | Lightning | Drafts | Total | Within 33B? |
|---|---|---|---|---|
| Model card nominal, no drafts | 30B | 0 | 30B | yes |
| Header count without MTP (equals HF `safetensors.total` + 2,944), no drafts | 31,577,940,288 | 0 | 31.58B | yes, 1,422,059,712 headroom |
| **Header count, all tensors including MTP, no drafts (recommended conservative figure)** | **32,913,266,240** | 0 | **32.91B** | **yes, 86,733,760 headroom (0.087B)** |
| Header count + DSpark | 32,913,266,240 | ≥ 763,713,984 | ≥ 33.68B | **no** |

The conservative figure leaves 86.7M parameters of headroom: no other model of any useful size fits beside Lightning unless organizers confirm that the unused MTP layers do not count.

## Parameter count from safetensors headers

Measured on 2026-10-17 with `scripts/count_params.py` (reproduce: `uv run python scripts/count_params.py --repo <checkpoint> --revision <sha> --json out.json`). The script reads only each file's header (two HTTP range requests per file: the 8-byte length, then the JSON header) and never downloads weights. It checks that the API returns the requested revision.

| | NVFP4 @ `bee7596271d1495f6992ae224aefde4410e816b8` | BF16 @ `a9904d24bcc1d289a1950fa9d2b978c47cf903b9` |
|---|---|---|
| Files / tensors | 52 / 18,487 | 14 / 6,513 |
| Stored elements by dtype | BF16 1,828,973,120; F32 7,922,583; F8_E4M3 2,776,897,536; U8 15,093,055,488 | BF16 32,913,263,168; F32 3,072 |
| NVFP4-packed weights (U8, two FP4 values per byte, × 2) | 5,935 tensors, 15,093,055,488 bytes → 30,186,110,976 | none |
| FP8 weights (Mamba `in_proj` / `out_proj`) | 46 tensors, 890,265,600 | none (BF16) |
| Unquantized BF16 / F32 tensors outside MTP | 493,647,296 + 7,916,416 | 31,577,937,344 + 2,944 |
| MTP tensors (`mtp.layers.0`, `mtp.layers.1`; BF16 in both) | 1,335,325,952 | 1,335,325,952 |
| Quantization metadata (`weight_scale`, `weight_scale_2`, `input_scale`), **excluded** | 11,974 tensors, 1,886,637,975 elements | none |
| **Parameters** | **32,913,266,240** | **32,913,266,240** |

**Cross-check:** the NVFP4 count, after unpacking and excluding scales, equals the BF16 count exactly. That confirms the unpacking rule (only U8 `.weight` tensors that have a sibling `.weight_scale` are doubled; no other U8 tensor exists) and that the quantized export drops no tensors.

**31.58B versus 32.91B:**
- Hugging Face's `safetensors.total` (31,577,937,344) is not computed by Hugging Face from the tensors. It is copied from `metadata.total_parameters` in the BF16 repo's `model.safetensors.index.json`, written at export.
- That figure equals exactly the non-MTP BF16 tensors. It leaves out the 270 `mtp.*` tensors (1,335,325,952 parameters) and 23 non-MTP F32 tensors (2,944 elements: `mixer.gate.e_score_correction_bias`, 128 values, one per routed expert, in each of 23 MoE layers).
- The 32.91B figure is the Hugging Face API's per-dtype listing summed (BF16 32,913,263,168 + F32 3,072), which counts every tensor. The previous version of this file gave 32,913,263,168 as the per-dtype sum; that was the BF16 entry alone.
- The NVFP4 repo's API figure (17.8B) counts packed bytes and scale tensors as elements, so it is not a parameter count.

**Measured:** tensor names, dtypes and shapes at the two pinned revisions; the sums above.
**Assumed, not verified:**
- Quantization scales are metadata, not parameters. Counting them would add 1.89B and put the NVFP4 checkpoint over 33B as stored; the BF16 reference shows they are not part of the model.
- The 2,944 F32 elements are model parameters (included). They are negligible either way.
- `config.json` says `num_nextn_predict_layers: 1`, but the checkpoint holds `mtp.layers.0` and `mtp.layers.1`. We did not check how vLLM maps them. Whether MTP weights count when speculative decoding is off is an accounting question for the organizers; the conservative figure includes them.
- The model card's "30B" is nominal; its basis is not stated.
