#!/bin/sh
# One-time setup for the SDM kernel: a Python 3.13 venv with
# emergent-matter-sdm-core, which Emergent Matter publishes on its own package
# index (not PyPI), in the kernel's runtime folder
# ($CAD_KERNELS_HOME/sdm, by default ~/.cache/text-to-cad/kernels/sdm).
# Needs uv (https://docs.astral.sh/uv/).
set -eu
runtime="${CAD_KERNELS_HOME:-$HOME/.cache/text-to-cad/kernels}/sdm"
mkdir -p "$runtime"
uv venv --python 3.13 "$runtime/.venv"
uv pip install --python "$runtime/.venv/bin/python" \
  "emergent-matter-sdm-core[export]" \
  --index https://get.softwaredefinedmatter.com/simple
echo "SDM kernel ready: $runtime/.venv"
