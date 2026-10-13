#!/bin/sh
# One-time setup for the Manifold kernel: its package (package.json beside
# this script) in the kernel's runtime folder
# ($CAD_KERNELS_HOME/manifold, by default ~/.cache/text-to-cad/kernels/manifold).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
runtime="${CAD_KERNELS_HOME:-$HOME/.cache/text-to-cad/kernels}/manifold"
mkdir -p "$runtime"
cp "$here/package.json" "$runtime/package.json"
npm install --prefix "$runtime" --no-package-lock --no-audit --no-fund
echo "Manifold kernel ready: $runtime/node_modules"
