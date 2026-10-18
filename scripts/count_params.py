"""Count a Hugging Face checkpoint's parameters from its safetensors headers (no weights).

For the 33B parameter rule (docs/model-inventory.md). Each safetensors file starts with an
8-byte little-endian header length followed by a JSON header listing every tensor's dtype and
shape, so two HTTP range requests per file are enough; the weights are never downloaded.

    uv run python scripts/count_params.py --repo nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 \\
        --revision bee7596271d1495f6992ae224aefde4410e816b8 [--json out.json] [--cache DIR]

Counting rules (stated in the output, so a reader can disagree with them):
- A tensor's stored elements are the product of its shape.
- NVFP4 weights are stored as U8 with two FP4 values per byte, so each packed U8 `.weight`
  counts twice (the last dimension is halved on disk). This is only applied when the tensor
  has a sibling `.weight_scale` (the per-block FP8 scale that NVFP4 always carries); any other
  U8 tensor is reported, not unpacked.
- Quantization metadata (`weight_scale`, `weight_scale_2`, `input_scale`, `k_scale`,
  `v_scale`) is not a model parameter: it is counted separately and excluded from the total.
- Tensors whose name contains `mtp` (multi-token prediction layers) are reported as their own
  group so they can be included or excluded by the accounting rule; they are in the total.
"""

import argparse
import json
import re
import struct
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from math import prod
from pathlib import Path

import requests

HF = "https://huggingface.co"
QUANT_SUFFIXES = ("weight_scale", "weight_scale_2", "input_scale", "k_scale", "v_scale")
MTP = re.compile(r"(^|\.)mtp(\.|$)")


def _get(url: str, start: int, end: int, tries: int = 5) -> bytes:
    for i in range(tries):
        try:
            r = requests.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=60)
            r.raise_for_status()
            if r.status_code != 206:
                raise RuntimeError(f"{url}: server ignored the range request ({r.status_code})")
            return r.content
        except (requests.ConnectionError, requests.Timeout):
            if i == tries - 1:
                raise
            time.sleep(2 ** i)
    raise AssertionError


def safetensors_files(repo: str, revision: str) -> list[str]:
    r = requests.get(f"{HF}/api/models/{repo}/revision/{revision}", timeout=60)
    r.raise_for_status()
    info = r.json()
    if info.get("sha") != revision:
        raise RuntimeError(f"{repo}: asked for {revision}, API returned {info.get('sha')}")
    return sorted(s["rfilename"] for s in info["siblings"]
                  if s["rfilename"].endswith(".safetensors"))


def header(repo: str, revision: str, name: str, cache: Path | None) -> dict:
    cached = cache / repo.replace("/", "__") / revision / f"{name}.json" if cache else None
    if cached and cached.exists():
        return json.loads(cached.read_text())
    url = f"{HF}/{repo}/resolve/{revision}/{name}"
    (n,) = struct.unpack("<Q", _get(url, 0, 7))
    raw = _get(url, 8, 8 + n - 1)
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(raw)
    return json.loads(raw)


def classify(tensors: dict[str, dict]) -> dict:
    """tensors: name -> {"dtype", "shape"} for the whole checkpoint."""
    stored_by_dtype = defaultdict(int)
    groups = defaultdict(lambda: {"tensors": 0, "stored": 0, "params": 0})
    other_u8 = []
    for name, t in tensors.items():
        stored = prod(t["shape"])
        stored_by_dtype[t["dtype"]] += stored
        base, _, leaf = name.rpartition(".")
        if leaf in QUANT_SUFFIXES:
            group, params = "quant_metadata", 0
        elif t["dtype"] == "U8":
            if leaf == "weight" and f"{base}.weight_scale" in tensors:
                group, params = "nvfp4_packed", 2 * stored
            else:
                group, params = "u8_unpacked", stored
                other_u8.append(name)
        else:
            group, params = f"plain_{t['dtype']}", stored
        if MTP.search(name):
            group = f"mtp:{group}"
        g = groups[group]
        g["tensors"] += 1
        g["stored"] += stored
        g["params"] += params
    total = sum(g["params"] for g in groups.values())
    mtp = sum(g["params"] for k, g in groups.items() if k.startswith("mtp:"))
    return {
        "tensors": len(tensors),
        "stored_elements_by_dtype": dict(sorted(stored_by_dtype.items())),
        "stored_elements_total": sum(stored_by_dtype.values()),
        "groups": dict(sorted(groups.items())),
        "parameters_total": total,
        "parameters_mtp": mtp,
        "parameters_without_mtp": total - mtp,
        "quant_metadata_elements": sum(g["stored"] for k, g in groups.items()
                                       if k.endswith("quant_metadata")),
        "u8_not_unpacked": other_u8,
    }


def count(repo: str, revision: str, cache: Path | None = None, workers: int = 4) -> dict:
    files = safetensors_files(repo, revision)
    with ThreadPoolExecutor(workers) as ex:
        headers = list(ex.map(lambda f: header(repo, revision, f, cache), files))
    tensors = {}
    for f, h in zip(files, headers):
        for name, t in h.items():
            if name == "__metadata__":
                continue
            if name in tensors:
                raise RuntimeError(f"{name} appears in more than one file")
            tensors[name] = {"dtype": t["dtype"], "shape": t["shape"], "file": f}
    return {"repo": repo, "revision": revision, "files": len(files), **classify(tensors)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--revision", required=True, help="full commit sha")
    ap.add_argument("--cache", type=Path, help="directory for downloaded headers")
    ap.add_argument("--json", type=Path, help="write the full result here")
    args = ap.parse_args(argv)
    result = count(args.repo, args.revision, args.cache)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{result['repo']} @ {result['revision']}: {result['files']} files, "
          f"{result['tensors']} tensors")
    for k, v in result["stored_elements_by_dtype"].items():
        print(f"  stored {k:>8}: {v:>16,}")
    for k, g in result["groups"].items():
        print(f"  {k:<26} tensors {g['tensors']:>6}  stored {g['stored']:>16,}  "
              f"params {g['params']:>16,}")
    print(f"  parameters total        : {result['parameters_total']:>16,}")
    print(f"  of which MTP            : {result['parameters_mtp']:>16,}")
    print(f"  quantization metadata   : {result['quant_metadata_elements']:>16,} (excluded)")
    if result["u8_not_unpacked"]:
        print(f"  U8 tensors not unpacked : {len(result['u8_not_unpacked'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
