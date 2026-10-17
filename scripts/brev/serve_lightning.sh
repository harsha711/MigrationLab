#!/usr/bin/env bash
# Runs ON the Brev GPU instance (event-provided NVIDIA Brev credits only).
#
# Serves the pinned Nemotron 3.5 Lightning checkpoint with vLLM on 127.0.0.1:8000. The
# endpoint is never exposed publicly: reach it from the runner machine through an SSH tunnel
# (see docs/serving.md). Before serving, it records the hardware, driver, CUDA, vLLM version and
# exact launch command in $OUT, and writes $OUT/migproto-serving.env with the MIGPROTO_*
# variables the controller records in agent_config.
#
# Usage: bash serve_lightning.sh            (HF_TOKEN in the environment if the model is gated)
#        EXTRA_FLAGS="--max-model-len 65536" bash serve_lightning.sh
set -euo pipefail

VLLM_VERSION=${VLLM_VERSION:-0.27.1}
MODEL=nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4
REVISION=bee7596271d1495f6992ae224aefde4410e816b8
SERVED_NAME=nemotron-3.5-lightning
PORT=${PORT:-8000}
OUT=${OUT:-$HOME/migproto-serving}
mkdir -p "$OUT"

if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
if [ ! -x "$OUT/venv/bin/vllm" ]; then
  uv venv "$OUT/venv" --python 3.12
  uv pip install --python "$OUT/venv/bin/python" "vllm==$VLLM_VERSION"
fi

# The PyPI vLLM wheel uses CUDA 13 PyTorch. On an instance whose driver is older (Brev's
# Scaleway H100 image ships driver 570 / CUDA 12.8), CUDA_COMPAT=13-0 installs NVIDIA's
# forward-compatibility libraries (data-center GPUs only) and puts them first on the path.
if [ -n "${CUDA_COMPAT:-}" ]; then
  dpkg -s "cuda-compat-$CUDA_COMPAT" >/dev/null 2>&1 \
    || sudo apt-get install -y "cuda-compat-$CUDA_COMPAT"
  export LD_LIBRARY_PATH="/usr/local/cuda-${CUDA_COMPAT/-/.}/compat${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

# "1x H100 (max throughput)" recipe from the model card (docs/bounties.md, section 5).
# No speculative decoding, so no draft model (docs/model-inventory.md).
FLAGS=(
  --revision "$REVISION"
  --served-model-name "$SERVED_NAME"
  --max-num-seqs 256 --max-num-batched-tokens 16384
  --enable-prefix-caching --async-scheduling
  --mamba-backend flashinfer --moe-backend humming --linear-backend humming
  --mamba-ssu-algorithm horizontal --mamba-cache-mode align
  --mamba-ssm-cache-dtype float16 --enable-mamba-cache-stochastic-rounding
  --mamba-cache-philox-rounds 5
  --reasoning-parser nemotron_v3 --tool-call-parser qwen3_coder --enable-auto-tool-choice
  --host 127.0.0.1 --port "$PORT"
)
# shellcheck disable=SC2206
[ -n "${EXTRA_FLAGS:-}" ] && FLAGS+=($EXTRA_FLAGS)

GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader | sort | uniq -c | sed 's/^ *//' | paste -sd ';')
DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
CUDA=$(nvidia-smi | sed -n 's/.*CUDA Version: *\([0-9.]*\).*/\1/p' | head -1)
VLLM_INSTALLED=$("$OUT/venv/bin/python" -c "import vllm; print(vllm.__version__)")
COMMAND="VLLM_HUMMING_MOE_GEMM_TYPE=indexed vllm serve $MODEL ${FLAGS[*]}"
[ -n "${CUDA_COMPAT:-}" ] && COMMAND="LD_LIBRARY_PATH=$LD_LIBRARY_PATH (cuda-compat-$CUDA_COMPAT) $COMMAND"

{
  echo "recorded_at: $(date -u +%FT%TZ)"
  echo "host: $(hostname)"
  echo "gpu: $GPU"
  echo "driver: $DRIVER"
  echo "cuda_driver_api: $CUDA"
  "$OUT/venv/bin/python" -c "import torch, vllm; print('vllm:', vllm.__version__); print('torch:', torch.__version__, 'cuda', torch.version.cuda)"
  echo "command: $COMMAND"
  echo
  nvidia-smi
} > "$OUT/environment.txt"

{
  echo "export MIGPROTO_LLM_MODEL=$SERVED_NAME"
  echo "export MIGPROTO_LLM_REVISION=$REVISION"
  echo "export MIGPROTO_SERVING_BACKEND=vllm"
  echo "export MIGPROTO_SERVING_VERSION=$(printf '%q' "$VLLM_INSTALLED")"
  echo "export MIGPROTO_SERVING_FLAGS=$(printf '%q' "$COMMAND")"
  echo "export MIGPROTO_SERVING_HARDWARE=$(printf '%q' "$GPU")"
  echo "export MIGPROTO_SERVING_DRIVER=$(printf '%q' "$DRIVER")"
  echo "export MIGPROTO_SERVING_CUDA=$(printf '%q' "$CUDA")"
  echo "export MIGPROTO_PREFIX_CACHING=true"
  echo "export MIGPROTO_REASONING_PARSER=nemotron_v3"
} > "$OUT/migproto-serving.env"

echo "environment recorded in $OUT/environment.txt and $OUT/migproto-serving.env"
echo "serving $MODEL@$REVISION as $SERVED_NAME on 127.0.0.1:$PORT (log: $OUT/vllm.log)"
VLLM_HUMMING_MOE_GEMM_TYPE=indexed "$OUT/venv/bin/vllm" serve "$MODEL" "${FLAGS[@]}" 2>&1 \
  | tee "$OUT/vllm.log"
