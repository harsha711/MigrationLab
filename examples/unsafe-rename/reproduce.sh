#!/usr/bin/env bash
# Reproduce the intentionally introduced unsafe rename (not an upstream bug).
set -euo pipefail
cd <repo>
uv run migproto setup && uv run migproto baseline --skip-pytest && uv run migproto unsafe
