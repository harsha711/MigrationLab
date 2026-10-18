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
# The resolver pulls nvcc, crt and nvvm 13.4 while torch's CUDA runtime headers are 13.0, and
# FlashInfer's JIT build refuses mismatched compiler and headers. Pin the compiler to the
# runtime's release (13.0.88, the version cuda-toolkit 13.0.3 itself requires).
uv pip install --python "$OUT/venv/bin/python" \
  "nvidia-cuda-nvcc==13.0.88.*" "nvidia-cuda-crt==13.0.88.*" "nvidia-nvvm==13.0.88.*"

# The PyPI vLLM wheel uses CUDA 13 PyTorch. On an instance whose driver is older (Brev's
# Scaleway H100 image ships driver 570 / CUDA 12.8), CUDA_COMPAT=13-0 installs NVIDIA's
# forward-compatibility libraries (data-center GPUs only) and puts them first on the path.
if [ -n "${CUDA_COMPAT:-}" ]; then
  dpkg -s "cuda-compat-$CUDA_COMPAT" >/dev/null 2>&1 \
    || sudo apt-get install -y "cuda-compat-$CUDA_COMPAT"
  export LD_LIBRARY_PATH="/usr/local/cuda-${CUDA_COMPAT/-/.}/compat${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
# Humming (the Hopper NVFP4 fallback) JIT-compiles kernels through a helper binary that loads
# libnvrtc-builtins by name; the pip CUDA libraries are not on the default search path.
CU_LIB=$("$OUT/venv/bin/python" -c "import nvidia, os; print(os.path.join(nvidia.__path__[0], 'cu13', 'lib'))" 2>/dev/null || true)
[ -d "$CU_LIB" ] && export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$CU_LIB"
# Humming also builds a C++ launcher with torch.utils.cpp_extension, which needs ninja (in the
# venv) on PATH and a CUDA toolkit; use the pip one when the image has none.
export PATH="$OUT/venv/bin:$PATH"
if [ -z "${CUDA_HOME:-}" ] && [ -d "$CU_LIB" ]; then
  export CUDA_HOME="${CU_LIB%/lib}"
  # FlashInfer links its JIT ops with -L$CUDA_HOME/lib64{,/stubs} -lcudart -lcuda; the pip
  # layout has lib/ and only versioned sonames, so add the links a full toolkit would have.
  ln -sfn lib "$CUDA_HOME/lib64"
  ln -sf libcudart.so.13 "$CU_LIB/libcudart.so"
  mkdir -p "$CU_LIB/stubs"
  LIBCUDA=/usr/lib/x86_64-linux-gnu/libcuda.so
  [ -n "${CUDA_COMPAT:-}" ] && LIBCUDA="/usr/local/cuda-${CUDA_COMPAT/-/.}/compat/libcuda.so"
  ln -sf "$LIBCUDA" "$CU_LIB/stubs/libcuda.so"
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
COMMAND="LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-} CUDA_HOME=${CUDA_HOME:-} ${CUDA_COMPAT:+(cuda-compat-$CUDA_COMPAT) }$COMMAND"

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
